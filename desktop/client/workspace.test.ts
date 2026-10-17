import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { Invoke } from "./shell.ts";
import { WORKSPACE_ERROR_REASONS, WorkspaceError, createWorkspaces, type WorkspaceChoice } from "./workspace.ts";

// The shell's source is the authority on its command names and error reasons.
const LIB = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");
const WORKSPACE = readFileSync(new URL("../src-tauri/src/workspace.rs", import.meta.url), "utf8");
const SUPERVISOR = readFileSync(new URL("../src-tauri/src/supervisor.rs", import.meta.url), "utf8");

const CHOSEN: WorkspaceChoice = { source: "remembered", path: "/Users/me/knowledge", valid: true };

interface Recorded {
  command: string;
  args: Record<string, unknown> | undefined;
}

function fakeInvoke(answer: (command: string) => unknown): { invoke: Invoke; calls: Recorded[] } {
  const calls: Recorded[] = [];
  const invoke = (async (command: string, args?: Record<string, unknown>) => {
    calls.push({ command, args });
    return answer(command);
  }) as Invoke;
  return { invoke, calls };
}

function rejecting(reason: unknown): Invoke {
  return (async () => {
    throw reason;
  }) as Invoke;
}

describe("the workspace commands", () => {
  it("reads the choice through workspace_choice_status", async () => {
    for (const choice of [CHOSEN, { source: "none", path: null, valid: false } as const]) {
      const { invoke, calls } = fakeInvoke(() => choice);
      expect(await createWorkspaces(invoke).status()).toEqual(choice);
      expect(calls).toEqual([{ command: "workspace_choice_status", args: undefined }]);
    }
  });

  it("opens and creates through their commands, answering the new choice", async () => {
    for (const [method, command] of [
      ["open", "open_workspace"],
      ["create", "create_workspace"],
    ] as const) {
      const { invoke, calls } = fakeInvoke(() => CHOSEN);
      expect(await createWorkspaces(invoke)[method]("/Users/me/knowledge")).toEqual(CHOSEN);
      expect(calls).toEqual([{ command, args: { path: "/Users/me/knowledge" } }]);
    }
  });

  it("picks a folder, or nothing when the user cancels", async () => {
    for (const picked of ["/Users/me/new", null]) {
      const { invoke, calls } = fakeInvoke(() => picked);
      expect(await createWorkspaces(invoke).pickFolder()).toBe(picked);
      expect(calls).toEqual([{ command: "pick_workspace_folder", args: undefined }]);
    }
  });

  it("picks files, or nothing when the user cancels", async () => {
    for (const picked of [["/Users/me/a.md", "/Users/me/b.txt"], null]) {
      const { invoke, calls } = fakeInvoke(() => picked);
      expect(await createWorkspaces(invoke).pickFiles()).toEqual(picked);
      expect(calls).toEqual([{ command: "pick_files", args: undefined }]);
    }
  });

  it("refuses a file pick that is not a list of paths", async () => {
    for (const answer of ["/a", 42, [1], [null]]) {
      const error = await createWorkspaces(fakeInvoke(() => answer).invoke)
        .pickFiles()
        .catch((e: unknown) => e);
      expect((error as WorkspaceError).reason).toBe("unknown");
    }
  });

  it("refuses an answer that is not a choice", async () => {
    for (const answer of [null, "x", { source: "elsewhere", path: null, valid: false }, { source: "none", valid: 1 }]) {
      const error = await createWorkspaces(fakeInvoke(() => answer).invoke)
        .status()
        .catch((e: unknown) => e);
      expect(error).toBeInstanceOf(WorkspaceError);
      expect((error as WorkspaceError).reason).toBe("unknown");
    }
    const picked = await createWorkspaces(fakeInvoke(() => 42).invoke)
      .pickFolder()
      .catch((e: unknown) => e);
    expect((picked as WorkspaceError).reason).toBe("unknown");
  });

  it("turns the shell's refusal into a typed error", async () => {
    for (const reason of WORKSPACE_ERROR_REASONS) {
      const workspaces = createWorkspaces(rejecting({ reason, detail: "an English detail" }));
      const error = await workspaces.create("/x").catch((e: unknown) => e);
      expect(error).toBeInstanceOf(WorkspaceError);
      expect((error as WorkspaceError).reason).toBe(reason);
      expect((error as WorkspaceError).message).toBe("an English detail");
    }
  });

  it("answers unknown for a rejection that is not the shell's", async () => {
    for (const thrown of ["no such command", { reason: "other", detail: "x" }, null]) {
      const error = await createWorkspaces(rejecting(thrown))
        .open("/x")
        .catch((e: unknown) => e);
      expect((error as WorkspaceError).reason).toBe("unknown");
    }
  });
});

describe("the shell's side of the contract", () => {
  it("registers every command the wrapper calls", () => {
    const handler = /generate_handler!\[([^\]]*)\]/.exec(LIB)?.[1] ?? "";
    for (const command of ["workspace_choice_status", "open_workspace", "create_workspace", "pick_workspace_folder", "pick_files"]) {
      expect(handler).toMatch(new RegExp(`\\b${command}\\b`));
      expect(LIB).toMatch(new RegExp(`fn ${command}\\(`));
    }
  });

  it("names every error reason the shell can answer", () => {
    const sources = WORKSPACE + LIB;
    const reasons = new Set([...sources.matchAll(/WorkspaceCommandError::new\(\s*"([a-z_]+)"/g)].map((m) => m[1]));
    expect([...reasons].sort()).toEqual([...WORKSPACE_ERROR_REASONS].sort());
  });

  it("knows the no-workspace engine state the supervisor publishes", () => {
    expect(SUPERVISOR).toMatch(/\n\s+NoWorkspace,\n/);
  });
});
