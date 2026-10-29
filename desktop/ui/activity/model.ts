// The Activity screen's state, with no DOM. It reads what the engine is doing
// and, while the screen is open, reads again now and then. A failed read keeps
// what was shown and says so: activity is advisory, never an error page.
import type { Client, Schemas } from "../../client/index.ts";

export type ActivityClient = Pick<Client, "engineActivity">;

export const POLL_MS = 3000;

export interface ActivityState {
  /** What the engine reported; `null` until read. */
  activity: Schemas["EngineActivity"] | null;
  /** The last read failed. */
  failed: boolean;
}

export class ActivityModel {
  #state: ActivityState = { activity: null, failed: false };
  readonly #listeners = new Set<(state: ActivityState) => void>();
  #client: ActivityClient | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;

  get state(): ActivityState {
    return this.#state;
  }

  subscribe(listener: (state: ActivityState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #set(state: ActivityState): void {
    this.#state = state;
    for (const listener of this.#listeners) listener(state);
  }

  /** The engine of this launch, or none while it is not running. */
  async setClient(client: ActivityClient | null): Promise<void> {
    this.#client = client;
    if (client === null) {
      this.#set({ activity: null, failed: false });
      return;
    }
    await this.load();
  }

  async load(): Promise<void> {
    const client = this.#client;
    if (client === null) return;
    try {
      const activity = await client.engineActivity();
      if (!Array.isArray(activity?.recent) || typeof activity.budget?.calls_limit !== "number") throw new Error("unreadable");
      if (client === this.#client) this.#set({ activity, failed: false });
    } catch {
      if (client === this.#client) this.#set({ ...this.#state, failed: true });
    }
  }

  /** Read again on a timer while the screen is open, and not otherwise. */
  watch(open: boolean): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = open ? setInterval(() => void this.load(), POLL_MS) : null;
    if (open) void this.load();
  }
}
