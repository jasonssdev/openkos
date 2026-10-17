// What the status page says, kept apart from the DOM so it can be tested. No
// function here reads the token, and a failure is worded from its class and the
// engine's fixed message only, never from an unknown error's text.
import {
  ApiError,
  EngineUnreachable,
  ProtocolError,
  type EngineState,
  type Schemas,
} from "../client/index.ts";
import type { BadgeTone, NavEntry } from "./system/index.ts";

export interface EngineWording {
  text: string;
  detail?: string;
}

export function describeEngine(state: EngineState): EngineWording {
  switch (state.state) {
    case "starting":
      return { text: "Starting the engine…" };
    case "ready":
      return { text: "The engine is running." };
    case "restarting":
      return {
        text: `The engine stopped unexpectedly. Restarting it (attempt ${state.attempt + 1})…`,
        detail: state.detail,
      };
    case "failed":
      return { text: "The engine could not be started.", detail: state.detail };
    case "stopped":
      return { text: "The engine has stopped." };
    case "no_workspace":
      return { text: "No library is open yet." };
    default:
      return { text: "The engine's state is unknown." };
  }
}

/** The sidebar's engine badge: a tone and a word, never color alone. */
export function engineBadge(state: EngineState | null): [BadgeTone, string] {
  switch (state?.state) {
    case "starting":
      return ["info", "Engine starting"];
    case "ready":
      return ["success", "Engine running"];
    case "restarting":
      return ["warning", "Engine restarting"];
    case "failed":
      return ["error", "Engine not running"];
    case "stopped":
      return ["error", "Engine stopped"];
    case "no_workspace":
      return ["neutral", "No library open"];
    case undefined:
      return ["error", "Engine state unknown"];
    default:
      return ["neutral", "Engine state unknown"];
  }
}

/** The sidebar: Ask, Add files, To review with its count when one is known, and Status. */
export function navEntries(reviewCount: number | undefined): NavEntry[] {
  return [
    { id: "ask", label: "Ask" },
    { id: "add", label: "Add files" },
    reviewCount === undefined ? { id: "review", label: "To review" } : { id: "review", label: "To review", count: reviewCount },
    { id: "status", label: "Status" },
  ];
}

export function openDecisions(status: Schemas["WorkspaceStatus"]): number {
  return Object.values(status.open_decisions_by_kind).reduce((sum, n) => sum + n, 0);
}

/**
 * The count beside To review: what its list shows once read, else what the
 * status counted, else unknown.
 */
export function reviewCount(
  review: { loaded: boolean; decisions: readonly unknown[] } | null,
  statusOpen: number | undefined,
): number | undefined {
  return review?.loaded ? review.decisions.length : statusOpen;
}

export interface WorkspaceSummary {
  shown: [string, string][];
  more: [string, string, "code"][];
}

/** The status facts in the brief's words (your library, pages, files), the counts first; where the folder is waits behind a disclosure. */
export function workspaceSummary(status: Schemas["WorkspaceStatus"]): WorkspaceSummary {
  const all = new Map(statusFacts(status));
  const pick = (term: string): [string, string] => [term, all.get(term) ?? ""];
  return {
    shown: [pick("To review"), pick("Pages"), pick("Files you added")],
    more: [["Folder", status.root, "code"]],
  };
}

export function statusFacts(status: Schemas["WorkspaceStatus"]): [string, string][] {
  const open = openDecisions(status);
  return [
    ["Folder", status.root],
    ["Files you added", String(status.sources)],
    ["Pages", String(status.concepts)],
    ["To review", String(open)],
  ];
}

const UNREADABLE = "Your library's status could not be read";

export function statusFailure(error: unknown): string {
  if (error instanceof ApiError) return `${UNREADABLE}: ${error.message}.`;
  if (error instanceof ProtocolError) {
    return `${UNREADABLE}: the engine answered in a form this app cannot read.`;
  }
  if (error instanceof EngineUnreachable) return `${UNREADABLE}: ${error.message}.`;
  return `${UNREADABLE}.`;
}
