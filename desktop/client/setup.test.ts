import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { BrowserNotOpened, openRuntimeDownload } from "./setup.ts";
import type { Invoke } from "./shell.ts";

const LIB = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");

function recording(answer: () => unknown): { invoke: Invoke; calls: { command: string; args: unknown }[] } {
  const calls: { command: string; args: unknown }[] = [];
  const invoke = (async (command: string, args?: Record<string, unknown>) => {
    calls.push({ command, args });
    return answer();
  }) as Invoke;
  return { invoke, calls };
}

describe("openRuntimeDownload", () => {
  it("asks the shell to open its fixed address, naming none itself", async () => {
    const { invoke, calls } = recording(() => null);
    await openRuntimeDownload(invoke);
    expect(calls).toEqual([{ command: "open_runtime_download", args: undefined }]);
  });

  it("turns any refusal into one fixed error, never the shell's text", async () => {
    const { invoke } = recording(() => {
      throw "the browser could not be opened: /secret/path";
    });
    const error = await openRuntimeDownload(invoke).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(BrowserNotOpened);
    expect((error as Error).message).not.toContain("/secret/path");
  });

  it("is a command the shell registers", () => {
    const handler = /generate_handler!\[([^\]]*)\]/.exec(LIB)?.[1] ?? "";
    expect(handler).toMatch(/\bopen_runtime_download\b/);
    expect(LIB).toMatch(/fn open_runtime_download\(\)/);
  });
});
