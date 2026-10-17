// Every word the review screen shows, as pure functions of the API's data.
// docs/decision-wording.md is the authority: a sentence here that differs from
// the brief is a defect in one of them. Nothing here touches the DOM, reads the
// token or shows the engine's own text, a code, a Concept ID or a command.
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";

type Decision = Schemas["Decision"];
type Page = Schemas["Page"];
type Action = Schemas["Action"];

/** The pages a screen has read, by Concept ID. A missing entry could not be read. */
export type Pages = ReadonlyMap<string, Page>;

/** The brief's rule 1: never on screen. */
export const ENGINE_WORDS = [
  "merge",
  "concept",
  "bundle",
  "okf",
  "tier",
  "volatility",
  "provenance",
  "ingest",
  "adjudicate",
  "judge",
  "canonical",
  "survivor",
  "absorbed",
  "relation",
  "sensitivity",
  "workspace",
  "queue",
  "pending",
  "decision key",
  "token",
  "model call",
] as const;

// -- nouns, dates, pages ------------------------------------------------------ //

export interface Noun {
  one: string;
  many: string;
  withArticle: string;
}

const NOUNS: Record<string, [string, string]> = {
  Person: ["person", "people"],
  Organization: ["organization", "organizations"],
  Place: ["place", "places"],
  Event: ["event", "events"],
  Project: ["project", "projects"],
  Decision: ["decision", "decisions"],
  Procedure: ["how-to", "how-tos"],
  Concept: ["idea", "ideas"],
  Entity: ["thing", "things"],
  Insight: ["saved answer", "saved answers"],
  Source: ["file", "files"],
};

/** What a page is about, named from its OKF type; anything else is a thing. */
export function typeNoun(okfType: string | null | undefined): Noun {
  const [one, many] = NOUNS[okfType ?? ""] ?? ["thing", "things"];
  const article = /^[aeiou]/.test(one) ? "an" : "a";
  return { one, many, withArticle: `${article} ${one}` };
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** "12 October", with the year when it is not `now`'s; null when unreadable. */
export function formatDate(iso: string, now: Date): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const day = `${date.getDate()} ${MONTHS[date.getMonth()]}`;
  return date.getFullYear() === now.getFullYear() ? day : `${day} ${date.getFullYear()}`;
}

export function sourceLine(source: Schemas["PageSource"], now: Date): string {
  const added = source.added_at === null ? null : formatDate(source.added_at, now);
  return added === null ? `From: ${source.file_name}` : `From: ${source.file_name}, added ${added}`;
}

const WHO_CAN_SEE: Record<Page["sensitivity"], { label: string; meaning: string }> = {
  public: { label: "Shareable", meaning: "Fine to share or export." },
  private: { label: "Private", meaning: "Stays in your library. Sent to a cloud AI model only if you turn one on." },
  confidential: { label: "Only on this Mac", meaning: "Never sent anywhere, even with a cloud AI model on." },
};

export function whoCanSee(level: Page["sensitivity"]): { label: string; meaning: string } {
  return WHO_CAN_SEE[level];
}

const UNREADABLE_PAGE = "A page that couldn't be read";

function titleOf(pages: Pages, id: string): string {
  return pages.get(id)?.title || UNREADABLE_PAGE;
}

function quoted(titles: string[]): string {
  return titles.map((t) => `'${t}'`).join(", ");
}

// -- options ------------------------------------------------------------------ //

/** What a person can choose: an action the engine offers, the check, or later. */
export type Choice = Action | "check" | "later";

export interface Option {
  choice: Choice;
  label: string;
  does: string;
  /** For "It's a {kind}": the OKF type chosen. */
  okfType?: string;
}

export const DECIDE_LATER: Option = {
  choice: "later",
  label: "Decide later",
  does: "Nothing changes. This stays in To review.",
};

const CHECK_THESE: Option = {
  choice: "check",
  label: "Check these",
  does: "Checking doesn't change anything. You'll still decide.",
};

export const CHANGED =
  "Something about these pages changed since this was suggested. Here is the current version.";

export const CANNOT_ACT = "This can't be settled in the app yet.";

export interface PageView {
  title: string;
  /** What the page is about, with its article: "a person". */
  kind: string;
  excerpt: string;
  whoCanSee: { label: string; meaning: string } | null;
  sources: string[];
}

export interface DecisionView {
  question: string;
  /** The line above the question: the reason a combine is or is not offered. */
  lead: string | null;
  /** A connection's sentence, shown as the thing being asked about. */
  statement: string | null;
  /** Label/value rows, for "how quickly it goes out of date". */
  facts: [string, string][];
  /** The model's reason, in its own words, in quotation marks. */
  reason: string | null;
  pages: PageView[];
  options: Option[];
  changed: string | null;
  cannotAct: string | null;
}

function pageView(pages: Pages, id: string, now: Date): PageView {
  const page = pages.get(id);
  if (page === undefined) {
    return { title: UNREADABLE_PAGE, kind: "", excerpt: "", whoCanSee: null, sources: [] };
  }
  return {
    title: page.title || UNREADABLE_PAGE,
    kind: typeNoun(page.okf_type).withArticle,
    excerpt: page.excerpt,
    whoCanSee: whoCanSee(page.sensitivity),
    sources: page.sources.map((s) => sourceLine(s, now)),
  };
}

function modelReason(text: string | null | undefined): string | null {
  return text ? `“${text}”` : null;
}

// -- identity ----------------------------------------------------------------- //

export type IdentityShape = "pair" | "group" | "kinds";

function members(decision: Decision): string[] {
  const detail = decision.detail;
  return detail && "member_ids" in detail ? detail.member_ids : decision.targets;
}

/** Which identity question this is: a pair, a group to mark, or kinds to choose. */
export function identityShape(decision: Decision): IdentityShape {
  // The engine's reason decides, never the pages read: a page that could not
  // be read has no type, which must not turn a pair into a choice of kinds.
  const reasons = decision.withheld.map((w) => w.reason);
  if (reasons.includes("cross_type")) return "kinds";
  if (reasons.includes("more_than_two_members") || members(decision).length > 2) return "group";
  return "pair";
}

/** The distinct OKF types among the members, in member order. */
export function kindsAmong(decision: Decision, pages: Pages): string[] {
  const kinds: string[] = [];
  for (const id of members(decision)) {
    const type = pages.get(id)?.okf_type;
    if (type && !kinds.includes(type)) kinds.push(type);
  }
  return kinds;
}

function identityNoun(decision: Decision): Noun {
  const detail = decision.detail;
  return typeNoun(detail && "okf_type" in detail ? detail.okf_type : null);
}

function evaluationLead(decision: Decision, noun: Noun): string | null {
  switch (decision.evaluation) {
    case "judged_same":
      return `The AI model thinks these are the same ${noun.one}.`;
    case "judged_different":
      return `The AI model thinks these are different ${noun.many}.`;
    case "judged_uncertain":
      return "The AI model couldn't tell.";
    case "pending_evaluation":
      return "These haven't been checked yet.";
    default:
      return null;
  }
}

function identityView(decision: Decision, pages: Pages): Pick<DecisionView, "question" | "lead" | "options"> {
  const noun = identityNoun(decision);
  const shape = identityShape(decision);
  const offers = (action: Action) => decision.actions.includes(action);
  const options: Option[] = [];
  let question = `Are these the same ${noun.one}?`;
  if (decision.evaluation === "pending_evaluation") options.push(CHECK_THESE);
  if (offers("merge")) {
    options.push({ choice: "merge", label: "Combine them", does: "We'll make one page with everything from both." });
  }
  if (offers("merge_group") && shape === "kinds") {
    question = `What is ${titleOf(pages, members(decision)[0] ?? "")}?`;
    for (const okfType of kindsAmong(decision, pages)) {
      const kind = typeNoun(okfType);
      options.push({
        choice: "merge_group",
        label: `It's ${kind.withArticle}`,
        does: `We'll keep one page, as ${kind.withArticle}.`,
        okfType,
      });
    }
  } else if (offers("merge_group")) {
    question = "Which of these are the same?";
    options.push({
      choice: "merge_group",
      label: "Combine the ones I marked",
      does: "We'll make one page from the pages you marked.",
    });
  }
  if (offers("keep_distinct")) {
    const grouped = offers("merge_group");
    options.push({
      choice: "keep_distinct",
      label: grouped ? (shape === "kinds" ? "They're different things" : "Keep all separate") : "Keep separate",
      does: grouped && shape !== "kinds" ? "We won't suggest combining any of these again." : "We won't suggest combining these again.",
    });
  }
  return { question, lead: evaluationLead(decision, noun), options };
}

// -- connections -------------------------------------------------------------- //

const CONNECTIONS: Record<string, { clause: (a: string, b: string) => string; oneWay: boolean }> = {
  related_to: { clause: (a, b) => `${a} and ${b} are related`, oneWay: false },
  references: { clause: (a, b) => `${a} mentions ${b}`, oneWay: false },
  part_of: { clause: (a, b) => `${a} is part of ${b}`, oneWay: true },
  member_of: { clause: (a, b) => `${a} is a member of ${b}`, oneWay: true },
  depends_on: { clause: (a, b) => `${a} depends on ${b}`, oneWay: true },
  caused_by: { clause: (a, b) => `${a} was caused by ${b}`, oneWay: true },
  produced_by: { clause: (a, b) => `${a} was made by ${b}`, oneWay: true },
};

function connectionClause(decision: Decision, pages: Pages, reversed: boolean): string | null {
  const detail = decision.detail;
  if (!detail || !("suggested_type" in detail)) return null;
  const a = titleOf(pages, detail.effective_source_id);
  const b = titleOf(pages, detail.effective_target_id);
  const clause = (CONNECTIONS[detail.suggested_type] ?? CONNECTIONS["related_to"]!).clause;
  return reversed ? clause(b, a) : clause(a, b);
}

function connectionView(decision: Decision, pages: Pages): Pick<DecisionView, "question" | "statement" | "options"> {
  const clause = connectionClause(decision, pages, false);
  const options: Option[] = [];
  if (clause !== null && decision.actions.includes("relate")) {
    options.push({ choice: "relate", label: "Yes, connect them", does: `We'll record that ${clause}.` });
  }
  if (clause !== null && decision.actions.includes("relate_reversed")) {
    const reversed = connectionClause(decision, pages, true);
    options.push({ choice: "relate_reversed", label: `No — ${reversed}.`, does: "We'll record that instead." });
  }
  return { question: "Is this right?", statement: clause === null ? null : `${clause}.`, options };
}

// -- how quickly it goes out of date --------------------------------------------- //

const TIERS: Record<string, { line: string; adverb: string; changes: string }> = {
  static: { line: "Rarely — they stay true.", adverb: "rarely", changes: "We won't remind you to recheck them." },
  slow: {
    line: "Slowly — over months or years.",
    adverb: "slowly",
    changes: "We'll remind you to recheck them now and then.",
  },
  volatile: {
    line: "Quickly — they can change within weeks.",
    adverb: "quickly",
    changes: "We'll remind you to recheck them often.",
  },
};

function tierSentence(typeName: string, tier: string): string {
  const adverb = TIERS[tier]?.adverb ?? "at their own pace";
  return `From now on, ${typeNoun(typeName).many} are treated as changing ${adverb}.`;
}

function tierView(decision: Decision): Pick<DecisionView, "question" | "facts" | "options"> {
  const detail = decision.detail;
  if (!detail || !("suggested_tier" in detail)) return { question: CANNOT_ACT, facts: [], options: [] };
  const noun = typeNoun(detail.type_name);
  const facts: [string, string][] = [
    ["Now", TIERS[detail.current_default]?.line ?? "Not set."],
    ["Suggested", TIERS[detail.suggested_tier]?.line ?? "Not set."],
  ];
  const options: Option[] = decision.actions.includes("set_tier")
    ? [
        {
          choice: "set_tier",
          label: "Use this",
          does: [tierSentence(detail.type_name, detail.suggested_tier), TIERS[detail.suggested_tier]?.changes]
            .filter(Boolean)
            .join(" "),
        },
      ]
    : [];
  return { question: `How quickly do facts about ${noun.many} go out of date?`, facts, options };
}

// -- one decision ------------------------------------------------------------- //

function listedOnly(decision: Decision, pages: Pages): string {
  switch (decision.kind) {
    case "contradiction": {
      const subject = decision.targets[0];
      return subject === undefined
        ? "Two pages say different things."
        : `Two pages say different things about ${titleOf(pages, subject)}.`;
    }
    case "revision":
      return "A newer file may replace an older one.";
    case "watch_refusal":
      return "A file was not added.";
    default:
      return CANNOT_ACT;
  }
}

function reasonOf(decision: Decision): string | null {
  const detail = decision.detail;
  return detail && "rationale" in detail ? modelReason(detail.rationale) : null;
}

/** Everything one decision's screen says, before any preview. */
export function describeDecision(decision: Decision, pages: Pages, now: Date = new Date()): DecisionView {
  const base: DecisionView = {
    question: "",
    lead: null,
    statement: null,
    facts: [],
    reason: reasonOf(decision),
    pages: (decision.kind === "identity" ? members(decision) : decision.targets).map((id) => pageView(pages, id, now)),
    options: [],
    changed: decision.fresh === false ? CHANGED : null,
    cannotAct: null,
  };
  let view: DecisionView;
  if (decision.kind === "identity") view = { ...base, ...identityView(decision, pages) };
  else if (decision.kind === "relation_type") view = { ...base, ...connectionView(decision, pages) };
  else if (decision.kind === "volatility") view = { ...base, ...tierView(decision) };
  else view = { ...base, question: listedOnly(decision, pages) };
  if (view.options.length === 0) return { ...view, options: [], cannotAct: CANNOT_ACT };
  return { ...view, options: [...view.options, DECIDE_LATER] };
}

export interface ListEntry {
  question: string;
  pages: string;
  changed: boolean;
  canAct: boolean;
}

/** One line of To review. */
export function listEntry(decision: Decision, pages: Pages): ListEntry {
  const view = describeDecision(decision, pages);
  return {
    question: view.statement ?? view.question,
    pages: view.pages.map((p) => p.title).join(" · "),
    changed: decision.fresh === false,
    canAct: view.cannotAct === null,
  };
}

// -- previews and confirmations ------------------------------------------------ //

function otherPages(n: number): string | null {
  if (n === 0) return null;
  return n === 1
    ? "1 other page that mentions it will point to the combined page."
    : `${n} other pages that mention it will point to the combined page.`;
}

function mergeLines(decision: Decision, plan: Schemas["MergePlan"], pages: Pages): string[] {
  const noun = identityNoun(decision);
  const name = titleOf(pages, plan.survivor_id);
  const shape = identityShape(decision);
  const others = otherPages(plan.files_rewritten);
  let first: string;
  if (shape === "kinds") {
    const kind = typeNoun(plan.survivor_type);
    const notes: string[] = [];
    for (const step of plan.steps) {
      if (step.absorbed_type === null || step.type_outcome === "same_type") continue;
      const other = typeNoun(step.absorbed_type).withArticle;
      notes.push(
        step.type_outcome === "recorded"
          ? `and remember it was also described as ${other}.`
          : `It was already noted as possibly something else, so we won't note ${other}.`,
      );
    }
    const recorded = notes.find((n) => n.startsWith("and "));
    first = `We'll keep one page, as ${kind.withArticle}, named '${name}'${recorded ? `, ${recorded}` : "."}`;
    for (const note of notes.filter((n) => !n.startsWith("and "))) first += ` ${note}`;
  } else if (decision.actions.includes("merge_group") && plan.kind === "merge") {
    const marked = plan.steps.length + 1;
    first = [`We'll make one page from the ${marked} pages you marked. It keeps the name '${name}'.`, others]
      .filter(Boolean)
      .join(" ");
    if (plan.kept_distinct_ids.length > 0) {
      first += ` The pages you didn't mark stay as they are, and we won't suggest combining them with '${name}' again.`;
    }
  } else {
    first = ["We'll make one page with everything from both.", `The page keeps the name '${name}'.`, others]
      .filter(Boolean)
      .join(" ");
  }
  const lines = [first];
  if (plan.cross_source_same_pair || plan.cross_type_concern !== null) {
    lines.push(`These came from different files: check that they really are the same ${noun.one}.`);
  }
  return lines;
}

/** What the previewed plan will do, from the plan itself. */
export function previewLines(decision: Decision, plan: Schemas["PreviewPlan"], pages: Pages): string[] {
  switch (plan.kind) {
    case "merge":
      return mergeLines(decision, plan, pages);
    case "keep_distinct":
      return [
        plan.member_ids.length > 2
          ? "We won't suggest combining any of these again."
          : "We won't suggest combining these again.",
      ];
    case "relate": {
      const reversed = plan.source_id !== connectionSource(decision);
      return [`We'll record that ${connectionClause(decision, pages, reversed)}.`];
    }
    case "set_tier":
      return [tierSentence(plan.type_name, plan.tier)];
    default: {
      const never: never = plan;
      return never;
    }
  }
}

function connectionSource(decision: Decision): string | null {
  const detail = decision.detail;
  return detail && "effective_source_id" in detail ? detail.effective_source_id : null;
}

/** The confirmation after apply, in the past tense. */
export function confirmation(decision: Decision, applied: Schemas["Applied"], pages: Pages): string[] {
  const lines: string[] = [];
  switch (applied.action) {
    case "merge":
    case "merge_group": {
      const survivor = applied.merged?.[0]?.[0];
      lines.push(`Combined into '${survivor === undefined ? UNREADABLE_PAGE : titleOf(pages, survivor)}'.`);
      if (applied.committed_confidential) lines.push("The combined page is marked Only on this Mac.");
      break;
    }
    case "keep_distinct":
      lines.push("Kept separate.");
      break;
    case "relate":
    case "relate_reversed":
      lines.push(`We recorded that ${connectionClause(decision, pages, applied.action === "relate_reversed")}.`);
      break;
    case "set_tier": {
      const detail = decision.detail;
      if (detail && "suggested_tier" in detail) lines.push(tierSentence(detail.type_name, detail.suggested_tier));
      break;
    }
    default:
      lines.push("Done.");
  }
  return lines;
}

// -- the check ---------------------------------------------------------------- //

export const CHECK_NEVER_COMBINES = "Checking never combines anything. You'll still decide each one.";

export interface Consent {
  heading: string;
  lines: string[];
  /** The agreeing button, or null when there is nothing to check. */
  agree: string | null;
  decline: string;
}

export function checkConsent(quote: Schemas["JudgingQuote"]): Consent {
  const n = quote.to_judge;
  if (n === 0) return { heading: "These are already checked.", lines: [], agree: null, decline: "OK" };
  const lines = [
    quote.binding.is_local
      ? "The AI model on this Mac will compare them. Nothing leaves this Mac."
      : `The text of these pages will be sent to the provider of ${quote.binding.model} to compare them. Pages marked 'Only on this Mac' are not sent. The provider may charge you.`,
  ];
  if (quote.served > 0) {
    lines.push(
      quote.served === 1
        ? "1 is already checked and won't be checked again."
        : `${quote.served} are already checked and won't be checked again.`,
    );
  }
  if (quote.notices.some((notice) => notice.code === "unattended_active")) {
    lines.push("OpenKOS is also working in the background, so this may be slower.");
  }
  lines.push(CHECK_NEVER_COMBINES);
  return { heading: n === 1 ? "Check these two?" : `Check ${n} pairs?`, lines, agree: `Check ${n}`, decline: "Not now" };
}

export function checkProgress(done: number, total: number, stopping: boolean): string {
  return stopping
    ? "Stopping after the current one. Everything already checked is kept."
    : `Checking ${done} of ${total}…`;
}

export function checkResult(checked: number, ready: number): string {
  return `Checked ${checked}. ${ready === 1 ? "1 is" : `${ready} are`} ready for you to decide.`;
}

// -- splitting again ---------------------------------------------------------- //

export function historyLine(record: Schemas["MergeRecord"], now: Date): string {
  const title = record.absorbed_title === null ? "a page" : `'${record.absorbed_title}'`;
  const when = formatDate(record.merged_at, now);
  return when === null ? `Combined from ${title}` : `Combined from ${title} on ${when}`;
}

function stepTitle(step: Schemas["UnmergeStep"]): string {
  return step.absorbed_title === null ? "a page" : `'${step.absorbed_title}'`;
}

/** Asked before a split; the later combines are listed when they go first. */
export function splitQuestion(steps: Schemas["UnmergeStep"][]): string {
  const later = steps.slice(0, -1);
  const last = steps.at(-1);
  if (later.length === 0) {
    return `Split ${last === undefined ? "a page" : stepTitle(last)} out again? Both pages go back exactly as they were.`;
  }
  return `To split this, the later combines on this page are split first: ${later.map(stepTitle).join(", ")}. Continue?`;
}

export function splitDone(titles: string[]): string {
  if (titles.length === 1) return `Split. ${quoted(titles)} is back as it was.`;
  const head = titles.slice(0, -1).map((t) => `'${t}'`).join(", ");
  return `Split. ${head} and '${titles.at(-1)}' are back as they were.`;
}

// -- problems ----------------------------------------------------------------- //

/** The one thing a problem lets the person do next. */
export type ProblemAction = "try_again" | "reload" | "ok" | "requote";

export interface Problem {
  message: string;
  action: ProblemAction;
}

const PROBLEMS: Record<string, Problem> = {
  workspace_busy: { message: "OpenKOS is finishing something else. Nothing was changed.", action: "try_again" },
  run_active: { message: "A check is already running.", action: "ok" },
  stale: { message: "These pages changed while you were looking. Here is the current version.", action: "reload" },
  write_drift: {
    message: "These pages changed while you were looking. Here is the current version.",
    action: "reload",
  },
  member_missing: { message: "One of these pages no longer exists, so there's nothing to decide.", action: "ok" },
  backend_unavailable: {
    message: "The AI model isn't running. OpenKOS needs it to check pages.",
    action: "try_again",
  },
  backend_model_not_found: {
    message: "The AI model isn't installed. OpenKOS needs it to check pages.",
    action: "ok",
  },
  record_failed: { message: "Your choice couldn't be saved. Nothing was changed.", action: "try_again" },
  invalid_selection: {
    message: "Something about your choice didn't match these pages. Please choose again.",
    action: "reload",
  },
  merge_group_interrupted: {
    message: "Only some of these were combined. Nothing else changed, and each combine can be split again.",
    action: "reload",
  },
  survivor_missing: { message: "This page was combined into another one. Split that first.", action: "ok" },
  not_merged: { message: "These pages are already separate.", action: "reload" },
  survivor_edited: {
    message: "This page was changed after it was combined, so it can't be split here.",
    action: "ok",
  },
  unmerge_interrupted: {
    message: "Only some of these were split. Each page that was split is back as it was.",
    action: "reload",
  },
  quote_stale: {
    message: "Something changed since you agreed — the AI model or the pages. Please look again.",
    action: "requote",
  },
};

/**
 * The brief's message and action for a failure. `when` says whether a write
 * may have run: after one, a failure the brief does not name never claims that
 * nothing changed.
 */
export function problemFor(error: unknown, when: "before" | "after"): Problem {
  if (error instanceof ApiError) {
    const known = PROBLEMS[error.code];
    if (known !== undefined) return known;
  }
  if (error instanceof EngineUnreachable) {
    return when === "before"
      ? { message: "OpenKOS isn't responding. Nothing was changed.", action: "try_again" }
      : { message: "OpenKOS stopped responding. Look at To review to see what changed.", action: "reload" };
  }
  return when === "before"
    ? { message: "Something went wrong. Nothing was changed.", action: "try_again" }
    : { message: "Something went wrong. Look at To review to see what changed.", action: "reload" };
}
