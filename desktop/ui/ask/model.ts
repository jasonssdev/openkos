// The Ask screen's view model (ADR-0066): its state and transitions over the
// two client methods it calls, with no DOM. An ask is a job on the engine
// (ADR-0068): start it, poll it until it ends, show each phase. It never sends
// `include_confidential` by default, so the answer keeps the API's conservative
// default: a page marked Only on this Mac is counted, never named. The person
// may ask one withheld question again with it (ADR-0075), and only after the
// settings say the AI model runs on this Mac. The history lives in memory only.
import { ApiError, type Client, type Schemas } from "../../client/index.ts";
import { MODEL_NOT_HERE, problemFor, type TurnResult } from "./wording.ts";

export type AskClient = Pick<Client, "ask" | "askJob" | "modelSettings">;

export const POLL_MS = 1000;
/** Questions kept in the session's history, the shown one included. */
export const HISTORY_KEPT = 6;

export interface Turn {
  id: number;
  question: string;
  result: TurnResult;
}

export interface AskState {
  /** An engine is there to ask. */
  ready: boolean;
  /** An ask is on its way; another is not started. */
  running: boolean;
  /** The last question sent was blank. */
  blank: boolean;
  /** This session's questions, newest first. */
  turns: Turn[];
  /** The turn on screen. */
  shown: number | null;
}

export interface AskModelOptions {
  sleep?: (ms: number) => Promise<void>;
}

type Job = Schemas["AskJob"];

export class AskModel {
  #state: AskState = { ready: false, running: false, blank: false, turns: [], shown: null };
  readonly #listeners = new Set<(state: AskState) => void>();
  readonly #sleep: (ms: number) => Promise<void>;
  #client: AskClient | null = null;
  #nextId = 1;

  constructor(options: AskModelOptions = {}) {
    this.#sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  }

  get state(): AskState {
    return this.#state;
  }

  subscribe(listener: (state: AskState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #set(patch: Partial<AskState>): void {
    this.#state = { ...this.#state, ...patch };
    for (const listener of this.#listeners) listener(this.#state);
  }

  #settle(id: number, result: TurnResult): void {
    this.#set({ turns: this.#state.turns.map((turn) => (turn.id === id ? { ...turn, result } : turn)) });
  }

  /** The engine of this launch, or none while it is not running. */
  setClient(client: AskClient | null): void {
    this.#client = client;
    this.#set({ ready: client !== null });
  }

  /** Ask `question`, follow the job to its end and show the outcome. */
  async ask(question: string, consent = false): Promise<void> {
    const client = this.#client;
    const text = question.trim();
    if (text === "") {
      this.#set({ blank: true });
      return;
    }
    if (client === null || this.#state.running) return;
    const id = this.#nextId++;
    const turn: Turn = { id, question: text, result: { kind: "running", phase: null } };
    this.#set({ blank: false, running: true, shown: id, turns: [turn, ...this.#state.turns].slice(0, HISTORY_KEPT) });
    let result: TurnResult;
    try {
      result = await this.#follow(client, id, await client.ask(consent ? { question: text, include_confidential: true } : { question: text }));
    } catch (error) {
      result = { kind: "problem", problem: problemFor(error) };
    }
    this.#settle(id, result);
    this.#set({ running: false });
  }

  async #follow(client: AskClient, id: number, started: Job): Promise<TurnResult> {
    let job = started;
    while (job.state === "running") {
      this.#settle(id, { kind: "running", phase: job.phase });
      await this.#sleep(POLL_MS);
      job = await client.askJob(job.ask_id);
    }
    if (job.state === "finished" && job.answer !== null) return { kind: "answer", answer: job.answer };
    const error = job.error ?? { code: "internal_error", retryable: false, message: "" };
    return { kind: "problem", problem: problemFor(new ApiError({ status: 0, ...error, retryAfterSeconds: null })) };
  }

  /** Ask the shown question again, in place of its failed turn. */
  async retry(): Promise<void> {
    const turn = this.#state.turns.find((t) => t.id === this.#state.shown);
    if (turn === undefined || turn.result.kind !== "problem" || !turn.result.problem.retry || this.#state.running) return;
    this.#set({ turns: this.#state.turns.filter((t) => t.id !== turn.id) });
    await this.ask(turn.question);
  }

  /**
   * Ask the shown, withheld question again so its answer may name the pages
   * marked Only on this Mac. Those pages reach the AI model then, so nothing is
   * sent unless the settings, read now, say the model runs on this Mac.
   */
  async showPrivate(): Promise<void> {
    const client = this.#client;
    const turn = this.#state.turns.find((t) => t.id === this.#state.shown);
    if (client === null || this.#state.running || turn === undefined) return;
    if (turn.result.kind !== "answer" || turn.result.answer.outcome !== "withheld") return;
    this.#set({ running: true });
    let local: boolean;
    try {
      local = (await client.modelSettings()).is_local;
    } catch (error) {
      this.#settle(turn.id, { kind: "problem", problem: problemFor(error) });
      this.#set({ running: false });
      return;
    }
    if (!local) {
      this.#settle(turn.id, { kind: "problem", problem: MODEL_NOT_HERE });
      this.#set({ running: false });
      return;
    }
    this.#set({ running: false, turns: this.#state.turns.filter((t) => t.id !== turn.id) });
    await this.ask(turn.question, true);
  }

  /** Show an earlier question and how it went. */
  show(id: number): void {
    if (this.#state.turns.some((turn) => turn.id === id)) this.#set({ shown: id });
  }
}
