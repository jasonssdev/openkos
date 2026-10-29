// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { Schemas } from "../../client/index.ts";
import type { ActivityState } from "./model.ts";
import { renderActivity } from "./render.ts";

const NOW = Date.parse("2026-10-27T12:00:00Z");
const EMPTY: Schemas["EngineActivity"] = {
  current: null,
  recent: [],
  waiting_files: 0,
  uncommitted_paths: 0,
  budget: { calls_used: 0, calls_limit: 500, exhausted: false },
  jobs_unreadable: false,
};
const JOB: Schemas["EngineJob"] = {
  kind: "watch",
  started_at: "2026-10-27T11:30:00Z",
  ended_at: "2026-10-27T11:31:00Z",
  outcome: "completed",
  chat_calls: 3,
  units_done: 2,
  units_deferred: 0,
  needs_attention: false,
};

function draw(state: ActivityState): HTMLElement {
  const root = document.createElement("div");
  renderActivity(root, state, NOW);
  return root;
}
const text = (root: HTMLElement) => root.textContent ?? "";

describe("the Activity screen", () => {
  it("waits for the engine before anything is read", () => {
    const root = draw({ activity: null, failed: false });
    expect(text(root)).toContain("Activity");
    expect(text(root)).toContain("Waiting for OpenKOS to start…");
  });

  it("says nothing is running or waiting for an engine with no history", () => {
    const root = draw({ activity: EMPTY, failed: false });
    expect(text(root)).toContain("Nothing is running.");
    expect(text(root)).toContain("Nothing is waiting.");
    expect(text(root)).toContain("0 of 500 AI model requests used today.");
    expect(text(root)).toContain("Nothing has run yet.");
    expect(root.querySelector("progress")?.max).toBe(500);
  });

  it("shows the running task, what waits and the recent tasks", () => {
    const running = { ...JOB, kind: "maintenance", ended_at: null, outcome: null };
    const root = draw({
      activity: { ...EMPTY, current: running, waiting_files: 2, uncommitted_paths: 1, recent: [JOB], budget: { calls_used: 40, calls_limit: 100, exhausted: false } },
      failed: false,
    });
    expect(text(root)).toContain("Tidying your library.");
    expect(text(root)).toContain("2 files are waiting in your watched folder.");
    expect(text(root)).toContain("1 change is waiting to be saved.");
    expect(text(root)).toContain("40 of 100 AI model requests used today.");
    expect(text(root)).toContain("Looked at your watched folder");
    expect(text(root)).toContain("Finished. 2 done.");
    expect(text(root)).toContain("30 minutes ago");
    expect(root.querySelector("progress")?.value).toBe(40);
  });

  it("marks a task that needs a look, in words", () => {
    const root = draw({ activity: { ...EMPTY, recent: [{ ...JOB, outcome: "failed", needs_attention: true }] }, failed: false });
    expect(text(root)).toContain("Something went wrong.");
    expect(root.querySelector(".ui-badge")?.textContent).toContain("Needs a look");
  });

  it("says when the daily limit is reached", () => {
    const root = draw({ activity: { ...EMPTY, budget: { calls_used: 500, calls_limit: 500, exhausted: true } }, failed: false });
    expect(text(root)).toContain("The daily limit is reached. Background work resumes tomorrow.");
  });

  it("says plainly when the record cannot be read, instead of showing it as empty", () => {
    const root = draw({ activity: { ...EMPTY, jobs_unreadable: true }, failed: false });
    expect(text(root)).toContain("can't be read");
    expect(text(root)).not.toContain("Nothing is running.");
    expect(root.querySelector("progress")).toBeNull();
  });

  it("keeps what was shown and says the last read failed", () => {
    const root = draw({ activity: EMPTY, failed: true });
    expect(text(root)).toContain("Activity could not be read just now.");
    expect(text(root)).toContain("Nothing is running.");
  });
});
