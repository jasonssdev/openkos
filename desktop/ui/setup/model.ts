// The welcome and the guided setup as state and transitions, with no DOM
// (ADR-0066). The welcome chooses a library through the shell's commands
// (ADR-0071); setup reads the local API's readiness and pulls the missing
// models through it (ADR-0057). Every word comes from wording.ts.
import type { Schemas, WorkspaceChoice } from "../../client/index.ts";
import {
  type PullView,
  type Refusal,
  pullFailure,
  pullFailureFor,
  stoppedNote,
  welcomeRefusal,
} from "./wording.ts";

/** How often setup checks again on its own while Ollama does not answer: gently. */
export const RUNTIME_POLL_MS = 5000;
/** How often a running download is read. */
export const PULL_POLL_MS = 1000;

type Listener<S> = (state: S) => void;

class Observable<S> {
  #listeners = new Set<Listener<S>>();

  constructor(public state: S) {}

  subscribe(listener: Listener<S>): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  protected set(patch: Partial<S>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.#listeners) listener(this.state);
  }
}

// --- The welcome ---------------------------------------------------------

/** The shell's workspace commands the welcome calls (`client/workspace.ts`). */
export interface WelcomePorts {
  status(): Promise<WorkspaceChoice>;
  pickFolder(): Promise<string | null>;
  create(path: string): Promise<WorkspaceChoice>;
  open(path: string): Promise<WorkspaceChoice>;
}

export interface WelcomeState {
  /** The choice running now, if any. */
  busy: "create" | "open" | null;
  /** A library was chosen; the app is starting it. */
  started: boolean;
  problem: Refusal | null;
  /** The library chosen before, when it is no longer where it was. */
  moved: string | null;
}

export class WelcomeModel extends Observable<WelcomeState> {
  readonly #ports: WelcomePorts;

  constructor(ports: WelcomePorts) {
    super({ busy: null, started: false, problem: null, moved: null });
    this.#ports = ports;
  }

  async load(): Promise<void> {
    try {
      const choice = await this.#ports.status();
      const moved = choice.source === "remembered" && !choice.valid ? choice.path : null;
      this.set({ moved });
    } catch {
      // The welcome works without it: only the "moved" line is lost.
    }
  }

  /** Choose an empty or new folder and create a library there. */
  create(): Promise<void> {
    return this.#choose("create", null);
  }

  /** Choose a folder that holds a library and open it. */
  open(): Promise<void> {
    return this.#choose("open", null);
  }

  /** Open the folder a refusal offered, with no second folder window. */
  openPath(path: string): Promise<void> {
    return this.#choose("open", path);
  }

  async #choose(action: "create" | "open", given: string | null): Promise<void> {
    if (this.state.busy !== null || this.state.started) return;
    this.set({ busy: action, problem: null });
    let path = given;
    try {
      path ??= await this.#ports.pickFolder();
      if (path === null) {
        this.set({ busy: null });
        return;
      }
      await this.#ports[action](path);
      this.set({ busy: null, started: true });
    } catch (error) {
      this.set({ busy: null, problem: welcomeRefusal(action, error, path ?? "") });
    }
  }
}

// --- Guided setup --------------------------------------------------------

type Readiness = Schemas["SetupReadiness"];
type Pull = Schemas["PullStatus"];

/** The local API's setup calls (`client/index.ts`) and the shell's download page. */
export interface SetupPorts {
  setupReadiness(): Promise<Readiness>;
  startModelPull(body: { model: string }): Promise<Pull>;
  modelPull(pullId: string): Promise<Pull>;
  stopModelPull(pullId: string): Promise<Pull>;
  openDownload(): Promise<void>;
}

export interface ModelNote {
  kind: "stopped" | "failed";
  text: string;
}

export interface SetupState {
  readiness: Readiness | null;
  /** The last readiness read failed. */
  failed: boolean;
  /** A readiness read is running. */
  checking: boolean;
  /** The download running now; the engine allows one at a time. */
  pull: { model: string; id: string; view: PullView } | null;
  /** Downloads asked for while another runs, in order. */
  waiting: string[];
  /** What happened to a model's last download, until it is tried again. */
  notes: Record<string, ModelNote>;
  browserFailed: boolean;
}

export type SetupStep = "loading" | "failed" | "runtime" | "models" | "ready";

/** Where setup is: the first thing that is not ready yet. */
export function setupStep(state: SetupState): SetupStep {
  const readiness = state.readiness;
  if (readiness === null) return state.failed ? "failed" : "loading";
  if (readiness.ready) return "ready";
  return readiness.runtime.reachable ? "models" : "runtime";
}

export interface ModelRow {
  tag: string;
  status: "installed" | "missing" | "waiting" | "running";
  pullable: boolean;
  progress?: PullView;
  note?: ModelNote;
}

/**
 * The models setup offers: the required ones, which the engine's `ready`
 * waits for. Optional ones (a judge) are not offered here (#1510).
 */
export function modelRows(state: SetupState): ModelRow[] {
  return (state.readiness?.models ?? [])
    .filter((model) => model.required)
    .map((model) => {
      const row: ModelRow = { tag: model.tag, status: "missing", pullable: model.pullable };
      if (state.pull?.model === model.tag) {
        row.status = "running";
        row.progress = state.pull.view;
      } else if (state.waiting.includes(model.tag)) {
        row.status = "waiting";
      } else if (model.installed === true) {
        row.status = "installed";
      }
      const note = state.notes[model.tag];
      if (note && row.status === "missing") row.note = note;
      return row;
    });
}

export class SetupModel extends Observable<SetupState> {
  readonly #ports: SetupPorts;
  #reading: Promise<void> | null = null;
  #watched = false;
  #disposed = false;
  #runtimeTimer: ReturnType<typeof setTimeout> | undefined;
  #pullTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(ports: SetupPorts) {
    super({ readiness: null, failed: false, checking: false, pull: null, waiting: [], notes: {}, browserFailed: false });
    this.#ports = ports;
  }

  /** Read the readiness again; a read already running is shared, never doubled. */
  load(): Promise<void> {
    this.#reading ??= this.#read().finally(() => {
      this.#reading = null;
      this.#scheduleRuntimeCheck();
    });
    return this.#reading;
  }

  /** Whether the screen is shown: only then does setup check again on its own. */
  watch(on: boolean): void {
    this.#watched = on;
    if (!on) clearTimeout(this.#runtimeTimer);
    else this.#scheduleRuntimeCheck();
  }

  /** Stop every timer; a disposed model reads nothing more. */
  dispose(): void {
    this.#disposed = true;
    clearTimeout(this.#runtimeTimer);
    clearTimeout(this.#pullTimer);
  }

  async openDownload(): Promise<void> {
    try {
      await this.#ports.openDownload();
      this.set({ browserFailed: false });
    } catch {
      this.set({ browserFailed: true });
    }
  }

  /** Download `tag`, or queue it behind the download that runs. */
  async download(tag: string): Promise<void> {
    const { pull, waiting } = this.state;
    if (pull !== null) {
      if (pull.model !== tag && !waiting.includes(tag)) this.set({ waiting: [...waiting, tag] });
      return;
    }
    await this.#start(tag);
  }

  /** Download every missing model, one after another. */
  async downloadAll(): Promise<void> {
    const missing = modelRows(this.state).filter((row) => row.status === "missing" && row.pullable);
    const [first, ...rest] = missing.map((row) => row.tag);
    if (first === undefined) return;
    if (this.state.pull !== null) {
      for (const tag of [first, ...rest]) await this.download(tag);
      return;
    }
    this.set({ waiting: rest });
    await this.#start(first);
  }

  /** Stop the running download; the waiting ones are dropped. */
  async stop(): Promise<void> {
    const pull = this.state.pull;
    if (pull === null) return;
    this.set({ waiting: [] });
    try {
      this.#follow(await this.#ports.stopModelPull(pull.id));
    } catch {
      // The download is still followed; its next read shows how it ended.
    }
  }

  async #read(): Promise<void> {
    this.set({ checking: true });
    try {
      const readiness = await this.#ports.setupReadiness();
      this.set({ readiness, failed: false, checking: false });
    } catch {
      this.set({ failed: true, checking: false });
    }
  }

  #scheduleRuntimeCheck(): void {
    clearTimeout(this.#runtimeTimer);
    if (this.#disposed || !this.#watched || this.#reading !== null) return;
    if (setupStep(this.state) !== "runtime") return;
    this.#runtimeTimer = setTimeout(() => void this.load(), RUNTIME_POLL_MS);
  }

  async #start(tag: string): Promise<void> {
    const notes = { ...this.state.notes };
    delete notes[tag];
    this.set({ notes });
    let started: Pull;
    try {
      started = await this.#ports.startModelPull({ model: tag });
    } catch (error) {
      this.#ended(tag, { kind: "failed", text: pullFailure(error) });
      return;
    }
    this.#follow(started);
  }

  #follow(job: Pull): void {
    if (this.#disposed) return;
    switch (job.state) {
      case "running":
      case "stopping": {
        const view: PullView = { state: job.state, phase: job.phase, completed: job.completed_bytes, total: job.total_bytes };
        this.set({ pull: { model: job.model, id: job.pull_id, view } });
        clearTimeout(this.#pullTimer);
        this.#pullTimer = setTimeout(() => void this.#poll(job.pull_id, job.model), PULL_POLL_MS);
        return;
      }
      case "finished": {
        const outcome = job.outcome;
        if (outcome?.status === "stopped") {
          this.#ended(job.model, { kind: "stopped", text: stoppedNote(outcome.completed_bytes, outcome.total_bytes) });
          return;
        }
        this.#next();
        return;
      }
      default:
        this.#ended(job.model, { kind: "failed", text: pullFailureFor(job.error?.code ?? "") });
    }
  }

  async #poll(id: string, model: string): Promise<void> {
    let job: Pull;
    try {
      job = await this.#ports.modelPull(id);
    } catch (error) {
      this.#ended(model, { kind: "failed", text: pullFailure(error) });
      return;
    }
    this.#follow(job);
  }

  /** A download ended without the model: say why, drop the queue, look again. */
  #ended(model: string, note: ModelNote): void {
    this.set({ pull: null, waiting: [], notes: { ...this.state.notes, [model]: note } });
    void this.load();
  }

  /** A download finished: start the next one, or look again when none waits. */
  #next(): void {
    const [next, ...rest] = this.state.waiting;
    this.set({ pull: null, waiting: rest });
    if (next !== undefined) void this.#start(next);
    else void this.load();
  }
}
