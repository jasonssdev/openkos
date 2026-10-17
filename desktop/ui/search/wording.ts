// What the app says about rebuilding search (docs/decision-wording.md,
// "Rebuilding search"), kept apart from the DOM so every sentence is tested.
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";

type SearchIndex = Schemas["SearchIndex"];
type Outcome = Schemas["RebuildOutcome"];

export const SEARCH_WORDS = {
  rebuild: "Rebuild search",
  starting: "Starting…",
  stop: "Stop",
  stopping: "Stopping…",
  gettingReady: "Getting ready…",
  finishing: "Finishing…",
  local: "The AI model on this Mac reads your pages again. Nothing leaves this Mac.",
  stopped: "Stopped. Search wasn't changed.",
};

const pages = (n: number) => (n === 1 ? "1 page" : `${n} pages`);

/** Why search needs rebuilding, or `null` when it does not. */
export function needLine(index: SearchIndex): string | null {
  if (index.model_changed) return "Search needs to be rebuilt for the new AI model. Until it is, answers may miss some pages.";
  if (index.needs_rebuild) return "Search is behind your latest changes, so answers may miss them. Rebuilding brings it up to date.";
  return null;
}

/** Where a rebuild sends page text, before it starts. */
export function costLine(sendsTo: string | null): string {
  return sendsTo === null ? SEARCH_WORDS.local : `You'll see what is sent to ${sendsTo} before it starts.`;
}

/** The line beside the progress while it runs. */
export function progressLine(phase: Schemas["RebuildStatus"]["phase"], done: number, total: number): string {
  if (phase === "finishing") return SEARCH_WORDS.finishing;
  if (phase === "embedding" && total > 0) return `Reading ${done} of ${pages(total)}…`;
  return SEARCH_WORDS.gettingReady;
}

/** The question before page text is sent to a cloud AI model for search. */
export function cloudWarning(host: string): { title: string; description: string; lines: string[]; confirm: string; cancel: string } {
  return {
    title: `Rebuild search with ${host}?`,
    description: `OpenKOS will send the text of your pages to ${host} so search can find them by what they mean.`,
    lines: ["Pages marked Shareable or Private are sent.", "Pages marked Only on this Mac are never sent.", `${host} may charge you for this.`],
    confirm: SEARCH_WORDS.rebuild,
    cancel: "Not now",
  };
}

/** What a finished rebuild did, in one or two sentences. */
export function doneLine(outcome: Outcome): string {
  const lines = [outcome.complete ? "Search is up to date." : "Search was rebuilt."];
  if (outcome.withheld > 0) {
    lines.push(
      `${pages(outcome.withheld)} marked Only on this Mac ${outcome.withheld === 1 ? "wasn't" : "weren't"} sent, so search finds ${outcome.withheld === 1 ? "it" : "them"} only by ${outcome.withheld === 1 ? "its" : "their"} words.`,
    );
  }
  if (outcome.failed > 0) lines.push(`The AI model couldn't read ${pages(outcome.failed)}. Rebuilding again tries ${outcome.failed === 1 ? "it" : "them"} again.`);
  if (outcome.skipped > 0) lines.push(`${pages(outcome.skipped)} couldn't be opened.`);
  return lines.join(" ");
}

const PROBLEMS: Record<string, string> = {
  rebuild_active: "Search is already being rebuilt.",
  backend_unavailable: "The AI model isn't running. OpenKOS needs it to rebuild search. Search wasn't changed.",
  backend_model_not_found: "The AI model for search isn't installed. Search wasn't changed.",
  backend_error: "The AI model ran into a problem. Search wasn't changed.",
  workspace_busy: "OpenKOS is busy. Try again in a moment. Search wasn't changed.",
  search_unavailable: "Search couldn't be rebuilt. Search wasn't changed.",
  workspace_unreadable: "Your library couldn't be read. Search wasn't changed.",
  // The engine lost the rebuild: the registry lives in memory and a restart empties it.
  not_found: "OpenKOS restarted while it was rebuilding search. Search wasn't changed.",
};

/** Why a rebuild did not finish. A rebuild writes only at its end, so nothing changed. */
export function problemFor(error: unknown): string {
  if (error instanceof ApiError) return PROBLEMS[error.code] ?? "Something went wrong. Search wasn't changed.";
  if (error instanceof EngineUnreachable) return "OpenKOS isn't responding.";
  return "Something went wrong. Search wasn't changed.";
}
