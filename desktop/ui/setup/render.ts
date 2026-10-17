// Draws the welcome and the guided setup from their state with the design
// system's components (ADR-0066, ADR-0069). It decides nothing: the state and
// its transitions live in model.ts, every word in wording.ts, and every value
// is written as text.
import {
  badge,
  callout,
  disclosure,
  el,
  pageHeader,
  progressBar,
  redraw,
  skeleton,
  spinner,
  uiButton,
  uiCard,
} from "../system/index.ts";
import {
  type ModelRow,
  type SetupModel,
  type SetupState,
  type WelcomeModel,
  type WelcomeState,
  modelRows,
  optionalRows,
  setupStep,
} from "./model.ts";
import { WORDS, memoryNote, movedLibrary, pullProgress, runtimeStep } from "./wording.ts";

/** Redraws the welcome into `root`, keeping focus on the same control. */
export function renderWelcome(root: HTMLElement, model: WelcomeModel, state: WelcomeState): void {
  redraw(root, "welcome", () => root.replaceChildren(...welcome(model, state)));
}

function welcome(model: WelcomeModel, state: WelcomeState): HTMLElement[] {
  const header = pageHeader({ title: WORDS.welcomeTitle, description: WORDS.welcomeLead });
  if (state.started) return [header, spinner(WORDS.starting)];
  const moved = state.moved ? withCode(movedLibrary(state.moved), state.moved) : null;
  const create = uiButton(WORDS.create, () => model.create(), {
    variant: "primary",
    loading: state.busy === "create",
    loadingLabel: WORDS.creating,
    disabled: state.busy === "open",
    focusKey: "welcome:create",
  });
  const open = uiButton(WORDS.open, () => model.open(), {
    loading: state.busy === "open",
    loadingLabel: WORDS.opening,
    disabled: state.busy === "create",
    focusKey: "welcome:open",
  });
  return [
    header,
    ...(moved ? [moved] : []),
    el("p", {}, WORDS.welcomeText),
    el("div", { class: "ui-actions" }, create, open),
    el("p", { class: "ui-field__hint" }, WORDS.createHint),
    ...(state.problem ? [refusal(model, state.problem)] : []),
  ];
}

/** A sentence with `value` in it set in monospace, e.g. a folder. */
function withCode(sentence: string, value: string): HTMLElement {
  const [before, ...after] = sentence.split(value);
  return el("p", {}, before ?? "", el("code", {}, value), after.join(value));
}

function refusal(model: WelcomeModel, problem: NonNullable<WelcomeState["problem"]>): HTMLElement {
  const openPath = problem.openPath;
  const actions = openPath ? [uiButton(WORDS.openInstead, () => model.openPath(openPath), { size: "sm", focusKey: "welcome:open-it" })] : [];
  const node = callout("error", problem.text, { actions, live: "alert" });
  if (problem.detail?.trim()) {
    node.querySelector(".ui-callout__body")?.append(
      disclosure({ summary: WORDS.showDetails, focusKey: "welcome:details" }, el("pre", { class: "ui-pre" }, problem.detail.trim())),
    );
  }
  return node;
}

export interface SetupOptions {
  /** Setup is complete and the person goes on to the app. */
  onDone: () => void;
}

/** Redraws guided setup into `root`, keeping focus on the same control. */
export function renderSetup(root: HTMLElement, model: SetupModel, state: SetupState, options: SetupOptions): void {
  const step = setupStep(state);
  redraw(root, `setup:${step}`, () => root.replaceChildren(...setup(root, model, state, step, options)));
}

function setup(root: HTMLElement, model: SetupModel, state: SetupState, step: ReturnType<typeof setupStep>, options: SetupOptions): HTMLElement[] {
  const note = state.readiness ? memoryNote(state.readiness.memory) : null;
  const memory = note ? callout("info", note) : null;
  if (step === "ready") {
    const start = uiButton(WORDS.startUsing, options.onDone, { variant: "primary", focusKey: "setup:start" });
    const more = moreModels(root, model, state);
    return [
      pageHeader({ title: WORDS.readyTitle, description: WORDS.readyText }),
      ...(memory ? [memory] : []),
      el("div", { class: "ui-actions" }, start),
      ...(more ? [more] : []),
    ];
  }
  const header = pageHeader({ title: WORDS.setupTitle, description: WORDS.setupLead });
  const failed = state.failed
    ? callout("error", WORDS.checkFailed, {
        actions: [uiButton(WORDS.tryAgain, () => model.load(), { size: "sm", loading: state.checking, loadingLabel: WORDS.checkingAgain, focusKey: "setup:retry" })],
        live: "alert",
      })
    : null;
  const body: (HTMLElement | null)[] = [];
  if (step === "loading") body.push(skeleton(3, WORDS.checking));
  if (step === "runtime") body.push(runtimeCard(model, state));
  if (step === "models") body.push(modelsCard(model, state));
  return [header, ...[failed, memory, ...body].filter((node): node is HTMLElement => node !== null)];
}

// Whether the person opened the optional models, kept across redraws.
const moreOpened = new WeakMap<HTMLElement, boolean>();

/** The optional models, held back until asked for; open while one downloads. */
function moreModels(root: HTMLElement, model: SetupModel, state: SetupState): HTMLElement | null {
  const rows = optionalRows(state);
  if (rows.length === 0) return null;
  const busy = state.pull !== null;
  const list = el(
    "dl",
    { class: "ui-facts", attrs: { "aria-label": WORDS.moreSummary } },
    ...rows.flatMap((row) => [el("dt", {}, el("code", {}, row.tag)), el("dd", {}, ...modelStatus(model, row, false, busy))]),
  );
  return disclosure(
    {
      summary: WORDS.moreSummary,
      open: busy || (moreOpened.get(root) ?? false),
      onToggle: (open) => moreOpened.set(root, open),
      focusKey: "setup:more",
    },
    el("p", {}, WORDS.moreText),
    list,
  );
}

function runtimeCard(model: SetupModel, state: SetupState): HTMLElement {
  const runtime = (state.readiness as NonNullable<SetupState["readiness"]>).runtime;
  const words = runtimeStep(runtime);
  const check = uiButton(WORDS.checkAgain, () => model.load(), {
    variant: words.download ? "secondary" : "primary",
    loading: state.checking,
    loadingLabel: WORDS.checkingAgain,
    focusKey: "setup:check",
  });
  const actions = words.download
    ? [uiButton(WORDS.getOllama, () => model.openDownload(), { variant: "primary", focusKey: "setup:get" }), check]
    : [check];
  const browser =
    words.download && state.browserFailed ? callout("warning", WORDS.browserFailed.replace("{url}", words.download)) : null;
  return uiCard(
    { title: words.title, meta: WORDS.stepOf(1, 2), headingLevel: 2 },
    ...words.text.map((line) => el("p", {}, line)),
    el("div", { class: "ui-actions" }, ...actions),
    browser,
  );
}

function modelsCard(model: SetupModel, state: SetupState): HTMLElement {
  const rows = modelRows(state);
  const missing = rows.filter((row) => row.status === "missing" && row.pullable);
  const all =
    missing.length > 1 && state.pull === null
      ? el("div", { class: "ui-actions" }, uiButton(WORDS.downloadAll, () => model.downloadAll(), { variant: "primary", focusKey: "setup:all" }))
      : null;
  const list = el(
    "dl",
    { class: "ui-facts", attrs: { "aria-label": WORDS.modelsLabel } },
    ...rows.flatMap((row) => [el("dt", {}, el("code", {}, row.tag)), el("dd", {}, ...modelStatus(model, row, missing.length === 1, state.pull !== null))]),
  );
  return uiCard({ title: WORDS.modelsTitle, meta: WORDS.stepOf(2, 2), headingLevel: 2 }, el("p", {}, WORDS.modelsText), list, all);
}

function modelStatus(model: SetupModel, row: ModelRow, only: boolean, busy: boolean): HTMLElement[] {
  switch (row.status) {
    case "installed":
      return [badge("success", WORDS.installed)];
    case "waiting":
      return [el("p", {}, WORDS.waiting)];
    case "running": {
      const progress = row.progress as NonNullable<ModelRow["progress"]>;
      const known = progress.state === "running" && progress.phase === "downloading" ? progress.total : null;
      return [
        progressBar(`Downloading ${row.tag}`, progress.completed, known),
        el("p", {}, pullProgress(progress)),
        el(
          "div",
          { class: "ui-actions" },
          uiButton(WORDS.stop, () => model.stop(), { size: "sm", disabled: progress.state === "stopping", focusKey: "setup:stop" }),
        ),
      ];
    }
    case "missing": {
      if (!row.pullable) return [el("p", {}, WORDS.notPullable)];
      const label = row.note?.kind === "stopped" ? WORDS.continueDownload : row.note?.kind === "failed" ? WORDS.tryAgain : WORDS.download;
      const note = row.note
        ? row.note.kind === "failed"
          ? el("p", { class: "ui-field__error" }, `✕ ${row.note.text}`)
          : el("p", { class: "ui-field__hint" }, row.note.text)
        : null;
      // While another download runs, this one is queued behind it.
      const start = uiButton(label, () => model.download(row.tag), {
        size: "sm",
        variant: only && !busy ? "primary" : "secondary",
        focusKey: `setup:download:${row.tag}`,
      });
      return [...(note ? [note] : []), el("div", { class: "ui-actions" }, start)];
    }
  }
}
