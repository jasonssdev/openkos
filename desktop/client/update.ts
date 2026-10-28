// The shell's own commands for the app's updates (ADR-0079). The shell asks the
// project's feed whether a newer build exists and tells the page; it downloads
// and installs one only when the page sends back the version the person
// confirmed, and the updater checks its signature first. Whether the app checks
// at all is the person's choice, kept by the shell. These commands work whether
// or not the engine runs.

import type { Invoke } from "./shell.ts";

/** Every reason the update commands refuse with (`src-tauri/src/update.rs`, `lib.rs`). */
export const UPDATE_ERROR_REASONS = ["choice_unkept", "no_update", "update_changed", "install_failed"] as const;

export type UpdateErrorReason = (typeof UPDATE_ERROR_REASONS)[number] | "unknown";

/** Whether the person lets the app check for updates. */
export interface UpdateSettings {
  check: boolean;
}

/** What one check found (`src-tauri/src/update.rs`, `UpdateCheck`). A failure carries nothing. */
export type UpdateCheck =
  | { state: "off" }
  | { state: "up_to_date" }
  | { state: "available"; version: string; current_version: string; notes: string | null }
  | { state: "failed" };

/** An update command the shell refused, or answered in a shape it never sends. */
export class UpdateError extends Error {
  readonly reason: UpdateErrorReason;

  constructor(reason: UpdateErrorReason, message: string) {
    super(message);
    this.name = "UpdateError";
    this.reason = reason;
  }
}

const UNKNOWN = "the shell did not answer the update command as expected";

function isReason(value: unknown): value is (typeof UPDATE_ERROR_REASONS)[number] {
  return (UPDATE_ERROR_REASONS as readonly unknown[]).includes(value);
}

function refusal(error: unknown): UpdateError {
  if (typeof error === "object" && error !== null) {
    const { reason, detail } = error as Record<string, unknown>;
    if (isReason(reason) && typeof detail === "string") return new UpdateError(reason, detail);
  }
  return new UpdateError("unknown", UNKNOWN);
}

async function run(invoke: Invoke, command: string, args?: Record<string, unknown>): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch (error) {
    throw refusal(error);
  }
}

function settings(answer: unknown): UpdateSettings {
  if (typeof answer === "object" && answer !== null && typeof (answer as Record<string, unknown>).check === "boolean") {
    return { check: (answer as { check: boolean }).check };
  }
  throw new UpdateError("unknown", UNKNOWN);
}

function check(answer: unknown): UpdateCheck {
  if (typeof answer === "object" && answer !== null) {
    const { state, version, current_version, notes } = answer as Record<string, unknown>;
    if (state === "off" || state === "up_to_date" || state === "failed") return { state };
    if (
      state === "available" &&
      typeof version === "string" &&
      typeof current_version === "string" &&
      (notes === null || typeof notes === "string")
    ) {
      return { state, version, current_version, notes };
    }
  }
  throw new UpdateError("unknown", UNKNOWN);
}

export type Updates = ReturnType<typeof createUpdates>;

/** The update commands over the shell's `invoke`. */
export function createUpdates(invoke: Invoke) {
  return {
    /** Whether the app checks for updates. */
    async settings(): Promise<UpdateSettings> {
      return settings(await run(invoke, "update_settings"));
    },
    /** Turn checking on or off; off also forgets an update on offer. */
    async setCheck(on: boolean): Promise<UpdateSettings> {
      return settings(await run(invoke, "set_update_check", { on }));
    },
    /** Ask whether a newer build exists. Nothing is downloaded. */
    async check(): Promise<UpdateCheck> {
      return check(await run(invoke, "check_for_update"));
    },
    /**
     * Download, verify and install `version`, the one the person confirmed,
     * then restart. On success the app restarts and this never settles.
     */
    async install(version: string): Promise<void> {
      await run(invoke, "install_update", { version });
    },
  };
}
