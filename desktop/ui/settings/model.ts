// The settings screen as state and transitions, with no DOM (ADR-0066). The AI
// model is read and changed through the local API's settings routes, previewed
// before it is applied; turning on a cloud model waits for the warning to be
// confirmed. The key goes only to the shell's keychain commands (ADR-0070) and
// is never kept here once sent. The library is chosen through the shell's
// workspace commands (ADR-0071). Every word comes from wording.ts.
import {
  ApiError,
  ApiKeyError,
  retryWhileBusy,
  UpdateError,
  type Schemas,
  type UpdateCheck,
  type UpdateSettings,
  type WatchStatus,
  type WorkspaceChoice,
} from "../../client/index.ts";
import type { Undo } from "../review/model.ts";
import { problemFor, titleOf, undoneLine } from "../review/wording.ts";
import { type Refusal, welcomeRefusal } from "../setup/wording.ts";
import {
  WATCH,
  WORDS,
  cloudWarning,
  keyAfterSaveFailure,
  keyFailure,
  saveFailure,
  savedNote,
  updateFailure,
  watchFailure,
  watchCommandFailure,
} from "./wording.ts";

type Settings = Schemas["ModelSettings"];
type Preview = Schemas["ModelSettingsPreview"];
type Inbox = Schemas["InboxSettings"];
type InboxPreview = Schemas["InboxSettingsPreview"];
type Activity = Schemas["UnattendedActivity"];

/**
 * The AI models a library uses on this Mac when it comes back from the cloud:
 * the engine's packaged defaults (`config.DEFAULT_MODEL`,
 * `config.DEFAULT_EMBEDDING_MODEL`), which a test holds equal to them.
 */
export const LOCAL_MODELS = { model: "qwen3:8b", embeddingModel: "bge-m3" } as const;

/** The local API's settings calls (`client/index.ts`) for one engine launch. */
export interface SettingsApi {
  modelSettings(): Promise<Settings>;
  previewModelSettings(body: Schemas["ModelSettingsPreviewRequest"]): Promise<Preview>;
  applyModelSettings(body: Schemas["ModelSettingsApplyRequest"]): Promise<Schemas["ModelSettingsResponse"]>;
  unattendedActivity(): Promise<Activity>;
  inboxSettings(): Promise<Inbox>;
  previewInboxSettings(body: Schemas["InboxSettingsPreviewRequest"]): Promise<InboxPreview>;
  applyInboxSettings(body: Schemas["InboxSettingsApplyRequest"]): Promise<Schemas["InboxSettingsResponse"]>;
  reconciledPairs(): Promise<Schemas["ReconciledPairList"]>;
  declinedContradictions(): Promise<Schemas["DeclinedContradictionList"]>;
  readPage(conceptId: string): Promise<Schemas["Page"]>;
  previewUnreconcile(body: Schemas["UnreconcilePreviewRequest"]): Promise<Schemas["UnreconcilePreview"]>;
  applyUnreconcile(body: Schemas["UnreconcileApplyRequest"]): Promise<unknown>;
  previewReopenContradiction(body: Schemas["ContradictionReopenPreviewRequest"]): Promise<Schemas["ContradictionReopenPreview"]>;
  applyReopenContradiction(body: Schemas["ContradictionReopenApplyRequest"]): Promise<unknown>;
}

/** An earlier decision that can be undone from here: named by its pages. */
export interface UndoRow {
  key: string;
  text: string;
  undo: Exclude<Undo, { kind: "split" }>;
}

export type UndoList = "settled" | "declined";

/** The shell's own commands, which work whether or not the engine runs. */
export interface SettingsPorts {
  keys: { set(key: string): Promise<void>; clear(): Promise<void>; isSet(): Promise<boolean> };
  workspaces: {
    status(): Promise<WorkspaceChoice>;
    pickFolder(): Promise<string | null>;
    open(path: string): Promise<WorkspaceChoice>;
    create(path: string): Promise<WorkspaceChoice>;
  };
  /** The inbox watch (ADR-0078): the shell runs the daemon only while the person turned it on. */
  watch: {
    state(): Promise<WatchStatus>;
    set(on: boolean): Promise<WatchStatus>;
    pickFolder(): Promise<string | null>;
    /** Ask the shell to raise the notification that the watch paused on the limit. */
    notifyBudgetStop(): Promise<void>;
  };
  /** The app's own updates (ADR-0079): the shell checks, and installs only what the person confirms. */
  updates: {
    settings(): Promise<UpdateSettings>;
    setCheck(on: boolean): Promise<UpdateSettings>;
    check(): Promise<UpdateCheck>;
    install(version: string): Promise<void>;
  };
}

export type Where = "local" | "cloud";

export interface Draft {
  where: Where;
  baseUrl: string;
  model: string;
  embeddingModel: string;
  /** Typed, not yet sent; cleared once it is. */
  key: string;
}

export type DraftField = "baseUrl" | "model" | "embeddingModel" | "key";

export interface SettingsState {
  /** `waiting` until an engine runs; `failed` when the first read failed. */
  phase: "waiting" | "loading" | "ready" | "failed";
  settings: Settings | null;
  /** Whether the shell keeps a key; `null` when it did not say. */
  key: "saved" | "none" | "unsupported" | null;
  editing: boolean;
  draft: Draft;
  saving: boolean;
  removingKey: boolean;
  /** The cloud warning, while it waits for an answer. */
  confirming: { lines: string[] } | null;
  /** What the last change did. */
  note: string | null;
  problem: string | null;
  library: WorkspaceChoice | null;
  libraryBusy: "open" | "create" | null;
  libraryProblem: Refusal | null;
  /** A library was chosen; the app is starting it. */
  librarySwitching: boolean;
  /** Pairs settled as "both right" or "one replaces the other", each with its Undo. */
  settled: UndoRow[];
  /** Disagreements left as they are, each with its way to point it out again. */
  declined: UndoRow[];
  /** The key of the row being undone. */
  undoing: string | null;
  /** The watch folder and its limits; `null` until read. */
  inbox: Inbox | null;
  /** What the shell says of the watch; `null` until read. */
  watch: WatchStatus | null;
  watchBusy: "choosing" | "starting" | "stopping" | null;
  /** A folder chosen and previewed, waiting for the person to start watching it. */
  watchConfirm: { folder: string } | null;
  /** The person stopped the watch in this session. */
  stoppedByYou: boolean;
  /** The engine's latest unattended job outcome; `null` until read. */
  activity: Activity | null;
  /** Whether the app checks for updates; `null` until read. */
  updates: UpdateSettings | null;
  /** What the last check found; `null` before one ran. */
  update: UpdateCheck | null;
  updateBusy: "checking" | "switching" | "installing" | null;
  /** The person asked to install the update on offer: the confirmation is open. */
  updateConfirm: boolean;
  /** Why the last update command was refused. */
  updateProblem: string | null;
}

/**
 * What the persistent watch status says (ADR-0078, "Five"): `on`, `stopped` (the
 * person turned it off), `budget` (the daemon runs but its latest job ran out of
 * the AI model's limit: it idles, it does not exit, so only the engine's job
 * record can say so) or `failed` (the shell gave up restarting it). `null`:
 * nothing to say, because the watch was never turned on or cannot be read.
 */
export type WatchKind = "on" | "stopped" | "budget" | "failed";

export function watchKind(state: Pick<SettingsState, "watch" | "activity" | "inbox" | "stoppedByYou">): WatchKind | null {
  const watch = state.watch;
  if (watch === null || watch.state === "no_workspace") return null;
  if (watch.state === "failed") return "failed";
  if (!watch.wanted) return state.stoppedByYou || state.inbox?.inbox ? "stopped" : null;
  return state.activity?.budget_exhausted ? "budget" : "on";
}

/** The version on offer, or `null`: the sidebar names it until it is installed or turned off. */
export function updateOffered(state: Pick<SettingsState, "update">): string | null {
  return state.update?.state === "available" ? state.update.version : null;
}

/** A cloud model is on: the shell shows it while it lasts. */
export function cloudOn(state: Pick<SettingsState, "settings">): boolean {
  return state.settings?.is_local === false;
}

const EMPTY_DRAFT: Draft = { where: "local", baseUrl: "", model: "", embeddingModel: "", key: "" };

type Listener = (state: SettingsState) => void;

export class SettingsModel {
  state: SettingsState = {
    phase: "waiting",
    settings: null,
    key: null,
    editing: false,
    draft: EMPTY_DRAFT,
    saving: false,
    removingKey: false,
    confirming: null,
    note: null,
    problem: null,
    library: null,
    libraryBusy: null,
    libraryProblem: null,
    librarySwitching: false,
    settled: [],
    declined: [],
    undoing: null,
    inbox: null,
    watch: null,
    watchBusy: null,
    watchConfirm: null,
    stoppedByYou: false,
    activity: null,
    updates: null,
    update: null,
    updateBusy: null,
    updateConfirm: false,
    updateProblem: null,
  };

  readonly #ports: SettingsPorts;
  readonly #listeners = new Set<Listener>();
  #api: SettingsApi | null = null;
  #pending: Preview | null = null;
  #watchPending: InboxPreview | null = null;
  /** The first look at the watch has been taken: a stop already there is not news. */
  #baselined = false;
  #wasPaused = false;

  constructor(ports: SettingsPorts) {
    this.#ports = ports;
  }

  subscribe(listener: Listener): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #set(patch: Partial<SettingsState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.#listeners) listener(this.state);
  }

  /** The engine of this launch, or `null` while none runs. Reads everything again. */
  async connect(api: SettingsApi | null): Promise<void> {
    this.#api = api;
    if (api === null) {
      this.#set({ phase: this.state.settings === null ? "waiting" : this.state.phase });
      return;
    }
    await this.load();
  }

  async load(): Promise<void> {
    const api = this.#api;
    if (api === null) return;
    if (this.state.settings === null) this.#set({ phase: "loading" });
    const [settings, key, library, inbox, watch, activity] = await Promise.allSettled([
      api.modelSettings(),
      this.#ports.keys.isSet(),
      this.#ports.workspaces.status(),
      api.inboxSettings(),
      this.#ports.watch.state(),
      api.unattendedActivity(),
    ]);
    if (api !== this.#api) return;
    this.#set({
      ...(inbox.status === "fulfilled" ? { inbox: inbox.value } : {}),
      ...(watch.status === "fulfilled" ? { watch: watch.value } : {}),
      ...(activity.status === "fulfilled" ? { activity: activity.value } : {}),
      ...(settings.status === "fulfilled"
        ? { phase: "ready" as const, settings: settings.value }
        : { phase: this.state.settings === null ? ("failed" as const) : this.state.phase }),
      key: keyState(key),
      ...(library.status === "fulfilled" ? { library: library.value } : {}),
      librarySwitching: false,
    });
    this.#noticeBudgetStop();
    await this.#loadUndoLists(api);
  }

  /** A list that cannot be read is shown empty; the settings above it stay. */
  async #loadUndoLists(api: SettingsApi): Promise<void> {
    const [pairs, declined] = await Promise.allSettled([api.reconciledPairs(), api.declinedContradictions()]);
    const rows = {
      pairs: pairs.status === "fulfilled" ? (pairs.value.pairs ?? []) : [],
      declined: declined.status === "fulfilled" ? (declined.value.contradictions ?? []) : [],
    };
    const ids = [...new Set([...rows.pairs.flatMap(pairIds), ...rows.declined.flatMap(pairIds)])];
    const read = await Promise.all(ids.map((id) => api.readPage(id).catch(() => null)));
    const pages = new Map<string, Schemas["Page"]>();
    for (const page of read) if (page !== null) pages.set(page.concept_id, page);
    if (api !== this.#api) return;
    this.#set({
      settled: rows.pairs.map((pair) => settledRow(pair, pages)),
      declined: rows.declined.map((item) => declinedRow(item, pages)),
    });
  }

  /** Undo one row after its own preview, then read the lists again. */
  async undo(list: UndoList, key: string): Promise<void> {
    const api = this.#api;
    const row = this.state[list].find((r) => r.key === key);
    if (api === null || row === undefined || this.state.undoing !== null) return;
    this.#set({ undoing: key, note: null, problem: null });
    let sent = false;
    let withdraws = false;
    try {
      let run: () => Promise<unknown>;
      if (row.undo.kind === "unreconcile") {
        const { version, withdraws_deprecation } = await api.previewUnreconcile({ first_id: row.undo.first, second_id: row.undo.second });
        run = () => api.applyUnreconcile({ version });
        withdraws = withdraws_deprecation;
      } else if (row.undo.kind === "reopen_contradiction") {
        const { version } = await api.previewReopenContradiction({
          first_id: row.undo.first,
          second_id: row.undo.second,
          merged_absorbed_id: row.undo.absorbed,
        });
        run = () => api.applyReopenContradiction({ version });
      } else {
        return;
      }
      sent = true;
      await retryWhileBusy(run);
    } catch (error) {
      this.#set({ undoing: null, problem: problemFor(error, sent ? "after" : "before").message });
      await this.#loadUndoLists(api);
      return;
    }
    this.#set({ undoing: null, note: undoneLine(row.undo, withdraws) });
    await this.#loadUndoLists(api);
  }

  edit(): void {
    const settings = this.state.settings;
    if (settings === null) return;
    const where: Where = settings.backend === "ollama" ? "local" : "cloud";
    this.#set({ editing: true, draft: this.#draftFor(where), note: null, problem: null });
  }

  cancel(): void {
    this.#pending = null;
    this.#set({ editing: false, draft: EMPTY_DRAFT, confirming: null, problem: null });
  }

  choose(where: Where): void {
    if (where === this.state.draft.where) return;
    this.#set({ draft: { ...this.#draftFor(where), key: this.state.draft.key }, problem: null });
  }

  /** Typing is kept without a redraw, so the field keeps its focus and caret. */
  setField(field: DraftField, value: string): void {
    this.state = { ...this.state, draft: { ...this.state.draft, [field]: value } };
  }

  /** The draft for `where`: the settings in use when they are of that kind. */
  #draftFor(where: Where): Draft {
    const settings = this.state.settings;
    const same = settings !== null && (settings.backend === "ollama") === (where === "local");
    if (where === "local") {
      return same
        ? { where, baseUrl: "", model: settings.model, embeddingModel: settings.embedding_model, key: "" }
        : { where, baseUrl: "", ...LOCAL_MODELS, key: "" };
    }
    return same
      ? { where, baseUrl: settings.base_url ?? "", model: settings.model, embeddingModel: settings.embedding_model, key: "" }
      : { where, baseUrl: "", model: "", embeddingModel: "", key: "" };
  }

  #change(): Schemas["ModelSettingsPreviewRequest"] {
    const { draft, settings } = this.state;
    if (draft.where === "local") {
      return { backend: "ollama", base_url: null, embedding_base_url: null, model: draft.model, embedding_model: draft.embeddingModel };
    }
    return {
      backend: "openai-compatible",
      base_url: draft.baseUrl,
      // A separate search address set by hand is kept while the service stays a cloud one.
      embedding_base_url: settings?.backend === "openai-compatible" ? settings.embedding_base_url : null,
      model: draft.model,
      embedding_model: draft.embeddingModel,
    };
  }

  /** Preview the draft; a new cloud service waits for the warning, anything else is applied. */
  async save(): Promise<void> {
    const api = this.#api;
    const current = this.state.settings;
    if (api === null || current === null || this.state.saving || !this.state.editing) return;
    this.#set({ saving: true, problem: null, note: null });
    let preview: Preview | null;
    try {
      preview = await api.previewModelSettings(this.#change());
    } catch (error) {
      if (!(error instanceof ApiError && error.code === "settings_unchanged")) {
        this.#set({ saving: false, problem: saveFailure(error) });
        return;
      }
      preview = null;
    }
    if (preview !== null && needsWarning(current, preview.proposed)) {
      this.#pending = preview;
      this.#set({ saving: false, confirming: { lines: cloudWarning(preview.proposed) } });
      return;
    }
    await this.#apply(api, preview);
  }

  /** The warning was confirmed: apply what it was shown for. */
  async confirm(): Promise<void> {
    const preview = this.#pending;
    const api = this.#api;
    if (preview === null || api === null) return;
    this.#pending = null;
    this.#set({ confirming: null });
    await this.#apply(api, preview);
  }

  /** The warning was declined: nothing changes and the draft stays. */
  dismiss(): void {
    this.#pending = null;
    this.#set({ confirming: null });
  }

  async #apply(api: SettingsApi, preview: Preview | null): Promise<void> {
    const key = this.state.draft.where === "cloud" ? this.state.draft.key.trim() : "";
    this.#set({ saving: true });
    let settings = this.state.settings;
    if (preview !== null) {
      try {
        const answer = await retryWhileBusy(() => api.applyModelSettings({ version: preview.version }));
        settings = answer.changed.settings;
      } catch (error) {
        this.#set({ saving: false, problem: saveFailure(error) });
        if (error instanceof ApiError && error.code === "stale") await this.load();
        return;
      }
    }
    // The settings go first: setting the key restarts the engine, which then
    // starts with both.
    let keyProblem: string | null = null;
    let keyState = this.state.key;
    if (key !== "") {
      try {
        await this.#ports.keys.set(key);
        keyState = "saved";
      } catch (error) {
        keyProblem = preview === null ? keyFailure(error) : keyAfterSaveFailure(error);
      }
    }
    const note =
      preview !== null && settings !== null
        ? savedNote(settings, preview.search_rebuild)
        : key !== "" && keyProblem === null
          ? WORDS.keySavedNote
          : null;
    this.#set({
      saving: false,
      settings,
      key: keyState,
      editing: keyProblem !== null && preview === null,
      draft: { ...EMPTY_DRAFT },
      note: keyProblem === null ? note : null,
      problem: keyProblem,
    });
  }

  async removeKey(): Promise<void> {
    if (this.state.removingKey) return;
    this.#set({ removingKey: true, problem: null, note: null });
    try {
      await this.#ports.keys.clear();
      this.#set({ removingKey: false, key: "none", note: WORDS.keyRemoved });
    } catch (error) {
      this.#set({ removingKey: false, problem: keyFailure(error) });
    }
  }

  /**
   * Read the watch again: the shell's state always, and the engine's job record
   * while the watch is wanted and an engine runs. A read that fails keeps what
   * was known. The daemon can stop on its own, so the page asks now and then.
   */
  async refreshWatch(): Promise<void> {
    const api = this.#api;
    const [watch] = await Promise.allSettled([this.#ports.watch.state()]);
    const state = watch.status === "fulfilled" ? watch.value : this.state.watch;
    let activity = this.state.activity;
    if (api !== null && state?.wanted === true) {
      const [read] = await Promise.allSettled([api.unattendedActivity()]);
      if (api !== this.#api) return;
      if (read.status === "fulfilled") activity = read.value;
    }
    this.#set({ watch: state, activity });
    this.#noticeBudgetStop();
  }

  /**
   * The daemon idles on a spent budget and exits with no code, so the shell
   * cannot tell the person; the page sees it first, in the job record, and asks
   * the shell for the notification once per stop. A stop already there when the
   * app first looked is not announced, and a failure is the shell's to raise.
   */
  #noticeBudgetStop(): void {
    if (this.state.watch === null) return;
    const paused = watchKind(this.state) === "budget";
    const announce = this.#baselined && paused && !this.#wasPaused;
    this.#baselined = true;
    this.#wasPaused = paused;
    if (announce) void this.#ports.watch.notifyBudgetStop().catch(() => {});
  }

  /** Choose the folder to watch and preview it; nothing is written until `confirmWatch`. */
  async chooseWatchFolder(): Promise<void> {
    const api = this.#api;
    if (api === null || this.state.watchBusy !== null) return;
    this.#watchPending = null;
    this.#set({ watchBusy: "choosing", watchConfirm: null, problem: null, note: null });
    try {
      const path = await this.#ports.watch.pickFolder();
      if (path === null) {
        this.#set({ watchBusy: null });
        return;
      }
      try {
        const preview = await api.previewInboxSettings({ inbox: path });
        this.#watchPending = preview;
        this.#set({ watchBusy: null, watchConfirm: { folder: preview.proposed.inbox ?? path } });
      } catch (error) {
        const saved = this.state.inbox?.inbox ?? null;
        if (!(error instanceof ApiError && error.code === "inbox_unchanged" && saved !== null)) throw error;
        // The folder is already the saved one: only the watch is left to start.
        this.#set({ watchBusy: null, watchConfirm: { folder: saved } });
      }
    } catch (error) {
      this.#set({ watchBusy: null, problem: watchFailure(error) });
    }
  }

  /** Not this folder after all: nothing was written. */
  cancelWatch(): void {
    this.#watchPending = null;
    this.#set({ watchConfirm: null });
  }

  /** Save the previewed folder, then start the watch on it. */
  async confirmWatch(): Promise<void> {
    const api = this.#api;
    const confirm = this.state.watchConfirm;
    if (api === null || confirm === null || this.state.watchBusy !== null) return;
    const preview = this.#watchPending;
    this.#watchPending = null;
    this.#set({ watchBusy: "starting", watchConfirm: null, problem: null, note: null });
    let folder = confirm.folder;
    if (preview !== null) {
      try {
        const answer = await retryWhileBusy(() => api.applyInboxSettings({ version: preview.version }));
        this.#set({ inbox: answer.changed.settings });
        folder = answer.changed.settings.inbox ?? folder;
      } catch (error) {
        this.#set({ watchBusy: null, problem: watchFailure(error) });
        if (error instanceof ApiError && error.code === "stale") await this.load();
        return;
      }
    }
    try {
      const watch = await this.#ports.watch.set(true);
      this.#set({ watchBusy: null, watch, stoppedByYou: false, note: WATCH.startedNote(folder) });
    } catch (error) {
      this.#set({ watchBusy: null, problem: WATCH.notStarted(error) });
    }
  }

  /** Start watching the folder that is already saved. */
  async startWatch(): Promise<void> {
    if (this.state.watchBusy !== null) return;
    this.#set({ watchBusy: "starting", problem: null, note: null });
    try {
      const watch = await this.#ports.watch.set(true);
      this.#set({ watchBusy: null, watch, stoppedByYou: false });
    } catch (error) {
      this.#set({ watchBusy: null, problem: watchCommandFailure(error) });
    }
  }

  /** Stop the watch, then take the folder out of the library's settings. */
  async stopWatch(): Promise<void> {
    const api = this.#api;
    if (this.state.watchBusy !== null) return;
    this.#set({ watchBusy: "stopping", problem: null, note: null });
    try {
      const watch = await this.#ports.watch.set(false);
      this.#set({ watch, stoppedByYou: true });
    } catch (error) {
      this.#set({ watchBusy: null, problem: watchCommandFailure(error) });
      return;
    }
    if (api !== null && this.state.inbox?.inbox) {
      try {
        const preview = await api.previewInboxSettings({ inbox: null });
        const answer = await retryWhileBusy(() => api.applyInboxSettings({ version: preview.version }));
        this.#set({ inbox: answer.changed.settings });
      } catch (error) {
        if (!(error instanceof ApiError && error.code === "inbox_unchanged")) {
          this.#set({ watchBusy: null, problem: WATCH.folderKept });
          return;
        }
      }
    }
    this.#set({ watchBusy: null, note: WATCH.stoppedNote });
  }

  /**
   * Read whether the app checks for updates, then check once. The shell asks
   * nothing when checking is off. Needs no engine: an update may be the fix
   * for one that does not start.
   */
  async startUpdates(): Promise<void> {
    const [read] = await Promise.allSettled([this.#ports.updates.settings()]);
    if (read.status === "fulfilled") this.#set({ updates: read.value });
    await this.checkForUpdate();
  }

  /** Ask the shell whether a newer version exists. Nothing is downloaded; a failure is quiet. */
  async checkForUpdate(): Promise<void> {
    if (this.state.updateBusy !== null) return;
    this.#set({ updateBusy: "checking", updateProblem: null });
    let update: UpdateCheck;
    try {
      update = await this.#ports.updates.check();
    } catch {
      update = { state: "failed" };
    }
    this.#set({ updateBusy: null, update });
  }

  /** Turn checking on or off. Off forgets the version on offer. */
  async setUpdateCheck(on: boolean): Promise<void> {
    if (this.state.updateBusy !== null) return;
    this.#set({ updateBusy: "switching", updateProblem: null });
    try {
      const updates = await this.#ports.updates.setCheck(on);
      this.#set({ updateBusy: null, updates, ...(on ? {} : { update: { state: "off" as const }, updateConfirm: false }) });
    } catch (error) {
      this.#set({ updateBusy: null, updateProblem: updateFailure(error) });
    }
  }

  /** The person asked to install the version on offer: ask them to confirm first. */
  askInstall(): void {
    if (updateOffered(this.state) === null || this.state.updateBusy !== null) return;
    this.#set({ updateConfirm: true, updateProblem: null });
  }

  /** Not now: nothing is downloaded. */
  cancelInstall(): void {
    this.#set({ updateConfirm: false });
  }

  /**
   * The person confirmed: install the version they were shown. The shell
   * restarts the app into it, so success never comes back here.
   */
  async confirmInstall(): Promise<void> {
    const version = updateOffered(this.state);
    if (!this.state.updateConfirm || version === null || this.state.updateBusy !== null) return;
    this.#set({ updateConfirm: false, updateBusy: "installing", updateProblem: null });
    try {
      await this.#ports.updates.install(version);
      this.#set({ updateBusy: null });
    } catch (error) {
      const gone = error instanceof UpdateError && (error.reason === "update_changed" || error.reason === "no_update");
      this.#set({ updateBusy: null, updateProblem: updateFailure(error), ...(gone ? { update: null } : {}) });
    }
  }

  openLibrary(): Promise<void> {
    return this.#chooseLibrary("open");
  }

  createLibrary(): Promise<void> {
    return this.#chooseLibrary("create");
  }

  async #chooseLibrary(action: "open" | "create"): Promise<void> {
    if (this.state.libraryBusy !== null) return;
    this.#set({ libraryBusy: action, libraryProblem: null });
    let path: string | null = null;
    try {
      path = await this.#ports.workspaces.pickFolder();
      if (path === null) {
        this.#set({ libraryBusy: null });
        return;
      }
      const choice = await this.#ports.workspaces[action](path);
      this.#set({ libraryBusy: null, library: choice, librarySwitching: true });
    } catch (error) {
      this.#set({ libraryBusy: null, libraryProblem: welcomeRefusal(action, error, path ?? "") });
    }
  }
}

function pairIds(pair: { first_id: string; second_id: string }): string[] {
  return [pair.first_id, pair.second_id];
}

function settledRow(pair: Schemas["ReconciledPair"], pages: ReadonlyMap<string, Schemas["Page"]>): UndoRow {
  // `target_id` is the page a reconcile marked out of date.
  const outOfDate = pair.relation_type === "supersedes" && pair.target_id !== null ? titleOf(pages, pair.target_id) : null;
  return {
    key: `${pair.first_id}|${pair.second_id}`,
    text: `'${titleOf(pages, pair.first_id)}' and '${titleOf(pages, pair.second_id)}'`,
    undo: { kind: "unreconcile", first: pair.first_id, second: pair.second_id, revision: pair.relation_type === "revises", outOfDate },
  };
}

function declinedRow(item: Schemas["DeclinedContradiction"], pages: ReadonlyMap<string, Schemas["Page"]>): UndoRow {
  // Found inside a combined page, it is named by that page alone.
  const text =
    item.merged_absorbed_id === null
      ? `'${titleOf(pages, item.first_id)}' and '${titleOf(pages, item.second_id)}'`
      : `'${titleOf(pages, item.first_id)}'`;
  return {
    key: `${item.first_id}|${item.second_id}|${item.merged_absorbed_id ?? ""}`,
    text,
    undo: { kind: "reopen_contradiction", first: item.first_id, second: item.second_id, absorbed: item.merged_absorbed_id },
  };
}

/** A cloud service the person has not been warned about yet. */
function needsWarning(current: Settings, proposed: Settings): boolean {
  return !proposed.is_local && proposed.remote_hosts.some((host) => !current.remote_hosts.includes(host));
}

function keyState(answer: PromiseSettledResult<boolean>): SettingsState["key"] {
  if (answer.status === "fulfilled") return answer.value ? "saved" : "none";
  return answer.reason instanceof ApiKeyError && answer.reason.reason === "not_supported" ? "unsupported" : null;
}
