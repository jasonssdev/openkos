import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable, ProtocolError, type Schemas } from "../client/index.ts";
import { describeEngine, statusFacts, statusFailure } from "./view.ts";

const TOKEN = "f".repeat(64);

describe("describeEngine", () => {
  it.each([
    [{ state: "starting", attempt: 1 }, "Starting the engine…", undefined],
    [
      { state: "ready", attempt: 1, base_url: "http://127.0.0.1:1/v1", token: TOKEN, pid: 3 },
      "The engine is running.",
      undefined,
    ],
    [
      { state: "restarting", attempt: 1, delay_ms: 500, reason: "exited", detail: "boom" },
      "The engine stopped unexpectedly. Restarting it (attempt 2)…",
      "boom",
    ],
    [{ state: "failed", reason: "r", detail: "trace" }, "The engine could not be started.", "trace"],
    [{ state: "stopped" }, "The engine has stopped.", undefined],
  ] as const)("words %o", (state, text, detail) => {
    expect(describeEngine(state)).toEqual(detail === undefined ? { text } : { text, detail });
  });

  it("words a state it does not know", () => {
    expect(describeEngine({ state: "rebooting" } as never)).toEqual({ text: "The engine's state is unknown." });
  });

  it("never shows the token", () => {
    const ready = { state: "ready", attempt: 1, base_url: "http://127.0.0.1:1/v1", token: TOKEN, pid: 3 } as const;
    expect(JSON.stringify(describeEngine(ready))).not.toContain(TOKEN);
  });
});

describe("statusFacts", () => {
  it("lists the folder, the counts and the open decisions summed", () => {
    const status = {
      root: "/Users/me/knowledge",
      sources: 4,
      concepts: 21,
      open_decisions_by_kind: { identity: 2, relation_type: 3 },
    } as unknown as Schemas["WorkspaceStatus"];
    expect(statusFacts(status)).toEqual([
      ["Folder", "/Users/me/knowledge"],
      ["Sources", "4"],
      ["Concepts", "21"],
      ["Decisions waiting for you", "5"],
    ]);
  });

  it("counts no open decisions as zero", () => {
    const status = { root: "/w", sources: 0, concepts: 0, open_decisions_by_kind: {} } as unknown as Schemas["WorkspaceStatus"];
    expect(statusFacts(status).at(-1)).toEqual(["Decisions waiting for you", "0"]);
  });
});

describe("statusFailure", () => {
  it("words an API error with the engine's message", () => {
    const error = new ApiError({
      status: 500,
      code: "workspace_unreadable",
      retryable: false,
      message: "the workspace is unreadable",
      retryAfterSeconds: null,
    });
    expect(statusFailure(error)).toBe("The workspace status could not be read: the workspace is unreadable.");
  });

  it("words an answer the app cannot read", () => {
    expect(statusFailure(new ProtocolError({ status: 502, path: "/v1/status" }))).toBe(
      "The workspace status could not be read: the engine answered in a form this app cannot read.",
    );
  });

  it("words an unreachable engine", () => {
    expect(statusFailure(new EngineUnreachable({ path: "/v1/status" }))).toBe(
      "The workspace status could not be read: the engine could not be reached.",
    );
  });

  it("shows nothing of an error it does not know", () => {
    expect(statusFailure(new Error(`token ${TOKEN}`))).toBe("The workspace status could not be read.");
  });
});
