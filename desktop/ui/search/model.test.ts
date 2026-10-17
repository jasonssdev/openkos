import { describe, expect, it, vi } from "vitest";
import { ApiError, type Schemas } from "../../client/index.ts";
import { SearchModel, type SearchClient } from "./model.ts";
import { cloudWarning, doneLine, SEARCH_WORDS } from "./wording.ts";

type Index = Schemas["SearchIndex"];
type Status = Schemas["RebuildStatus"];

const OUTCOME: Schemas["RebuildOutcome"] = { embedded: 2, unchanged: 0, removed: 0, skipped: 0, failed: 0, withheld: 0, complete: true };
const NEEDS: Index = { model_changed: true, stale_indexes: [], needs_rebuild: true, sends_to: null };
const CURRENT: Index = { model_changed: false, stale_indexes: [], needs_rebuild: false, sends_to: null };

const status = (over: Partial<Status> = {}): Status => ({
  rebuild_id: "r1",
  state: "running",
  phase: null,
  done: 0,
  total: 0,
  outcome: null,
  error: null,
  ...over,
});

function setup(over: { index?: Index; polls?: Status[]; answer?: boolean; sleep?: () => Promise<void> } = {}) {
  const polls = [...(over.polls ?? [status({ phase: "embedding", done: 1, total: 2 }), status({ state: "finished", outcome: OUTCOME })])];
  let index = over.index ?? NEEDS;
  const client = {
    searchIndex: vi.fn(async () => index),
    startSearchRebuild: vi.fn(async (_body: { allow_remote: boolean }) => status()),
    searchRebuild: vi.fn(async () => {
      const next = polls.shift() ?? status({ state: "finished", outcome: OUTCOME });
      if (next.state === "finished") index = CURRENT;
      return next;
    }),
    stopSearchRebuild: vi.fn(async () => status({ state: "stopping" })),
  };
  const confirm = vi.fn(async () => over.answer ?? true);
  const model = new SearchModel({ confirm, sleep: over.sleep ?? (async () => {}) });
  return { model, client, confirm, connect: () => model.setClient(client as unknown as SearchClient) };
}

describe("the search model", () => {
  it("reads whether search needs rebuilding when it gets an engine", async () => {
    const { model, connect } = setup();
    await connect();
    expect(model.state.index).toEqual(NEEDS);
  });

  it("rebuilds with progress, then reads the state again", async () => {
    const { model, client, connect, confirm } = setup();
    await connect();
    const seen: string[] = [];
    model.subscribe((s) => seen.push(s.job ? `${s.job.state}:${s.job.done}/${s.job.total}` : "none"));
    await model.rebuild();
    expect(client.startSearchRebuild).toHaveBeenCalledWith({ allow_remote: false });
    expect(confirm).not.toHaveBeenCalled();
    expect(seen).toContain("running:1/2");
    expect(model.state.job).toBeNull();
    expect(model.state.result).toEqual({ kind: "done", text: doneLine(OUTCOME) });
    expect(model.state.index).toEqual(CURRENT);
  });

  it("asks before sending to a cloud AI model for search, and sends nothing on Not now", async () => {
    const cloud = { ...NEEDS, sends_to: "api.example.com" };
    const declined = setup({ index: cloud, answer: false });
    await declined.connect();
    await declined.model.rebuild();
    expect(declined.confirm).toHaveBeenCalledWith(cloudWarning("api.example.com"));
    expect(declined.client.startSearchRebuild).not.toHaveBeenCalled();

    const agreed = setup({ index: cloud, answer: true });
    await agreed.connect();
    await agreed.model.rebuild();
    expect(agreed.client.startSearchRebuild).toHaveBeenCalledWith({ allow_remote: true });
  });

  it("stops after the page in flight and says search wasn't changed", async () => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { model, client, connect } = setup({
      polls: [status({ state: "running" }), status({ state: "stopped" })],
      sleep: () => gate,
    });
    await connect();
    const running = model.rebuild();
    await vi.waitFor(() => expect(model.state.job).not.toBeNull());
    await model.stop();
    expect(model.state.job?.state).toBe("stopping");
    release();
    await running;
    expect(client.stopSearchRebuild).toHaveBeenCalledWith("r1");
    expect(model.state.result).toEqual({ kind: "stopped", text: SEARCH_WORDS.stopped });
  });

  it("words a refused start and a failed run", async () => {
    const refused = setup();
    await refused.connect();
    refused.client.startSearchRebuild.mockRejectedValueOnce(
      new ApiError({ status: 409, code: "rebuild_active", retryable: true, message: "", retryAfterSeconds: null }),
    );
    await refused.model.rebuild();
    expect(refused.model.state.result).toEqual({ kind: "problem", text: "Search is already being rebuilt." });

    const failed = setup({
      polls: [status({ state: "failed", error: { code: "backend_unavailable", retryable: true, message: "" } })],
    });
    await failed.connect();
    await failed.model.rebuild();
    expect(failed.model.state.result?.kind).toBe("problem");
    expect(failed.model.state.result?.text).toContain("The AI model isn't running.");
    expect(failed.model.state.job).toBeNull();
  });

  it("does nothing with no engine, and one rebuild at a time", async () => {
    const { model, client, connect } = setup();
    await model.rebuild();
    expect(client.startSearchRebuild).not.toHaveBeenCalled();
    await connect();
    const first = model.rebuild();
    await model.rebuild();
    await first;
    expect(client.startSearchRebuild).toHaveBeenCalledTimes(1);
  });

  it("forgets the state of an engine that went away", async () => {
    const { model, connect } = setup();
    await connect();
    await model.setClient(null);
    expect(model.state.index).toBeNull();
  });
});
