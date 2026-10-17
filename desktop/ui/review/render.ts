// Draws the review screen from the model's state and wires each control to a
// transition (ADR-0066). It decides nothing: what to ask, offer and say comes
// from model.ts and wording.ts. Every value is written as text.
import { button, card, choiceGroup, el, heading, notice, redraw, type Tone } from "../components.ts";
import type { ReviewModel, ReviewState } from "./model.ts";
import {
  CANNOT_ACT,
  COMBINED_HEADING,
  describeDecision,
  identityShape,
  listEntry,
  typeNoun,
  type Option,
  type PageView,
  type Problem,
} from "./wording.ts";

/** The live regions a screen reader hears; they stay in the page between redraws. */
export interface LiveRegions {
  status: HTMLElement;
  alert: HTMLElement;
}

const ACTION_LABELS: Record<Problem["action"], string> = {
  try_again: "Try again",
  reload: "Show the current version",
  ok: "OK",
  requote: "Look again",
};

function problemNotice(problem: Problem, resolve: () => unknown, focusKey: string): HTMLElement {
  return notice("error", problem.message, button(ACTION_LABELS[problem.action], resolve, { variant: "primary", focusKey }));
}

function busy(text: string): HTMLElement {
  return el("p", { class: "busy" }, text);
}

function pageCard(page: PageView): HTMLElement {
  return card(
    el("h3", {}, page.title),
    page.kind ? el("p", { class: "muted" }, page.kind.charAt(0).toUpperCase() + page.kind.slice(1)) : null,
    page.excerpt ? el("p", {}, page.excerpt) : null,
    page.whoCanSee
      ? el("p", { class: "muted" }, el("span", { class: "label" }, page.whoCanSee.label), ` — ${page.whoCanSee.meaning}`)
      : null,
    page.sources.length > 0 ? el("ul", { class: "sources" }, ...page.sources.map((line) => el("li", {}, line))) : null,
  );
}

function optionRow(option: Option, onChoose: () => unknown, index: number, unavailable: string | null): HTMLElement {
  const reasonId = unavailable ? `option-reason-${index}` : undefined;
  return el(
    "li",
    { class: "option" },
    button(option.label, onChoose, {
      variant: option.choice === "later" ? "quiet" : "secondary",
      focusKey: `option:${index}`,
      disabled: unavailable !== null,
      ...(reasonId ? { describedBy: reasonId } : {}),
    }),
    el("p", { class: "muted" }, option.does),
    unavailable ? el("p", { class: "reason", attrs: { id: reasonId! } }, unavailable) : null,
  );
}

// -- the list ---------------------------------------------------------------------- //

function drawList(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const parts: HTMLElement[] = [heading(1, "To review")];
  if (state.notice) parts.push(notice(state.notice.tone, state.notice.text));
  if (state.listProblem) {
    parts.push(problemNotice(state.listProblem, () => model.load(), "list-problem"));
    return parts;
  }
  if (!state.loaded) {
    parts.push(busy("Looking for things to review…"));
    return parts;
  }
  const unjudged = state.decisions.some((d) => d.kind === "identity" && d.evaluation === "pending_evaluation");
  if (unjudged) {
    parts.push(
      el(
        "div",
        { class: "actions" },
        button("Check all", () => model.startCheck(), { variant: "secondary", focusKey: "check-all" }),
        el("p", { class: "muted" }, "Let the AI model compare the pages it hasn't checked yet. You'll still decide each one."),
      ),
    );
  }
  if (state.decisions.length === 0) {
    parts.push(el("p", {}, "Nothing to review right now."));
  } else {
    const list = el("ul", { class: "entries" });
    for (const decision of state.decisions) {
      const entry = listEntry(decision, state.pages);
      const open = button("", () => model.open(decision.decision_key), { focusKey: `entry:${decision.decision_key}` });
      open.classList.add("entry");
      open.append(
        ...[
          el("span", { class: "entry__question" }, entry.question),
          el("span", { class: "entry__pages muted" }, entry.pages),
          entry.changed ? el("span", { class: "tag tag--warning" }, "! Changed") : null,
          entry.canAct ? null : el("span", { class: "tag" }, "i Can't be settled here yet"),
        ].filter((node) => node !== null),
      );
      list.append(el("li", {}, open));
    }
    parts.push(list);
  }
  if (state.combined.length > 0) parts.push(drawCombined(model, state));
  return parts;
}

function drawCombined(model: ReviewModel, state: ReviewState): HTMLElement {
  const section = el("section", { attrs: { "aria-labelledby": "combined-heading" } });
  section.append(el("h2", { attrs: { id: "combined-heading" } }, COMBINED_HEADING));
  for (const page of state.combined) {
    section.append(el("h3", {}, page.title));
    section.append(
      el(
        "ul",
        { class: "history" },
        ...page.lines.map((line) =>
          el(
            "li",
            {},
            el("span", {}, line.text),
            button("Split again", () => model.openSplit(page.survivor, line.absorbed), {
              focusKey: `split:${page.survivor}:${line.absorbed}`,
            }),
          ),
        ),
      ),
    );
  }
  return section;
}

// -- one decision ------------------------------------------------------------------ //

function drawSelection(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const open = state.open!;
  const decision = open.decision;
  if (decision.kind !== "identity" || !decision.actions.includes("merge_group")) return [];
  const title = (id: string) => state.pages.get(id)?.title || "A page that couldn't be read";
  const parts: HTMLElement[] = [];
  const shape = identityShape(decision);
  if (shape === "group") {
    const members = decision.detail && "member_ids" in decision.detail ? decision.detail.member_ids : decision.targets;
    parts.push(
      choiceGroup(
        "Mark the pages that are the same",
        "checkbox",
        "marked",
        members.map((id) => ({ value: id, label: title(id), checked: open.selection.marked.includes(id) })),
        (id) => model.toggleMark(id),
      ),
    );
  }
  const names = model.namesFor();
  const askName = shape === "group" ? names.length >= 1 : open.selection.okfType !== null && names.length > 1;
  if (askName) {
    parts.push(
      choiceGroup(
        "Which one should the combined page keep its name from?",
        "radio",
        "name",
        names.map((id) => ({ value: id, label: title(id), checked: open.selection.name === id })),
        (id) => model.chooseName(id),
      ),
    );
  }
  return parts;
}

function unavailableReason(model: ReviewModel, state: ReviewState, option: Option): string | null {
  const open = state.open!;
  if (option.choice !== "merge_group" || option.okfType !== undefined) return null;
  if (open.selection.marked.length < 2) return "Mark at least two pages";
  return model.canCombineMarked() ? null : "Choose which one keeps its name";
}

function drawDecision(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const open = state.open!;
  const view = describeDecision(open.decision, state.pages);
  const parts: HTMLElement[] = [
    el("div", { class: "actions" }, button("Back to To review", () => model.close(), { variant: "quiet", focusKey: "back" })),
  ];
  if (open.changed) parts.push(notice("warning", open.changed));
  const section = el("section", { class: "decision", attrs: { "aria-labelledby": "question" } });
  if (view.lead) section.append(el("p", { class: "lead" }, view.lead));
  section.append(heading(2, view.question, "question"));
  if (view.statement) section.append(el("p", { class: "statement" }, view.statement));
  if (view.facts.length > 0) {
    section.append(el("dl", {}, ...view.facts.flatMap(([term, value]) => [el("dt", {}, term), el("dd", {}, value)])));
  }
  if (view.pages.length > 0) section.append(el("div", { class: "pages" }, ...view.pages.map(pageCard)));
  if (view.reason) {
    section.append(el("figure", { class: "reason-block" }, el("figcaption", {}, "Why the AI model thinks so"), el("blockquote", {}, view.reason)));
  }
  parts.push(section);
  if (view.cannotAct) {
    parts.push(notice("info", CANNOT_ACT));
    return parts;
  }
  if (open.problem) {
    parts.push(problemNotice(open.problem, () => model.resolveProblem(), "problem"));
  }
  const step = open.step;
  switch (step.name) {
    case "choosing": {
      parts.push(...drawSelection(model, state));
      const kindChosen = open.selection.okfType;
      const options = view.options.map((option, index) =>
        optionRow(option, () => model.choose(option), index, unavailableReason(model, state, option)),
      );
      parts.push(el("ul", { class: "options", attrs: { "aria-label": "Your options" } }, ...options));
      if (kindChosen !== null && model.namesForKind().length > 1) {
        const option = view.options.find((o) => o.okfType === kindChosen);
        if (option) {
          parts.push(
            el(
              "div",
              { class: "actions" },
              button(`Continue as ${typeNoun(kindChosen).withArticle}`, () => model.choose(option), {
                variant: "primary",
                focusKey: "continue",
                disabled: open.selection.name === null,
              }),
            ),
          );
        }
      }
      break;
    }
    case "previewing":
      parts.push(busy("Getting this ready…"));
      break;
    case "preview":
    case "applying": {
      const applying = step.name === "applying";
      parts.push(
        el("h3", {}, "What will happen"),
        ...step.lines.map((line) => el("p", {}, line)),
        el(
          "div",
          { class: "actions" },
          button("Confirm", () => model.confirm(), { variant: "primary", focusKey: "confirm", disabled: applying }),
          button("Choose again", () => model.back(), { focusKey: "choose-again", disabled: applying }),
        ),
      );
      if (applying) parts.push(busy("Saving your choice…"));
      break;
    }
    case "done": {
      const actions = [button("Back to To review", () => model.close(), { variant: "primary", focusKey: "done-back" })];
      if (step.undo) actions.unshift(button("Undo", () => model.undo(), { focusKey: "undo" }));
      parts.push(notice("done", step.lines.join(" "), ...actions));
      break;
    }
    case "undoing":
      parts.push(notice("done", step.lines.join(" ")), busy("Undoing…"));
      break;
  }
  return parts;
}

// -- the check and split again ----------------------------------------------------------- //

function drawCheck(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const check = state.check!;
  const parts: HTMLElement[] = [];
  switch (check.phase) {
    case "quoting":
      parts.push(heading(2, "Check with the AI model"), busy("Getting ready to check…"));
      break;
    case "consent": {
      const consent = check.consent!;
      parts.push(heading(2, consent.heading));
      if (check.text) parts.push(notice("warning", check.text));
      parts.push(...consent.lines.map((line) => el("p", {}, line)));
      const actions = el("div", { class: "actions" });
      if (consent.agree) actions.append(button(consent.agree, () => model.agreeCheck(), { variant: "primary", focusKey: "agree" }));
      actions.append(button(consent.decline, () => model.closeCheck(), { focusKey: "decline" }));
      parts.push(actions);
      break;
    }
    case "running":
    case "stopping":
      parts.push(
        heading(2, "Checking"),
        el("p", {}, check.text ?? ""),
        el(
          "div",
          { class: "actions" },
          button("Stop", () => model.stopCheck(), { focusKey: "stop", disabled: check.phase === "stopping" }),
        ),
      );
      break;
    case "done":
      parts.push(
        heading(2, "Checked"),
        notice("done", check.text ?? "", button("OK", () => model.closeCheck(), { variant: "primary", focusKey: "ok" })),
      );
      break;
    case "problem":
      parts.push(
        heading(2, "Check with the AI model"),
        problemNotice(check.problem!, () => model.resolveCheckProblem(), "check-problem"),
        el("div", { class: "actions" }, button("Close", () => model.closeCheck(), { variant: "quiet", focusKey: "close" })),
      );
      break;
  }
  return parts;
}

function drawSplit(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const split = state.split!;
  const parts: HTMLElement[] = [heading(2, "Split again")];
  switch (split.phase) {
    case "previewing":
      parts.push(busy("Getting this ready…"));
      break;
    case "confirm":
    case "applying":
      parts.push(
        el("p", {}, split.text ?? ""),
        el(
          "div",
          { class: "actions" },
          button(split.phase === "confirm" ? "Split again" : "Splitting…", () => model.confirmSplit(), {
            variant: "primary",
            focusKey: "split-confirm",
            disabled: split.phase === "applying",
          }),
          button("Cancel", () => model.closeSplit(), { focusKey: "split-cancel", disabled: split.phase === "applying" }),
        ),
      );
      break;
    case "done":
      parts.push(notice("done", split.text ?? "", button("OK", () => model.closeSplit(), { variant: "primary", focusKey: "split-ok" })));
      break;
    case "problem":
      parts.push(problemNotice(split.problem!, () => model.resolveSplitProblem(), "split-problem"));
      break;
  }
  return parts;
}

// -- the screen -------------------------------------------------------------------------- //

/** What the live regions should say for this state, if anything. */
function announcement(state: ReviewState): { tone: Tone; text: string } | null {
  const problem = state.split?.problem ?? state.check?.problem ?? state.open?.problem ?? state.listProblem;
  if (problem) return { tone: "error", text: problem.message };
  if (state.split?.text && state.split.phase === "done") return { tone: "done", text: state.split.text };
  if (state.check?.text) return { tone: "info", text: state.check.text };
  if (state.open?.step.name === "done") return { tone: "done", text: state.open.step.lines.join(" ") };
  return state.notice;
}

function viewKey(state: ReviewState): string {
  if (state.split) return `split:${state.split.survivor}:${state.split.absorbed}`;
  if (state.check) return "check";
  if (state.open) return `decision:${state.open.key}`;
  return "list";
}

/** Redraws the review screen into `root`, keeping focus, and updates the live regions. */
export function renderReview(root: HTMLElement, live: LiveRegions, model: ReviewModel, state: ReviewState): void {
  redraw(root, viewKey(state), () => {
    const parts = state.split
      ? drawSplit(model, state)
      : state.check
        ? drawCheck(model, state)
        : state.open
          ? drawDecision(model, state)
          : drawList(model, state);
    root.replaceChildren(...parts);
  });
  const said = announcement(state);
  const politeText = said && said.tone !== "error" ? said.text : "";
  const alertText = said?.tone === "error" ? said.text : "";
  if (live.status.textContent !== politeText) live.status.textContent = politeText;
  if (live.alert.textContent !== alertText) live.alert.textContent = alertText;
}
