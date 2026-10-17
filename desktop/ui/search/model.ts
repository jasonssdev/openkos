// Rebuilding search, as state and transitions with no DOM (ADR-0066). One
// model for the session, shown wherever search may be behind: Settings, Ask
// and Status. A rebuild is a job on the engine: start it, poll it until it
// ends, show where it is. With a cloud AI model for search it asks first, with
// the cloud warning's send rule, and only then agrees to send.
import { ApiError, type Client, type Schemas } from "../../client/index.ts";
import { SEARCH_WORDS, cloudWarning, doneLine, problemFor } from "./wording.ts";

export type SearchClient = Pick<Client, "searchIndex" | "startSearchRebuild" | "searchRebuild" | "stopSearchRebuild">;

export const POLL_MS = 1000;

type Status = Schemas["RebuildStatus"];

export interface SearchPorts {
  /** Asks the person; resolves `true` to go ahead. */
  confirm: (question: ReturnType<typeof cloudWarning>) => Promise<boolean>;
  sleep?: (ms: number) => Promise<void>;
}

export interface SearchState {
  /** Whether search needs rebuilding; `null` until read. */
  index: Schemas["SearchIndex"] | null;
  /** The rebuild on its way. */
  job: Status | null;
  /** The rebuild is being started. */
  starting: boolean;
  /** How the last rebuild ended. */
  result: { kind: "done" | "stopped" | "problem"; text: string } | null;
}

export class SearchModel {
  #state: SearchState = { index: null, job: null, starting: false, result: null };
  readonly #listeners = new Set<(state: SearchState) => void>();
  readonly #ports: SearchPorts;
  readonly #sleep: (ms: number) => Promise<void>;
  #client: SearchClient | null = null;

  constructor(ports: SearchPorts) {
    this.#ports = ports;
    this.#sleep = ports.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  }

  get state(): SearchState {
    return this.#state;
  }

  subscribe(listener: (state: SearchState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #set(patch: Partial<SearchState>): void {
    this.#state = { ...this.#state, ...patch };
    for (const listener of this.#listeners) listener(this.#state);
  }

  /** The engine of this launch, or none while it is not running. */
  async setClient(client: SearchClient | null): Promise<void> {
    this.#client = client;
    if (client === null) {
      this.#set({ index: null, job: null, starting: false });
      return;
    }
    await this.load();
  }

  /** Read again whether search needs rebuilding. A failed read keeps what was shown. */
  async load(): Promise<void> {
    const client = this.#client;
    if (client === null) return;
    try {
      const index = await client.searchIndex();
      if (client === this.#client) this.#set({ index });
    } catch {
      // Search's state is advisory: the screens keep working without it.
    }
  }

  /** Rebuild search, asking first when page text would leave this Mac. */
  async rebuild(): Promise<void> {
    const client = this.#client;
    if (client === null || this.#state.job !== null || this.#state.starting) return;
    const host = this.#state.index?.sends_to ?? null;
    this.#set({ starting: true, result: null });
    if (host !== null && !(await this.#ports.confirm(cloudWarning(host)))) {
      this.#set({ starting: false });
      return;
    }
    let result: SearchState["result"];
    try {
      result = await this.#follow(client, await client.startSearchRebuild({ allow_remote: host !== null }));
    } catch (error) {
      result = { kind: "problem", text: problemFor(error) };
    }
    this.#set({ job: null, starting: false, result });
    await this.load();
  }

  async #follow(client: SearchClient, started: Status): Promise<SearchState["result"]> {
    let job = started;
    this.#set({ starting: false, job });
    while (job.state === "running" || job.state === "stopping") {
      await this.#sleep(POLL_MS);
      job = await client.searchRebuild(job.rebuild_id);
      // A stop asked meanwhile keeps showing until the engine settles it.
      const stopping = this.#state.job?.state === "stopping" && job.state === "running";
      this.#set({ job: stopping ? { ...job, state: "stopping" } : job });
    }
    if (job.state === "finished" && job.outcome !== null) return { kind: "done", text: doneLine(job.outcome) };
    if (job.state === "stopped") return { kind: "stopped", text: SEARCH_WORDS.stopped };
    const error = job.error ?? { code: "internal_error", retryable: false, message: "" };
    return { kind: "problem", text: problemFor(new ApiError({ status: 0, ...error, retryAfterSeconds: null })) };
  }

  /** Ask the rebuild to stop after the page in flight. */
  async stop(): Promise<void> {
    const client = this.#client;
    const job = this.#state.job;
    if (client === null || job === null || job.state !== "running") return;
    this.#set({ job: { ...job, state: "stopping" } });
    try {
      await client.stopSearchRebuild(job.rebuild_id);
    } catch {
      // The poll reports how it ends either way.
    }
  }
}
