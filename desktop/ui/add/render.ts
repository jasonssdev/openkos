// Draws the Add files screen from its state with the design system's components
// (ADR-0073, ADR-0069). It decides nothing: every word comes from wording.ts or
// is fixed here, and every value, a file name included, is written as text.
import "./add.css";
import { callout, el, pageHeader, progressBar, redraw, spinner, uiButton, uiCard, type Child } from "../system/index.ts";
import type { AddState } from "./model.ts";
import { describeOutcome, describeQuote, elapsedLine, expectationLine, partsLine, progressLine } from "./wording.ts";

export interface AddActions {
  choose: () => unknown;
  agree: () => unknown;
  decline: () => unknown;
  stop: () => unknown;
  close: () => unknown;
  resolveProblem: () => unknown;
  /** Files put in during a run: agree to them, or turn them down. */
  agreeIncoming: () => unknown;
  dismissIncoming: () => unknown;
  /** Take a waiting file out of the queue. */
  remove: (path: string) => unknown;
}

const WAITING_ID = "add-waiting";

const PROBLEM_LABELS = { try_again: "Try again", requote: "Look again", ok: "OK" } as const;

function list(items: { text: string; note?: string | null }[]): HTMLElement {
  return el(
    "ul",
    { class: "add-files" },
    ...items.map((item) => el("li", {}, item.text, item.note ? el("span", { class: "add-files__note" }, item.note) : null)),
  );
}

function start(state: AddState, actions: AddActions): Child[] {
  const waiting = state.ready ? null : el("p", { class: "ui-field__hint", attrs: { id: WAITING_ID } }, "Waiting for OpenKOS to start…");
  return [
    el(
      "div",
      { class: "add-drop", attrs: { role: "group", "aria-label": "Drop files" } },
      el("p", { class: "add-drop__title" }, "Drop files here"),
      el("p", { class: "ui-field__hint" }, "Drop them anywhere in this window, or choose them from your Mac."),
      uiButton("Choose files…", actions.choose, {
        variant: "primary",
        disabled: !state.ready,
        focusKey: "add-choose",
        ...(state.ready ? {} : { describedBy: WAITING_ID }),
      }),
    ),
    waiting,
  ];
}

interface ConsentWiring {
  agree: () => unknown;
  decline: () => unknown;
  /** Keeps focus on the same button across redraws; apart from the main card's. */
  focus: string;
}

function consentCard(quote: NonNullable<AddState["quote"]>, wiring: ConsentWiring): Child {
  const view = describeQuote(quote);
  const expectation = view.agree ? expectationLine(quote) : null;
  const decline = uiButton(view.decline, wiring.decline, { variant: view.agree ? "quiet" : "primary", focusKey: `${wiring.focus}-decline` });
  return uiCard(
    { title: view.heading, headingLevel: 2 },
    list(view.files.map((file) => ({ text: file.name, note: file.note }))),
    view.privacy ? el("p", {}, view.privacy) : null,
    view.ceiling ? el("p", {}, view.ceiling) : null,
    expectation ? el("p", { class: "ui-card__meta" }, expectation) : null,
    el(
      "div",
      { class: "ui-actions" },
      view.agree ? uiButton(view.agree, wiring.agree, { variant: "primary", focusKey: `${wiring.focus}-agree` }) : null,
      decline,
    ),
  );
}

function consent(state: AddState, actions: AddActions): Child[] {
  return [consentCard(state.quote!, { agree: actions.agree, decline: actions.decline, focus: "add" })];
}

/** Files put in during a run, or after its result: looked at, asked about, or why they could not be. */
function incoming(state: AddState, actions: AddActions): Child[] {
  const found = state.incoming;
  if (found === null) return [];
  if (found.problem !== null) {
    return [callout("error", found.problem, { live: "alert" }), el("div", { class: "ui-actions" }, uiButton("OK", actions.dismissIncoming, { variant: "primary", focusKey: "add-incoming-ok" }))];
  }
  if (found.quote === null) return [spinner("Looking at your files…")];
  return [consentCard(found.quote, { agree: actions.agreeIncoming, decline: actions.dismissIncoming, focus: "add-incoming" })];
}

/** Files waiting for their turn, each with a way to take it out; once Stop is pressed they are only listed. */
function queue(state: AddState, actions: AddActions): Child[] {
  if (state.queue.length === 0) return [];
  const stopping = state.phase === "stopping";
  return [
    el(
      "section",
      { class: "add-queue" },
      el("h2", { class: "add-queue__title" }, "Waiting"),
      stopping ? el("p", { class: "ui-card__meta" }, "These will be left. Add them again later.") : null,
      el(
        "ul",
        { class: "add-files" },
        ...state.queue.map((item) => {
          const remove = stopping ? null : uiButton("Remove", () => actions.remove(item.path), { variant: "quiet", size: "sm", focusKey: `add-remove-${item.path}` });
          remove?.setAttribute("aria-label", `Remove ${item.name}`);
          return el("li", { class: "add-queue__item" }, item.name, remove);
        }),
      ),
    ),
  ];
}

/** The drop zone a run leaves open: files put in here wait for the ones being added. */
function dropMore(state: AddState, actions: AddActions): Child[] {
  if (state.phase === "stopping") return [];
  return [
    el(
      "div",
      { class: "add-drop add-drop--more", attrs: { role: "group", "aria-label": "Drop more files" } },
      el("p", { class: "add-drop__title" }, "Drop more files here"),
      el("p", { class: "ui-field__hint" }, "They are added after these."),
      uiButton("Choose files…", actions.choose, { focusKey: "add-choose" }),
    ),
  ];
}

function running(state: AddState, actions: AddActions): Child[] {
  const stopping = state.phase === "stopping";
  const job = state.job;
  const line = state.quote && job ? progressLine(state.quote, job, { before: state.before, waiting: state.queue.length }) : { heading: "Getting started", step: null };
  const parts = job ? partsLine(job) : null;
  const expectation = state.quote && job?.phase === "extracting" ? expectationLine(state.quote) : null;
  const elapsed = job?.phase === "refreshing" ? null : elapsedLine(state.elapsed);
  return [
    uiCard(
      { title: line.heading, headingLevel: 2 },
      line.step ? el("p", { attrs: { role: "status" } }, line.step) : null,
      job ? progressBar(line.heading, job.parts_done ?? 0, job.phase === "extracting" ? (job.parts_total ?? null) : null) : null,
      parts ? el("p", { class: "ui-card__meta" }, parts) : null,
      elapsed ? el("p", { class: "ui-card__meta" }, elapsed) : null,
      expectation ? el("p", { class: "ui-card__meta" }, expectation) : null,
      stopping ? el("p", { class: "ui-card__meta" }, "Stopping after this file. Files already added are kept.") : null,
      el("div", { class: "ui-actions" }, uiButton("Stop", actions.stop, { focusKey: "add-stop", loading: stopping, loadingLabel: "Stopping…" })),
    ),
    ...queue(state, actions),
    ...incoming(state, actions),
    ...dropMore(state, actions),
  ];
}

function done(state: AddState, actions: AddActions): Child[] {
  const outcome = state.outcome!;
  const view = describeOutcome(outcome);
  const tone = outcome.status === "completed" ? "success" : "warning";
  return [
    callout(tone, view.heading, { live: "status" }),
    list(view.lines.map((text) => ({ text }))),
    ...view.notes.map((note) => callout("warning", note)),
    el("div", { class: "ui-actions" }, uiButton("OK", actions.close, { variant: "primary", focusKey: "add-ok" })),
    ...incoming(state, actions),
  ];
}

function problem(state: AddState, actions: AddActions): Child[] {
  const found = state.problem!;
  const buttons = [uiButton(PROBLEM_LABELS[found.action], found.action === "ok" ? actions.close : actions.resolveProblem, { variant: "primary", focusKey: "add-problem" })];
  if (found.action !== "ok") buttons.push(uiButton("Not now", actions.close, { variant: "quiet", focusKey: "add-close" }));
  return [callout("error", found.text, { live: "alert" }), el("div", { class: "ui-actions" }, ...buttons)];
}

function body(state: AddState, actions: AddActions): Child[] {
  switch (state.phase) {
    case "idle":
      return start(state, actions);
    case "quoting":
      return [spinner("Looking at your files…")];
    case "consent":
      return consent(state, actions);
    case "starting":
      return [spinner("Starting…"), ...queue(state, actions), ...incoming(state, actions), ...dropMore(state, actions)];
    case "running":
    case "stopping":
      return running(state, actions);
    case "done":
      return done(state, actions);
    case "problem":
      return problem(state, actions);
  }
}

/** Redraws the Add files screen into `root`, keeping focus where it was. */
export function renderAdd(root: HTMLElement, state: AddState, actions: AddActions): void {
  const header = pageHeader({
    title: "Add files",
    description: "Drop files on this window or choose them from your Mac. OpenKOS keeps them and makes pages from what is in them.",
  });
  redraw(root, `add-${state.phase === "stopping" ? "running" : state.phase}`, () =>
    root.replaceChildren(header, ...(body(state, actions).filter((n) => n !== null && n !== false && n !== undefined) as (Node | string)[])),
  );
}
