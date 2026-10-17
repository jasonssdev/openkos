// Draws the review screen from the model's state with the design system's
// components (ADR-0066, ADR-0069): what waits, as a list on the left, and the
// open decision, check or split on the right. It decides nothing: what to ask,
// offer and say comes from model.ts and wording.ts, and every value is written
// as text. Anything secondary waits behind a disclosure (desktop/DESIGN.md).
import {
  answerList,
  badge,
  callout,
  choiceList,
  compare,
  disclosure,
  el,
  emptyState,
  facts,
  listRow,
  redraw,
  skeleton,
  spinner,
  splitView,
  uiButton,
  uiCard,
  uiList,
  type AnswerOption,
} from "../system/index.ts";
import { offersCheckAll, type Notice, type ReviewModel, type ReviewState } from "./model.ts";
import {
  CANNOT_ACT,
  CLAIMS_HEADING,
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

const CHECK_ALL_DOES =
  "Let the AI model compare the pages it hasn't checked yet, and the ones that changed. You'll still decide each one.";

const UNREADABLE = "A page that couldn't be read";

const TONE: Record<Notice["tone"], "info" | "success" | "warning" | "error"> = {
  info: "info",
  done: "success",
  warning: "warning",
  error: "error",
};

/** A heading; the one marked `focus` takes focus when its view opens. */
function heading(level: 1 | 2 | 3, text: string, className: string, focus: boolean): HTMLHeadingElement {
  const node = el(`h${level}`, { class: className, attrs: { tabindex: "-1" } }, text);
  if (focus) node.dataset["view"] = "heading";
  return node;
}

function problemCallout(problem: Problem, resolve: () => unknown, focusKey: string): HTMLElement {
  return callout("error", problem.message, {
    actions: [uiButton(ACTION_LABELS[problem.action], resolve, { variant: "primary", focusKey })],
  });
}

// -- the list ---------------------------------------------------------------------- //

/** True when the detail pane shows something with its own heading to focus. */
function detailOwnsFocus(state: ReviewState): boolean {
  return state.split !== null || state.check !== null || state.open !== null || state.combinedOpen;
}

function drawList(model: ReviewModel, state: ReviewState): HTMLElement {
  const actions: HTMLElement[] = [];
  const checkAll = state.loaded && state.listProblem === null && offersCheckAll(state.decisions);
  if (checkAll) {
    actions.push(uiButton("Check all", () => model.startCheck(), { size: "sm", focusKey: "check-all" }));
  }
  const pane = el(
    "div",
    { class: "ui-pane" },
    el(
      "header",
      { class: "ui-pane__header" },
      heading(1, "To review", "ui-pane__title", !detailOwnsFocus(state)),
      ...actions,
    ),
    checkAll ? el("p", { class: "ui-pane__hint" }, CHECK_ALL_DOES) : null,
  );
  if (state.listProblem) {
    pane.append(problemCallout(state.listProblem, () => model.load(), "list-problem"));
    return pane;
  }
  if (!state.loaded) {
    pane.append(skeleton(4, "Looking for things to review…"));
    return pane;
  }
  if (state.decisions.length > 0) {
    const rows = state.decisions.map((decision) => {
      const entry = listEntry(decision, state.pages);
      return listRow({
        title: entry.title,
        ...(entry.meta ? { meta: entry.meta } : {}),
        ...(entry.changed ? { trailing: badge("warning", "Changed") } : {}),
        selected: state.open?.key === decision.decision_key,
        focusKey: `entry:${decision.decision_key}`,
        onSelect: () => model.open(decision.decision_key),
      });
    });
    pane.append(uiList("Decisions", rows));
  }
  if (state.combined.length > 0) {
    const n = state.combined.length;
    pane.append(
      uiList(COMBINED_HEADING, [
        listRow({
          title: COMBINED_HEADING,
          meta: n === 1 ? "1 page" : `${n} pages`,
          selected: state.combinedOpen,
          focusKey: "combined",
          onSelect: () => model.showCombined(),
        }),
      ]),
    );
  }
  return pane;
}

// -- one decision ------------------------------------------------------------------ //

/** What a screen beyond the review adds to it: who can see each page, on its card. */
export interface ReviewExtras {
  pageAction?: (page: NonNullable<PageView["ref"]>) => HTMLElement | null;
}

function capitalized(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function pageCard(page: PageView, extras: ReviewExtras): HTMLElement {
  // With the label choice on the card, its label is not repeated in the meta line.
  const action = page.ref !== null && extras.pageAction ? extras.pageAction(page.ref) : null;
  const label = action === null ? (page.whoCanSee?.label ?? null) : null;
  const meta = [page.kind ? capitalized(page.kind) : null, page.date ?? null, label].filter(Boolean).join(" · ");
  // A newer decision's quote takes the excerpt's place: it is what is being compared.
  const body = page.quote
    ? el("blockquote", { class: "ui-quote" }, page.quote)
    : page.excerpt
      ? el("p", {}, page.excerpt)
      : null;
  return uiCard(
    {
      title: page.title,
      ...(meta ? { meta } : {}),
      ...(page.sources.length > 0 ? { footer: el("ul", { class: "ui-plain-list" }, ...page.sources.map((line) => el("li", {}, line))) } : {}),
    },
    body,
    action,
  );
}

/** What the AI model saw as not matching, in its own words. */
function drawClaims(claims: string[]): HTMLElement | null {
  if (claims.length === 0) return null;
  return el(
    "section",
    { class: "ui-stack", attrs: { "aria-label": CLAIMS_HEADING } },
    el("h3", { class: "ui-subtitle" }, CLAIMS_HEADING),
    el("ul", { class: "ui-plain-list" }, ...claims.map((claim) => el("li", {}, el("blockquote", { class: "ui-quote" }, claim)))),
  );
}

function drawPages(pages: PageView[], extras: ReviewExtras): HTMLElement | null {
  if (pages.length === 0) return null;
  const cards = pages.map((page) => pageCard(page, extras));
  if (cards.length === 2) return compare(pages.map((p) => p.title).join(" and "), cards[0]!, cards[1]!);
  return el("div", { class: "ui-compare", attrs: { role: "group", "aria-label": "The pages" } }, ...cards);
}

function drawSelection(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const open = state.open!;
  const decision = open.decision;
  // Which pages, and which name, are asked only after "Same thing".
  if (decision.kind !== "identity" || !decision.actions.includes("merge_group") || !open.selection.same) return [];
  const title = (id: string) => state.pages.get(id)?.title || UNREADABLE;
  const parts: HTMLElement[] = [];
  const shape = identityShape(decision);
  if (shape === "group") {
    const members = decision.detail && "member_ids" in decision.detail ? decision.detail.member_ids : decision.targets;
    parts.push(
      choiceList({
        legend: "Mark the pages that are the same",
        type: "checkbox",
        name: "marked",
        items: members.map((id) => ({ value: id, label: title(id), checked: open.selection.marked.includes(id) })),
        onChange: (id) => model.toggleMark(id),
      }),
    );
  }
  const names = model.namesFor();
  const askName = shape === "group" ? names.length >= 1 : open.selection.okfType !== null && names.length > 1;
  if (askName) {
    parts.push(
      choiceList({
        legend: "Which one should the combined page keep its name from?",
        type: "radio",
        name: "name",
        items: names.map((id) => ({ value: id, label: title(id), checked: open.selection.name === id })),
        onChange: (id) => model.chooseName(id),
      }),
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

function answersFor(model: ReviewModel, state: ReviewState, options: Option[]): HTMLElement {
  const items: AnswerOption[] = options.map((option, index) => {
    const reason = unavailableReason(model, state, option);
    return {
      label: option.label,
      does: option.does,
      onChoose: () => model.choose(option),
      variant: option.choice === "later" ? "quiet" : "secondary",
      focusKey: `option:${index}`,
      disabled: reason !== null,
      reason,
    };
  });
  return answerList("Your answer", items);
}

function drawStep(model: ReviewModel, state: ReviewState, options: Option[], after: string | null): HTMLElement[] {
  const open = state.open!;
  const step = open.step;
  switch (step.name) {
    case "choosing": {
      const parts = [...drawSelection(model, state), answersFor(model, state, options)];
      if (after) parts.push(el("p", { class: "ui-lead" }, after));
      if (open.selection.same) {
        parts.push(el("div", { class: "ui-actions" }, uiButton("Back", () => model.askAgain(), { variant: "quiet", focusKey: "ask-again" })));
      }
      const kind = open.selection.okfType;
      const option = kind === null ? undefined : options.find((o) => o.okfType === kind);
      if (kind !== null && option && model.namesForKind().length > 1) {
        parts.push(
          el(
            "div",
            { class: "ui-actions" },
            uiButton(`Continue as ${typeNoun(kind).withArticle}`, () => model.choose(option), {
              variant: "primary",
              focusKey: "continue",
              disabled: open.selection.name === null,
            }),
          ),
        );
      }
      return parts;
    }
    case "previewing":
      return [spinner("Getting this ready…")];
    case "preview":
    case "applying": {
      const applying = step.name === "applying";
      return [
        el(
          "section",
          { class: "ui-stack", attrs: { "aria-labelledby": "what-will-happen" } },
          el("h3", { class: "ui-subtitle", attrs: { id: "what-will-happen" } }, "What will happen"),
          ...step.lines.map((line) => el("p", {}, line)),
          el(
            "div",
            { class: "ui-actions" },
            uiButton("Confirm", () => model.confirm(), {
              variant: "primary",
              focusKey: "confirm",
              loading: applying,
              loadingLabel: "Saving your choice…",
            }),
            uiButton("Choose again", () => model.back(), { variant: "quiet", focusKey: "choose-again", disabled: applying }),
          ),
        ),
      ];
    }
    case "done":
    case "undoing": {
      const undoing = step.name === "undoing";
      const actions: HTMLElement[] = [];
      if (undoing || step.undo) {
        actions.push(uiButton("Undo", () => model.undo(), { focusKey: "undo", loading: undoing, loadingLabel: "Undoing…" }));
      }
      actions.push(uiButton("Close", () => model.close(), { variant: "quiet", focusKey: "done-close", disabled: undoing }));
      return [callout("success", step.lines.join(" "), { actions })];
    }
  }
}

function drawDecision(model: ReviewModel, state: ReviewState, extras: ReviewExtras): HTMLElement[] {
  const open = state.open!;
  const view = describeDecision(open.decision, state.pages);
  // After "Same thing", the second question takes the first one's place.
  const asked = open.selection.same && view.next ? view.next : view;
  const parts: HTMLElement[] = [];
  if (open.changed) parts.push(callout("warning", open.changed));
  parts.push(
    el(
      "header",
      { class: "ui-question-header" },
      asked.lead ? el("p", { class: "ui-lead" }, asked.lead) : null,
      heading(2, asked.question, "ui-question", true),
    ),
  );
  if (view.statement) parts.push(el("p", { class: "ui-statement" }, view.statement));
  if (view.facts.length > 0) parts.push(facts(view.facts));
  const pages = drawPages(view.pages, extras);
  if (pages) parts.push(pages);
  const claims = drawClaims(view.claims);
  if (claims) parts.push(claims);
  if (view.reason) {
    parts.push(disclosure({ summary: "Why the AI model thinks so", focusKey: "reason" }, el("blockquote", { class: "ui-quote" }, view.reason)));
  }
  if (view.cannotAct) {
    parts.push(callout("info", CANNOT_ACT));
    return parts;
  }
  if (open.problem) parts.push(problemCallout(open.problem, () => model.resolveProblem(), "problem"));
  parts.push(...drawStep(model, state, asked.options, view.after));
  return parts;
}

// -- the check, split again, and the combined pages ------------------------------------ //

function drawCheck(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const check = state.check!;
  switch (check.phase) {
    case "quoting":
      return [heading(2, "Check with the AI model", "ui-question", true), spinner("Getting ready to check…")];
    case "consent": {
      const consent = check.consent!;
      const actions: HTMLElement[] = [];
      if (consent.agree) actions.push(uiButton(consent.agree, () => model.agreeCheck(), { variant: "primary", focusKey: "agree" }));
      actions.push(uiButton(consent.decline, () => model.closeCheck(), { variant: consent.agree ? "quiet" : "primary", focusKey: "decline" }));
      return [
        heading(2, consent.heading, "ui-question", true),
        check.text ? callout("warning", check.text) : null,
        ...consent.lines.map((line) => el("p", {}, line)),
        el("div", { class: "ui-actions" }, ...actions),
      ].filter((node): node is HTMLElement => node !== null);
    }
    case "running":
    case "stopping":
      return [
        heading(2, "Checking", "ui-question", true),
        el("p", {}, check.text ?? ""),
        el(
          "div",
          { class: "ui-actions" },
          uiButton("Stop", () => model.stopCheck(), { focusKey: "stop", disabled: check.phase === "stopping" }),
        ),
      ];
    case "done":
      return [
        heading(2, "Checked", "ui-question", true),
        callout("success", check.text ?? "", {
          actions: [uiButton("OK", () => model.closeCheck(), { variant: "primary", focusKey: "ok" })],
        }),
      ];
    case "problem":
      return [
        heading(2, "Check with the AI model", "ui-question", true),
        problemCallout(check.problem!, () => model.resolveCheckProblem(), "check-problem"),
        el("div", { class: "ui-actions" }, uiButton("Close", () => model.closeCheck(), { variant: "quiet", focusKey: "close" })),
      ];
  }
}

function drawSplit(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const split = state.split!;
  const parts: HTMLElement[] = [heading(2, "Split again", "ui-question", true)];
  switch (split.phase) {
    case "previewing":
      parts.push(spinner("Getting this ready…"));
      break;
    case "confirm":
    case "applying": {
      const applying = split.phase === "applying";
      parts.push(
        el("p", {}, split.text ?? ""),
        el(
          "div",
          { class: "ui-actions" },
          uiButton("Split again", () => model.confirmSplit(), {
            variant: "primary",
            focusKey: "split-confirm",
            loading: applying,
            loadingLabel: "Splitting…",
          }),
          uiButton("Cancel", () => model.closeSplit(), { variant: "quiet", focusKey: "split-cancel", disabled: applying }),
        ),
      );
      break;
    }
    case "done":
      parts.push(
        callout("success", split.text ?? "", {
          actions: [uiButton("OK", () => model.closeSplit(), { variant: "primary", focusKey: "split-ok" })],
        }),
      );
      break;
    case "problem":
      parts.push(problemCallout(split.problem!, () => model.resolveSplitProblem(), "split-problem"));
      break;
  }
  return parts;
}

function drawCombined(model: ReviewModel, state: ReviewState): HTMLElement[] {
  const parts: HTMLElement[] = [heading(2, COMBINED_HEADING, "ui-question", true)];
  for (const page of state.combined) {
    parts.push(
      el(
        "section",
        { class: "ui-stack" },
        el("h3", { class: "ui-subtitle" }, page.title || UNREADABLE),
        el(
          "ul",
          { class: "ui-plain-list" },
          ...page.lines.map((line) =>
            el(
              "li",
              { class: "ui-line" },
              el("span", {}, line.text),
              uiButton("Split again", () => model.openSplit(page.survivor, line.absorbed), {
                size: "sm",
                focusKey: `split:${page.survivor}:${line.absorbed}`,
              }),
            ),
          ),
        ),
      ),
    );
  }
  return parts;
}

function drawNothingOpen(state: ReviewState): HTMLElement[] {
  const parts: HTMLElement[] = [];
  if (state.notice) parts.push(callout(TONE[state.notice.tone], state.notice.text));
  if (!state.loaded || state.listProblem) return parts;
  parts.push(
    state.decisions.length === 0
      ? emptyState({
          title: "Nothing to review right now",
          text: "When OpenKOS finds pages that may be the same thing, or a connection to confirm, they wait here.",
        })
      : emptyState({ title: "Nothing open", text: "Choose something from the list to see its pages and decide." }),
  );
  return parts;
}

function drawDetail(model: ReviewModel, state: ReviewState, extras: ReviewExtras): HTMLElement {
  const parts = state.split
    ? drawSplit(model, state)
    : state.check
      ? drawCheck(model, state)
      : state.open
        ? drawDecision(model, state, extras)
        : state.combinedOpen
          ? drawCombined(model, state)
          : drawNothingOpen(state);
  return el("div", { class: "ui-detail" }, ...parts);
}

// -- the screen -------------------------------------------------------------------------- //

/** What the live regions should say for this state, if anything. */
function announcement(state: ReviewState): Notice | null {
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
  // Answering "Same thing" asks a new question: focus moves to it.
  if (state.open) return `decision:${state.open.key}:${state.open.selection.same ? "then" : "first"}`;
  if (state.combinedOpen) return "combined";
  return "list";
}

/** Redraws the review screen into `root`, keeping focus, and updates the live regions. */
export function renderReview(
  root: HTMLElement,
  live: LiveRegions,
  model: ReviewModel,
  state: ReviewState,
  extras: ReviewExtras = {},
): void {
  redraw(root, viewKey(state), () =>
    root.replaceChildren(splitView(drawList(model, state), drawDetail(model, state, extras), { list: "To review", detail: "Decision" })),
  );
  const said = announcement(state);
  const politeText = said && said.tone !== "error" ? said.text : "";
  const alertText = said?.tone === "error" ? said.text : "";
  if (live.status.textContent !== politeText) live.status.textContent = politeText;
  if (live.alert.textContent !== alertText) live.alert.textContent = alertText;
}
