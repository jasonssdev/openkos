// What the app says about who can see a page (docs/decision-wording.md, "Who
// can see a page"), kept apart from the DOM so every sentence is tested. The
// labels are the brief's; the engine's levels never reach the screen.
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";
import { whoCanSee } from "../review/wording.ts";

export type Level = Schemas["SensitivityPreview"]["level"];

const ORDER: Level[] = ["public", "private", "confidential"];

export const LABEL_WORDS = {
  group: "Who can see it",
  keepHere: "Keep only on this Mac",
  keptHere: "Only on this Mac",
  undo: "Undo",
  undoing: "Undoing…",
  saving: "Saving…",
};

/** The three labels, from the least to the most kept. */
export function labelOptions(): { value: Level; label: string; meaning: string }[] {
  return ORDER.map((value) => ({ value, ...whoCanSee(value) }));
}

const quoted = (title: string) => `“${title}”`;

/** The confirmation after a change. `raised` pages made from it were raised with it. */
export function changedLine(title: string, level: Level, raised: number): string {
  const now = `${quoted(title)} is now ${whoCanSee(level).label}`;
  if (raised <= 0) return `${now}.`;
  const those = raised === 1 ? "that page" : "those pages";
  const others = raised === 1 ? "so is 1 page made from it" : `so are ${raised} pages made from it`;
  return `${now}, and ${others}. To change ${those} back, change ${raised === 1 ? "it" : "each one"} on its own.`;
}

/** The confirmation after an undo. */
export function undoneLine(title: string, level: Level): string {
  return `${quoted(title)} is ${whoCanSee(level).label} again.`;
}

/** The question before a page marked Only on this Mac is let out while a cloud AI model is on. */
export function lowerWarning(
  title: string,
  level: Level,
  host: string,
): { title: string; description: string; confirm: string; cancel: string } {
  const label = whoCanSee(level).label;
  return {
    title: `Let ${quoted(title)} be sent to ${host}?`,
    description: `While a cloud AI model is on, pages marked ${label} are sent to ${host} whenever the AI model needs them. ${quoted(title)} will be too.`,
    confirm: `Mark it ${label}`,
    cancel: "Keep it only on this Mac",
  };
}

const BEFORE: Record<string, string> = {
  workspace_busy: "OpenKOS is finishing something else. Nothing was changed.",
  stale: "This page changed while you were looking. Nothing was changed. Try again.",
  write_drift: "This page changed while you were looking. Nothing was changed. Try again.",
  page_not_found: "This page no longer exists, so nothing was changed.",
};

/** Why a change did not happen. `sent`: the change itself was sent, so an unknown failure may have changed it. */
export function problemFor(error: unknown, sent: boolean): string {
  if (error instanceof ApiError && BEFORE[error.code] !== undefined) return BEFORE[error.code] as string;
  if (error instanceof EngineUnreachable) {
    return sent ? "OpenKOS stopped responding. Look at the page again to see whether it changed." : "OpenKOS isn't responding. Nothing was changed.";
  }
  return sent ? "Something went wrong. Look at the page again to see whether it changed." : "Something went wrong. Nothing was changed.";
}
