// Draws who can see a page with the design system's components (ADR-0066,
// ADR-0069). It decides nothing: every word comes from wording.ts. Two forms:
// the three labels as a choice, where a page is shown in full, and the one
// action "Keep only on this Mac", where a page is only named.
import { badge, callout, el, segmented, uiButton } from "../system/index.ts";
import type { LabelModel, LabelState, PageRef } from "./model.ts";
import { LABEL_WORDS, labelOptions, type Level } from "./wording.ts";

/** The last change to this page, with Undo when it can be undone, or why it failed. */
function notice(page: PageRef, model: LabelModel, state: LabelState): HTMLElement | null {
  if (state.notice?.conceptId === page.conceptId) {
    const undo = state.notice.undo;
    return callout("success", state.notice.text, {
      live: "status",
      actions:
        undo === null
          ? []
          : [
              uiButton(LABEL_WORDS.undo, () => model.undo(), {
                size: "sm",
                loading: state.busy === page.conceptId,
                loadingLabel: LABEL_WORDS.undoing,
                focusKey: `label-undo:${page.conceptId}`,
              }),
            ],
    });
  }
  if (state.problem?.conceptId === page.conceptId) return callout("error", state.problem.text, { live: "alert" });
  return null;
}

/** The three labels as one choice; choosing one changes it at once. */
export function labelChoice(page: PageRef, model: LabelModel, state: LabelState): HTMLElement {
  const level = model.levelOf(page);
  const options = labelOptions();
  const choice = segmented(
    LABEL_WORDS.group,
    options.map(({ value, label }) => ({ value, label })),
    level,
    (value) => void model.set(page, value as Level),
  );
  choice.querySelectorAll<HTMLElement>('[role="radio"]').forEach((radio, index) => {
    radio.dataset["focus"] = `label:${page.conceptId}:${options[index]?.value ?? index}`;
  });
  if (state.busy === page.conceptId) choice.setAttribute("aria-busy", "true");
  const meaning = options.find((o) => o.value === level)?.meaning ?? "";
  return el(
    "div",
    { class: "ui-stack" },
    el("p", { class: "ui-card__meta" }, LABEL_WORDS.group),
    choice,
    el("p", { class: "ui-field__hint" }, meaning),
    notice(page, model, state),
  );
}

/** The one action: keep a named page only on this Mac. */
export function keepHereAction(page: PageRef, model: LabelModel, state: LabelState): HTMLElement {
  const kept = model.levelOf(page) === "confidential";
  const action = kept
    ? badge("neutral", LABEL_WORDS.keptHere)
    : uiButton(LABEL_WORDS.keepHere, () => model.set(page, "confidential"), {
        variant: "quiet",
        size: "sm",
        loading: state.busy === page.conceptId,
        loadingLabel: LABEL_WORDS.saving,
        disabled: state.busy !== null && state.busy !== page.conceptId,
        focusKey: `label-keep:${page.conceptId}`,
      });
  return el("div", { class: "ui-stack" }, action, notice(page, model, state));
}
