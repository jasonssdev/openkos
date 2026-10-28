import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { Invoke } from "./shell.ts";
import { UPDATE_ERROR_REASONS, UpdateError, createUpdates, type UpdateCheck } from "./update.ts";

// The shell's source is the authority on its command names, states and reasons.
const LIB = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");
const UPDATE = readFileSync(new URL("../src-tauri/src/update.rs", import.meta.url), "utf8");

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

const AVAILABLE: UpdateCheck = { state: "available", version: "0.7.0", current_version: "0.6.0", notes: "Notes." };

describe("the update commands", () => {
  it("reads and sets whether the app checks, through update_settings and set_update_check", async () => {
    const read = fakeInvoke(() => ({ check: true }));
    expect(await createUpdates(read.invoke).settings()).toEqual({ check: true });
    expect(read.calls).toEqual([{ command: "update_settings", args: undefined }]);
    for (const on of [true, false]) {
      const set = fakeInvoke(() => ({ check: on }));
      expect(await createUpdates(set.invoke).setCheck(on)).toEqual({ check: on });
      expect(set.calls).toEqual([{ command: "set_update_check", args: { on } }]);
    }
  });

  it("checks through check_for_update and answers what the shell found", async () => {
    const answers: UpdateCheck[] = [{ state: "off" }, { state: "up_to_date" }, AVAILABLE, { state: "failed" }];
    for (const answer of answers) {
      const { invoke, calls } = fakeInvoke(() => answer);
      expect(await createUpdates(invoke).check()).toEqual(answer);
      expect(calls).toEqual([{ command: "check_for_update", args: undefined }]);
    }
  });

  it("installs by the version the person was shown, through install_update", async () => {
    const { invoke, calls } = fakeInvoke(() => null);
    await createUpdates(invoke).install("0.7.0");
    expect(calls).toEqual([{ command: "install_update", args: { version: "0.7.0" } }]);
  });

  it("refuses an answer the shell never sends", async () => {
    const odd = [null, "available", { state: "soon" }, { state: "available", version: 7, current_version: "0.6.0", notes: null }];
    for (const answer of odd) {
      const { invoke } = fakeInvoke(() => answer);
      const error = await createUpdates(invoke).check().catch((e: unknown) => e);
      expect(error).toBeInstanceOf(UpdateError);
      expect((error as UpdateError).reason).toBe("unknown");
    }
    const { invoke } = fakeInvoke(() => ({ check: "yes" }));
    expect(await createUpdates(invoke).settings().catch((e: unknown) => e)).toBeInstanceOf(UpdateError);
  });

  it("turns the shell's refusal into a typed error, and anything else into unknown", async () => {
    for (const reason of UPDATE_ERROR_REASONS) {
      const error = await createUpdates(rejecting({ reason, detail: "an English detail" }))
        .install("0.7.0")
        .catch((e: unknown) => e);
      expect(error).toBeInstanceOf(UpdateError);
      expect((error as UpdateError).reason).toBe(reason);
    }
    const unexpected = await createUpdates(rejecting("no command")).install("0.7.0").catch((e: unknown) => e);
    expect((unexpected as UpdateError).reason).toBe("unknown");
  });
});

describe("the shell's side of the contract", () => {
  it("registers every command the wrapper calls", () => {
    const handler = /generate_handler!\[([^\]]*)\]/.exec(LIB)?.[1] ?? "";
    for (const command of ["update_settings", "set_update_check", "check_for_update", "install_update"]) {
      expect(handler).toMatch(new RegExp(`\\b${command}\\b`));
      expect(LIB).toMatch(new RegExp(`async fn ${command}\\(|fn ${command}\\(`));
    }
  });

  it("names every state a check can end in", () => {
    const block = /pub enum UpdateCheck \{([\s\S]*?)\n\}/.exec(UPDATE)?.[1] ?? "";
    const variants = [...block.matchAll(/^    ([A-Z][A-Za-z]+)/gm)].map((m) =>
      (m[1] ?? "").replace(/([a-z])([A-Z])/g, "$1_$2").toLowerCase(),
    );
    expect(variants.sort()).toEqual(["available", "failed", "off", "up_to_date"]);
  });

  it("knows every reason the shell refuses with", () => {
    const shell = new Set([...`${LIB}\n${UPDATE}`.matchAll(/UpdateCommandError::new\(\s*"([a-z_]+)"/g)].map((m) => m[1]));
    expect([...shell].sort()).toEqual([...UPDATE_ERROR_REASONS].sort());
  });
});
