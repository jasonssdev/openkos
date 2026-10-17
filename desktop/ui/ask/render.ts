// Draws the Ask screen from its state with the design system's components
// (ADR-0066, ADR-0069). It decides nothing: every word comes from wording.ts or
// is fixed here, and every value, the answer included, is written as text. The
// field is built once and kept, so what the person is typing and the caret
// survive every redraw while an answer is on its way.
import "./ask.css";
import { callout, disclosure, el, listRow, pageHeader, redraw, spinner, textField, uiButton, uiCard, uiList } from "../system/index.ts";
import type { Schemas } from "../../client/index.ts";
import type { AskState, Turn } from "./model.ts";
import { describeAnswer, phaseLine, turnMeta } from "./wording.ts";

export interface AskActions {
  ask: (question: string) => unknown;
  retry: () => unknown;
  show: (id: number) => unknown;
  /** The disclosures the person opened; they stay open across redraws. */
  expanded: ReadonlySet<string>;
  onToggle: (key: string, open: boolean) => void;
  /** Beside each page an answer came from: what the person can do with it. */
  citationAction?: (citation: Schemas["AnswerCitation"]) => HTMLElement | null;
  /** Under a finished answer: what the screen adds, such as rebuilding search. */
  afterAnswer?: (answer: Schemas["Answer"]) => HTMLElement | null;
}

const FIELD_ID = "ask-question";
const ERROR_ID = `${FIELD_ID}-error`;
const WAITING_ID = "ask-waiting";
const EARLIER = "ask-earlier";

interface Parts {
  input: HTMLInputElement;
  field: HTMLElement;
  actions: HTMLElement;
  result: HTMLElement;
  earlier: HTMLElement;
}

const built = new WeakMap<HTMLElement, { parts: Parts; actions: { current: AskActions } }>();

function build(root: HTMLElement, current: { current: AskActions }): Parts {
  const field = textField({ id: FIELD_ID, label: "Your question", placeholder: "What would you like to know?" });
  const input = field.querySelector("input") as HTMLInputElement;
  input.dataset["focus"] = FIELD_ID;
  input.autocomplete = "off";
  const actions = el("div", { class: "ui-actions" });
  const form = el("form", { class: "ask-form", attrs: { "aria-label": "Ask a question" } }, field, actions);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void current.current.ask(input.value);
  });
  // The answer is announced when it replaces the progress line.
  const result = el("div", { attrs: { "aria-live": "polite" } });
  const earlier = el("div");
  root.replaceChildren(
    pageHeader({ title: "Ask", description: "Ask about anything in your library. The answer comes from your pages and says which ones." }),
    form,
    result,
    earlier,
  );
  return { input, field, actions, result, earlier };
}

function markBlank(parts: Parts, blank: boolean): void {
  parts.field.querySelector(`#${ERROR_ID}`)?.remove();
  if (blank) {
    parts.input.setAttribute("aria-invalid", "true");
    parts.input.setAttribute("aria-describedby", ERROR_ID);
    parts.field.append(el("p", { class: "ui-field__error", attrs: { id: ERROR_ID } }, "✕ Type a question first."));
  } else {
    parts.input.removeAttribute("aria-invalid");
    parts.input.removeAttribute("aria-describedby");
  }
}

function askButton(parts: Parts, state: AskState, actions: AskActions): HTMLElement[] {
  const button = uiButton("Ask", () => actions.ask(parts.input.value), {
    variant: "primary",
    loading: state.running,
    loadingLabel: "Asking…",
    disabled: !state.ready,
    focusKey: "ask-submit",
    ...(state.ready ? {} : { describedBy: WAITING_ID }),
  });
  const waiting = state.ready ? null : el("p", { class: "ui-field__hint", attrs: { id: WAITING_ID } }, "Waiting for OpenKOS to start…");
  return waiting ? [button, waiting] : [button];
}

function turnCard(turn: Turn, actions: AskActions): HTMLElement {
  const title = { title: turn.question, headingLevel: 2 as const };
  const result = turn.result;
  switch (result.kind) {
    case "running":
      return uiCard(title, spinner(phaseLine(result.phase)));
    case "problem":
      return uiCard(
        title,
        callout("error", result.problem.text, {
          actions: result.problem.retry ? [uiButton("Try again", actions.retry, { focusKey: "ask-retry" })] : [],
        }),
      );
    case "answer": {
      const told = describeAnswer(result.answer);
      const notes = told.notes.map((note) => el("p", { class: "ui-card__meta" }, note));
      if (told.kind === "empty") return uiCard({ ...title, meta: told.title }, el("p", {}, told.text), ...notes);
      const paragraphs = told.text
        .split(/\n\s*\n/)
        .map((text) => text.trim())
        .filter((text) => text !== "")
        .map((text) => el("p", { class: "ask-answer" }, text));
      return uiCard(told.from.length > 0 ? { ...title, footer: fromFooter(told.from, result.answer, actions) } : title, ...paragraphs, ...notes);
    }
  }
}

/** "From:" and the pages, each with the screen's action beside it when it has one. */
function fromFooter(titles: string[], answer: Schemas["Answer"], actions: AskActions): string | HTMLElement {
  const act = actions.citationAction;
  if (act === undefined) return `From: ${titles.join(" · ")}`;
  return el(
    "div",
    { class: "ask-from" },
    el("p", {}, "From:"),
    el(
      "ul",
      { class: "ui-plain-list" },
      ...answer.citations.map((citation) => el("li", { class: "ask-from__page" }, el("span", {}, citation.title), act(citation))),
    ),
  );
}

function earlierQuestions(state: AskState, actions: AskActions): HTMLElement | null {
  const others = state.turns.filter((turn) => turn.id !== state.shown);
  if (others.length === 0) return null;
  const rows = others.map((turn) =>
    listRow({ title: turn.question, meta: turnMeta(turn.result), onSelect: () => actions.show(turn.id), focusKey: `ask-turn-${turn.id}` }),
  );
  return disclosure(
    { summary: "Earlier questions", open: actions.expanded.has(EARLIER), onToggle: (open) => actions.onToggle(EARLIER, open), focusKey: EARLIER },
    uiList("Earlier questions", rows),
  );
}

/** Redraws the Ask screen into `root`, keeping the field and the focused control. */
export function renderAsk(root: HTMLElement, state: AskState, actions: AskActions): void {
  let entry = built.get(root);
  if (entry === undefined || !root.contains(entry.parts.input)) {
    const current = { current: actions };
    entry = { parts: build(root, current), actions: current };
    built.set(root, entry);
  }
  entry.actions.current = actions;
  const parts = entry.parts;
  markBlank(parts, state.blank);
  redraw(parts.actions, "ask-actions", () => parts.actions.replaceChildren(...askButton(parts, state, actions)));
  const shown = state.turns.find((turn) => turn.id === state.shown);
  const after = shown?.result.kind === "answer" && actions.afterAnswer ? actions.afterAnswer(shown.result.answer) : null;
  redraw(parts.result, "ask-result", () =>
    parts.result.replaceChildren(...(shown ? [turnCard(shown, actions)] : []), ...(after ? [after] : [])),
  );
  redraw(parts.earlier, "ask-earlier", () => parts.earlier.replaceChildren(...[earlierQuestions(state, actions)].filter((n) => n !== null)));
  root.dataset["view"] = "ask";
}
