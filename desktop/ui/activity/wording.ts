// What the Activity screen says, kept apart from the DOM so every sentence is
// tested. Plain words: no job ids, no outcome codes, no engine vocabulary.
import type { Schemas } from "../../client/index.ts";

type Job = Schemas["EngineJob"];
type Budget = Schemas["DailyBudget"];

export const ACTIVITY_WORDS = {
  navLabel: "Activity",
  title: "Activity",
  description: "What OpenKOS is doing in the background.",
  now: "Right now",
  waiting: "Waiting",
  budget: "Today's AI model use",
  recent: "Done lately",
  nothingRecent: "Nothing has run yet.",
  limitReached: "The daily limit is reached. Background work resumes tomorrow.",
  unreadable:
    "The record of background work can't be read, so nothing is shown here. It is only a log: deleting the file jobs.db in the library's .openkos folder starts a fresh one.",
  failed: "Activity could not be read just now. What is shown may be out of date.",
  waitingForEngine: "Waiting for OpenKOS to start…",
};

const TITLES: Record<string, string> = {
  watch: "Looked at your watched folder",
  maintenance: "Tidied your library",
  "commit-retry": "Tried again to save changes",
};

const NOW_LINES: Record<string, string> = {
  watch: "Looking at your watched folder.",
  maintenance: "Tidying your library.",
  "commit-retry": "Trying again to save changes.",
};

export function jobTitle(kind: string): string {
  return TITLES[kind] ?? "Background work";
}

export function currentLine(job: Job | null): string {
  if (job === null) return "Nothing is running.";
  return NOW_LINES[job.kind] ?? "Working in the background.";
}

const ENDED: Record<string, string> = {
  completed: "Finished.",
  budget_exhausted: "Stopped at the daily limit.",
  timed_out: "Ran out of time.",
  stopped: "Stopped.",
  busy: "Skipped, the library was busy.",
  commit_failed: "Could not save its changes.",
  refused: "Did not run.",
  failed: "Something went wrong.",
};

/** How a finished task ended, then what it did and what it left for later. */
export function outcomeLine(job: Job): string {
  const ended = (job.outcome === null ? undefined : ENDED[job.outcome]) ?? "Did not finish.";
  const counts: string[] = [];
  if (job.units_done > 0) counts.push(`${job.units_done} done`);
  if (job.units_deferred > 0) counts.push(`${job.units_deferred} left for later`);
  if (counts.length > 0) return `${ended} ${counts.join(", ")}.`;
  return job.outcome === "completed" ? `${ended} Nothing to do.` : ended;
}

const plural = (n: number, one: string) => (n === 1 ? `1 ${one}` : `${n} ${one}s`);

/** How long ago, in words, from `now` (milliseconds); empty for a time it cannot read. */
export function since(iso: string, now: number): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${plural(minutes, "minute")} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${plural(hours, "hour")} ago`;
  return `${plural(Math.floor(hours / 24), "day")} ago`;
}

export function budgetLine(budget: Budget): string {
  const word = budget.calls_limit === 1 ? "request" : "requests";
  return `${budget.calls_used} of ${budget.calls_limit} AI model ${word} used today.`;
}

/** What waits: files in the watched folder, and changes not yet saved. */
export function queueLines(files: number, changes: number): string[] {
  const lines: string[] = [];
  if (files > 0) lines.push(`${files} ${files === 1 ? "file is" : "files are"} waiting in your watched folder.`);
  if (changes > 0) lines.push(`${changes} ${changes === 1 ? "change is" : "changes are"} waiting to be saved.`);
  return lines.length > 0 ? lines : ["Nothing is waiting."];
}
