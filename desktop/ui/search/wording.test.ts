import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";
import { ENGINE_WORDS } from "../review/wording.ts";
import { SEARCH_WORDS, cloudWarning, costLine, doneLine, needLine, problemFor, progressLine } from "./wording.ts";

type Outcome = Schemas["RebuildOutcome"];
const DONE: Outcome = { embedded: 3, unchanged: 1, removed: 0, skipped: 0, failed: 0, withheld: 0, complete: true };
const index = (over: Partial<Schemas["SearchIndex"]> = {}): Schemas["SearchIndex"] => ({
  model_changed: false,
  stale_indexes: [],
  needs_rebuild: false,
  sends_to: null,
  ...over,
});
const apiError = (code: string) =>
  new ApiError({ status: 503, code, retryable: false, message: "engine text: reindex vectors", retryAfterSeconds: null });

describe("the search wording", () => {
  it("says why search needs rebuilding, the new AI model first", () => {
    expect(needLine(index())).toBeNull();
    expect(needLine(index({ model_changed: true, needs_rebuild: true }))).toBe(
      "Search needs to be rebuilt for the new AI model. Until it is, answers may miss some pages.",
    );
    expect(needLine(index({ stale_indexes: ["fts"], needs_rebuild: true }))).toBe(
      "Search is behind your latest changes, so answers may miss them. Rebuilding brings it up to date.",
    );
  });

  it("shows where it is while it runs", () => {
    expect(progressLine(null, 0, 0)).toBe("Getting ready…");
    expect(progressLine("embedding", 2, 5)).toBe("Reading 2 of 5 pages…");
    expect(progressLine("embedding", 1, 1)).toBe("Reading 1 of 1 page…");
    expect(progressLine("finishing", 5, 5)).toBe("Finishing…");
  });

  it("warns before sending to a cloud AI model, with the cloud warning's send rule", () => {
    const warning = cloudWarning("api.example.com");
    expect(warning.title).toBe("Rebuild search with api.example.com?");
    expect(warning.lines).toEqual([
      "Pages marked Shareable or Private are sent.",
      "Pages marked Only on this Mac are never sent.",
      "api.example.com may charge you for this.",
    ]);
    expect([warning.confirm, warning.cancel]).toEqual(["Rebuild search", "Not now"]);
  });

  it("says what a finished rebuild did", () => {
    expect(doneLine(DONE)).toBe("Search is up to date.");
    expect(doneLine({ ...DONE, withheld: 2, complete: false })).toBe(
      "Search was rebuilt. 2 pages marked Only on this Mac weren't sent, so search finds them only by their words.",
    );
    expect(doneLine({ ...DONE, failed: 1, skipped: 1, complete: false })).toBe(
      "Search was rebuilt. The AI model couldn't read 1 page. Rebuilding again tries it again. 1 page couldn't be opened.",
    );
  });

  it("words each failure and says search wasn't changed", () => {
    expect(problemFor(apiError("backend_unavailable"))).toBe(
      "The AI model isn't running. OpenKOS needs it to rebuild search. Search wasn't changed.",
    );
    expect(problemFor(apiError("something_new"))).toBe("Something went wrong. Search wasn't changed.");
    expect(problemFor(new EngineUnreachable({ path: "/v1/search" }))).toBe("OpenKOS isn't responding.");
  });

  it("uses no engine words", () => {
    const all = [
      ...Object.values(SEARCH_WORDS),
      needLine(index({ model_changed: true })),
      needLine(index({ needs_rebuild: true })),
      progressLine("embedding", 1, 2),
      costLine("h"),
      ...Object.values(cloudWarning("h")).flat(),
      doneLine({ ...DONE, withheld: 1, failed: 1, skipped: 1, complete: false }),
      ...["rebuild_active", "backend_unavailable", "search_unavailable", "not_found", "x"].map((c) => problemFor(apiError(c))),
    ].join(" ").toLowerCase();
    for (const word of [...ENGINE_WORDS, "embed", "embedding", "reindex", "vector", "vectors", "index"]) {
      expect(all).not.toMatch(new RegExp(`\\b${word}\\b`));
    }
  });
});

describe("docs/decision-wording.md", () => {
  const brief = readFileSync(new URL("../../../docs/decision-wording.md", import.meta.url), "utf8");
  const section = brief.slice(brief.indexOf("## Rebuilding search"), brief.indexOf("## Open questions"));

  it("states every sentence word for word", () => {
    const warning = cloudWarning("{host}");
    for (const line of [
      SEARCH_WORDS.local,
      SEARCH_WORDS.stopped,
      needLine(index({ model_changed: true })),
      needLine(index({ needs_rebuild: true })),
      costLine("{host}"),
      warning.title,
      warning.description,
      ...warning.lines,
      "Search is up to date.",
    ]) {
      expect(section).toContain(line);
    }
    for (const code of ["rebuild_active", "backend_unavailable", "backend_model_not_found", "workspace_busy", "search_unavailable", "not_found"]) {
      expect(section).toContain(problemFor(apiError(code)));
    }
  });
});
