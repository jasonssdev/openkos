import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { API_KEY_ERROR_REASONS, ApiKeyError, createApiKeys, type Invoke } from "./shell.ts";

const SECRET = "sk-test-0123456789";

// The shell's source is the authority on its command names and error reasons.
const LIB = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");
const APIKEY = readFileSync(new URL("../src-tauri/src/apikey.rs", import.meta.url), "utf8");

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

describe("the API key commands", () => {
  it("sets the key through set_api_key", async () => {
    const { invoke, calls } = fakeInvoke(() => null);
    await createApiKeys(invoke).set(SECRET);
    expect(calls).toEqual([{ command: "set_api_key", args: { key: SECRET } }]);
  });

  it("clears the key through clear_api_key", async () => {
    const { invoke, calls } = fakeInvoke(() => null);
    await createApiKeys(invoke).clear();
    expect(calls).toEqual([{ command: "clear_api_key", args: undefined }]);
  });

  it("asks whether a key is set, and only that", async () => {
    for (const answer of [true, false]) {
      const { invoke, calls } = fakeInvoke(() => answer);
      expect(await createApiKeys(invoke).isSet()).toBe(answer);
      expect(calls).toEqual([{ command: "api_key_status", args: undefined }]);
    }
  });

  it("offers no way to read the key back", () => {
    const { invoke } = fakeInvoke(() => null);
    expect(Object.keys(createApiKeys(invoke)).sort()).toEqual(["clear", "isSet", "set"]);
  });

  it("refuses a status that is not a boolean", async () => {
    const { invoke } = fakeInvoke(() => SECRET);
    const error = await createApiKeys(invoke).isSet().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiKeyError);
    expect((error as ApiKeyError).reason).toBe("unknown");
    expect(String(error)).not.toContain(SECRET);
  });

  it("turns the shell's refusal into a typed error", async () => {
    for (const reason of API_KEY_ERROR_REASONS) {
      const keys = createApiKeys(rejecting({ reason, detail: "an English detail" }));
      const error = await keys.set(SECRET).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ApiKeyError);
      expect((error as ApiKeyError).reason).toBe(reason);
      expect((error as ApiKeyError).message).toBe("an English detail");
    }
  });

  it("never echoes an unexpected rejection, which could carry the key", async () => {
    for (const thrown of [`no command set_api_key ${SECRET}`, { reason: "other", detail: SECRET }, null]) {
      const error = await createApiKeys(rejecting(thrown)).set(SECRET).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ApiKeyError);
      expect((error as ApiKeyError).reason).toBe("unknown");
      expect(String(error)).not.toContain(SECRET);
    }
  });
});

describe("the shell's side of the contract", () => {
  it("registers every command the wrapper calls", () => {
    const handler = /generate_handler!\[([^\]]*)\]/.exec(LIB)?.[1] ?? "";
    for (const command of ["set_api_key", "clear_api_key", "api_key_status"]) {
      expect(handler).toMatch(new RegExp(`\\b${command}\\b`));
      expect(LIB).toMatch(new RegExp(`async fn ${command}\\(`));
    }
  });

  it("names every error reason the shell can answer", () => {
    const reasons = new Set([...APIKEY.matchAll(/reason: "([a-z_]+)"|=> "([a-z_]+)",/g)].map((m) => m[1] ?? m[2]));
    expect([...reasons].sort()).toEqual([...API_KEY_ERROR_REASONS].sort());
  });
});
