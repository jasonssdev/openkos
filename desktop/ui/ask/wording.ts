// What the Ask screen says (docs/decision-wording.md, "Ask"), kept apart from
// the DOM so every sentence is tested. The answer is the API's own, already
// gated (ADR-0068): a page marked Only on this Mac is only ever counted here,
// never named, unless the person chooses to show it (ADR-0075). A failure is worded from its class and code only, never from an
// unknown error's text.
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";

type Answer = Schemas["Answer"];
export type AskPhase = NonNullable<Schemas["AskJob"]["phase"]>;

/** The brief's rule 1, and the engine's words for asking: none reaches the screen. */
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
  "confidential",
  "workspace",
  "queue",
  "pending",
  "token",
  "query",
  "retrieval",
  "index",
  "indexes",
  "context",
  "citation",
  "citations",
];

/** What a finished ask shows: an answer with where it came from, or why there is none. */
export type AnswerWording =
  | { kind: "answered"; text: string; from: string[]; notes: string[] }
  | { kind: "empty"; title: string; text: string; notes: string[] };

export interface AskProblem {
  text: string;
  /** Asking the same question again can help. */
  retry: boolean;
}

/** One question and how it went. */
export type TurnResult =
  | { kind: "running"; phase: AskPhase | null }
  | { kind: "answer"; answer: Answer }
  | { kind: "problem"; problem: AskProblem };

const PHASES: Record<AskPhase, string> = {
  retrieving: "Looking through your pages…",
  assembling: "Gathering what's relevant…",
  checking: "Checking your pages hold an answer…",
  synthesizing: "Writing the answer…",
};

/** The line under the spinner while the answer is on its way. */
export function phaseLine(phase: AskPhase | null): string {
  return phase === null ? PHASES.retrieving : PHASES[phase];
}

const pages = (n: number) => (n === 1 ? "1 page" : `${n} pages`);
const PRIVATE_LABEL = "marked Only on this Mac";

/**
 * What search did for this answer. `searchCurrent`: search has been rebuilt
 * since, so the stale-search warning is told as past, and never says search is
 * behind while the notice beside it says it is up to date.
 */
function searchNote(answer: Answer, searchCurrent: boolean): string | null {
  if (answer.lexical_unavailable || answer.dense_unavailable) {
    return "The search was limited this time, so this answer could miss some pages.";
  }
  if (answer.stale_indexes.length > 0 && searchCurrent) {
    return "Search has been rebuilt since this answer. Ask again to include your latest changes.";
  }
  if (answer.stale_indexes.length > 0) {
    return "Some recent changes to your library may not be searched yet, so this answer could miss them.";
  }
  return null;
}

function leftOutNote(answer: Answer): string | null {
  if (answer.withheld <= 0) return null;
  return `${pages(answer.withheld)} ${PRIVATE_LABEL} ${answer.withheld === 1 ? "was" : "were"} left out.`;
}

function notes(...lines: (string | null)[]): string[] {
  return lines.filter((line): line is string => line !== null);
}

const NO_MATCH = { title: "Nothing matched", text: "Nothing in your library matches this question. Try asking with other words." };

const NO_MATCH_BY_CAUSE: Partial<Record<Answer["no_match_cause"], { title: string; text: string }>> = {
  all_unreadable: { title: "Nothing could be read", text: "The pages that match this question couldn't be read." },
  empty_query: { title: "Nothing to ask", text: "Type a question first." },
};

/** The finished ask, in the brief's words. */
export function describeAnswer(answer: Answer, searchCurrent = false): AnswerWording {
  switch (answer.outcome) {
    case "answered":
      return {
        kind: "answered",
        text: answer.answer ?? "",
        from: answer.citations.map((citation) => citation.title),
        notes: notes(
          answer.citations.length === 0 ? "This answer doesn't point to a particular page." : null,
          answer.sufficiency_degraded ? "This answer wasn't checked against your pages before it was written." : null,
          searchNote(answer, searchCurrent),
          leftOutNote(answer),
        ),
      };
    case "withheld": {
      const drewOn = answer.withheld > 0 ? `${pages(answer.withheld)} ${PRIVATE_LABEL}` : `pages ${PRIVATE_LABEL}`;
      return {
        kind: "empty",
        title: "This answer stays on this Mac",
        text: `It drew on ${drewOn}, so it isn't shown here, and neither are their names. You can show it while your AI model runs on this Mac.`,
        notes: [],
      };
    }
    case "no_answer_in_context":
      return {
        kind: "empty",
        title: "No answer in your pages",
        text: "Your library has pages about this, but none of them answers the question.",
        notes: notes(searchNote(answer, searchCurrent), leftOutNote(answer)),
      };
    default:
      return { kind: "empty", ...(NO_MATCH_BY_CAUSE[answer.no_match_cause] ?? NO_MATCH), notes: notes(searchNote(answer, searchCurrent), leftOutNote(answer)) };
  }
}

const PROBLEMS: Record<string, AskProblem> = {
  ask_active: { text: "OpenKOS is already answering another question. Wait a moment, then try again.", retry: true },
  backend_unavailable: { text: "The AI model isn't running. OpenKOS needs it to answer questions.", retry: true },
  backend_model_not_found: { text: "The AI model isn't installed. OpenKOS needs it to answer questions.", retry: false },
  generation_capped: {
    text: "The AI model stopped before the answer was finished. Try again, or ask a shorter question.",
    retry: true,
  },
  backend_error: { text: "The AI model ran into a problem.", retry: true },
  workspace_unreadable: { text: "Your library couldn't be read.", retry: true },
  workspace_not_found: { text: "Your library couldn't be found.", retry: false },
  // The engine lost the ask: the registry lives in memory and a restart empties it.
  not_found: { text: "OpenKOS restarted while it was answering. Ask again.", retry: true },
  invalid_request: { text: "This question couldn't be asked. Try writing it another way.", retry: false },
  request_too_large: { text: "This question is too long. Try a shorter one.", retry: false },
};

/** Showing a private answer was asked for, but the AI model is not on this Mac. */
export const MODEL_NOT_HERE: AskProblem = {
  text: "Your AI model isn't on this Mac, so these pages stay private.",
  retry: false,
};

/** The button under an answer that stays private. */
export const SHOW_PRIVATE = "Show this answer";

const SOMETHING_WENT_WRONG: AskProblem = { text: "Something went wrong.", retry: true };

/** Why the ask did not finish, and whether asking again can help. Asking writes nothing, so nothing changed. */
export function problemFor(error: unknown): AskProblem {
  if (error instanceof ApiError) return PROBLEMS[error.code] ?? SOMETHING_WENT_WRONG;
  if (error instanceof EngineUnreachable) return { text: "OpenKOS isn't responding.", retry: true };
  return SOMETHING_WENT_WRONG;
}

/** One word for how an earlier question went, beside it in the list. */
export function turnMeta(result: TurnResult): string {
  switch (result.kind) {
    case "running":
      return "Answering…";
    case "problem":
      return "Didn't finish";
    case "answer":
      if (result.answer.outcome === "answered") return "Answered";
      if (result.answer.outcome === "withheld") return "Stays on this Mac";
      return "No answer";
  }
}
