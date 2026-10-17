import { describe, expect, it, vi } from "vitest";
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";
import { AskModel, HISTORY_KEPT, POLL_MS, type AskClient, type AskState } from "./model.ts";

type Job = Schemas["AskJob"];
type Answer = Schemas["Answer"];

const ANSWER: Answer = {
  question: "Who runs the allotment?",
  outcome: "answered",
  answer: "Maria.",
  no_match_cause: "none",
  citations: [{ concept_id: "people/maria", title: "Maria", confidential: false, excerpted: false, history: null }],
  omitted: [],
  attribution: "reported",
  withheld: 0,
  sufficiency_checked: true,
  sufficiency_degraded: false,
  lexical_unavailable: false,
  dense_unavailable: false,
  stale_indexes: [],
};

function job(patch: Partial<Job> = {}): Job {
  return { ask_id: "a1", state: "running", phase: null, completed: null, total: null, answer: null, error: null, ...patch };
}

const SETTINGS = {
  backend: "ollama",
  base_url: null,
  embedding_base_url: null,
  model: "qwen3:8b",
  embedding_model: "bge-m3",
  remote_hosts: [],
  is_local: true,
  takes_api_key: false,
} satisfies Schemas["ModelSettings"];

const WITHHELD: Answer = { ...ANSWER, outcome: "withheld", answer: null, citations: [], withheld: 2 };

function fakeClient(start: Job | Error, polls: (Job | Error)[] = [], settings: Schemas["ModelSettings"] | Error = SETTINGS) {
  const queue = [...polls];
  const client = {
    ask: vi.fn(async () => {
      if (start instanceof Error) throw start;
      return start;
    }),
    modelSettings: vi.fn(async () => {
      if (settings instanceof Error) throw settings;
      return settings;
    }),
    askJob: vi.fn(async () => {
      const next = queue.shift();
      if (next === undefined) throw new Error("polled too often");
      if (next instanceof Error) throw next;
      return next;
    }),
  };
  return client as typeof client & AskClient;
}

function model(client: AskClient | null) {
  const sleep = vi.fn(async (_ms: number) => {});
  const ask = new AskModel({ sleep });
  ask.setClient(client);
  const seen: AskState[] = [];
  ask.subscribe((state) => seen.push(state));
  return { ask, sleep, seen };
}

describe("the ask model", () => {
  it("is not ready without an engine, and asks nothing", async () => {
    const { ask } = model(null);
    expect(ask.state.ready).toBe(false);
    await ask.ask("Who?");
    expect(ask.state.turns).toEqual([]);
  });

  it("asks without consent to name private pages, polls until the answer, and shows each phase", async () => {
    const client = fakeClient(job(), [
      job({ phase: "retrieving" }),
      job({ phase: "synthesizing" }),
      job({ state: "finished", phase: "synthesizing", answer: ANSWER }),
    ]);
    const { ask, sleep, seen } = model(client);
    await ask.ask("  Who runs the allotment?  ");
    expect(client.ask).toHaveBeenCalledWith({ question: "Who runs the allotment?" });
    expect(client.askJob).toHaveBeenCalledWith("a1");
    expect(sleep).toHaveBeenCalledWith(POLL_MS);
    expect(sleep).toHaveBeenCalledTimes(3);
    const phases = seen.map((s) => s.turns[0]?.result).filter((r) => r?.kind === "running");
    expect(phases.map((r) => r?.kind === "running" && r.phase)).toEqual([null, null, "retrieving", "synthesizing"]);
    expect(ask.state.turns).toHaveLength(1);
    expect(ask.state.shown).toBe(ask.state.turns[0]?.id);
    expect(ask.state.turns[0]).toMatchObject({ question: "Who runs the allotment?", result: { kind: "answer", answer: ANSWER } });
  });

  it("takes an answer the start already carries without polling", async () => {
    const client = fakeClient(job({ state: "finished", answer: ANSWER }));
    const { ask, sleep } = model(client);
    await ask.ask("Who?");
    expect(sleep).not.toHaveBeenCalled();
    expect(ask.state.turns[0]?.result).toEqual({ kind: "answer", answer: ANSWER });
  });

  it("marks a blank question and asks nothing", async () => {
    const client = fakeClient(job());
    const { ask } = model(client);
    await ask.ask("   ");
    expect(ask.state.blank).toBe(true);
    expect(client.ask).not.toHaveBeenCalled();
    expect(ask.state.turns).toEqual([]);
  });

  it("clears the blank mark on the next real question", async () => {
    const { ask } = model(fakeClient(job({ state: "finished", answer: ANSWER })));
    await ask.ask("");
    await ask.ask("Who?");
    expect(ask.state.blank).toBe(false);
  });

  it("ignores a second question while one is running", async () => {
    let release: (j: Job) => void = () => {};
    const client = fakeClient(job());
    client.ask.mockImplementationOnce(() => new Promise<Job>((resolve) => (release = resolve)));
    const { ask } = model(client);
    const first = ask.ask("One?");
    expect(ask.state.running).toBe(true);
    await ask.ask("Two?");
    expect(client.ask).toHaveBeenCalledTimes(1);
    release(job({ state: "finished", answer: ANSWER }));
    await first;
    expect(ask.state.running).toBe(false);
    expect(ask.state.turns.map((t) => t.question)).toEqual(["One?"]);
  });

  it("words a busy engine and offers to try again", async () => {
    const busy = new ApiError({ status: 409, code: "ask_active", retryable: true, message: "an ask is already running", retryAfterSeconds: null });
    const { ask } = model(fakeClient(busy));
    await ask.ask("Who?");
    expect(ask.state.turns[0]?.result).toEqual({
      kind: "problem",
      problem: { text: "OpenKOS is already answering another question. Wait a moment, then try again.", retry: true },
    });
  });

  it("words a job that failed from its error, never its message", async () => {
    const failed = job({ state: "failed", error: { code: "backend_unavailable", retryable: true, message: "the model backend is unreachable" } });
    const { ask } = model(fakeClient(job(), [failed]));
    await ask.ask("Who?");
    expect(ask.state.turns[0]?.result).toEqual({
      kind: "problem",
      problem: { text: "The AI model isn't running. OpenKOS needs it to answer questions.", retry: true },
    });
  });

  it("treats a failed job with no error, or a finished one with no answer, as something that went wrong", async () => {
    for (const end of [job({ state: "failed" }), job({ state: "finished" })]) {
      const { ask } = model(fakeClient(end));
      await ask.ask("Who?");
      expect(ask.state.turns[0]?.result).toEqual({ kind: "problem", problem: { text: "Something went wrong.", retry: true } });
    }
  });

  it("words a poll that cannot reach the engine", async () => {
    const { ask } = model(fakeClient(job(), [new EngineUnreachable({ path: "/v1/ask/a1" })]));
    await ask.ask("Who?");
    expect(ask.state.turns[0]?.result).toEqual({ kind: "problem", problem: { text: "OpenKOS isn't responding.", retry: true } });
    expect(ask.state.running).toBe(false);
  });

  it("asks a failed question again in its place", async () => {
    const client = fakeClient(new EngineUnreachable({ path: "/v1/ask" }));
    const { ask } = model(client);
    await ask.ask("Who?");
    client.ask.mockResolvedValueOnce(job({ state: "finished", answer: ANSWER }));
    await ask.retry();
    expect(client.ask).toHaveBeenCalledTimes(2);
    expect(ask.state.turns).toHaveLength(1);
    expect(ask.state.turns[0]).toMatchObject({ question: "Who?", result: { kind: "answer" } });
  });

  it("does not retry what asking again cannot fix", async () => {
    const missing = new ApiError({ status: 502, code: "backend_model_not_found", retryable: false, message: "x", retryAfterSeconds: null });
    const client = fakeClient(missing);
    const { ask } = model(client);
    await ask.ask("Who?");
    await ask.retry();
    expect(client.ask).toHaveBeenCalledTimes(1);
  });

  it("keeps a short history, newest first, and shows an earlier question again", async () => {
    const client = fakeClient(job({ state: "finished", answer: ANSWER }));
    const { ask } = model(client);
    for (let i = 1; i <= HISTORY_KEPT + 2; i += 1) await ask.ask(`Question ${i}?`);
    expect(ask.state.turns).toHaveLength(HISTORY_KEPT);
    expect(ask.state.turns[0]?.question).toBe(`Question ${HISTORY_KEPT + 2}?`);
    const earlier = ask.state.turns[2] as AskState["turns"][number];
    ask.show(earlier.id);
    expect(ask.state.shown).toBe(earlier.id);
    ask.show(-1);
    expect(ask.state.shown).toBe(earlier.id);
  });

  it("forgets its engine when the engine goes away", () => {
    const { ask, seen } = model(fakeClient(job()));
    ask.setClient(null);
    expect(ask.state.ready).toBe(false);
    expect(seen.at(-1)?.ready).toBe(false);
  });

  describe("showing a private answer", () => {
    const withheldClient = (settings: Schemas["ModelSettings"] | Error = SETTINGS) => {
      const client = fakeClient(job({ state: "finished", answer: WITHHELD }), [], settings);
      client.ask.mockResolvedValueOnce(job({ state: "finished", answer: WITHHELD }));
      client.ask.mockResolvedValueOnce(job({ state: "finished", answer: ANSWER }));
      return client;
    };

    it("never sends consent on an ordinary ask", async () => {
      const client = withheldClient();
      const { ask } = model(client);
      await ask.ask("Who?");
      expect(client.ask).toHaveBeenCalledWith({ question: "Who?" });
    });

    it("asks again with consent, in place of the withheld turn, when the AI model runs on this Mac", async () => {
      const client = withheldClient();
      const { ask } = model(client);
      await ask.ask("Who?");
      await ask.showPrivate();
      expect(client.modelSettings).toHaveBeenCalledTimes(1);
      expect(client.ask).toHaveBeenLastCalledWith({ question: "Who?", include_confidential: true });
      expect(ask.state.turns).toHaveLength(1);
      expect(ask.state.turns[0]).toMatchObject({ question: "Who?", result: { kind: "answer", answer: ANSWER } });
    });

    it("sends nothing when the AI model is not on this Mac", async () => {
      const client = withheldClient({ ...SETTINGS, is_local: false, remote_hosts: ["api.example.com"] });
      const { ask } = model(client);
      await ask.ask("Who?");
      await ask.showPrivate();
      expect(client.ask).toHaveBeenCalledTimes(1);
      expect(ask.state.turns[0]?.result).toEqual({
        kind: "problem",
        problem: { text: "Your AI model isn't on this Mac, so these pages stay private.", retry: false },
      });
    });

    it("sends nothing when the settings cannot be read", async () => {
      const client = withheldClient(new EngineUnreachable({ path: "/v1/settings/model" }));
      const { ask } = model(client);
      await ask.ask("Who?");
      await ask.showPrivate();
      expect(client.ask).toHaveBeenCalledTimes(1);
      expect(ask.state.turns[0]?.result).toMatchObject({ kind: "problem", problem: { text: "OpenKOS isn't responding." } });
    });

    it("does nothing for an answer that was not withheld", async () => {
      const client = fakeClient(job({ state: "finished", answer: ANSWER }));
      const { ask } = model(client);
      await ask.ask("Who?");
      await ask.showPrivate();
      expect(client.modelSettings).not.toHaveBeenCalled();
      expect(client.ask).toHaveBeenCalledTimes(1);
    });
  });
});
