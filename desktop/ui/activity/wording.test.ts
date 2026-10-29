import { describe, expect, it } from "vitest";
import type { Schemas } from "../../client/index.ts";
import { ACTIVITY_WORDS, budgetLine, currentLine, jobTitle, outcomeLine, queueLines, since } from "./wording.ts";

type Job = Schemas["EngineJob"];
const job = (over: Partial<Job> = {}): Job => ({
  kind: "watch",
  started_at: "2026-10-27T10:00:00Z",
  ended_at: "2026-10-27T10:01:00Z",
  outcome: "completed",
  chat_calls: 4,
  units_done: 2,
  units_deferred: 0,
  needs_attention: false,
  ...over,
});
const NOW = Date.parse("2026-10-27T12:00:00Z");

describe("activity wording", () => {
  it("names each kind of task in plain words", () => {
    expect(jobTitle("watch")).toBe("Looked at your watched folder");
    expect(jobTitle("maintenance")).toBe("Tidied your library");
    expect(jobTitle("commit-retry")).toBe("Tried again to save changes");
    expect(jobTitle("something-new")).toBe("Background work");
  });

  it("says what is running now, and when nothing is", () => {
    expect(currentLine(null)).toBe("Nothing is running.");
    expect(currentLine(job({ kind: "maintenance", ended_at: null, outcome: null }))).toBe("Tidying your library.");
    expect(currentLine(job({ kind: "watch", ended_at: null, outcome: null }))).toBe("Looking at your watched folder.");
    expect(currentLine(job({ kind: "commit-retry", ended_at: null, outcome: null }))).toBe("Trying again to save changes.");
  });

  it("says how a task ended, with what it did and what it left", () => {
    expect(outcomeLine(job())).toBe("Finished. 2 done.");
    expect(outcomeLine(job({ units_done: 0 }))).toBe("Finished. Nothing to do.");
    expect(outcomeLine(job({ outcome: "budget_exhausted", units_deferred: 3 }))).toBe(
      "Stopped at the daily limit. 2 done, 3 left for later.",
    );
    expect(outcomeLine(job({ outcome: "failed", units_done: 1, units_deferred: 1 }))).toBe("Something went wrong. 1 done, 1 left for later.");
    expect(outcomeLine(job({ outcome: "timed_out", units_done: 0 }))).toBe("Ran out of time.");
    expect(outcomeLine(job({ outcome: "stopped", units_done: 0 }))).toBe("Stopped.");
    expect(outcomeLine(job({ outcome: "busy", units_done: 0 }))).toBe("Skipped, the library was busy.");
    expect(outcomeLine(job({ outcome: "commit_failed", units_done: 0 }))).toBe("Could not save its changes.");
    expect(outcomeLine(job({ outcome: "refused", units_done: 0 }))).toBe("Did not run.");
    expect(outcomeLine(job({ outcome: null, units_done: 0 }))).toBe("Did not finish.");
  });

  it("says how long ago, from the clock it is given", () => {
    expect(since("2026-10-27T11:59:50Z", NOW)).toBe("just now");
    expect(since("2026-10-27T11:59:00Z", NOW)).toBe("1 minute ago");
    expect(since("2026-10-27T11:30:00Z", NOW)).toBe("30 minutes ago");
    expect(since("2026-10-27T11:00:00Z", NOW)).toBe("1 hour ago");
    expect(since("2026-10-27T07:00:00Z", NOW)).toBe("5 hours ago");
    expect(since("2026-10-25T12:00:00Z", NOW)).toBe("2 days ago");
    expect(since("not a time", NOW)).toBe("");
  });

  it("says what is used of today's budget", () => {
    expect(budgetLine({ calls_used: 12, calls_limit: 500, exhausted: false })).toBe("12 of 500 AI model requests used today.");
    expect(budgetLine({ calls_used: 1, calls_limit: 1, exhausted: true })).toBe("1 of 1 AI model request used today.");
  });

  it("lists what waits, and says so when nothing does", () => {
    expect(queueLines(0, 0)).toEqual(["Nothing is waiting."]);
    expect(queueLines(1, 0)).toEqual(["1 file is waiting in your watched folder."]);
    expect(queueLines(3, 2)).toEqual(["3 files are waiting in your watched folder.", "2 changes are waiting to be saved."]);
    expect(queueLines(0, 1)).toEqual(["1 change is waiting to be saved."]);
  });

  it("has fixed words for the unavailable and limit states", () => {
    expect(ACTIVITY_WORDS.unreadable).toContain("can't be read");
    expect(ACTIVITY_WORDS.limitReached).toContain("daily limit");
  });
});
