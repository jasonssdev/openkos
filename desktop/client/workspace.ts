// The shell's own commands for the workspace it serves (#1487). A person who
// never opens a terminal chooses a folder here: an existing workspace to open,
// or an empty or new folder to create one in, which the shell does by running
// the engine's own `init` there with no prompt. The shell remembers the choice
// in its own configuration directory, never in the workspace. Until something
// is chosen, `engine_state` answers `no_workspace` and no engine runs.
// `OPENKOS_WORKSPACE`, when set, wins and both commands refuse. Opening or
// creating restarts the engine, so read `engine_state` again afterwards.

import type { Invoke } from "./shell.ts";

/** Every reason the shell's workspace commands refuse with (`src-tauri/src/workspace.rs`, `lib.rs`). */
export const WORKSPACE_ERROR_REASONS = [
  "set_by_environment",
  "invalid_path",
  "not_a_folder",
  "not_a_workspace",
  "already_a_workspace",
  "folder_not_empty",
  "engine_not_located",
  "init_failed",
  "store_failed",
] as const;

export type WorkspaceErrorReason = (typeof WORKSPACE_ERROR_REASONS)[number] | "unknown";

/** Where the served workspace comes from. */
export type WorkspaceSource = "environment" | "remembered" | "none";

/** What `workspace_choice_status` answers, and what opening or creating returns. */
export interface WorkspaceChoice {
  source: WorkspaceSource;
  /** The folder, or `null` when nothing is chosen. */
  path: string | null;
  /** Whether that folder is a workspace now; false when there is none. */
  valid: boolean;
}

/** A workspace command the shell refused, or answered in a shape it never sends. */
export class WorkspaceError extends Error {
  readonly reason: WorkspaceErrorReason;

  constructor(reason: WorkspaceErrorReason, message: string) {
    super(message);
    this.name = "WorkspaceError";
    this.reason = reason;
  }
}

const UNKNOWN = "the shell did not answer the workspace command as expected";

function isReason(value: unknown): value is (typeof WORKSPACE_ERROR_REASONS)[number] {
  return (WORKSPACE_ERROR_REASONS as readonly unknown[]).includes(value);
}

function refusal(error: unknown): WorkspaceError {
  if (typeof error === "object" && error !== null) {
    const { reason, detail } = error as Record<string, unknown>;
    if (isReason(reason) && typeof detail === "string") return new WorkspaceError(reason, detail);
  }
  return new WorkspaceError("unknown", UNKNOWN);
}

async function run(invoke: Invoke, command: string, args?: Record<string, unknown>): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch (error) {
    throw refusal(error);
  }
}

function choice(answer: unknown): WorkspaceChoice {
  if (typeof answer === "object" && answer !== null) {
    const { source, path, valid } = answer as Record<string, unknown>;
    if (
      (source === "environment" || source === "remembered" || source === "none") &&
      (typeof path === "string" || path === null) &&
      typeof valid === "boolean"
    ) {
      return { source, path, valid };
    }
  }
  throw new WorkspaceError("unknown", UNKNOWN);
}

export type Workspaces = ReturnType<typeof createWorkspaces>;

/** The workspace commands over the shell's `invoke`. */
export function createWorkspaces(invoke: Invoke) {
  return {
    /** Which workspace is served, and whether it is one. */
    async status(): Promise<WorkspaceChoice> {
      return choice(await run(invoke, "workspace_choice_status"));
    },
    /** Open the existing workspace at the absolute `path` and restart the engine on it. */
    async open(path: string): Promise<WorkspaceChoice> {
      return choice(await run(invoke, "open_workspace", { path }));
    },
    /**
     * Create a workspace in the empty or missing folder at the absolute `path`
     * with the engine's `init` (recommended models, no prompt), then start the
     * engine on it. A folder with anything in it is refused.
     */
    async create(path: string): Promise<WorkspaceChoice> {
      return choice(await run(invoke, "create_workspace", { path }));
    },
    /** The system folder picker; `null` when the user cancels. */
    async pickFolder(): Promise<string | null> {
      const answer = await run(invoke, "pick_workspace_folder");
      if (answer === null || typeof answer === "string") return answer;
      throw new WorkspaceError("unknown", UNKNOWN);
    },
  };
}
