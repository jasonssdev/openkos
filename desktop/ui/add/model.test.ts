import { describe, expect, it, vi } from "vitest";
import { ApiError, type Schemas } from "../../client/index.ts";
import { fileQuote, job, outcome, quote, result } from "./fixtures.ts";
import { AddModel, POLL_MS, type AddClient, type AddState } from "./model.ts";

type Job = Schemas["IngestJob"];
type Quote = Schemas["IngestQuote"];

const api = (code: string, retryable = false, status = 409) => new ApiError({ status, code, retryable, message: "x", retryAfterSeconds: null });

const ONE = quote([fileQuote("a.md")]);
const TWO = quote([fileQuote("a.md"), fileQuote("b.md")]);

interface Script {
  quotes?: (Quote | Error)[];
  refusedQuotes?: (Quote | Error)[];
  start?: (Job | Error)[];
  polls?: (Job | Error)[];
  stop?: Job | Error;
}

function fakeClient(script: Script) {
  const next = <T>(queue: (T | Error)[] | undefined, what: string): T => {
    const item = queue?.shift();
    if (item === undefined) throw new Error(`unexpected ${what}`);
    if (item instanceof Error) throw item;
    return item;
  };
  const client = {
    quoteIngest: vi.fn(async (_body: Schemas["IngestQuoteRequest"]) => next(script.quotes, "quote")),
    quoteRefusedFile: vi.fn(async (_key: string, _body: Schemas["RefusedFileQuoteRequest"]) => next(script.refusedQuotes, "refused quote")),
    startIngest: vi.fn(async (_body: Schemas["IngestRunRequest"]) => next(script.start, "start")),
    ingestRun: vi.fn(async (_id: string) => next(script.polls, "poll")),
    stopIngest: vi.fn(async (_id: string) => {
      if (script.stop instanceof Error) throw script.stop;
      if (script.stop === undefined) throw new Error("unexpected stop");
      return script.stop;
    }),
  } satisfies AddClient;
  return client;
}

function make(script: Script, picked: string[] | null = ["/Users/me/a.md"], wait: () => Promise<void> = async () => {}) {
  const client = fakeClient(script);
  const sleep = vi.fn(async (_ms: number) => wait());
  const pick = vi.fn(async () => picked);
  const model = new AddModel({ pick, sleep });
  model.setClient(client);
  const seen: AddState[] = [];
  model.subscribe((s) => seen.push(s));
  return { model, client, sleep, pick, seen };
}

const finished = (patch: Partial<Job> = {}) => job({ state: "finished", outcome: outcome([result("a.md")]), ...patch });

describe("AddModel", () => {
  it("starts idle and waits for the engine", () => {
    const model = new AddModel({ pick: async () => null });
    expect(model.state).toMatchObject({ ready: false, phase: "idle", quote: null });
    model.setClient(fakeClient({}));
    expect(model.state.ready).toBe(true);
  });

  it("quotes the chosen files without asking to include private ones", async () => {
    const { model, client, pick } = make({ quotes: [ONE] }, ["/Users/me/a.md", "/Users/me/b.md"]);
    await model.choose();
    expect(pick).toHaveBeenCalledOnce();
    expect(client.quoteIngest).toHaveBeenCalledWith({ paths: ["/Users/me/a.md", "/Users/me/b.md"] });
    expect(model.state).toMatchObject({ phase: "consent", quote: ONE });
  });

  it("quotes dropped files exactly as it quotes chosen ones, without opening the chooser", async () => {
    const { model, client, pick } = make({ quotes: [ONE] });
    await model.drop(["/Users/me/a.md", "/Users/me/b.md"]);
    expect(pick).not.toHaveBeenCalled();
    expect(client.quoteIngest).toHaveBeenCalledWith({ paths: ["/Users/me/a.md", "/Users/me/b.md"] });
    expect(model.state).toMatchObject({ phase: "consent", quote: ONE });
    expect(client.startIngest).not.toHaveBeenCalled();
  });

  it("never starts a run for a drop until the person agrees", async () => {
    const { model, client } = make({ quotes: [ONE], start: [job({ state: "running" })], polls: [finished()] });
    await model.drop(["/Users/me/a.md"]);
    expect(client.startIngest).not.toHaveBeenCalled();
    await model.agree();
    expect(client.startIngest).toHaveBeenCalledWith({ token: ONE.token });
  });

  it("ignores a drop of nothing, before the engine is there, or while a quote or run is open", async () => {
    const waiting = new AddModel({ pick: async () => null });
    await waiting.drop(["/Users/me/a.md"]);
    expect(waiting.state.phase).toBe("idle");
    const { model, client } = make({ quotes: [ONE] });
    await model.drop([]);
    expect(client.quoteIngest).not.toHaveBeenCalled();
    await model.drop(["/Users/me/a.md"]);
    await model.drop(["/Users/me/other.md"]);
    expect(client.quoteIngest).toHaveBeenCalledOnce();
    expect(model.state.quote).toBe(ONE);
  });

  it("takes a drop after a result or a problem, like a refused file", async () => {
    const { model, client } = make({ quotes: [ONE, TWO], start: [finished()] });
    await model.drop(["/Users/me/a.md"]);
    await model.agree();
    expect(model.state.phase).toBe("done");
    await model.drop(["/Users/me/b.md"]);
    expect(client.quoteIngest).toHaveBeenLastCalledWith({ paths: ["/Users/me/b.md"] });
    expect(model.state.phase).toBe("consent");
  });

  it("shows a folder or unreadable drop with the engine's own wording, as the chooser does", async () => {
    const { model } = make({ quotes: [quote([fileQuote("notes", { status: "refused", refusal: "not_a_file" })])] });
    await model.drop(["/Users/me/notes"]);
    expect(model.state.phase).toBe("consent");
    expect(model.state.quote?.files[0]?.refusal).toBe("not_a_file");
  });

  it("shows the quoting step while it waits", async () => {
    const { model, seen } = make({ quotes: [ONE] });
    await model.choose();
    expect(seen.map((s) => s.phase)).toContain("quoting");
  });

  it("changes nothing when the chooser is cancelled or picks nothing", async () => {
    for (const picked of [null, []]) {
      const { model, client } = make({}, picked);
      await model.choose();
      expect(model.state.phase).toBe("idle");
      expect(client.quoteIngest).not.toHaveBeenCalled();
    }
  });

  it("does nothing without an engine", async () => {
    const { model, pick } = make({});
    model.setClient(null);
    await model.choose();
    expect(pick).not.toHaveBeenCalled();
  });

  it("reports a failed quote and offers the step again", async () => {
    const { model, client } = make({ quotes: [api("workspace_busy", true), ONE] });
    await model.choose();
    expect(model.state.phase).toBe("problem");
    expect(model.state.problem).toMatchObject({ action: "try_again" });
    await model.resolveProblem();
    expect(client.quoteIngest).toHaveBeenCalledTimes(2);
    expect(client.quoteIngest).toHaveBeenLastCalledWith({ paths: ["/Users/me/a.md"] });
    expect(model.state.phase).toBe("consent");
  });

  it("declining the consent changes nothing", async () => {
    const { model, client } = make({ quotes: [ONE] });
    await model.choose();
    model.decline();
    expect(model.state).toMatchObject({ phase: "idle", quote: null });
    expect(client.startIngest).not.toHaveBeenCalled();
  });

  it("runs with the quote's token, polls each second and ends with the outcome", async () => {
    const polls = [job({ index: 1, total: 1, phase: "extracting" }), finished()];
    const { model, client, sleep, seen } = make({ quotes: [ONE], start: [job()], polls });
    await model.choose();
    await model.agree();
    expect(client.startIngest).toHaveBeenCalledWith({ token: ONE.token });
    expect(client.ingestRun).toHaveBeenCalledWith("r1");
    expect(sleep).toHaveBeenCalledWith(POLL_MS);
    expect(seen.some((s) => s.phase === "running" && s.job?.phase === "extracting")).toBe(true);
    expect(model.state.phase).toBe("done");
    expect(model.state.outcome?.files[0]?.name).toBe("a.md");
  });

  it("ignores a second agree while running", async () => {
    const { model, client } = make({ quotes: [ONE], start: [job()], polls: [finished()] });
    await model.choose();
    await Promise.all([model.agree(), model.agree()]);
    expect(client.startIngest).toHaveBeenCalledOnce();
  });

  it("stops between files and still reports what was added", async () => {
    const stopped = job({ state: "stopping" });
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { model, client, seen } = make(
      {
        quotes: [TWO],
        start: [job()],
        polls: [finished({ outcome: outcome([result("a.md"), result("b.md", { status: "not_reached", created: 0 })], { status: "stopped" }) })],
        stop: stopped,
      },
      ["/Users/me/a.md"],
      () => gate,
    );
    await model.choose();
    const run = model.agree();
    await vi.waitFor(() => expect(model.state.phase).toBe("running"));
    await model.stop();
    release();
    await run;
    expect(client.stopIngest).toHaveBeenCalledWith("r1");
    expect(seen.map((s) => s.phase)).toContain("stopping");
    expect(model.state.outcome?.status).toBe("stopped");
  });

  it("offers a new look when the quote went stale at start", async () => {
    const { model, client } = make({ quotes: [ONE, TWO], start: [api("quote_stale")] });
    await model.choose();
    await model.agree();
    expect(model.state.problem?.action).toBe("requote");
    await model.resolveProblem();
    expect(client.quoteIngest).toHaveBeenCalledTimes(2);
    expect(client.quoteIngest).toHaveBeenLastCalledWith({ paths: ["/Users/me/a.md"] });
    expect(model.state).toMatchObject({ phase: "consent", quote: TWO });
  });

  it("asks to try later when files are already being added, then starts the same run", async () => {
    const { model, client } = make({ quotes: [ONE], start: [api("ingest_active", true), job()], polls: [finished()] });
    await model.choose();
    await model.agree();
    expect(model.state.problem?.text).toContain("already adding files");
    await model.resolveProblem();
    expect(client.startIngest).toHaveBeenCalledTimes(2);
    expect(client.startIngest).toHaveBeenLastCalledWith({ token: ONE.token });
    expect(model.state.phase).toBe("done");
  });

  it("says a busy library at start can be tried again", async () => {
    const { model } = make({ quotes: [ONE], start: [api("workspace_busy", true)] });
    await model.choose();
    await model.agree();
    expect(model.state.problem?.action).toBe("try_again");
  });

  it("shows a run that failed as a problem", async () => {
    const failed = job({ state: "failed", error: { code: "workspace_busy", retryable: true, message: "" } });
    const { model } = make({ quotes: [ONE], start: [job()], polls: [failed] });
    await model.choose();
    await model.agree();
    expect(model.state.phase).toBe("problem");
    expect(model.state.problem?.text).toContain("busy");
  });

  it("says the engine restarted when the run is gone", async () => {
    const { model } = make({ quotes: [ONE], start: [job()], polls: [api("not_found", false, 404)] });
    await model.choose();
    await model.agree();
    expect(model.state.phase).toBe("problem");
    expect(model.state.problem?.text).toContain("restarted");
  });

  it("closes a problem or an outcome back to the start", async () => {
    const { model } = make({ quotes: [ONE], start: [job()], polls: [finished()] });
    await model.choose();
    await model.agree();
    model.close();
    expect(model.state).toMatchObject({ phase: "idle", quote: null, job: null, outcome: null, problem: null });
  });

  it("quotes a refused file by its decision and runs it the same way", async () => {
    const { model, client } = make({ refusedQuotes: [ONE], start: [job()], polls: [finished()] });
    await model.addRefused("key-1");
    expect(client.quoteRefusedFile).toHaveBeenCalledWith("key-1", {});
    expect(model.state.phase).toBe("consent");
    await model.agree();
    expect(client.startIngest).toHaveBeenCalledWith({ token: ONE.token });
    expect(model.state.phase).toBe("done");
  });

  it("looks again at a refused file the same way it first did", async () => {
    const { model, client } = make({ refusedQuotes: [ONE, ONE], start: [api("quote_stale")] });
    await model.addRefused("key-1");
    await model.agree();
    await model.resolveProblem();
    expect(client.quoteRefusedFile).toHaveBeenCalledTimes(2);
    expect(client.quoteIngest).not.toHaveBeenCalled();
  });

  it("reports a refused file that cannot be quoted", async () => {
    const { model } = make({ refusedQuotes: [api("stale")] });
    await model.addRefused("key-1");
    expect(model.state.phase).toBe("problem");
  });

  it("does not start a new choice while a consent card is open", async () => {
    const { model, client } = make({ quotes: [ONE] });
    await model.choose();
    await model.choose();
    expect(client.quoteIngest).toHaveBeenCalledOnce();
  });
});

describe("AddModel queue", () => {
  const B = quote([fileQuote("b.md"), fileQuote("c.md")]);
  const doneFor = (...names: string[]) => finished({ outcome: outcome(names.map((n) => result(n))) });

  /** A run on a.md that stays on until `release` is called. */
  async function running(script: Script, picked: string[] | null = ["/Users/me/a.md"]) {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    let first = true;
    const wait = () => {
      if (!first) return Promise.resolve();
      first = false;
      return gate;
    };
    const made = make(script, picked, wait);
    await made.model.choose();
    const run = made.model.agree();
    await vi.waitFor(() => expect(made.model.state.phase).toBe("running"));
    return { ...made, release, run };
  }

  it("quotes files dropped during a run and waits for consent without touching the run", async () => {
    const { model, client, release, run } = await running({ quotes: [ONE, B], start: [job()], polls: [finished()] });
    await model.drop(["/Users/me/b.md", "/Users/me/c.md"]);
    expect(client.quoteIngest).toHaveBeenLastCalledWith({ paths: ["/Users/me/b.md", "/Users/me/c.md"] });
    expect(model.state.phase).toBe("running");
    expect(model.state.incoming).toEqual({ quote: B, problem: null });
    expect(model.state.queue).toEqual([]);
    expect(client.startIngest).toHaveBeenCalledOnce();
    release();
    await run;
  });

  it("opens the chooser for more files during a run", async () => {
    const { model, pick, client, release, run } = await running({ quotes: [ONE, B], start: [job()], polls: [finished()] });
    await model.choose();
    expect(pick).toHaveBeenCalledTimes(2);
    expect(client.quoteIngest).toHaveBeenCalledTimes(2);
    release();
    await run;
  });

  it("queues each agreed file by name, in order, and removes one the person no longer wants", async () => {
    const { model, release, run } = await running({ quotes: [ONE, B], start: [job()], polls: [finished()] });
    await model.drop(["/Users/me/b.md", "/Users/me/c.md"]);
    model.agreeIncoming();
    expect(model.state.incoming).toBeNull();
    expect(model.state.queue.map((q) => q.name)).toEqual(["b.md", "c.md"]);
    model.removeQueued("/Users/me/b.md");
    expect(model.state.queue.map((q) => q.name)).toEqual(["c.md"]);
    release();
    await run;
  });

  it("adds nothing for a dropped quote the person turns down", async () => {
    const { model, release, run } = await running({ quotes: [ONE, B], start: [job()], polls: [finished()] });
    await model.drop(["/Users/me/b.md"]);
    model.dismissIncoming();
    expect(model.state.incoming).toBeNull();
    expect(model.state.queue).toEqual([]);
    release();
    await run;
    expect(model.state.outcome?.files.map((f) => f.name)).toEqual(["a.md"]);
  });

  it("starts the next waiting file by itself, each with its own one-file token", async () => {
    const { model, client, release, run } = await running({
      quotes: [ONE, B],
      start: [job(), job({ run_id: "r2" }), job({ run_id: "r3" })],
      polls: [doneFor("a.md"), doneFor("b.md"), doneFor("c.md")],
    });
    await model.drop(["/Users/me/b.md", "/Users/me/c.md"]);
    model.agreeIncoming();
    release();
    await run;
    expect(client.startIngest).toHaveBeenCalledTimes(3);
    const second = client.startIngest.mock.calls[1]![0].token!;
    expect(second.files).toEqual([B.token.files[0]]);
    expect(second.calls).toBe(B.token.files[0]!.calls);
    expect(second.binding).toEqual(B.token.binding);
    const third = client.startIngest.mock.calls[2]![0].token!;
    expect(third.files).toEqual([B.token.files[1]]);
    expect(model.state.phase).toBe("done");
    expect(model.state.queue).toEqual([]);
    expect(model.state.outcome?.files.map((f) => f.name)).toEqual(["a.md", "b.md", "c.md"]);
    expect(model.state.outcome?.status).toBe("completed");
  });

  it("never starts a waiting file that was removed", async () => {
    const { model, client, release, run } = await running({
      quotes: [ONE, B],
      start: [job(), job({ run_id: "r3" })],
      polls: [doneFor("a.md"), doneFor("c.md")],
    });
    await model.drop(["/Users/me/b.md", "/Users/me/c.md"]);
    model.agreeIncoming();
    model.removeQueued("/Users/me/b.md");
    release();
    await run;
    expect(client.startIngest).toHaveBeenCalledTimes(2);
    expect(client.startIngest.mock.calls[1]![0].token!.files.map((f: { path: string }) => f.path)).toEqual(["/Users/me/c.md"]);
  });

  it("counts the files before it and the ones waiting in what it shows", async () => {
    const { model, seen, release, run } = await running({
      quotes: [ONE, B],
      start: [job(), job({ run_id: "r2" }), job({ run_id: "r3" })],
      polls: [doneFor("a.md"), job({ run_id: "r2", index: 1, total: 1, phase: "extracting" }), doneFor("b.md"), doneFor("c.md")],
    });
    await model.drop(["/Users/me/b.md", "/Users/me/c.md"]);
    model.agreeIncoming();
    release();
    await run;
    const second = seen.find((s) => s.job?.run_id === "r2" && s.phase === "running");
    expect(second).toMatchObject({ before: 1 });
    expect(second?.queue.map((q) => q.name)).toEqual(["c.md"]);
    expect(second?.quote?.files[0]?.name).toBe("b.md");
  });

  it("leaves the waiting files out after a stop and says they were not reached", async () => {
    const stopped = job({ state: "stopping" });
    const { model, client, release, run } = await running({
      quotes: [ONE, B],
      start: [job()],
      polls: [finished({ outcome: outcome([result("a.md")], { status: "stopped" }) })],
      stop: stopped,
    });
    await model.drop(["/Users/me/b.md", "/Users/me/c.md"]);
    model.agreeIncoming();
    await model.stop();
    release();
    await run;
    expect(client.startIngest).toHaveBeenCalledOnce();
    expect(model.state.phase).toBe("done");
    expect(model.state.queue).toEqual([]);
    expect(model.state.outcome?.status).toBe("stopped");
    expect(model.state.outcome?.files.map((f) => [f.name, f.status])).toEqual([
      ["a.md", "added"],
      ["b.md", "not_reached"],
      ["c.md", "not_reached"],
    ]);
  });

  it("does not go on to the waiting files when the library was busy", async () => {
    const busy = finished({ outcome: outcome([result("a.md", { status: "busy" })], { status: "busy", indexes: "not_needed" }) });
    const { model, client, release, run } = await running({ quotes: [ONE, B], start: [job()], polls: [busy] });
    await model.drop(["/Users/me/b.md"]);
    model.agreeIncoming();
    release();
    await run;
    expect(client.startIngest).toHaveBeenCalledOnce();
    expect(model.state.outcome?.status).toBe("busy");
    expect(model.state.outcome?.files.map((f) => f.status)).toEqual(["busy", "not_reached", "not_reached"]);
  });

  it("starts what was dropped at once when the run ended before the person agreed", async () => {
    const { model, client, release, run } = await running({ quotes: [ONE, B], start: [job(), job({ run_id: "r2" })], polls: [doneFor("a.md"), doneFor("b.md", "c.md")] });
    await model.drop(["/Users/me/b.md", "/Users/me/c.md"]);
    release();
    await run;
    expect(model.state.phase).toBe("done");
    expect(model.state.incoming).toEqual({ quote: B, problem: null });
    await model.agreeIncoming();
    expect(client.startIngest).toHaveBeenLastCalledWith({ token: B.token });
    expect(model.state.phase).toBe("done");
    expect(model.state.outcome?.files.map((f) => f.name)).toEqual(["b.md", "c.md"]);
  });

  it("shows a problem quoting dropped files without disturbing the run", async () => {
    const { model, release, run } = await running({ quotes: [ONE, api("workspace_busy", true)], start: [job()], polls: [finished()] });
    await model.drop(["/Users/me/b.md"]);
    expect(model.state.phase).toBe("running");
    expect(model.state.incoming?.quote).toBeNull();
    expect(model.state.incoming?.problem).toContain("busy");
    model.dismissIncoming();
    expect(model.state.incoming).toBeNull();
    release();
    await run;
  });

  it("queues nothing from files that cannot be added", async () => {
    const refused = quote([fileQuote("x", { status: "refused", refusal: "not_a_file" })]);
    const { model, release, run } = await running({ quotes: [ONE, refused], start: [job()], polls: [finished()] });
    await model.drop(["/Users/me/x"]);
    expect(model.state.incoming?.quote).toBe(refused);
    model.agreeIncoming();
    expect(model.state.queue).toEqual([]);
    release();
    await run;
  });

  it("counts seconds on the file it is on, starting over on the next", async () => {
    let clock = 0;
    const client = fakeClient({
      quotes: [TWO],
      start: [job({ index: 1, total: 2, phase: "extracting" })],
      polls: [job({ index: 1, total: 2, phase: "extracting" }), job({ index: 2, total: 2, phase: "extracting" }), job({ index: 2, total: 2, phase: "extracting" }), finished()],
    });
    const model = new AddModel({ pick: async () => ["/Users/me/a.md"], now: () => clock, sleep: async () => void (clock += 7000) });
    model.setClient(client);
    const seen: (number | null)[] = [];
    model.subscribe((s) => seen.push(s.elapsed));
    await model.choose();
    await model.agree();
    expect(seen.filter((e) => e !== null)).toEqual([0, 7, 0, 7]);
    expect(model.state.elapsed).toBeNull();
  });

  it("takes no more files once Stop is pressed, and drops a question still open", async () => {
    const { model, client, release, run } = await running({
      quotes: [ONE, B],
      start: [job()],
      polls: [finished({ outcome: outcome([result("a.md")], { status: "stopped" }) })],
      stop: job({ state: "stopping" }),
    });
    await model.drop(["/Users/me/b.md"]);
    expect(model.state.incoming?.quote).toBe(B);
    await model.stop();
    expect(model.state.incoming).toBeNull();
    await model.drop(["/Users/me/c.md"]);
    expect(client.quoteIngest).toHaveBeenCalledTimes(2);
    release();
    await run;
  });

  it("clears the queue and incoming files when the result is closed", async () => {
    const { model, release, run } = await running({ quotes: [ONE, B], start: [job()], polls: [finished()] });
    await model.drop(["/Users/me/b.md"]);
    release();
    await run;
    model.close();
    expect(model.state).toMatchObject({ phase: "idle", queue: [], incoming: null, before: 0, elapsed: null });
  });
});
