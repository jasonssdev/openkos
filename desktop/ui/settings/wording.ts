// What the settings screen says (docs/decision-wording.md, "Settings"), kept
// apart from the DOM so it can be tested. Plain words only: no engine words,
// never a command, and never a key. A refusal is worded from its code or
// reason, never from the engine's or the shell's text.
import { ApiError, ApiKeyError, EngineUnreachable, UpdateError, WatchError, type Schemas } from "../../client/index.ts";

/** The fixed words of the screen. */
export const WORDS = {
  title: "Settings",
  navLabel: "Settings",
  loading: "Reading your settings.",
  loadFailed: "OpenKOS couldn't read your settings.",
  tryAgain: "Try again",

  modelTitle: "AI model",
  change: "Change",
  cancel: "Cancel",
  save: "Save",
  saving: "Saving…",
  whereLabel: "Where the AI model runs",
  local: "On this Mac",
  cloud: "A cloud AI model",
  localText: "OpenKOS uses Ollama on this Mac. Nothing you add leaves it.",
  cloudText: "A service on the internet runs the AI model. You'll see what is sent before you turn it on.",
  address: "Address of the service",
  addressHint: "For example https://api.openai.com/v1",
  chatModel: "AI model",
  chatModelHint: "The name the service gives it.",
  searchModel: "AI model for search",
  searchModelHint: "The service's model for finding pages.",
  key: "API key",
  keyHint: "Kept in your Mac's keychain, never in your library.",
  keySaved: "Key saved",
  noKey: "No key",
  removeKey: "Remove key",
  removingKey: "Removing…",
  keyRemoved: "The key was removed.",
  keySavedNote: "The key was saved.",
  keyNotSupported: "This Mac can't keep a key for OpenKOS, so a service that needs one won't work from here.",
  showDetails: "Show details",
  models: "AI models",
  searchRebuild:
    "Search needs to be rebuilt for the new AI model. Until it is, answers may miss some pages.",

  warningTitle: "Use a cloud AI model?",
  warningConfirm: "Use the cloud AI model",
  warningCancel: "Keep it on this Mac",

  cloudOn: "Cloud AI model on",

  settledTitle: "Pages you settled",
  declinedTitle: "Disagreements you left as they are",
  undo: "Undo",
  pointOutAgain: "Point this out again",
  undoing: "Undoing…",

  libraryTitle: "Library",
  libraryText: "Your library is the folder where OpenKOS keeps everything.",
  folder: "Folder",
  noLibrary: "No library is open.",
  libraryFromOutside: "This copy of OpenKOS is set to use a library chosen outside the app, so it can't be changed here.",
  openAnother: "Open another library…",
  opening: "Opening…",
  createNew: "Create a new library…",
  creating: "Setting up…",
  switching: "Opening your library…",
} as const;

const NO_LIBRARY = "Open a library first.";

/**
 * The "Watch a folder" card (docs/decision-wording.md, "Watch a folder"). It
 * says what happens before the watch is turned on (ADR-0078, "Three"), and that
 * turning it on also runs the rest of what the engine does unattended ("Four").
 */
export const WATCH = {
  title: "Watch a folder",
  intro: "OpenKOS can watch a folder on your Mac and add the files you put in it.",
  what: [
    "Text files (like .txt and .md) that stay unchanged for a short while are added to your library by themselves, using the AI model, up to the daily limit set for it. Other files are ignored, and your folder is never changed.",
    "A file you edit after it was added becomes a new version.",
    "Combining pages, connections and replacing an older version are never done by themselves. They wait in To review for you.",
  ],
  also: "Watching also keeps your library in order. While it is on, OpenKOS tries again to save changes that couldn't be saved, and now and then goes over your library to refresh search and look for pages to review. That uses the AI model too, within the same daily limit.",
  stops: "Watching stops when you quit OpenKOS or open another library.",
  folder: "Folder",
  choose: "Choose a folder…",
  chooseAnother: "Choose another folder…",
  choosing: "Choosing…",
  start: "Start watching",
  starting: "Starting…",
  stop: "Stop watching",
  stopping: "Stopping…",
  cancel: "Cancel",
  insideLibrary: "That folder is part of your library. Choose a folder outside it.",
  notAFolder: "That isn't a folder. Choose a folder.",
  cannotUse: "OpenKOS can't use that folder. Please choose another.",
  noLibrary: NO_LIBRARY,
  stoppedNote: "OpenKOS stopped watching.",
  folderKept: "Watching is off, but the folder is still saved in your library's settings.",
  confirmTitle: (folder: string): string => `Watch '${folder}'?`,
  startedNote: (folder: string): string => `OpenKOS is watching '${folder}'.`,
  /** The folder was saved but the shell could not start (or remember) the watch. */
  notStarted: (error: unknown): string => {
    const reason = error instanceof WatchError ? error.reason : "unknown";
    const why =
      reason === "no_workspace"
        ? NO_LIBRARY
        : reason === "watch_choice_unkept"
          ? "OpenKOS couldn't remember your choice."
          : "Something went wrong.";
    return `The folder was saved, but watching didn't start. ${why}`;
  },
} as const;

/**
 * The persistent watch status (ADR-0078, "Five"): the card's line, its badge
 * word, and the short form the sidebar keeps on every screen.
 */
export const WATCH_STATUS = {
  on: "Watching is on.",
  stopped: "Watching is off.",
  budget:
    "Watching is paused. The AI model reached the limit set for it, so OpenKOS is waiting before it adds more files. It carries on by itself when the limit allows.",
  failed: "Watching stopped because something went wrong.",
  startAgain: "Start again",
  badge: { on: "On", stopped: "Off", budget: "Paused", failed: "Stopped" },
  footer: { on: "Watching: on", stopped: "Watching: off", budget: "Watching: paused", failed: "Watching: stopped" },
} as const;

/**
 * The "Updates" card and the sidebar's offer (docs/decision-wording.md,
 * "Updates"). The app never updates by itself (ADR-0079): it says what is
 * available and installs only when the person confirms.
 */
export const UPDATES = {
  title: "Updates",
  intro:
    "OpenKOS can check for a newer version of the app when it opens. It asks the OpenKOS project's release page on GitHub, and nothing from your library is sent. Nothing is installed until you say so.",
  on: "OpenKOS checks for updates when it opens.",
  off: "OpenKOS doesn't check for updates. You can still check now.",
  turnOff: "Stop checking",
  turnOn: "Check when OpenKOS opens",
  switching: "Saving…",
  checkNow: "Check now",
  checking: "Checking…",
  upToDate: "You have the latest version.",
  failed: "OpenKOS couldn't check for updates. It keeps working as it is. Try again later.",
  available: (version: string, current: string): string => `OpenKOS ${version} is available. You have ${current}.`,
  notes: "What's new",
  install: "Install and restart…",
  installing: "Installing…",
  confirmTitle: (version: string): string => `Install OpenKOS ${version}?`,
  confirmLines: [
    "OpenKOS will download the new version, check that it was signed by the OpenKOS project, install it and restart.",
    "Your library stays as it is. Anything running now, like watching a folder, stops and starts again with the app.",
  ],
  confirm: "Install and restart",
  cancel: "Not now",
  footer: (version: string): string => `Update available: ${version}`,
  installFailed: "The update couldn't be installed. Try again later.",
  offerGone: "That update is no longer on offer. Check again.",
  choiceUnkept: "OpenKOS couldn't remember your choice. Nothing was changed.",
} as const;

/** Why an update command was refused, from the shell's reason alone. */
export function updateFailure(error: unknown): string {
  const reason = error instanceof UpdateError ? error.reason : "unknown";
  switch (reason) {
    case "no_update":
    case "update_changed":
      return UPDATES.offerGone;
    case "choice_unkept":
      return UPDATES.choiceUnkept;
    default:
      return UPDATES.installFailed;
  }
}

/** Why the folder could not be chosen or saved, from the engine's code alone. */
export function watchFailure(error: unknown): string {
  const code = error instanceof ApiError ? error.code : "";
  switch (code) {
    case "inbox_inside_workspace":
      return WATCH.insideLibrary;
    case "inbox_not_a_directory":
      return WATCH.notAFolder;
    case "invalid_inbox":
      return WATCH.cannotUse;
    default:
      return saveFailure(error);
  }
}

/** Why the watch could not be stopped, from the shell's reason alone. */
export function watchCommandFailure(error: unknown): string {
  const reason = error instanceof WatchError ? error.reason : "unknown";
  return reason === "watch_choice_unkept"
    ? "OpenKOS couldn't remember your choice. Nothing was changed."
    : `Something went wrong. ${NOTHING_CHANGED}`;
}

type Settings = Schemas["ModelSettings"];

/** Who receives what is sent: the service's host, or hosts. */
export function receiver(settings: Pick<Settings, "remote_hosts">): string {
  return settings.remote_hosts.join(" and ");
}

/** The one line saying where the AI model runs now. */
export function whereItRuns(settings: Settings): string {
  if (settings.is_local) return "On this Mac.";
  return `In the cloud, at ${receiver(settings)}.`;
}

/**
 * The warning before a cloud AI model is turned on (roadmap, MVP 6): who
 * receives it, what is sent, what never is, and that it may cost money. The
 * send rule is the engine's: everything except "Only on this Mac" is sent,
 * and pages start as Private, so the warning says so.
 */
export function cloudWarning(proposed: Pick<Settings, "remote_hosts">): string[] {
  const host = receiver(proposed);
  return [
    `OpenKOS will send text from your library to ${host} whenever the AI model needs it: when it reads a file you add, checks your pages or answers a question.`,
    "Pages marked Shareable or Private are sent. New pages start as Private, so most of your library is sent.",
    "Pages marked Only on this Mac are never sent.",
    `${host} may charge you for this. Check its prices before you continue.`,
  ];
}

/** After the settings were saved. */
export function savedNote(settings: Settings, searchRebuild: boolean): string {
  const where = settings.is_local
    ? "Saved. OpenKOS now uses the AI model on this Mac."
    : `Saved. OpenKOS now uses the AI model at ${receiver(settings)}.`;
  return searchRebuild ? `${where} ${WORDS.searchRebuild}` : where;
}

const NOTHING_CHANGED = "Nothing was changed.";

/** Why the settings could not be saved, from the engine's code alone. */
export function saveFailure(error: unknown): string {
  if (error instanceof EngineUnreachable) return `OpenKOS isn't responding. ${NOTHING_CHANGED}`;
  const code = error instanceof ApiError ? error.code : "";
  switch (code) {
    case "invalid_base_url":
    case "invalid_embedding_base_url":
      return "That address doesn't look right. It should start with https:// and have no password in it.";
    case "base_url_required":
      return "Enter the address of the service.";
    case "invalid_model":
      return "That AI model name can't be used. Names use letters, numbers, dots, dashes, colons and slashes.";
    case "invalid_embedding_model":
      return "That search model name can't be used. Names use letters, numbers, dots, dashes, colons and slashes.";
    case "stale":
      return "Your settings changed while you were editing them. Here they are now.";
    case "workspace_busy":
    case "workspace_lock_unavailable":
      return `OpenKOS is busy. Try again in a moment. ${NOTHING_CHANGED}`;
    case "write_refused":
      return `Your library's settings file has changes OpenKOS didn't make, so it can't change them here. ${NOTHING_CHANGED}`;
    default:
      return `Something went wrong. ${NOTHING_CHANGED}`;
  }
}

/** Why the key could not be kept or removed, from the shell's reason alone. */
export function keyFailure(error: unknown): string {
  const reason = error instanceof ApiKeyError ? error.reason : "unknown";
  switch (reason) {
    case "invalid_key":
      return "That key can't be used. Check that you copied all of it.";
    case "not_supported":
      return WORDS.keyNotSupported;
    case "store_failed":
      return "Your Mac's keychain couldn't keep the key. Try again.";
    default:
      return "The key couldn't be saved. Try again.";
  }
}

/** The settings were saved but the key was not. */
export function keyAfterSaveFailure(error: unknown): string {
  return `Your AI model settings were saved, but the key wasn't. ${keyFailure(error)}`;
}

/** What `/v1/settings/model` names each AI model, for the details. */
export function modelFacts(settings: Settings): [string, string, "code"][] {
  return [
    [WORDS.chatModel, settings.model, "code"],
    [WORDS.searchModel, settings.embedding_model, "code"],
  ];
}
