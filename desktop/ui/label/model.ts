// Who can see a page, as state and transitions with no DOM (ADR-0066). One
// model for the session: any screen that shows a page hands it the page and
// the label chosen, and it previews and applies in one step, as an undo does,
// because the person has just made the choice. Letting a page marked Only on
// this Mac out while a cloud AI model is on asks first. The levels it set are
// kept, so every screen shows them without reading the page again.
import { ApiError, type Client, retryWhileBusy } from "../../client/index.ts";
import { type Level, changedLine, lowerWarning, problemFor, undoneLine } from "./wording.ts";

export type LabelClient = Pick<Client, "previewSensitivity" | "applySensitivity">;

/** A page as a screen shows it: its id, its title and the level it read. */
export interface PageRef {
  conceptId: string;
  title: string;
  level: Level;
}

export interface LabelPorts {
  /** The host of the cloud AI model while one is on; `null` otherwise. */
  cloudHost: () => string | null;
  /** Asks the person; resolves `true` to go ahead. */
  confirm: (question: ReturnType<typeof lowerWarning>) => Promise<boolean>;
  sleep?: (ms: number) => Promise<void>;
}

export interface LabelState {
  /** The levels set this session, by page id. */
  levels: ReadonlyMap<string, Level>;
  /** The page whose label is being changed. */
  busy: string | null;
  /** The last change, and the level Undo puts back when it can. */
  notice: { conceptId: string; text: string; undo: Level | null } | null;
  problem: { conceptId: string; text: string } | null;
}

export class LabelModel {
  #state: LabelState = { levels: new Map(), busy: null, notice: null, problem: null };
  readonly #listeners = new Set<(state: LabelState) => void>();
  readonly #ports: LabelPorts;
  #client: LabelClient | null = null;
  #last: PageRef | null = null;

  constructor(ports: LabelPorts) {
    this.#ports = ports;
  }

  get state(): LabelState {
    return this.#state;
  }

  subscribe(listener: (state: LabelState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #set(patch: Partial<LabelState>): void {
    this.#state = { ...this.#state, ...patch };
    for (const listener of this.#listeners) listener(this.#state);
  }

  setClient(client: LabelClient | null): void {
    this.#client = client;
  }

  /** The page's level: the one set this session, or the one it was read with. */
  levelOf(page: PageRef): Level {
    return this.#state.levels.get(page.conceptId) ?? page.level;
  }

  /** Give `page` the label `level`. */
  async set(page: PageRef, level: Level): Promise<void> {
    const current = this.levelOf(page);
    if (current === level || this.#client === null || this.#state.busy !== null) return;
    const host = this.#ports.cloudHost();
    if (current === "confidential" && host !== null) {
      if (!(await this.#ports.confirm(lowerWarning(page.title, level, host)))) return;
    }
    const done = await this.#apply(page, level);
    if (done === null) return;
    // Undo puts back the level the engine read before the change, not the one the screen showed.
    this.#last = { ...page, level: done.before };
    const raised = done.raised;
    this.#set({
      busy: null,
      levels: done.levels,
      notice: { conceptId: page.conceptId, text: changedLine(page.title, level, raised), undo: raised === 0 ? done.before : null },
    });
  }

  /** Close the confirmation or the problem on screen; the levels stay as they are. */
  dismiss(): void {
    if (this.#state.notice !== null || this.#state.problem !== null) this.#set({ notice: null, problem: null });
  }

  /** Put back the level the last change replaced. */
  async undo(): Promise<void> {
    const notice = this.#state.notice;
    const page = this.#last;
    if (notice === null || notice.undo === null || page === null) return;
    const done = await this.#apply(page, notice.undo);
    if (done === null) return;
    this.#set({
      busy: null,
      levels: done.levels,
      notice: { conceptId: page.conceptId, text: undoneLine(page.title, notice.undo), undo: null },
    });
  }

  /**
   * Previews and applies, leaving `busy` set on success for the caller's one
   * update: the levels after, and how many other pages were raised. `null`
   * when nothing is left to report.
   */
  async #apply(page: PageRef, level: Level): Promise<{ levels: Map<string, Level>; raised: number; before: Level } | null> {
    const client = this.#client;
    if (client === null || this.#state.busy !== null) return null;
    this.#set({ busy: page.conceptId, problem: null, notice: null });
    let sent = false;
    try {
      const preview = await client.previewSensitivity({ concept_id: page.conceptId, level });
      sent = true;
      const applied = await retryWhileBusy(() => client.applySensitivity({ version: preview.version }), {
        ...(this.#ports.sleep ? { sleep: this.#ports.sleep } : {}),
      });
      const levels = new Map(this.#state.levels);
      levels.set(page.conceptId, level);
      for (const id of applied.changed.also_raised) levels.set(id, level);
      return { levels, raised: applied.changed.also_raised.length, before: preview.current };
    } catch (error) {
      if (error instanceof ApiError && error.code === "sensitivity_unchanged") {
        // Set meanwhile, elsewhere: the page already has the label asked for.
        const levels = new Map(this.#state.levels);
        levels.set(page.conceptId, level);
        this.#set({ busy: null, levels });
        return null;
      }
      this.#set({ busy: null, problem: { conceptId: page.conceptId, text: problemFor(error, sent) } });
      return null;
    }
  }
}
