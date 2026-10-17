// What the Add files screen says (docs/decision-wording.md, "Adding files"),
// kept apart from the DOM so every sentence is tested. A file is named by its
// file name, never its path. The quote's number is an upper bound (ADR-0073),
// so the copy says "at most", and only for a cloud model: on this Mac the file
// count is enough. A failure is worded from its code only, never from an
// unknown error's text.
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";

type Quote = Schemas["IngestQuote"];
type FileQuote = Schemas["IngestFileQuote"];
type Job = Schemas["IngestJob"];
type Outcome = Schemas["IngestRunOutcome"];
type Refusal = NonNullable<FileQuote["refusal"]>;

/** The brief's rule 1: none of these reaches the screen. */
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
  "source",
  "sources",
  "index",
  "indexes",
];

const files = (n: number) => (n === 1 ? "1 file" : `${n} files`);
const pages = (n: number) => (n === 1 ? "1 page" : `${n} pages`);

const REFUSALS: Record<Refusal, (file: string) => string> = {
  changed_after_import: (f) =>
    `A different version of ${f} is already in your library, and files you added are never replaced. To add this version too, rename the file and add it again.`,
  duplicate_name: (f) => `Two of the files you chose are called ${f}. Rename one of them and add it again.`,
  not_found: (f) => `${f} isn't there any more.`,
  not_a_file: (f) => `${f} is a folder, not a file. Choose the files inside it.`,
  inside_workspace: (f) => `${f} is part of your library's own storage, so it can't be added.`,
  unreadable: (f) => `${f} couldn't be opened. Check that you can open it, then add it again.`,
  inconsistent_workspace: (f) => `${f} can't be added from the app right now.`,
  symlinked_destination: (f) => `${f} can't be added from the app right now.`,
  not_absolute: (f) => `${f} can't be added from the app right now.`,
};

/** Why a file was not added, and the one thing the person can do. */
export function refusalLine(file: string, reason: Refusal): string {
  return REFUSALS[reason](file);
}

const KEPT_NOTES: Record<NonNullable<FileQuote["source_only"]>, string> = {
  not_text: "We'll keep this file, but the AI model can't read it, so no pages will be made from it.",
  blank: "This file is empty. We'll keep it, but there is nothing to make pages from.",
  confidential_floor:
    "Your library keeps new files Only on this Mac, so the AI model won't read this one. We'll keep the file without making pages.",
};

export interface FileLine {
  name: string;
  /** Beside the name; null for a file that will simply be read. */
  note: string | null;
  refused: boolean;
}

export interface QuoteView {
  heading: string;
  files: FileLine[];
  /** How many files will be added (read, or only kept). */
  addable: number;
  /** What leaves this Mac; null when the model reads nothing. */
  privacy: string | null;
  /** The most requests a cloud model is asked for; null on this Mac. */
  ceiling: string | null;
  /** The consent button; null when nothing can be added. */
  agree: string | null;
  decline: string;
}

function fileLine(file: FileQuote): FileLine {
  if (file.status === "refused") {
    return { name: file.name, note: file.refusal ? refusalLine(file.name, file.refusal) : "This file can't be added.", refused: true };
  }
  if (file.status === "unchanged") return { name: file.name, note: "Already in your library. Nothing to do.", refused: false };
  if (file.status === "source_only" && file.source_only) return { name: file.name, note: KEPT_NOTES[file.source_only], refused: false };
  return { name: file.name, note: null, refused: false };
}

function readLine(quote: Quote, read: number, total: number): string {
  const all = read === total;
  if (quote.is_local) {
    const them = read === 1 ? "it" : all && read === 2 ? "both" : all ? "all of them" : `${read} of them`;
    return `The AI model on this Mac will read ${them} and make pages. Nothing leaves this Mac.`;
  }
  const these = read === 1 ? "this file" : "these files";
  return `The text of ${these} will be sent to ${quote.host} to make pages with ${quote.model}. The provider may charge you.`;
}

/** The consent screen for a quote, in the brief's words. */
export function describeQuote(quote: Quote): QuoteView {
  const lines = quote.files.map(fileLine);
  const addable = quote.files.filter((f) => f.status === "extracts" || f.status === "source_only");
  const read = quote.files.filter((f) => f.status === "extracts").length;
  const only = addable.length === 1 ? addable[0] : undefined;
  if (addable.length === 0) {
    return { heading: "None of these can be added.", files: lines, addable: 0, privacy: null, ceiling: null, agree: null, decline: "OK" };
  }
  const calls = quote.token.calls;
  return {
    heading: only ? `Add ${only.name}?` : `Add ${addable.length} files?`,
    files: lines,
    addable: addable.length,
    privacy: read === 0 ? null : readLine(quote, read, addable.length),
    ceiling: read === 0 || quote.is_local ? null : `At most ${calls} ${calls === 1 ? "request" : "requests"} to ${quote.model}, usually fewer.`,
    agree: `Add ${files(addable.length)}`,
    decline: "Not now",
  };
}

const STEPS: Record<NonNullable<Job["phase"]>, string> = {
  reading: "Opening it…",
  extracting: "The AI model is reading it…",
  writing: "Saving…",
  refreshing: "Making it searchable…",
};

export interface ProgressLine {
  heading: string;
  step: string | null;
}

/** The running line: which file, and what is happening to it. */
export function progressLine(quote: Quote, job: Job): ProgressLine {
  if (job.phase === null || job.index === null || job.total === null) return { heading: "Getting started", step: null };
  if (job.phase === "refreshing") return { heading: "Almost done", step: STEPS.refreshing };
  const path = quote.token.files[job.index - 1]?.path;
  const name = quote.files.find((f) => f.path === path)?.name;
  const of = `${job.index} of ${job.total}`;
  return { heading: name ? `Adding ${of}: ${name}` : `Adding ${of}`, step: STEPS[job.phase] };
}

export interface OutcomeView {
  heading: string;
  lines: string[];
  notes: string[];
}

function resultLine(file: Outcome["files"][number]): string {
  switch (file.status) {
    case "added": {
      const made = `${file.name}: ${pages(file.created).replace(/ page/, " new page")}`;
      return file.revised > 0 ? `${made} and ${pages(file.revised)} updated` : made;
    }
    case "updated":
      return `${file.name}: finished adding.`;
    case "unchanged":
      return `${file.name}: already in your library.`;
    case "refused":
      return file.refusal ? refusalLine(file.name, file.refusal) : `${file.name}: couldn't be added.`;
    case "interrupted":
      return `${file.name}: stopped before it was finished. Add it again later.`;
    case "busy":
      return `${file.name}: your library was busy. Add it again in a moment.`;
    case "not_reached":
      return `${file.name}: wasn't reached. Add it again later.`;
    case "failed":
      return `${file.name}: couldn't be added.`;
  }
}

function extractionDebt(file: Outcome["files"][number]): boolean {
  return (file.status === "added" || file.status === "updated") && file.extraction_degraded;
}

/** What a finished run says: the headline, one line per file, and any warning. */
export function describeOutcome(outcome: Outcome): OutcomeView {
  const added = outcome.files.filter((f) => f.status === "added" || f.status === "updated").length;
  let heading: string;
  if (outcome.status === "stopped") heading = `Stopped. Added ${files(added)}; the rest were left.`;
  else if (outcome.status === "busy") heading = "Your library was busy, so adding stopped.";
  else heading = added === 0 ? "Nothing new was added." : `Added ${files(added)}.`;
  return {
    heading,
    lines: outcome.files.map((file) =>
      extractionDebt(file) ? `${file.name}: kept, but the AI model couldn't finish reading it. Add it again later to try again.` : resultLine(file),
    ),
    notes:
      outcome.indexes === "stale"
        ? ["Your files are added, but search isn't up to date with them yet, so answers may miss them for now."]
        : [],
  };
}

/** What the person can do about a problem: do the step again, look again, or close it. */
export interface AddProblem {
  text: string;
  action: "try_again" | "requote" | "ok";
}

const PROBLEMS: Record<string, AddProblem> = {
  quote_stale: { text: "A file or your AI model changed since we looked. Look again before adding.", action: "requote" },
  run_refused: { text: "These files can't be added as they were. Look again before adding.", action: "requote" },
  ingest_active: { text: "OpenKOS is already adding files. Wait for it to finish, then try again.", action: "try_again" },
  workspace_busy: { text: "Your library is busy right now. Try again in a moment.", action: "try_again" },
  workspace_lock_unavailable: { text: "Your library is busy right now. Try again in a moment.", action: "try_again" },
  ingest_refused: { text: "Files can't be added to your library right now.", action: "ok" },
  workspace_unreadable: { text: "Your library couldn't be read.", action: "try_again" },
  workspace_not_found: { text: "Your library couldn't be found.", action: "ok" },
  // The registry lives in memory; a restart empties it, and a run may have finished before.
  not_found: { text: "OpenKOS restarted. Check To review and Status to see what was added.", action: "ok" },
  request_too_large: { text: "That is too many files at once. Choose fewer.", action: "ok" },
  invalid_request: { text: "These files can't be added.", action: "ok" },
};

const SOMETHING_WENT_WRONG: AddProblem = { text: "Something went wrong.", action: "try_again" };

/** Why a step did not finish. Nothing was added by a step that failed before the run started. */
export function problemFor(error: unknown): AddProblem {
  if (error instanceof ApiError) return PROBLEMS[error.code] ?? SOMETHING_WENT_WRONG;
  if (error instanceof EngineUnreachable) return { text: "OpenKOS isn't responding.", action: "try_again" };
  return SOMETHING_WENT_WRONG;
}
