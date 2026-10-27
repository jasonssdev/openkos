// The Add files screen's view model (ADR-0073): its state and transitions over
// the five client methods it calls, with no DOM. Adding files spends model
// calls, so it follows judging's protocol: a free quote, the person's consent,
// then a run bound to the quote, polled until it ends. The files come from a
// small injected chooser (the shell opens the system's file dialog), so a test
// can fake it. It never sends `include_confidential`: a file the library keeps
// Only on this Mac stays kept from the AI model (mirrors Ask, #1507).
import { ApiError, type Client, type Schemas } from "../../client/index.ts";
import { problemFor, type AddProblem } from "./wording.ts";

export type AddClient = Pick<Client, "quoteIngest" | "quoteRefusedFile" | "startIngest" | "ingestRun" | "stopIngest">;

export const POLL_MS = 1000;

type Quote = Schemas["IngestQuote"];
type Job = Schemas["IngestJob"];
type Outcome = Schemas["IngestRunOutcome"];

export type AddPhase = "idle" | "quoting" | "consent" | "starting" | "running" | "stopping" | "done" | "problem";

export interface AddState {
  /** An engine is there to add files to. */
  ready: boolean;
  phase: AddPhase;
  quote: Quote | null;
  job: Job | null;
  outcome: Outcome | null;
  problem: AddProblem | null;
}

export interface AddModelOptions {
  /** Opens the system's file chooser: absolute paths, or null when cancelled. */
  pick: () => Promise<string[] | null>;
  sleep?: (ms: number) => Promise<void>;
}

/** What the files came from, so a stale quote can be looked at again. */
type Source = { kind: "paths"; paths: string[] } | { kind: "refused"; key: string };

const IDLE: Omit<AddState, "ready"> = { phase: "idle", quote: null, job: null, outcome: null, problem: null };

export class AddModel {
  #state: AddState = { ready: false, ...IDLE };
  readonly #listeners = new Set<(state: AddState) => void>();
  readonly #pick: () => Promise<string[] | null>;
  readonly #sleep: (ms: number) => Promise<void>;
  #client: AddClient | null = null;
  #source: Source | null = null;
  /** The step to repeat when the person chooses "Try again". */
  #again: (() => Promise<void>) | null = null;

  constructor(options: AddModelOptions) {
    this.#pick = options.pick;
    this.#sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
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

  /** Open the file chooser and quote what was chosen. */
  async choose(): Promise<void> {
    if (this.#client === null || this.#state.phase !== "idle") return;
    const paths = await this.#pick();
    if (paths === null || paths.length === 0) return;
    await this.#quote({ kind: "paths", paths });
  }

  /** Quote files dropped on the window: the chooser's path, without the chooser. */
  async drop(paths: string[]): Promise<void> {
    if (this.#client === null || paths.length === 0) return;
    if (this.#state.phase !== "idle" && this.#state.phase !== "done" && this.#state.phase !== "problem") return;
    await this.#quote({ kind: "paths", paths });
  }

  /** Quote the file a refused decision names. */
  async addRefused(decisionKey: string): Promise<void> {
    if (this.#client === null || (this.#state.phase !== "idle" && this.#state.phase !== "done" && this.#state.phase !== "problem")) return;
    await this.#quote({ kind: "refused", key: decisionKey });
  }

  async #quote(source: Source): Promise<void> {
    const client = this.#client;
    if (client === null) return;
    this.#source = source;
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

  async #follow(client: AddClient, started: Job): Promise<void> {
    let job = started;
    try {
      while (job.state === "running" || job.state === "stopping") {
        this.#set({ phase: job.state, job });
        await this.#sleep(POLL_MS);
        job = await client.ingestRun(job.run_id);
      }
    } catch (error) {
      this.#problem(error, () => this.#follow(client, job));
      return;
    }
    if (job.state === "finished" && job.outcome !== null) {
      this.#set({ phase: "done", job, outcome: job.outcome });
      return;
    }
    const failure = job.error ?? { code: "internal_error", retryable: false, message: "" };
    this.#problem(
      failureError(failure),
      () => this.#quote(this.#source ?? { kind: "paths", paths: [] }),
    );
  }

  /** Stop before the next file; files already added are kept. */
  async stop(): Promise<void> {
    const client = this.#client;
    const job = this.#state.job;
    if (client === null || job === null || this.#state.phase !== "running") return;
    this.#set({ phase: "stopping" });
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
    this.#source = null;
    this.#again = null;
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
