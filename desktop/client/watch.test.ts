import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { Invoke } from "./shell.ts";
import { WatchError, createWatch, type WatchStatus } from "./watch.ts";
import { WORKSPACE_ERROR_REASONS } from "./workspace.ts";

// The shell's source is the authority on its command names and its states.
const LIB = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");
const WATCH = readFileSync(new URL("../src-tauri/src/watch.rs", import.meta.url), "utf8");

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

const OFF: WatchStatus = { state: "off", wanted: false };

describe("the watch commands", () => {
  it("reads the state through watch_state", async () => {
    const answers: WatchStatus[] = [
      OFF,
      { state: "starting", attempt: 1, wanted: true },
      { state: "on", attempt: 1, pid: 42, wanted: true },
      { state: "restarting", attempt: 2, delay_ms: 500, reason: "exited", detail: "x", wanted: true },
      { state: "failed", reason: "exited", detail: "the daemon exited", wanted: true },
      { state: "no_workspace", wanted: true },
    ];
    for (const answer of answers) {
      const { invoke, calls } = fakeInvoke(() => answer);
      expect(await createWatch(invoke).state()).toEqual(answer);
      expect(calls).toEqual([{ command: "watch_state", args: undefined }]);
    }
  });

  it("turns the watch on and off through set_watch, answering the new state", async () => {
    for (const on of [true, false]) {
      const { invoke, calls } = fakeInvoke(() => ({ state: on ? "starting" : "off", attempt: 1, wanted: on }));
      const state = await createWatch(invoke).set(on);
      expect(state.wanted).toBe(on);
      expect(calls).toEqual([{ command: "set_watch", args: { on } }]);
    }
  });

  it("picks the folder through pick_inbox_folder, null when cancelled", async () => {
    for (const picked of ["/Users/me/Inbox", null]) {
      const { invoke, calls } = fakeInvoke(() => picked);
      expect(await createWatch(invoke).pickFolder()).toBe(picked);
      expect(calls).toEqual([{ command: "pick_inbox_folder", args: undefined }]);
    }
  });

  it("asks the shell to tell the person the watch paused, with no text of its own", async () => {
    const { invoke, calls } = fakeInvoke(() => null);

    await createWatch(invoke).notifyBudgetStop();

    expect(calls).toEqual([{ command: "notify_watch_budget", args: undefined }]);
  });

  it("refuses a state the shell never sends", async () => {
    for (const odd of [null, "on", { state: "sleeping", wanted: true }, { state: "on", wanted: "yes" }, { wanted: true }]) {
      const { invoke } = fakeInvoke(() => odd);
      const error = await createWatch(invoke).state().catch((e: unknown) => e);
      expect(error).toBeInstanceOf(WatchError);
      expect((error as WatchError).reason).toBe("unknown");
    }
    const { invoke } = fakeInvoke(() => 7);
    expect(await createWatch(invoke).pickFolder().catch((e: unknown) => e)).toBeInstanceOf(WatchError);
  });

  it("turns the shell's refusal into a typed error", async () => {
    for (const reason of ["no_workspace", "watch_choice_unkept"] as const) {
      const watch = createWatch(rejecting({ reason, detail: "an English detail" }));
      const error = await watch.set(true).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(WatchError);
      expect((error as WatchError).reason).toBe(reason);
    }
    const unexpected = await createWatch(rejecting("no command")).set(true).catch((e: unknown) => e);
    expect((unexpected as WatchError).reason).toBe("unknown");
  });
});

describe("the shell's side of the contract", () => {
  it("registers every command the wrapper calls", () => {
    const handler = /generate_handler!\[([^\]]*)\]/.exec(LIB)?.[1] ?? "";
    for (const command of ["watch_state", "set_watch", "pick_inbox_folder", "notify_watch_budget"]) {
      expect(handler).toMatch(new RegExp(`\\b${command}\\b`));
      expect(LIB).toMatch(new RegExp(`async fn ${command}\\(|fn ${command}\\(`));
    }
  });

  it("names every state the supervisor can be in", () => {
    const block = /pub enum WatchState \{([\s\S]*?)\n\}/.exec(WATCH)?.[1] ?? "";
    const variants = [...block.matchAll(/^    ([A-Z][A-Za-z]+)/gm)].map((m) =>
      (m[1] ?? "").replace(/([a-z])([A-Z])/g, "$1_$2").toLowerCase(),
    );
    expect(variants.sort()).toEqual(["failed", "no_workspace", "off", "on", "restarting", "starting"]);
  });

  it("reuses the workspace refusal reasons the watch commands answer with", () => {
    expect(WORKSPACE_ERROR_REASONS).toContain("no_workspace");
    expect(WORKSPACE_ERROR_REASONS).toContain("watch_choice_unkept");
  });
});
