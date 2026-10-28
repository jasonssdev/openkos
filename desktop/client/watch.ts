// The shell's own commands for the inbox watch (ADR-0078). The shell runs the
// daemon that does the watching, only while the person turned the watch on for
// the current workspace, and remembers that choice per workspace. These
// commands work whether or not the engine runs. `set_watch` starts or stops the
// daemon; the folder itself is written through the local API
// (`/v1/settings/inbox`), never by the shell.

import type { Invoke } from "./shell.ts";

/** Every reason the watch commands refuse with (`src-tauri/src/lib.rs`). */
export const WATCH_ERROR_REASONS = ["no_workspace", "watch_choice_unkept"] as const;

export type WatchErrorReason = (typeof WATCH_ERROR_REASONS)[number] | "unknown";

/** What the shell knows about its daemon (`src-tauri/src/watch.rs`, `WatchState`), plus the person's choice. */
export type WatchStatus = { wanted: boolean } & (
  | { state: "off" }
  | { state: "starting"; attempt: number }
  | { state: "on"; attempt: number; pid: number }
  | { state: "restarting"; attempt: number; delay_ms: number; reason: string; detail: string }
  | { state: "failed"; reason: string; detail: string }
  | { state: "no_workspace" }
);

const STATES = ["off", "starting", "on", "restarting", "failed", "no_workspace"] as const;

/** A watch command the shell refused, or answered in a shape it never sends. */
export class WatchError extends Error {
  readonly reason: WatchErrorReason;

  constructor(reason: WatchErrorReason, message: string) {
    super(message);
    this.name = "WatchError";
    this.reason = reason;
  }
}

const UNKNOWN = "the shell did not answer the watch command as expected";

function isReason(value: unknown): value is (typeof WATCH_ERROR_REASONS)[number] {
  return (WATCH_ERROR_REASONS as readonly unknown[]).includes(value);
}

function refusal(error: unknown): WatchError {
  if (typeof error === "object" && error !== null) {
    const { reason, detail } = error as Record<string, unknown>;
    if (isReason(reason) && typeof detail === "string") return new WatchError(reason, detail);
  }
  return new WatchError("unknown", UNKNOWN);
}

async function run(invoke: Invoke, command: string, args?: Record<string, unknown>): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch (error) {
    throw refusal(error);
  }
}

function status(answer: unknown): WatchStatus {
  if (typeof answer === "object" && answer !== null) {
    const { state, wanted } = answer as Record<string, unknown>;
    if (typeof wanted === "boolean" && (STATES as readonly unknown[]).includes(state)) {
      return answer as WatchStatus;
    }
  }
  throw new WatchError("unknown", UNKNOWN);
}

export type Watch = ReturnType<typeof createWatch>;

/** The watch commands over the shell's `invoke`. */
export function createWatch(invoke: Invoke) {
  return {
    /** Whether the watch is turned on for the current workspace, and what its daemon is doing. */
    async state(): Promise<WatchStatus> {
      return status(await run(invoke, "watch_state"));
    },
    /** Turn the watch on or off for the current workspace; on runs the whole daemon. */
    async set(on: boolean): Promise<WatchStatus> {
      return status(await run(invoke, "set_watch", { on }));
    },
    /**
     * Raise the macOS notification that the watch paused on the AI model's
     * limit. The text is the shell's: the page cannot choose it.
     */
    async notifyBudgetStop(): Promise<void> {
      await run(invoke, "notify_watch_budget");
    },
    /** The system folder picker for the folder to watch; `null` when the user cancels. */
    async pickFolder(): Promise<string | null> {
      const answer = await run(invoke, "pick_inbox_folder");
      if (answer === null || typeof answer === "string") return answer;
      throw new WatchError("unknown", UNKNOWN);
    },
  };
}
