import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  ApiError,
  ERROR_CODES,
  EngineUnreachable,
  ProtocolError,
  isBusy,
  isStale,
  isTryAgain,
  isUnmergeRefusal,
} from "./errors.ts";

// The engine's error table is the source of the codes: the envelope's `code`
// is a plain string in the OpenAPI document, so the snapshot cannot carry them.
const ENGINE_ERRORS = new URL("../../src/openkos/localapi/_errors.py", import.meta.url);

function engineCodes(): string[] {
  const source = readFileSync(ENGINE_ERRORS, "utf8");
  return [...source.matchAll(/^[A-Z][A-Z_]*: Final = "([a-z_]+)"$/gm)].map((m) => m[1]!);
}

function apiError(code: string, retryable = false): ApiError {
  return new ApiError({ status: 409, code, retryable, message: "m", retryAfterSeconds: null });
}

describe("the error codes", () => {
  it("reads a plausible number of codes from the engine", () => {
    expect(engineCodes().length).toBeGreaterThan(30);
  });

  it("names every code the engine defines", () => {
    const missing = engineCodes().filter((code) => !(ERROR_CODES as readonly string[]).includes(code));
    expect(missing).toEqual([]);
  });

  it("names no code the engine does not define", () => {
    const engine = new Set(engineCodes());
    expect(ERROR_CODES.filter((code) => !engine.has(code))).toEqual([]);
  });
});

describe("the classifications", () => {
  it("calls only workspace_busy busy", () => {
    expect(isBusy(apiError("workspace_busy", true))).toBe(true);
    expect(isBusy(apiError("write_drift", true))).toBe(false);
    expect(isBusy(new ProtocolError({ status: 503, path: "/v1/status" }))).toBe(false);
    expect(isBusy(new Error("workspace_busy"))).toBe(false);
  });

  it("calls a retryable envelope try-again", () => {
    expect(isTryAgain(apiError("write_drift", true))).toBe(true);
    expect(isTryAgain(apiError("invalid_selection", false))).toBe(false);
    expect(isTryAgain(new EngineUnreachable({ path: "/v1/status" }))).toBe(false);
  });

  it("calls stale and write_drift stale", () => {
    expect(isStale(apiError("stale"))).toBe(true);
    expect(isStale(apiError("write_drift", true))).toBe(true);
    expect(isStale(apiError("quote_stale"))).toBe(false);
  });

  it("calls the three unmerge refusals unmerge refusals", () => {
    for (const code of ["survivor_missing", "not_merged", "survivor_edited"]) {
      expect(isUnmergeRefusal(apiError(code))).toBe(true);
    }
    expect(isUnmergeRefusal(apiError("unmerge_interrupted"))).toBe(false);
    expect(isUnmergeRefusal(apiError("stale"))).toBe(false);
  });
});

describe("the error objects", () => {
  it("carry the envelope", () => {
    const error = new ApiError({
      status: 503,
      code: "workspace_busy",
      retryable: true,
      message: "the workspace is busy; try again",
      retryAfterSeconds: 2,
    });
    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("ApiError");
    expect(error.message).toBe("the workspace is busy; try again");
    expect([error.status, error.code, error.retryable, error.retryAfterSeconds]).toEqual([
      503,
      "workspace_busy",
      true,
      2,
    ]);
  });

  it("keep a code a newer engine sends", () => {
    expect(apiError("some_future_code").code).toBe("some_future_code");
  });

  it("name the protocol failure and the unreachable engine", () => {
    const protocol = new ProtocolError({ status: 502, path: "/v1/status" });
    expect(protocol.name).toBe("ProtocolError");
    expect(protocol.status).toBe(502);
    expect(protocol.message).toContain("502");
    const unreachable = new EngineUnreachable({ path: "/v1/status" });
    expect(unreachable.name).toBe("EngineUnreachable");
    expect(unreachable.message).toBe("the engine could not be reached");
  });
});
