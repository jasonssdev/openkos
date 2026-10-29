// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { Schemas } from "../../client/index.ts";
import type { SearchState } from "./model.ts";
import { rebuildSection } from "./render.ts";

const NEEDS: Schemas["SearchIndex"] = { model_changed: true, stale_indexes: [], needs_rebuild: true, sends_to: null };
const state = (over: Partial<SearchState> = {}): SearchState => ({ index: NEEDS, job: null, starting: false, result: null, ...over });
const text = (node: HTMLElement | null) => node?.textContent ?? "";
const actions = () => ({ rebuild: vi.fn(), stop: vi.fn(), dismiss: vi.fn() });

describe("the rebuild section", () => {
  it("is absent while search is current and nothing ran", () => {
    expect(rebuildSection(state({ index: { ...NEEDS, model_changed: false, needs_rebuild: false } }), actions())).toBeNull();
    expect(rebuildSection(state({ index: null }), actions())).toBeNull();
  });

  it("says why, what it costs, and offers Rebuild search", () => {
    const a = actions();
    const node = rebuildSection(state(), a)!;
    expect(text(node)).toContain("Search needs to be rebuilt for the new AI model.");
    expect(text(node)).toContain("Nothing leaves this Mac.");
    const button = Array.from(node.querySelectorAll("button")).find((b) => text(b) === "Rebuild search")!;
    button.click();
    expect(a.rebuild).toHaveBeenCalled();
    const cloud = rebuildSection(state({ index: { ...NEEDS, sends_to: "api.example.com" } }), a)!;
    expect(text(cloud)).toContain("You'll see what is sent to api.example.com before it starts.");
  });

  it("shows progress in words and a bar, with Stop", () => {
    const a = actions();
    const job: Schemas["RebuildStatus"] = { rebuild_id: "r", state: "running", phase: "embedding", done: 2, total: 4, outcome: null, error: null };
    const node = rebuildSection(state({ job }), a)!;
    expect(text(node)).toContain("Reading 2 of 4 pages…");
    expect(node.querySelector("progress")?.value).toBe(2);
    Array.from(node.querySelectorAll("button")).find((b) => text(b) === "Stop")!.click();
    expect(a.stop).toHaveBeenCalled();
    const stopping = rebuildSection(state({ job: { ...job, state: "stopping" } }), a)!;
    expect(text(stopping)).toContain("Stopping…");
  });

  it("says how it ended, and offers it again only when still needed", () => {
    const done = rebuildSection(
      state({ index: { ...NEEDS, model_changed: false, needs_rebuild: false }, result: { kind: "done", text: "Search is up to date." } }),
      actions(),
    )!;
    expect(text(done)).toContain("Search is up to date.");
    expect(Array.from(done.querySelectorAll("button")).map(text)).toEqual(["Dismiss"]);
    const failed = rebuildSection(state({ result: { kind: "problem", text: "The AI model isn't running." } }), actions())!;
    expect(text(failed)).toContain("The AI model isn't running.");
    expect(Array.from(failed.querySelectorAll("button")).map(text)).toEqual(["Rebuild search", "Dismiss"]);
  });

  it("closes a finished notice with Dismiss", () => {
    const a = { ...actions(), dismiss: vi.fn() };
    const done = rebuildSection(
      state({ index: { ...NEEDS, model_changed: false, needs_rebuild: false }, result: { kind: "done", text: "Search is up to date." } }),
      a,
    )!;
    expect(Array.from(done.querySelectorAll("button")).map(text)).toEqual(["Dismiss"]);
    done.querySelector("button")!.click();
    expect(a.dismiss).toHaveBeenCalled();
  });

  it("leaves how a rebuild ended to the screens that started it", () => {
    const done = state({ index: { ...NEEDS, model_changed: false, needs_rebuild: false }, result: { kind: "done", text: "Search is up to date." } });
    expect(rebuildSection(done, actions(), { showResult: false })).toBeNull();
    const behind = rebuildSection(state({ result: { kind: "done", text: "Search was rebuilt." } }), actions(), { showResult: false })!;
    expect(text(behind)).toContain("Search needs to be rebuilt");
    expect(text(behind)).not.toContain("Search was rebuilt.");
  });
});
