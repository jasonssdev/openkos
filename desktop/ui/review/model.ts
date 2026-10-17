// The review screen's state and every transition (ADR-0066). No DOM: the
// render layer subscribes and draws. Every word comes from wording.ts; every
// call goes through the narrow ReviewClient, so tests mock exactly what the
// screen uses.
import { ApiError, isBusy, isStale, retryWhileBusy, type Client, type Schemas } from "../../client/index.ts";
import {
  CHANGED,
  CHANGED_CHECK,
  DECIDE_LATER,
  checkConsent,
  checkProgress,
  checkResult,
  confirmation,
  historyLine,
  identityShape,
  previewLines,
  problemFor,
  splitDone,
  splitQuestion,
  undoneLine,
  type Consent,
  type Option,
  type Problem,
} from "./wording.ts";

type Decision = Schemas["Decision"];
type Page = Schemas["Page"];
type Action = Schemas["Action"];
type CheckKind = Schemas["QuoteRequest"]["kind"];

export type ReviewClient = Pick<
  Client,
  | "decisions"
  | "readPage"
  | "previewDecision"
  | "applyDecision"
  | "quoteJudging"
  | "startJudgingRun"
  | "judgingRun"
  | "stopJudgingRun"
  | "mergedConcepts"
  | "refreshQueue"
  | "previewUnmerge"
  | "applyUnmerge"
  | "previewReopen"
  | "applyReopen"
  | "previewUnrelate"
  | "applyUnrelate"
  | "previewTierChange"
  | "applyTierChange"
>;

type Tier = Schemas["TierChangePreviewRequest"]["tier"];
const TIERS: readonly string[] = ["static", "slow", "volatile"] satisfies Exclude<Tier, null>[];

/** What a confirmation's Undo reverses, taken from the previewed plan. */
export type Undo =
  | { kind: "split"; survivor: string; absorbed: string }
  | { kind: "reopen"; members: string[] }
  | { kind: "unrelate"; source: string; relationType: string; target: string }
  | { kind: "tier"; typeName: string; tier: Tier };

export interface ModelOptions {
  sleep?: (ms: number) => Promise<void>;
  /** Milliseconds, for the busy retry's deadline. */
  now?: () => number;
  /** Today, for the dates on screen. */
  today?: () => Date;
}

/** How long to wait between two reads of a running check. */
export const POLL_MS = 1000;

export interface Selection {
  /** The pages the person marked as the same thing, in member order. */
  marked: string[];
  /** The page whose name the combined page keeps; chosen by the person. */
  name: string | null;
  /** For pages of different kinds: the kind the person chose. */
  okfType: string | null;
}

export type Step =
  | { name: "choosing" }
  | { name: "previewing"; action: Action }
  | { name: "preview"; action: Action; preview: Schemas["DecisionPreview"]; lines: string[] }
  | { name: "applying"; action: Action; preview: Schemas["DecisionPreview"]; lines: string[] }
  | { name: "done"; lines: string[]; undo: Undo | null }
  | { name: "undoing"; lines: string[]; undo: Exclude<Undo, { kind: "split" }> };

export interface OpenDecision {
  key: string;
  decision: Decision;
  selection: Selection;
  step: Step;
  /** The brief's "changed" notice, when the pages changed under the person. */
  changed: string | null;
  problem: Problem | null;
}

export interface CheckState {
  phase: "quoting" | "consent" | "running" | "stopping" | "done" | "problem";
  consent: Consent | null;
  /** Progress, the result, or the reason the consent was shown again. */
  text: string | null;
  problem: Problem | null;
}

export interface SplitState {
  phase: "previewing" | "confirm" | "applying" | "done" | "problem";
  survivor: string;
  absorbed: string;
  text: string | null;
  problem: Problem | null;
}

export interface CombinedPage {
  survivor: string;
  title: string;
  lines: { text: string; absorbed: string }[];
}

export interface Notice {
  tone: "info" | "done" | "warning" | "error";
  text: string;
}

export interface ReviewState {
  loading: boolean;
  loaded: boolean;
  decisions: Decision[];
  pages: ReadonlyMap<string, Page>;
  listProblem: Problem | null;
  open: OpenDecision | null;
  check: CheckState | null;
  split: SplitState | null;
  /** Every combined page, newest combine first, with its history. */
  combined: CombinedPage[];
  /** The detail shows the combined pages, in place of a decision. */
  combinedOpen: boolean;
  notice: Notice | null;
}

const GONE = "These pages changed, and this question no longer applies.";

type Operation = { kind: "preview"; action: Action } | { kind: "apply" } | { kind: "undo" };

/** Waiting for a check: an unjudged pair, or any decision whose pages changed. */
function waitsForCheck(decision: Decision): boolean {
  return (decision.kind === "identity" && decision.evaluation === "pending_evaluation") || decision.fresh === false;
}

/** Whether To review offers Check all: some pair is unjudged, or changed. */
export function offersCheckAll(decisions: readonly Decision[]): boolean {
  return decisions.some((d) => d.kind === "identity" && (d.evaluation === "pending_evaluation" || d.fresh === false));
}

/** The ids of the pages a decision shows. */
function pageIds(decision: Decision): string[] {
  const detail = decision.detail;
  if (detail && "member_ids" in detail) return detail.member_ids;
  if (detail && "effective_source_id" in detail) return [detail.effective_source_id, detail.effective_target_id];
  return decision.targets;
}

function members(decision: Decision): string[] {
  const detail = decision.detail;
  return detail && "member_ids" in detail ? detail.member_ids : decision.targets;
}

export class ReviewModel {
  #state: ReviewState = {
    loading: false,
    loaded: false,
    decisions: [],
    pages: new Map(),
    listProblem: null,
    open: null,
    check: null,
    split: null,
    combined: [],
    combinedOpen: false,
    notice: null,
  };
  readonly #listeners = new Set<(state: ReviewState) => void>();
  readonly #client: ReviewClient;
  readonly #sleep: (ms: number) => Promise<void>;
  readonly #now: () => number;
  readonly #today: () => Date;
  #lastOp: Operation | null = null;
  #quote: Schemas["JudgingQuote"] | null = null;
  #runId: string | null = null;
  #splitPreview: Schemas["UnmergePreview"] | null = null;

  constructor(client: ReviewClient, options: ModelOptions = {}) {
    this.#client = client;
    this.#sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.#now = options.now ?? (() => Date.now());
    this.#today = options.today ?? (() => new Date());
  }

  get state(): ReviewState {
    return this.#state;
  }

  subscribe(listener: (state: ReviewState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #set(patch: Partial<ReviewState>): void {
    this.#state = { ...this.#state, ...patch };
    for (const listener of this.#listeners) listener(this.#state);
  }

  #setOpen(patch: Partial<OpenDecision>): void {
    if (this.#state.open !== null) this.#set({ open: { ...this.#state.open, ...patch } });
  }

  #retry<T>(call: () => Promise<T>): Promise<T> {
    return retryWhileBusy(call, { sleep: this.#sleep, now: this.#now });
  }

  // -- the list ----------------------------------------------------------------- //

  /**
   * A visit to To review: the queue is refreshed first, with no AI model, so a
   * decision about a page that no longer exists is gone and a changed one whose
   * check is already done is current; then everything is listed. A refresh
   * that fails is ignored: the list is read either way.
   */
  async visit(): Promise<void> {
    await this.#refreshQueue();
    await this.load();
  }

  #refreshQueue(): Promise<unknown> {
    return this.#client.refreshQueue().catch(() => undefined);
  }

  /**
   * Lists the open decisions and every combined page, and reads every page
   * they show. A combined list that cannot be read leaves the last one shown.
   */
  async load(): Promise<void> {
    this.#set({ loading: true });
    try {
      const [{ decisions }, merged] = await Promise.all([
        this.#client.decisions(),
        this.#client.mergedConcepts().catch(() => null),
      ]);
      const survivors = merged?.concepts.map((c) => c.concept_id) ?? [];
      const ids = [...new Set([...decisions.flatMap(pageIds), ...survivors])];
      const read = await Promise.all(ids.map((id) => this.#client.readPage(id).catch(() => null)));
      const pages = new Map<string, Page>();
      for (const page of read) if (page !== null) pages.set(page.concept_id, page);
      const combined = merged === null ? this.#state.combined : this.#combinedFrom(merged.concepts, pages);
      this.#set({ loading: false, loaded: true, decisions, pages, combined, listProblem: null });
    } catch (error) {
      this.#set({ loading: false, listProblem: problemFor(error, "before") });
    }
  }

  #combinedFrom(concepts: Schemas["MergedConcept"][], pages: ReadonlyMap<string, Page>): CombinedPage[] {
    const today = this.#today();
    return concepts.map((c) => ({
      survivor: c.concept_id,
      title: pages.get(c.concept_id)?.title ?? "",
      lines: c.merges.map((m) => ({ text: historyLine(m, today), absorbed: m.absorbed_id })),
    }));
  }

  dismissNotice(): void {
    this.#set({ notice: null });
  }

  // -- one decision --------------------------------------------------------------- //

  open(key: string): void {
    const decision = this.#state.decisions.find((d) => d.decision_key === key);
    if (decision === undefined) return;
    const shape = decision.kind === "identity" ? identityShape(decision) : "pair";
    this.#lastOp = null;
    this.#set({
      notice: null,
      combinedOpen: false,
      open: {
        key,
        decision,
        selection: { marked: shape === "group" ? [] : [...members(decision)], name: null, okfType: null },
        step: { name: "choosing" },
        changed: decision.fresh === false ? CHANGED_CHECK : null,
        problem: null,
      },
    });
  }

  close(): void {
    this.#set({ open: null, combinedOpen: false });
  }

  /** Shows every combined page, each with Split again, in place of a decision. */
  showCombined(): void {
    this.#set({ open: null, notice: null, combinedOpen: true });
  }

  toggleMark(id: string): void {
    const open = this.#state.open;
    if (open === null || !members(open.decision).includes(id)) return;
    const marked = open.selection.marked.includes(id)
      ? open.selection.marked.filter((m) => m !== id)
      : members(open.decision).filter((m) => m === id || open.selection.marked.includes(m));
    const name = open.selection.name !== null && marked.includes(open.selection.name) ? open.selection.name : null;
    this.#setOpen({ selection: { ...open.selection, marked, name } });
  }

  /** The pages that may give the combined page its name. */
  namesFor(): string[] {
    const open = this.#state.open;
    if (open === null) return [];
    const { marked, okfType } = open.selection;
    return okfType === null ? marked : this.namesForKind();
  }

  /** The marked pages of the chosen kind. */
  namesForKind(): string[] {
    const open = this.#state.open;
    if (open === null) return [];
    const { marked, okfType } = open.selection;
    return marked.filter((id) => this.#state.pages.get(id)?.okf_type === okfType);
  }

  chooseName(id: string): void {
    const open = this.#state.open;
    if (open === null || !this.namesFor().includes(id)) return;
    this.#setOpen({ selection: { ...open.selection, name: id } });
  }

  /** "Combine the ones I marked" needs two marks and a name among them. */
  canCombineMarked(): boolean {
    const selection = this.#state.open?.selection;
    if (selection === undefined) return false;
    // `chooseName` and `toggleMark` keep the name among the marked pages.
    return selection.marked.length >= 2 && selection.name !== null;
  }

  /** The person picked an option of the open decision. */
  async choose(option: Option): Promise<void> {
    const open = this.#state.open;
    if (open === null) return;
    switch (option.choice) {
      case "later":
      case "skip":
        // The engine's skip changes nothing; nothing is sent.
        this.#set({ open: null, notice: { tone: "info", text: DECIDE_LATER.does } });
        return;
      case "check":
        await this.startCheck();
        return;
      case "recheck":
        await this.recheck();
        return;
      case "merge_group":
        if (option.okfType !== undefined && !this.#chooseKind(option.okfType)) return;
        if (!this.canCombineMarked()) return;
        await this.#preview("merge_group");
        return;
      default:
        await this.#preview(option.choice);
    }
  }

  /** Sets the chosen kind; true when the name is settled and the preview can run. */
  #chooseKind(okfType: string): boolean {
    const open = this.#state.open;
    if (open === null) return false;
    const kept = open.selection.okfType === okfType ? open.selection.name : null;
    this.#setOpen({ selection: { ...open.selection, okfType, name: kept } });
    const names = this.namesForKind();
    if (names.length === 1) this.chooseName(names[0]!);
    return this.#state.open?.selection.name !== null;
  }

  #selectionFor(action: Action): Schemas["GroupSelection"] | undefined {
    const selection = this.#state.open?.selection;
    if (action !== "merge_group" || selection?.name == null) return undefined;
    return { survivor_id: selection.name, member_ids: selection.marked };
  }

  async #preview(action: Action, afterStale = false): Promise<void> {
    const open = this.#state.open;
    if (open === null) return;
    this.#lastOp = { kind: "preview", action };
    this.#setOpen({ step: { name: "previewing", action }, problem: null });
    const selection = this.#selectionFor(action);
    try {
      const preview = await this.#client.previewDecision(
        open.key,
        selection === undefined ? { action } : { action, selection },
      );
      const lines = previewLines(open.decision, preview.plan, this.#state.pages);
      this.#setOpen({ step: { name: "preview", action, preview, lines } });
    } catch (error) {
      if (isStale(error) && !afterStale) {
        await this.#reloadAndPreview(action);
        return;
      }
      this.#setOpen({ step: { name: "choosing" }, problem: problemFor(error, "before") });
    }
  }

  /** The pages changed: list again and preview the same choice, never apply. */
  async #reloadAndPreview(action: Action): Promise<void> {
    const key = this.#state.open?.key;
    await this.load();
    const decision = this.#state.decisions.find((d) => d.decision_key === key);
    if (decision === undefined) {
      this.#set({ open: null, notice: { tone: "warning", text: GONE } });
      return;
    }
    if (decision.fresh === false) {
      // Its preview would be stale too: it is checked again before anything else.
      this.#setOpen({ decision, changed: CHANGED_CHECK, step: { name: "choosing" }, problem: null });
      return;
    }
    this.#setOpen({ decision, changed: CHANGED });
    await this.#preview(action, true);
  }

  /** From a preview back to the choice; nothing is applied. */
  back(): void {
    if (this.#state.open?.step.name === "preview") this.#setOpen({ step: { name: "choosing" }, problem: null });
  }

  /** Applies the previewed choice against its version, unchanged. */
  async confirm(): Promise<void> {
    const open = this.#state.open;
    if (open === null || open.step.name !== "preview") return;
    const { action, preview, lines } = open.step;
    this.#lastOp = { kind: "apply" };
    this.#setOpen({ step: { name: "applying", action, preview, lines }, problem: null });
    try {
      const { applied } = await this.#retry(() =>
        this.#client.applyDecision(open.key, { action, version: preview.version }),
      );
      const undo = undoFor(preview.plan, applied);
      this.#setOpen({
        step: { name: "done", lines: confirmation(open.decision, applied, this.#state.pages), undo },
        changed: null,
      });
      await this.load();
    } catch (error) {
      if (isStale(error)) {
        await this.#reloadAndPreview(action);
        return;
      }
      const when = isBusy(error) || (error instanceof ApiError && error.code === "record_failed") ? "before" : "after";
      this.#setOpen({ step: { name: "preview", action, preview, lines }, problem: problemFor(error, when) });
    }
  }

  /** The one action a decision's problem offers. */
  async resolveProblem(): Promise<void> {
    const open = this.#state.open;
    if (open?.problem == null) return;
    const op = this.#lastOp;
    switch (open.problem.action) {
      case "try_again":
        this.#setOpen({ problem: null });
        if (op?.kind === "apply") await this.confirm();
        else if (op?.kind === "undo") await this.undo();
        else if (op?.kind === "preview") await this.#preview(op.action);
        return;
      case "ok":
        this.#set({ open: null });
        await this.load();
        return;
      default: {
        await this.load();
        const decision = this.#state.decisions.find((d) => d.decision_key === open.key);
        if (decision === undefined || op?.kind === "apply" || op?.kind === "undo") {
          this.#set({ open: null });
          return;
        }
        this.open(open.key);
      }
    }
  }

  /** After a check, the open decision is shown as it now is, from its choice. */
  #refreshOpen(): void {
    const open = this.#state.open;
    if (open === null) return;
    const decision = this.#state.decisions.find((d) => d.decision_key === open.key);
    if (decision !== undefined) {
      const changed = decision.fresh === false ? CHANGED_CHECK : null;
      this.#setOpen({ decision, changed, step: { name: "choosing" }, problem: null });
    }
  }

  /**
   * The confirmation's Undo. A combine's is Split again on its first absorbed
   * page. The others preview the reversal and apply it at once, echoing the
   * version: the person just saw the choice, and the undo puts back what was
   * there. A busy apply is retried; anything else is worded and never applied
   * again blindly.
   */
  async undo(): Promise<void> {
    const step = this.#state.open?.step;
    if (step?.name !== "done" || step.undo === null) return;
    const undo = step.undo;
    if (undo.kind === "split") {
      await this.openSplit(undo.survivor, undo.absorbed);
      return;
    }
    this.#lastOp = { kind: "undo" };
    this.#setOpen({ step: { name: "undoing", lines: step.lines, undo }, problem: null });
    let sent = false;
    try {
      const apply = await this.#previewUndo(undo);
      sent = true;
      await this.#retry(apply);
    } catch (error) {
      const when = !sent || isBusy(error) ? "before" : "after";
      const problem = problemFor(error, when);
      const kept = problem.action === "try_again" ? undo : null;
      this.#setOpen({ step: { name: "done", lines: step.lines, undo: kept }, problem });
      return;
    }
    this.#setOpen({ step: { name: "done", lines: [undoneLine(undo)], undo: null } });
    if (undo.kind === "reopen") await this.#client.refreshQueue().catch(() => undefined);
    await this.load();
  }

  /** Previews an undo; returns the apply of exactly that version. */
  async #previewUndo(undo: Exclude<Undo, { kind: "split" }>): Promise<() => Promise<unknown>> {
    const client = this.#client;
    switch (undo.kind) {
      case "reopen": {
        const { version } = await client.previewReopen({ member_ids: undo.members });
        return () => client.applyReopen({ version });
      }
      case "unrelate": {
        const { version } = await client.previewUnrelate({
          source_id: undo.source,
          relation_type: undo.relationType,
          target_id: undo.target,
        });
        return () => client.applyUnrelate({ version });
      }
      case "tier": {
        const { version } = await client.previewTierChange({ type_name: undo.typeName, tier: undo.tier });
        return () => client.applyTierChange({ version });
      }
    }
  }

  // -- the check -------------------------------------------------------------------- //

  /**
   * Refreshes the queue (no AI model) and lists again, so a changed decision
   * whose check is already done is not checked twice, then quotes the batch of
   * `kind` and shows the consent; nothing runs yet.
   */
  async startCheck(text: string | null = null, kind: CheckKind = "identity"): Promise<void> {
    this.#set({ check: { phase: "quoting", consent: null, text, problem: null } });
    await this.#refreshQueue();
    await this.load();
    this.#refreshOpen();
    await this.#quoteCheck(text, kind);
  }

  async #quoteCheck(text: string | null, kind: CheckKind): Promise<void> {
    this.#set({ check: { phase: "quoting", consent: null, text, problem: null } });
    try {
      this.#quote = await this.#client.quoteJudging({ kind });
      this.#set({ check: { phase: "consent", consent: checkConsent(this.#quote), text, problem: null } });
    } catch (error) {
      this.#checkProblem(problemFor(error, "before"));
    }
  }

  /**
   * Check again, for the changed decision on screen: refresh the queue and list
   * again, which costs nothing. Gone, it is said so; current, it is shown as it
   * now is; still changed, its kind is quoted and the person is asked first.
   */
  async recheck(): Promise<void> {
    const key = this.#state.open?.key;
    if (key === undefined) return;
    this.#set({ check: { phase: "quoting", consent: null, text: null, problem: null } });
    await this.#refreshQueue();
    await this.load();
    const decision = this.#state.decisions.find((d) => d.decision_key === key);
    if (decision === undefined) {
      this.#set({ check: null, open: null, notice: { tone: "warning", text: GONE } });
      return;
    }
    this.#refreshOpen();
    if (decision.fresh !== false) {
      this.#set({ check: null });
      return;
    }
    await this.#quoteCheck(null, decision.kind as CheckKind);
  }

  #checkProblem(problem: Problem): void {
    const check = this.#state.check;
    this.#set({ check: { phase: "problem", consent: check?.consent ?? null, text: null, problem } });
  }

  #checkText(phase: CheckState["phase"], text: string): void {
    this.#set({ check: { phase, consent: this.#state.check?.consent ?? null, text, problem: null } });
  }

  /** The person agreed: run against the quote's token, follow it to the end. */
  async agreeCheck(): Promise<void> {
    const quote = this.#quote;
    if (quote === null || this.#state.check?.phase !== "consent" || quote.to_judge === 0) return;
    const unjudged = this.#state.decisions.filter(waitsForCheck).map((d) => d.decision_key);
    let status: Schemas["RunStatus"];
    try {
      status = await this.#client.startJudgingRun({ token: quote.token });
    } catch (error) {
      if (error instanceof ApiError && error.code === "quote_stale") {
        await this.startCheck(problemFor(error, "before").message);
        return;
      }
      this.#checkProblem(problemFor(error, "before"));
      return;
    }
    this.#runId = status.run_id;
    const total = status.total ?? quote.to_judge;
    this.#checkText("running", checkProgress(status.index ?? 0, total, false));
    while (status.state === "running" || status.state === "stopping") {
      await this.#sleep(POLL_MS);
      try {
        status = await this.#client.judgingRun(status.run_id);
      } catch (error) {
        this.#checkProblem(problemFor(error, "after"));
        return;
      }
      // `stopCheck` may have run during the wait.
      const stopping = status.state === "stopping" || this.#checkPhase() === "stopping";
      if (status.state === "running" || status.state === "stopping") {
        this.#checkText(stopping ? "stopping" : "running", checkProgress(status.index ?? 0, status.total ?? total, stopping));
      }
    }
    this.#runId = null;
    // The run's verdicts reach a changed decision only through the refresh.
    await this.#refreshQueue();
    await this.load();
    this.#refreshOpen();
    if (status.state === "failed" || status.outcome === null) {
      const error = status.error ?? { code: "internal_error", retryable: false, message: "" };
      this.#checkProblem(problemFor(new ApiError({ status: 0, ...error, retryAfterSeconds: null }), "after"));
      return;
    }
    const ready = unjudged.filter((key) => {
      const now = this.#state.decisions.find((d) => d.decision_key === key);
      return now !== undefined && !waitsForCheck(now);
    }).length;
    this.#checkText("done", checkResult(status.outcome.judged, ready));
  }

  #checkPhase(): CheckState["phase"] | undefined {
    return this.#state.check?.phase;
  }

  /** Asks the running check to stop after the current one. */
  async stopCheck(): Promise<void> {
    const runId = this.#runId;
    if (runId === null || this.#state.check?.phase !== "running") return;
    this.#checkText("stopping", checkProgress(0, 0, true));
    try {
      await this.#client.stopJudgingRun(runId);
    } catch {
      // The run keeps being followed; its end is reported either way.
    }
  }

  async resolveCheckProblem(): Promise<void> {
    const problem = this.#state.check?.problem;
    if (problem == null) return;
    if (problem.action === "try_again" || problem.action === "requote") await this.startCheck();
    else this.closeCheck();
  }

  /** Closes the consent or the result; a running check is never abandoned. */
  closeCheck(): void {
    const phase = this.#state.check?.phase;
    if (phase === "running" || phase === "stopping") return;
    this.#quote = null;
    this.#set({ check: null });
  }

  // -- split again ------------------------------------------------------------------ //

  async openSplit(survivor: string, absorbed: string, afterStale = false): Promise<void> {
    this.#set({ split: { phase: "previewing", survivor, absorbed, text: null, problem: null } });
    try {
      // Never `discard_survivor_edits`: an edited page is refused, not offered (#1442).
      this.#splitPreview = await this.#client.previewUnmerge({ survivor_id: survivor, absorbed_id: absorbed });
      this.#set({
        split: { phase: "confirm", survivor, absorbed, text: splitQuestion(this.#splitPreview.steps), problem: null },
      });
    } catch (error) {
      if (isStale(error) && !afterStale) {
        await this.openSplit(survivor, absorbed, true);
        return;
      }
      this.#set({ split: { phase: "problem", survivor, absorbed, text: null, problem: problemFor(error, "before") } });
    }
  }

  async confirmSplit(): Promise<void> {
    const split = this.#state.split;
    const preview = this.#splitPreview;
    if (split === null || split.phase !== "confirm" || preview === null) return;
    this.#set({ split: { ...split, phase: "applying", problem: null } });
    try {
      await this.#retry(() => this.#client.applyUnmerge({ version: preview.version }));
      const titles = preview.steps.map((s) => s.absorbed_title ?? "a page");
      this.#set({ split: { ...split, phase: "done", text: splitDone(titles), problem: null } });
      const step = this.#state.open?.step;
      if (step?.name === "done" && step.undo?.kind === "split" && step.undo.survivor === split.survivor) {
        this.#setOpen({ step: { ...step, undo: null } });
      }
      await this.load();
    } catch (error) {
      if (isStale(error)) {
        await this.openSplit(split.survivor, split.absorbed, true);
        return;
      }
      const when = isBusy(error) ? "before" : "after";
      this.#set({ split: { ...split, phase: "problem", problem: problemFor(error, when) } });
    }
  }

  async resolveSplitProblem(): Promise<void> {
    const split = this.#state.split;
    if (split?.problem == null) return;
    if (split.problem.action === "try_again" && this.#splitPreview !== null) {
      this.#set({ split: { ...split, phase: "confirm", problem: null } });
      await this.confirmSplit();
      return;
    }
    this.#set({ split: null });
    if (split.problem.action === "reload") await this.load();
  }

  closeSplit(): void {
    if (this.#state.split?.phase === "applying") return;
    this.#splitPreview = null;
    this.#set({ split: null });
  }
}

/** What a confirmation's Undo reverses, from the plan that was applied. */
function undoFor(plan: Schemas["PreviewPlan"], applied: Schemas["Applied"]): Undo | null {
  switch (plan.kind) {
    case "merge": {
      const first = applied.merged?.[0];
      return first === undefined ? null : { kind: "split", survivor: first[0], absorbed: first[1] };
    }
    case "keep_distinct":
      return { kind: "reopen", members: plan.member_ids };
    case "relate":
      return { kind: "unrelate", source: plan.source_id, relationType: plan.relation_type, target: plan.target_id };
    case "set_tier": {
      const before = plan.configured_tier;
      if (before !== null && !TIERS.includes(before)) return null;
      return { kind: "tier", typeName: plan.type_name, tier: before as Tier };
    }
  }
}
