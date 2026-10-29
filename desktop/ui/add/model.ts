// The Add files screen's view model (ADR-0073): its state and transitions over
// the five client methods it calls, with no DOM. Adding files spends model
// calls, so it follows judging's protocol: a free quote, the person's consent,
// then a run bound to the quote, polled until it ends. The files come from a
// small injected chooser (the shell opens the system's file dialog), so a test
// can fake it. It never sends `include_confidential`: a file the library keeps
// Only on this Mac stays kept from the AI model (mirrors Ask, #1507).
//
// The engine runs one ingest at a time (a second start is refused as already
// being added), so the queue lives here: files dropped during a run are quoted
// and agreed to like any others, then wait as one-file runs that start, in
// order, as each run ends. A one-file token is the agreed quote's own entry for
// that file under its own binding; the engine still checks it on every start.
import { ApiError, type Client, type Schemas } from "../../client/index.ts";
import { problemFor, type AddProblem } from "./wording.ts";

export type AddClient = Pick<Client, "quoteIngest" | "quoteRefusedFile" | "startIngest" | "ingestRun" | "stopIngest">;

export const POLL_MS = 1000;

type Quote = Schemas["IngestQuote"];
type Job = Schemas["IngestJob"];
type Outcome = Schemas["IngestRunOutcome"];
type FileResult = Schemas["IngestFileResult"];

/** A file waiting for its turn: its own one-file quote, as agreed to. */
export interface QueuedFile {
  path: string;
  name: string;
  quote: Quote;
}

/** Files dropped or chosen during a run, quoted and awaiting the person's answer. */
export interface Incoming {
  /** `null` while it is being quoted. */
  quote: Quote | null;
  /** Why it could not be quoted, in words. */
  problem: string | null;
}

export type AddPhase = "idle" | "quoting" | "consent" | "starting" | "running" | "stopping" | "done" | "problem";

export interface AddState {
  /** An engine is there to add files to. */
  ready: boolean;
  phase: AddPhase;
  quote: Quote | null;
  job: Job | null;
  outcome: Outcome | null;
  problem: AddProblem | null;
  /** The files waiting for the run in progress, in the order they will be added. */
  queue: QueuedFile[];
  /** Files put in while a run was on, until the person agrees or turns them down. */
  incoming: Incoming | null;
  /** Files finished by earlier runs of this batch, for "3 of 5". */
  before: number;
  /** Whole seconds on the file in progress, or none. */
  elapsed: number | null;
}

export interface AddModelOptions {
  /** Opens the system's file chooser: absolute paths, or null when cancelled. */
  pick: () => Promise<string[] | null>;
  sleep?: (ms: number) => Promise<void>;
  /** Milliseconds, for the time spent on a file. */
  now?: () => number;
}

/** What the files came from, so a stale quote can be looked at again. */
type Source = { kind: "paths"; paths: string[] } | { kind: "refused"; key: string };

const IDLE: Omit<AddState, "ready"> = {
  phase: "idle",
  quote: null,
  job: null,
  outcome: null,
  problem: null,
  queue: [],
  incoming: null,
  before: 0,
  elapsed: null,
};

const BUSY = new Set<AddPhase>(["starting", "running", "stopping"]);
/** A run that will still go on to the files that wait: Stop ends that. */
const TAKING = new Set<AddPhase>(["starting", "running"]);
const OPEN = new Set<AddPhase>(["idle", "done", "problem"]);

export class AddModel {
  #state: AddState = { ready: false, ...IDLE };
  readonly #listeners = new Set<(state: AddState) => void>();
  readonly #pick: () => Promise<string[] | null>;
  readonly #sleep: (ms: number) => Promise<void>;
  readonly #now: () => number;
  #client: AddClient | null = null;
  #source: Source | null = null;
  /** The step to repeat when the person chooses "Try again". */
  #again: (() => Promise<void>) | null = null;
  /** What the batch's earlier runs reported, folded into the one result. */
  #earlier: Outcome[] = [];
  #stopped = false;
  #fileKey = "";
  #fileSince = 0;

  constructor(options: AddModelOptions) {
    this.#pick = options.pick;
    this.#sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.#now = options.now ?? Date.now;
  }

  get state(): AddState {
    return this.#state;
  }

  subscribe(listener: (state: AddState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #set(patch: Partial<AddState>): void {
    this.#state = { ...this.#state, ...patch };
    for (const listener of this.#listeners) listener(this.#state);
  }

  /** The engine of this launch, or none while it is not running. */
  setClient(client: AddClient | null): void {
    this.#client = client;
    this.#set({ ready: client !== null });
  }

  #problem(error: unknown, again: () => Promise<void>): void {
    this.#again = again;
    this.#set({ phase: "problem", problem: problemFor(error) });
  }

  /** Open the file chooser and quote what was chosen; during a run, quote it to join the queue. */
  async choose(): Promise<void> {
    const phase = this.#state.phase;
    if (this.#client === null || (phase !== "idle" && !TAKING.has(phase))) return;
    const paths = await this.#pick();
    if (paths === null || paths.length === 0) return;
    await this.#accept(paths);
  }

  /** Quote files dropped on the window: the chooser's path, without the chooser. */
  async drop(paths: string[]): Promise<void> {
    if (this.#client === null || paths.length === 0) return;
    if (!OPEN.has(this.#state.phase) && !TAKING.has(this.#state.phase)) return;
    await this.#accept(paths);
  }

  async #accept(paths: string[]): Promise<void> {
    // After a result the new files start a new batch; while a run is on, or while
    // the last result is still shown with files awaiting an answer, they wait.
    if (BUSY.has(this.#state.phase) || this.#state.incoming !== null) return this.#quoteIncoming(paths);
    await this.#quote({ kind: "paths", paths });
  }

  async #quoteIncoming(paths: string[]): Promise<void> {
    const client = this.#client;
    if (client === null) return;
    this.#set({ incoming: { quote: null, problem: null } });
    try {
      const quote = await client.quoteIngest({ paths });
      this.#set({ incoming: { quote, problem: null } });
    } catch (error) {
      this.#set({ incoming: { quote: null, problem: problemFor(error).text } });
    }
  }

  /** Agree to the files put in during a run: they wait their turn, or start now if nothing is running. */
  async agreeIncoming(): Promise<void> {
    const quote = this.#state.incoming?.quote;
    if (!quote) return;
    if (!BUSY.has(this.#state.phase)) {
      this.#reset({ kind: "paths", paths: quote.token.files.map((f) => f.path) });
      this.#set({ ...IDLE, quote });
      await this.#start(quote);
      return;
    }
    const waiting = quote.token.files.map((entry) => {
      const file = quote.files.find((f) => f.path === entry.path);
      const single: Quote = {
        ...quote,
        files: file ? [file] : [],
        calls: entry.calls,
        token: { binding: quote.token.binding, files: [entry], calls: entry.calls },
      };
      return { path: entry.path, name: file?.name ?? entry.path, quote: single };
    });
    this.#set({ incoming: null, queue: [...this.#state.queue, ...waiting] });
  }

  /** Turn down the files put in during a run, or dismiss why they could not be looked at. */
  dismissIncoming(): void {
    this.#set({ incoming: null });
  }

  /** Take a waiting file out of the queue. */
  removeQueued(path: string): void {
    this.#set({ queue: this.#state.queue.filter((q) => q.path !== path) });
  }

  /** Quote the file a refused decision names. */
  async addRefused(decisionKey: string): Promise<void> {
    if (this.#client === null || (this.#state.phase !== "idle" && this.#state.phase !== "done" && this.#state.phase !== "problem")) return;
    await this.#quote({ kind: "refused", key: decisionKey });
  }

  /** A new batch: nothing from the last one carries over. */
  #reset(source: Source | null): void {
    this.#source = source;
    this.#again = null;
    this.#earlier = [];
    this.#stopped = false;
    this.#fileKey = "";
  }

  async #quote(source: Source): Promise<void> {
    const client = this.#client;
    if (client === null) return;
    this.#reset(source);
    this.#set({ ...IDLE, phase: "quoting" });
    try {
      const quote =
        source.kind === "paths" ? await client.quoteIngest({ paths: source.paths }) : await client.quoteRefusedFile(source.key, {});
      this.#set({ phase: "consent", quote });
    } catch (error) {
      this.#problem(error, () => this.#quote(source));
    }
  }

  /** Agree to the quote: add the files it covers. */
  async agree(): Promise<void> {
    const quote = this.#state.quote;
    if (this.#client === null || quote === null || this.#state.phase !== "consent") return;
    await this.#start(quote);
  }

  async #start(quote: Quote): Promise<void> {
    const client = this.#client;
    if (client === null) return;
    this.#set({ phase: "starting", problem: null });
    let job: Job;
    try {
      job = await client.startIngest({ token: quote.token });
    } catch (error) {
      this.#problem(error, () => this.#start(quote));
      return;
    }
    await this.#follow(client, job);
  }

  /** Whole seconds on the file the job is on; a new file starts the count over. */
  #seconds(job: Job): number {
    const key = `${job.run_id}:${job.index}`;
    if (key !== this.#fileKey) {
      this.#fileKey = key;
      this.#fileSince = this.#now();
    }
    return Math.floor((this.#now() - this.#fileSince) / 1000);
  }

  async #follow(client: AddClient, started: Job): Promise<void> {
    let job = started;
    try {
      while (job.state === "running" || job.state === "stopping") {
        this.#set({ phase: job.state, job, elapsed: this.#seconds(job) });
        await this.#sleep(POLL_MS);
        job = await client.ingestRun(job.run_id);
      }
    } catch (error) {
      this.#problem(error, () => this.#follow(client, job));
      return;
    }
    if (job.state === "finished" && job.outcome !== null) return this.#ended(job, job.outcome);
    const failure = job.error ?? { code: "internal_error", retryable: false, message: "" };
    this.#set({ queue: [] });
    this.#problem(
      failureError(failure),
      () => this.#quote(this.#source ?? { kind: "paths", paths: [] }),
    );
  }

  /** A run ended: start the next waiting file, or finish with everything the batch did. */
  async #ended(job: Job, outcome: Outcome): Promise<void> {
    this.#earlier.push(outcome);
    const [next, ...rest] = this.#state.queue;
    if (next !== undefined && outcome.status === "completed" && !this.#stopped) {
      this.#set({ queue: rest, quote: next.quote, before: this.#state.before + outcome.files.length, job: null, elapsed: null });
      await this.#start(next.quote);
      return;
    }
    const left = this.#state.queue.map((q) => notReached(q));
    this.#set({ phase: "done", job, outcome: combine(this.#earlier, left, this.#stopped), queue: [], elapsed: null });
  }

  /** Stop before the next file; files already added are kept. */
  async stop(): Promise<void> {
    const client = this.#client;
    const job = this.#state.job;
    if (client === null || job === null || this.#state.phase !== "running") return;
    this.#stopped = true;
    this.#set({ phase: "stopping", incoming: null });
    try {
      this.#set({ job: await client.stopIngest(job.run_id) });
    } catch {
      // The poll reports how the run ended; a stop that could not be asked changes nothing.
    }
  }

  /** Decline the consent: nothing was sent and nothing was added. */
  decline(): void {
    if (this.#state.phase === "consent") this.close();
  }

  /** Back to the start, from a result or a problem. */
  close(): void {
    this.#reset(null);
    this.#set({ ...IDLE });
  }

  /** Do what the shown problem offers: repeat the step, look again, or close it. */
  async resolveProblem(): Promise<void> {
    const problem = this.#state.problem;
    if (problem === null) return;
    if (problem.action === "ok") return this.close();
    if (problem.action === "requote" && this.#source !== null) return this.#quote(this.#source);
    const again = this.#again;
    if (again === null) return this.close();
    await again();
  }
}

/** A run that ended in failure, as the error its code names. */
function failureError(body: NonNullable<Job["error"]>): ApiError {
  return new ApiError({ status: 0, ...body, retryAfterSeconds: null });
}

function notReached(entry: QueuedFile): FileResult {
  return { calls: 0, created: 0, extraction_degraded: false, name: entry.name, path: entry.path, revised: 0, source_id: null, status: "not_reached" };
}

/** The batch's runs as one result: every file, and the worst way any of them ended. */
function combine(runs: Outcome[], left: FileResult[], stopped: boolean): Outcome {
  const last = runs[runs.length - 1]!;
  if (runs.length === 1 && left.length === 0) return last;
  const ended = runs.map((r) => r.status);
  const status = ended.includes("stopped") || (stopped && left.length > 0) ? "stopped" : ended.includes("busy") ? "busy" : "completed";
  const indexes = runs.some((r) => r.indexes === "stale") ? "stale" : runs.some((r) => r.indexes === "refreshed") ? "refreshed" : "not_needed";
  return { ...last, status, indexes, calls: runs.reduce((sum, r) => sum + r.calls, 0), files: [...runs.flatMap((r) => r.files), ...left] };
}
