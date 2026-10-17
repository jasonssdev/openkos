// Every word the review screen shows, as pure functions of the API's data.
// docs/decision-wording.md is the authority: a sentence here that differs from
// the brief is a defect in one of them. Nothing here touches the DOM, reads the
// token or shows the engine's own text, a code, a Concept ID or a command.
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";
import type { Undo } from "./model.ts";

type Decision = Schemas["Decision"];
type Page = Schemas["Page"];
type Action = Schemas["Action"];
/** What a preview returns a plan of: a decision's, or a contradiction's or revision's (ADR-0072). */
export type AnyPlan = Schemas["PreviewPlan"] | Schemas["FindingPlan"];

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

/**
 * What a person can choose: an action the engine offers, the check, checking a
 * changed decision again, later, or "same", the answer to an identity question
 * that asks a second one (which pages, or what it is) before anything is previewed.
 */
export type Choice = Action | "check" | "recheck" | "later" | "same";

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

/** A decision listed with `fresh` false: it is checked again before it is decided. */
export const CHANGED_CHECK =
  "Something about these pages changed since this was suggested. Check them again before you decide.";

/**
 * A changed disagreement or newer decision. The check does not cover these
 * kinds and the free refresh does not recompute them, so nothing in the app can
 * make one current: it is listed as changed with Decide later only.
 */
export const CHANGED_FINDING =
  "Something about these pages changed since this was found, so it can't be settled as it was. The app can't look at them again yet.";

/** The kinds the check covers (`QuoteRequest.kind`): a changed one can be checked again. */
export function checkable(decision: Decision): boolean {
  return decision.kind === "identity" || decision.kind === "relation_type" || decision.kind === "volatility";
}

/** The notice a changed decision is shown with, or null when it is current. */
export function changedNotice(decision: Decision): string | null {
  if (decision.fresh !== false) return null;
  return checkable(decision) ? CHANGED_CHECK : CHANGED_FINDING;
}

const CHECK_AGAIN: Option = {
  choice: "recheck",
  label: "Check again",
  does: "We'll look at these pages as they are now. Checking doesn't change anything. You'll still decide.",
};

export const CANNOT_ACT = "This can't be settled in the app yet.";

export interface PageView {
  /** The page's id and level, for who can see it; `null` for a page that couldn't be read. */
  ref: { conceptId: string; title: string; level: Page["sensitivity"] } | null;
  title: string;
  /** What the page is about, with its article: "a person". */
  kind: string;
  excerpt: string;
  whoCanSee: { label: string; meaning: string } | null;
  sources: string[];
  /** A newer decision's quote from this page, in quotation marks. */
  quote?: string;
  /** A newer decision's date for this page, or "No date". */
  date?: string;
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
  /** The question asked after "Same thing", for a group or pages of different kinds. */
  next: { lead: string; question: string; options: Option[] } | null;
  /** What the AI model saw as not matching, each in quotation marks, as written. */
  claims: string[];
  /** One line under the options: where else the person can settle this. */
  after: string | null;
  changed: string | null;
  cannotAct: string | null;
}

function pageView(pages: Pages, id: string, now: Date): PageView {
  const page = pages.get(id);
  if (page === undefined) {
    return { ref: null, title: UNREADABLE_PAGE, kind: "", excerpt: "", whoCanSee: null, sources: [] };
  }
  return {
    ref: { conceptId: page.concept_id, title: page.title || UNREADABLE_PAGE, level: page.sensitivity },
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

/** What the "same?" question is about: the pages' kind, or a thing when the kinds differ. */
function questionNoun(decision: Decision): Noun {
  return identityShape(decision) === "kinds" ? typeNoun(null) : identityNoun(decision);
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

/** Above the question asked after "Same thing". */
export const SAID_SAME = "You said these are the same.";

/**
 * An identity question asks "Are these the same?" first, with both answers
 * worded alike (#1475). For a pair, "Same thing" is the combine. For a group,
 * or pages of different kinds, it asks the second question, which pages or what
 * it is, and only that is previewed; the person always chooses, never the app.
 */
function identityView(decision: Decision, pages: Pages): Pick<DecisionView, "question" | "lead" | "options" | "next"> {
  const noun = questionNoun(decision);
  const shape = identityShape(decision);
  const offers = (action: Action) => decision.actions.includes(action);
  const options: Option[] = [];
  let next: DecisionView["next"] = null;
  if (decision.evaluation === "pending_evaluation") options.push(CHECK_THESE);
  if (offers("merge")) {
    options.push({ choice: "merge", label: "Same thing", does: "We'll make one page with everything from both." });
  } else if (offers("merge_group")) {
    const kinds = shape === "kinds";
    options.push({
      choice: "same",
      label: "Same thing",
      does: kinds ? "Next, you'll choose what it is." : "Next, you'll mark which ones are the same.",
    });
    const then: Option[] = kinds
      ? kindsAmong(decision, pages).map((okfType) => {
          const kind = typeNoun(okfType);
          return { choice: "merge_group", label: `It's ${kind.withArticle}`, does: `We'll keep one page, as ${kind.withArticle}.`, okfType };
        })
      : [{ choice: "merge_group", label: "Combine the ones I marked", does: "We'll make one page from the pages you marked." }];
    next = {
      lead: SAID_SAME,
      question: kinds ? `What is ${titleOf(pages, members(decision)[0] ?? "")}?` : "Which of these are the same?",
      options: [...then, DECIDE_LATER],
    };
  }
  if (offers("keep_distinct")) {
    options.push({
      choice: "keep_distinct",
      label: "Different things",
      does: shape === "group" && offers("merge_group") ? "We won't suggest combining any of these again." : "We won't suggest combining these again.",
    });
  }
  return { question: `Are these the same ${noun.one}?`, lead: evaluationLead(decision, noun), options, next };
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

// -- contradictions, revisions and refused files (ADR-0072) --------------------- //

type ContradictionDetail = Schemas["ContradictionDetail"];
type RevisionDetail = Schemas["RevisionDetail"];
type RefusedFileDetail = Schemas["RefusedFileDetail"];

function contradictionOf(decision: Decision): ContradictionDetail | null {
  const finding = decision.finding;
  return decision.kind === "contradiction" && finding && "conflicting_claims" in finding ? finding : null;
}

function revisionOf(decision: Decision): RevisionDetail | null {
  const finding = decision.finding;
  return decision.kind === "revision" && finding && "change" in finding ? finding : null;
}

function refusedFileOf(decision: Decision): RefusedFileDetail | null {
  const finding = decision.finding;
  return decision.kind === "watch_refusal" && finding && "file_name" in finding ? finding : null;
}

/**
 * The pages a finding shows: both of a disagreement, only the page that was
 * combined into for one found inside a combined page, both of a newer decision,
 * none for a refused file. Null when the decision is not a finding with detail.
 */
export function findingPages(decision: Decision): string[] | null {
  const contradiction = contradictionOf(decision);
  if (contradiction !== null) {
    return contradiction.merged_absorbed_id === null
      ? [contradiction.first_id, contradiction.second_id]
      : [contradiction.first_id];
  }
  const revision = revisionOf(decision);
  if (revision !== null) return [revision.first_id, revision.second_id];
  return decision.kind === "watch_refusal" ? [] : null;
}

/** Whether a decision is previewed with the finding preview rather than the decision one. */
export function isFinding(decision: Decision): boolean {
  return decision.kind === "contradiction" || decision.kind === "revision";
}

const OUT_OF_DATE = (title: string) =>
  `We'll mark '${title}' as out of date. It stays in your library, but answers won't use it.`;
const UPDATES = (later: string, earlier: string) =>
  `Both stay current, and '${later}' is noted as an update to '${earlier}'.`;
const BOTH_RIGHT =
  "Both pages stay as they are, and each gets a short note that you checked them against each other.";
const BOTH_STAND =
  "Both pages stay current, and each gets a short note that you checked them against each other. We won't ask about these two again.";
const LEAVE_THEM = "Nothing on either page changes. We won't point this out again.";
const LEAVE_IT = "Nothing changes on the page. We won't point this out again.";

/** Above what the AI model saw as not matching. */
export const CLAIMS_HEADING = "What doesn't match";

/** Under a disagreement found inside a combined page: Split again is the other way out. */
export const SPLIT_INSTEAD =
  "If combining them was a mistake, you can split it again under 'Pages you combined'.";

function contradictionView(decision: Decision, pages: Pages): KindView {
  const finding = contradictionOf(decision);
  if (finding === null) return { question: listedOnly(decision, pages) };
  const offers = (action: Action) => decision.actions.includes(action);
  const first = titleOf(pages, finding.first_id);
  const claims = finding.conflicting_claims.map((claim) => `“${claim}”`);
  if (finding.merged_absorbed_id !== null) {
    return {
      question: `Part of '${first}' disagrees with what was combined into it.`,
      claims,
      options: offers("decline") ? [{ choice: "decline", label: "Leave it as it is", does: LEAVE_IT }] : [],
      after: SPLIT_INSTEAD,
    };
  }
  const second = titleOf(pages, finding.second_id);
  const options: Option[] = [];
  if (offers("supersede")) options.push({ choice: "supersede", label: `'${first}' is right`, does: OUT_OF_DATE(second) });
  if (offers("supersede_reversed")) {
    options.push({ choice: "supersede_reversed", label: `'${second}' is right`, does: OUT_OF_DATE(first) });
  }
  if (offers("reconcile")) options.push({ choice: "reconcile", label: "Both are right", does: BOTH_RIGHT });
  if (offers("decline")) options.push({ choice: "decline", label: "Leave them as they are", does: LEAVE_THEM });
  return { question: "These pages say different things. Which is right?", claims, options };
}

const DIRECTED: readonly Action[] = ["supersede", "revise", "supersede_reversed", "revise_reversed"];

/** One directed revision write: which page holds it, and what it does. */
function directedOption(action: Action, first: string, second: string, dated: boolean): Option {
  const reversed = action.endsWith("_reversed");
  const [holder, other] = reversed ? [second, first] : [first, second];
  const replaces = action.startsWith("supersede");
  const does = replaces ? OUT_OF_DATE(other) : UPDATES(holder, other);
  if (dated) return { choice: action, label: replaces ? "Yes, it replaces it" : "Yes, it updates it", does };
  return { choice: action, label: `'${holder}' ${replaces ? "replaces" : "updates"} '${other}'`, does };
}

/** A revision's date, as the page gives it; "No date" when there is none. */
function revisionDate(value: string | null, now: Date): string {
  if (value === null || value.trim() === "") return "No date";
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  // A date with no time is that day here, never shifted by the time zone.
  const iso = day ? new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3])).toISOString() : value;
  return formatDate(iso, now) ?? value;
}

function revisionView(decision: Decision, pages: Pages): KindView {
  const finding = revisionOf(decision);
  if (finding === null) return { question: listedOnly(decision, pages) };
  const first = titleOf(pages, finding.first_id);
  const second = titleOf(pages, finding.second_id);
  const dated = finding.later_id !== null;
  // The direction is the engine's, from the dates (ADR-0025): the app reads which write is offered.
  const directed = DIRECTED.filter((action) => decision.actions.includes(action));
  const options = directed.map((action) => directedOption(action, first, second, dated));
  if (decision.actions.includes("reconcile")) options.push({ choice: "reconcile", label: "Both still stand", does: BOTH_STAND });
  if (!dated) {
    return {
      lead: "These have no dates we can compare, so we can't tell which came first.",
      question: "How do these two relate?",
      options,
    };
  }
  const [later, earlier] = finding.later_id === finding.first_id ? [first, second] : [second, first];
  const replaces = directed.length > 0 ? directed[0]!.startsWith("supersede") : finding.change === "reverses";
  return { question: `Does '${later}' ${replaces ? "replace" : "update"} '${earlier}'?`, options };
}

const REFUSED: Record<RefusedFileDetail["reason"], string> = {
  changed_after_import:
    "A file with this name is already in your library, with different contents. To add this one too, give it a new name in your folder.",
  exceeds_pass_budget: "It's too large to add automatically.",
};

const ADD_IT = { label: "Add it", does: "We'll show you what adding it takes before anything is sent." };

/**
 * The way to add a refused file from the app, or null. A file too large for the
 * unattended pass (or one with no stated reason) is added by a person after a
 * quote, on the Add files screen. A file whose contents changed after an add
 * stays refused, and the remedy is a new name (ADR-0073).
 */
export function addRefusedOption(decision: Decision): { label: string; does: string } | null {
  if (decision.kind !== "watch_refusal") return null;
  const finding = refusedFileOf(decision);
  return finding === null || finding.reason === "exceeds_pass_budget" ? ADD_IT : null;
}

/**
 * A file that wasn't added: what happened and what to do about it. Adding it
 * runs the AI model, which needs the person's agreement first, so the screen
 * hands "Add it" (`addRefusedOption`) to the Add files screen rather than
 * previewing it here.
 */
function refusedFileView(decision: Decision, pages: Pages): KindView {
  const finding = refusedFileOf(decision);
  if (finding === null) return { question: listedOnly(decision, pages) };
  return { question: `'${finding.file_name}' wasn't added.`, statement: REFUSED[finding.reason], options: [] };
}

/** What a finding's plan will do, from the plan. */
function findingLines(decision: Decision, plan: Schemas["FindingPlan"], pages: Pages): string[] {
  if (plan.kind === "decline") return [plan.merged_absorbed_id === null ? LEAVE_THEM : LEAVE_IT];
  switch (plan.relation_type) {
    case "supersedes":
      return [OUT_OF_DATE(titleOf(pages, plan.deprecates_id ?? plan.target_id ?? ""))];
    case "revises":
      return [UPDATES(titleOf(pages, plan.holder_id ?? ""), titleOf(pages, plan.target_id ?? ""))];
    case "reconciled_with":
      return [decision.kind === "revision" ? BOTH_STAND : BOTH_RIGHT];
  }
}

/** A finding's confirmation, in the past tense, from the plan that was applied. */
function findingConfirmation(decision: Decision, plan: Schemas["FindingPlan"], pages: Pages): string {
  if (plan.kind === "decline") return plan.merged_absorbed_id === null ? "Left as they are." : "Left as it is.";
  switch (plan.relation_type) {
    case "supersedes":
      return `Marked '${titleOf(pages, plan.deprecates_id ?? plan.target_id ?? "")}' as out of date.`;
    case "revises":
      return `Noted that '${titleOf(pages, plan.holder_id ?? "")}' updates '${titleOf(pages, plan.target_id ?? "")}'.`;
    case "reconciled_with":
      return decision.kind === "revision" ? "Noted that both still stand." : "Noted that both are right.";
  }
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
  const detail = decision.detail ?? decision.finding;
  return detail && "rationale" in detail ? modelReason(detail.rationale) : null;
}

function pageViews(decision: Decision, pages: Pages, now: Date): PageView[] {
  const revision = revisionOf(decision);
  const ids = findingPages(decision) ?? (decision.kind === "identity" ? members(decision) : decision.targets);
  return ids.map((id) => {
    const view = pageView(pages, id, now);
    if (revision === null) return view;
    const first = id === revision.first_id;
    return {
      ...view,
      quote: `“${first ? revision.first_quote : revision.second_quote}”`,
      date: revisionDate(first ? revision.first_date : revision.second_date, now),
    };
  });
}

type KindView = Partial<Pick<DecisionView, "question" | "lead" | "statement" | "facts" | "options" | "next" | "claims" | "after">>;

/**
 * What each kind asks and offers: its question and one option per action,
 * offered only when the engine lists the action; its plan is worded in
 * `previewLines` and its result in `confirmation`, and the screen draws whatever
 * options it is given. A kind that offers nothing (a refused file, or a finding
 * without its detail) is listed with its plain sentence and "This can't be
 * settled in the app yet.", whatever the engine lists.
 */
const KINDS: Record<Decision["kind"], ((decision: Decision, pages: Pages) => KindView) | null> = {
  identity: identityView,
  relation_type: connectionView,
  volatility: tierView,
  contradiction: contradictionView,
  revision: revisionView,
  watch_refusal: refusedFileView,
};

/** Everything one decision's screen says, before any preview. */
export function describeDecision(decision: Decision, pages: Pages, now: Date = new Date()): DecisionView {
  const base: DecisionView = {
    question: "",
    lead: null,
    statement: null,
    facts: [],
    reason: reasonOf(decision),
    pages: pageViews(decision, pages, now),
    options: [],
    next: null,
    claims: [],
    after: null,
    changed: changedNotice(decision),
    cannotAct: null,
  };
  const settle = KINDS[decision.kind] ?? null;
  const view: DecisionView = settle === null ? { ...base, question: listedOnly(decision, pages) } : { ...base, ...settle(decision, pages) };
  // A changed decision's own actions would be refused as stale: it is checked
  // again first, and decided once it is current (#1475). A changed finding
  // cannot be checked again in the app, so it can only wait.
  // A finding with nothing to offer (no detail to word) is listed only, changed or not.
  if (addRefusedOption(decision) !== null) return view;
  if (view.options.length === 0 && !checkable(decision)) return { ...view, options: [], next: null, cannotAct: CANNOT_ACT };
  if (decision.fresh === false && settle !== null) {
    const options = checkable(decision) ? [CHECK_AGAIN, DECIDE_LATER] : [DECIDE_LATER];
    return { ...view, options, next: null, after: null };
  }
  if (view.options.length === 0) return { ...view, options: [], next: null, cannotAct: CANNOT_ACT };
  return { ...view, options: [...view.options, DECIDE_LATER] };
}

export interface ListEntry {
  /** What the row is about: the pages for an identity, else its sentence or question. */
  title: string;
  /** The question for an identity, why it cannot be settled, or nothing. */
  meta: string | null;
  changed: boolean;
  canAct: boolean;
}

/** One row of To review. */
export function listEntry(decision: Decision, pages: Pages): ListEntry {
  const view = describeDecision(decision, pages);
  const canAct = view.cannotAct === null;
  // Pages compared side by side are named by their titles, with the question.
  const byPages = decision.kind === "identity" || (isFinding(decision) && findingPages(decision) !== null);
  const title = byPages
    ? view.pages.map((p) => p.title).join(" · ")
    : decision.kind === "watch_refusal"
      ? view.question
      : (view.statement ?? view.question);
  return {
    title,
    meta: !canAct ? CANNOT_ACT : byPages ? view.question : null,
    changed: decision.fresh === false,
    canAct,
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
export function previewLines(decision: Decision, plan: AnyPlan, pages: Pages): string[] {
  switch (plan.kind) {
    case "reconcile":
    case "decline":
      return findingLines(decision, plan, pages);
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

/**
 * The confirmation after apply, in the past tense. A finding's names the page
 * marked out of date, so it is worded from the plan that was applied.
 */
export function confirmation(decision: Decision, applied: Schemas["Applied"], pages: Pages, plan?: AnyPlan): string[] {
  if (plan?.kind === "reconcile" || plan?.kind === "decline") return [findingConfirmation(decision, plan, pages)];
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
  return { heading: checkHeading(quote.kind, n), lines, agree: `Check ${n}`, decline: "Not now" };
}

function checkHeading(kind: Schemas["JudgingQuote"]["kind"], n: number): string {
  switch (kind) {
    case "relation_type":
      return n === 1 ? "Check this connection?" : `Check ${n} connections?`;
    case "volatility":
      return n === 1 ? "Check this suggestion?" : `Check ${n} suggestions?`;
    default:
      return n === 1 ? "Check these two?" : `Check ${n} pairs?`;
  }
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

export const COMBINED_HEADING = "Pages you combined";

// -- undoing ------------------------------------------------------------------ //

/** What an Undo other than Split again did, in the past tense. */
export function undoneLine(undo: Exclude<Undo, { kind: "split" }>): string {
  switch (undo.kind) {
    case "reopen":
      return "Undone. We'll ask you about these again.";
    case "unrelate":
      return "Undone. That connection is removed.";
    case "tier": {
      const many = typeNoun(undo.typeName).many;
      return `Undone. ${many.charAt(0).toUpperCase()}${many.slice(1)} are treated as before.`;
    }
  }
}

// -- problems ----------------------------------------------------------------- //

/** The one thing a problem lets the person do next. */
export type ProblemAction = "try_again" | "reload" | "ok" | "requote" | "set_up";

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
    message: "The AI model isn't running. OpenKOS needs it to check pages. Check setup, on Status, helps you start it.",
    action: "try_again",
  },
  backend_model_not_found: {
    message: "The AI model isn't installed. OpenKOS needs it to check pages. Setup can download it.",
    action: "set_up",
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
  not_kept_distinct: { message: "These pages aren't kept separate any more.", action: "ok" },
  not_related: { message: "These pages aren't connected any more.", action: "ok" },
  tier_unchanged: { message: "This setting is already as it was.", action: "ok" },
  already_reconciled: {
    message: "You've already settled how these pages relate, in another way. Nothing was changed.",
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
