import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable, ProtocolError, type Schemas } from "../client/index.ts";
import {
  describeEngine,
  engineBadge,
  navEntries,
  openDecisions,
  reviewCount,
  statusFacts,
  statusFailure,
  workspaceSummary,
} from "./view.ts";

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

const STATUS = {
  root: "/Users/me/knowledge",
  sources: 4,
  concepts: 21,
  open_decisions_by_kind: { identity: 2, relation_type: 3 },
} as unknown as Schemas["WorkspaceStatus"];

describe("engineBadge", () => {
  it.each([
    [{ state: "starting", attempt: 1 }, ["info", "Engine starting"]],
    [{ state: "ready", attempt: 1, base_url: "http://127.0.0.1:1/v1", token: TOKEN, pid: 3 }, ["success", "Engine running"]],
    [{ state: "restarting", attempt: 1, delay_ms: 500, reason: "exited", detail: "boom" }, ["warning", "Engine restarting"]],
    [{ state: "failed", reason: "r", detail: "trace" }, ["error", "Engine not running"]],
    [{ state: "stopped" }, ["error", "Engine stopped"]],
  ] as const)("gives %o a tone and a word", (state, expected) => {
    expect(engineBadge(state)).toEqual(expected);
  });

  it("words a state it does not know, or none at all, as unknown", () => {
    expect(engineBadge({ state: "rebooting" } as never)).toEqual(["neutral", "Engine state unknown"]);
    expect(engineBadge(null)).toEqual(["error", "Engine state unknown"]);
  });
});

describe("navEntries", () => {
  it("offers Ask as coming soon, To review with its count, then Status", () => {
    expect(navEntries(3)).toEqual([
      { id: "ask", label: "Ask", disabled: true, hint: "Coming soon" },
      { id: "review", label: "To review", count: 3 },
      { id: "status", label: "Status" },
    ]);
  });

  it("shows no count when none is known", () => {
    expect(navEntries(undefined)[1]).toEqual({ id: "review", label: "To review" });
  });
});

describe("openDecisions", () => {
  it("sums the open decisions of every kind", () => {
    expect(openDecisions(STATUS)).toBe(5);
  });
});

describe("reviewCount", () => {
  it("counts what To review lists once its list is read", () => {
    expect(reviewCount({ loaded: true, decisions: [{}, {}] }, 5)).toBe(2);
  });

  it("falls back to what the status counted before the list is read", () => {
    expect(reviewCount({ loaded: false, decisions: [] }, 5)).toBe(5);
    expect(reviewCount(null, 5)).toBe(5);
  });

  it("is unknown when nothing has been read", () => {
    expect(reviewCount(null, undefined)).toBeUndefined();
  });
});

describe("workspaceSummary", () => {
  it("shows the counts and keeps the folder for the details", () => {
    expect(workspaceSummary(STATUS)).toEqual({
      shown: [
        ["Decisions waiting for you", "5"],
        ["Concepts", "21"],
        ["Sources", "4"],
      ],
      more: [["Folder", "/Users/me/knowledge", "code"]],
    });
  });
});
