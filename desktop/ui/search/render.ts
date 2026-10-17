// Draws rebuilding search with the design system's components (ADR-0066,
// ADR-0069): why search is behind, the one action, its progress and how it
// ended. It decides nothing; every word comes from wording.ts.
import { callout, el, progressBar, spinner, uiButton } from "../system/index.ts";
import type { SearchState } from "./model.ts";
import { SEARCH_WORDS, costLine, needLine, progressLine } from "./wording.ts";

export interface SearchActions {
  rebuild: () => unknown;
  stop: () => unknown;
}

const TONE = { done: "success", stopped: "info", problem: "error" } as const;

function rebuildButton(state: SearchState, actions: SearchActions): HTMLElement {
  return uiButton(SEARCH_WORDS.rebuild, actions.rebuild, {
    loading: state.starting,
    loadingLabel: SEARCH_WORDS.starting,
    focusKey: "search-rebuild",
  });
}

/** The section, or `null` while search is current and nothing ran this session. */
export function rebuildSection(state: SearchState, actions: SearchActions): HTMLElement | null {
  const job = state.job;
  if (job !== null) {
    const stopping = job.state === "stopping";
    const line = stopping ? SEARCH_WORDS.stopping : progressLine(job.phase, job.done, job.total);
    return el(
      "div",
      { class: "ui-stack", attrs: { "aria-live": "polite" } },
      job.phase === "embedding" && job.total > 0 ? progressBar(line, job.done, job.total) : null,
      spinner(line),
      el(
        "div",
        { class: "ui-actions" },
        uiButton(SEARCH_WORDS.stop, actions.stop, { loading: stopping, loadingLabel: SEARCH_WORDS.stopping, focusKey: "search-stop" }),
      ),
    );
  }
  const index = state.index;
  const need = index === null ? null : needLine(index);
  const again = need !== null ? [rebuildButton(state, actions)] : [];
  if (state.result !== null) {
    return el("div", {}, callout(TONE[state.result.kind], state.result.text, { live: "status", actions: again }));
  }
  if (index === null || need === null) return null;
  return el("div", {}, callout("info", `${need} ${costLine(index.sends_to)}`, { actions: again }));
}
