// What the welcome and the guided setup say (docs/decision-wording.md, "Setup"),
// kept apart from the DOM so it can be tested. Plain words only: no engine
// words, and never a command. A refusal is worded from its reason, never from
// the shell's or the engine's text, which waits behind a disclosure at most.
import { ApiError, EngineUnreachable, WorkspaceError, type Schemas } from "../../client/index.ts";

/** The fixed words of the two screens. */
export const WORDS = {
  welcomeTitle: "Welcome to OpenKOS",
  welcomeLead: "Choose a folder for your library.",
  welcomeText: "Your library is a folder on this Mac. Everything you add to OpenKOS stays there.",
  create: "Create a new library",
  creating: "Setting up your library…",
  open: "Open an existing library",
  opening: "Opening your library…",
  createHint: "A new library needs an empty folder. You can make one in the window that opens.",
  openInstead: "Open it",
  starting: "Opening your library…",
  showDetails: "Show details",

  setupTitle: "Set up OpenKOS",
  setupLead: "OpenKOS needs the internet only for these steps. Nothing you add is sent.",
  checking: "Checking your setup.",
  checkFailed: "OpenKOS couldn't check its setup.",
  tryAgain: "Try again",
  getOllama: "Get Ollama",
  checkAgain: "Check again",
  checkingAgain: "Checking…",
  browserFailed: "Your browser didn't open. You can go to {url} yourself.",
  modelsTitle: "Download the AI models",
  modelsText:
    "OpenKOS needs these AI models on this Mac. They're large, so this can take a while. Once they're here, they work without the internet.",
  modelsLabel: "AI models",
  installed: "Installed",
  waiting: "Waiting…",
  download: "Download",
  downloadAll: "Download all",
  continueDownload: "Continue download",
  stop: "Stop",
  notPullable: "This AI model can't be downloaded from here.",
  readyTitle: "You're ready.",
  readyText: "OpenKOS works offline from now on.",
  startUsing: "Start using OpenKOS",
  stepOf: (step: number, of: number) => `Step ${step} of ${of}`,
} as const;

/** A download's size, in decimal units as Finder and Ollama show them. */
export function downloadSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}

/** Memory in whole gigabytes, as the Mac's own "About This Mac" says it. */
export function memorySize(bytes: number): string {
  return `${Math.round(bytes / 1024 ** 3)} GB`;
}

export interface PullView {
  state: "running" | "stopping";
  phase: Schemas["PullStatus"]["phase"];
  completed: number;
  total: number | null;
}

/** The line beside a download that is running. */
export function pullProgress(pull: PullView): string {
  if (pull.state === "stopping") return "Stopping…";
  switch (pull.phase) {
    case "downloading": {
      const done = downloadSize(pull.completed);
      if (!pull.total) return `${done} so far`;
      // Floored, so it reads 100% only when everything is there.
      const percent = Math.min(100, Math.floor((pull.completed * 100) / pull.total));
      return `${done} of ${downloadSize(pull.total)} (${percent}%)`;
    }
    case "verifying":
      return "Checking the download…";
    case "finishing":
      return "Finishing…";
    default:
      return "Getting ready…";
  }
}

/** After the person stopped a download. Ollama keeps what came, so it resumes. */
export function stoppedNote(completed: number, total: number | null): string {
  if (completed <= 0) return "Stopped.";
  const where = total ? `${downloadSize(completed)} of ${downloadSize(total)}` : downloadSize(completed);
  return `Stopped at ${where}. Downloading again continues from there.`;
}

const RESUMES = "Try again: it continues from where it stopped.";

/** Why a download did not start or did not finish, from its code alone. */
export function pullFailureFor(code: string): string {
  switch (code) {
    case "backend_unavailable":
      return "OpenKOS lost touch with Ollama. Try again: the download continues from where it stopped.";
    case "pull_active":
      return "Another download is already running. Try again in a moment.";
    case "backend_model_not_found":
      return "Ollama couldn't find this AI model to download.";
    case "model_not_configured":
      return "Your library doesn't use this AI model any more.";
    case "pull_not_supported":
      return WORDS.notPullable;
    default:
      return `The download didn't finish. ${RESUMES}`;
  }
}

/** Why a download could not be started or followed. */
export function pullFailure(error: unknown): string {
  if (error instanceof EngineUnreachable) return "OpenKOS isn't responding. Try again in a moment.";
  return pullFailureFor(error instanceof ApiError ? error.code : "");
}

export interface RuntimeStepWording {
  title: string;
  text: string[];
  /** Ollama's download page, offered only for an Ollama on this Mac. */
  download: string | null;
}

/** The first step, while the AI model's runtime does not answer. Never a cloud model. */
export function runtimeStep(runtime: Schemas["RuntimeReadiness"]): RuntimeStepWording {
  if (runtime.backend === "ollama" && runtime.is_local && runtime.download_url) {
    return {
      title: "Install Ollama",
      text: [
        "OpenKOS uses Ollama, a free app, to run AI models on this Mac. It isn't running yet.",
        "Get it from Ollama's website, install it and open it. We'll notice when it's running.",
      ],
      download: runtime.download_url,
    };
  }
  return {
    title: "Reach the AI model",
    text: [`OpenKOS can't reach the AI model at ${runtime.host}. Check that it's running, then check again.`],
    download: null,
  };
}

/** An honest note below ADR-0047's memory floor; never a block. */
export function memoryNote(memory: Schemas["MemoryReadiness"]): string | null {
  if (memory.meets_floor !== false || memory.total_bytes === null) return null;
  return (
    `This Mac has ${memorySize(memory.total_bytes)} of memory. OpenKOS is made for ${memorySize(memory.floor_bytes)} or more, ` +
    "so answers may be slow and some steps may not finish. You can still continue."
  );
}

export interface Refusal {
  text: string;
  /** The shell's own report, shown only behind "Show details". */
  detail?: string;
  /** A folder that can be opened instead, offered as "Open it". */
  openPath?: string;
}

const CANNOT_USE = "OpenKOS can't use that folder. Please choose another.";

/** Why choosing a folder was refused, from the shell's reason alone. */
export function welcomeRefusal(action: "create" | "open", error: unknown, path: string): Refusal {
  const reason = error instanceof WorkspaceError ? error.reason : "unknown";
  switch (reason) {
    case "set_by_environment":
      return { text: "This copy of OpenKOS is set to use a library chosen outside the app, so it can't be changed here." };
    case "invalid_path":
    case "not_a_folder":
      return { text: CANNOT_USE };
    case "not_a_workspace":
      return { text: "That folder doesn't hold an OpenKOS library. Choose another, or create a new library." };
    case "already_a_workspace":
      return { text: "That folder already holds an OpenKOS library.", openPath: path };
    case "folder_not_empty":
      return {
        text: "That folder already has files in it. Choose an empty folder, or make a new one, so nothing of yours is changed.",
      };
    case "engine_not_located":
      return { text: "Part of OpenKOS is missing, so it can't set up a library. Reinstalling OpenKOS may help." };
    case "init_failed":
      return { text: "OpenKOS couldn't set up a library in that folder.", detail: (error as WorkspaceError).message };
    case "init_timed_out":
      return { text: "Setting up the library took too long, so OpenKOS stopped. Please try again." };
    case "store_failed":
      return action === "create"
        ? { text: "Your library was set up, but OpenKOS couldn't remember it. Open it to continue.", openPath: path }
        : { text: "OpenKOS couldn't remember your choice. Please try again." };
    default:
      return { text: "Something went wrong. Please try again." };
  }
}

/** The line when the library chosen before is no longer where it was. */
export function movedLibrary(path: string): string {
  return `The library you used before isn't at ${path} any more. Open it from where it is now, or create a new one.`;
}
