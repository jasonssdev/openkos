// Draws the Activity screen with the design system's components: what runs now,
// what waits, today's model use, and what ran lately. Read-only, no controls.
// It decides nothing; every word comes from wording.ts.
import "./activity.css";
import { badge, callout, el, pageHeader, progressBar, redraw, skeleton, uiCard } from "../system/index.ts";
import type { ActivityState } from "./model.ts";
import { ACTIVITY_WORDS, budgetLine, currentLine, jobTitle, outcomeLine, queueLines, since } from "./wording.ts";

function paragraphs(lines: string[]): HTMLElement[] {
  return lines.map((line) => el("p", {}, line));
}

function recentRow(job: ActivityJob, now: number): HTMLLIElement {
  return el(
    "li",
    { class: "activity-row" },
    el(
      "span",
      { class: "activity-row__text" },
      el("span", { class: "activity-row__title" }, jobTitle(job.kind)),
      el("span", { class: "activity-row__meta" }, `${outcomeLine(job)} ${since(job.started_at, now)}`.trim()),
    ),
    job.needs_attention ? badge("warning", "Needs a look") : null,
  );
}

type ActivityJob = NonNullable<ActivityState["activity"]>["recent"][number];

/** Redraws the Activity screen into `root`. `now` is the clock, in milliseconds. */
export function renderActivity(root: HTMLElement, state: ActivityState, now: number): void {
  redraw(root, "activity", () => {
    const header = pageHeader({ title: ACTIVITY_WORDS.title, description: ACTIVITY_WORDS.description });
    const activity = state.activity;
    if (activity === null) {
      root.replaceChildren(header, skeleton(3, ACTIVITY_WORDS.waitingForEngine), el("p", {}, ACTIVITY_WORDS.waitingForEngine));
      return;
    }
    const stale = state.failed ? [callout("warning", ACTIVITY_WORDS.failed, { live: "status" })] : [];
    if (activity.jobs_unreadable) {
      root.replaceChildren(header, ...stale, callout("warning", ACTIVITY_WORDS.unreadable));
      return;
    }
    const { budget } = activity;
    root.replaceChildren(
      header,
      ...stale,
      uiCard({ title: ACTIVITY_WORDS.now, headingLevel: 2 }, el("p", { attrs: { role: "status" } }, currentLine(activity.current))),
      uiCard({ title: ACTIVITY_WORDS.waiting, headingLevel: 2 }, ...paragraphs(queueLines(activity.waiting_files, activity.uncommitted_paths))),
      uiCard(
        { title: ACTIVITY_WORDS.budget, headingLevel: 2 },
        progressBar(budgetLine(budget), budget.calls_used, budget.calls_limit),
        el("p", {}, budgetLine(budget)),
        budget.exhausted ? callout("warning", ACTIVITY_WORDS.limitReached) : null,
      ),
      uiCard(
        { title: ACTIVITY_WORDS.recent, headingLevel: 2 },
        activity.recent.length === 0
          ? el("p", {}, ACTIVITY_WORDS.nothingRecent)
          : el("ul", { class: "activity-list", attrs: { "aria-label": ACTIVITY_WORDS.recent } }, ...activity.recent.map((job) => recentRow(job, now))),
      ),
    );
  });
}
