// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "../../client/index.ts";
import type { SettingsModel, SettingsState } from "./model.ts";
import { renderSettings } from "./render.ts";
import { UPDATES, WATCH, WATCH_STATUS, WORDS } from "./wording.ts";

type Settings = Schemas["ModelSettings"];

const LOCAL: Settings = {
  backend: "ollama",
  base_url: null,
  embedding_base_url: null,
  model: "qwen3:8b",
  embedding_model: "bge-m3",
  remote_hosts: [],
  is_local: true,
  takes_api_key: false,
};

const CLOUD: Settings = {
  ...LOCAL,
  backend: "openai-compatible",
  base_url: "https://api.example.com/v1",
  model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  remote_hosts: ["api.example.com"],
  is_local: false,
  takes_api_key: true,
};

const READY: SettingsState = {
  phase: "ready",
  settings: LOCAL,
  key: "none",
  editing: false,
  draft: { where: "local", baseUrl: "", model: "", embeddingModel: "", key: "" },
  saving: false,
  removingKey: false,
  confirming: null,
  note: null,
  problem: null,
  library: { source: "remembered", path: "/Users/ana/Library", valid: true },
  libraryBusy: null,
  libraryProblem: null,
  librarySwitching: false,
  settled: [],
  declined: [],
  undoing: null,
  inbox: null,
  watch: null,
  watchBusy: null,
  watchConfirm: null,
  stoppedByYou: false,
  activity: null,
  updates: null,
  update: null,
  updateBusy: null,
  updateConfirm: false,
  updateProblem: null,
};

const NO_FOLDER = { inbox: null, quiet_seconds: 30, max_calls_per_day: 500 };
const WITH_FOLDER = { ...NO_FOLDER, inbox: "/Users/ana/Inbox" };
const OFF = { state: "off", wanted: false } as const;
const ON = { state: "on", attempt: 1, pid: 7, wanted: true } as const;

const SETTLED_ROW = {
  key: "a|b",
  text: "'Old plan' and 'New plan'",
  undo: { kind: "unreconcile", first: "a", second: "b", revision: false, outOfDate: "Old plan" },
} as const;
const DECLINED_ROW = {
  key: "c|d|",
  text: "'Tea' and 'Coffee'",
  undo: { kind: "reopen_contradiction", first: "c", second: "d", absorbed: null },
} as const;

const METHODS = [
  "load",
  "edit",
  "cancel",
  "choose",
  "setField",
  "save",
  "confirm",
  "dismiss",
  "removeKey",
  "openLibrary",
  "createLibrary",
  "undo",
  "chooseWatchFolder",
  "cancelWatch",
  "confirmWatch",
  "startWatch",
  "stopWatch",
  "checkForUpdate",
  "setUpdateCheck",
  "askInstall",
  "cancelInstall",
  "confirmInstall",
] as const;

function fakeModel() {
  return Object.fromEntries(METHODS.map((name) => [name, vi.fn()])) as unknown as SettingsModel &
    Record<(typeof METHODS)[number], ReturnType<typeof vi.fn>>;
}

function root(): HTMLElement {
  const node = document.createElement("div");
  document.body.replaceChildren(node);
  return node;
}

const button = (node: ParentNode, label: string) =>
  Array.from(node.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.textContent === label);
const input = (node: ParentNode, label: string) => {
  const found = Array.from(node.querySelectorAll("label")).find((l) => l.textContent === label);
  return node.querySelector<HTMLInputElement>(`#${found?.htmlFor ?? "missing"}`);
};

afterEach(() => document.body.replaceChildren());

describe("renderSettings", () => {
  it("says where the AI model runs and which library is open, and little else", () => {
    const node = root();
    renderSettings(node, fakeModel(), READY);

    expect(node.querySelector("h1")?.textContent).toBe("Settings");
    const titles = Array.from(node.querySelectorAll(".ui-card__title"), (t) => t.textContent);
    expect(titles).toEqual(["AI model", "Library"]);
    expect(node.textContent).toContain("On this Mac.");
    const library = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    expect(library.querySelector("code")?.textContent).toBe("/Users/ana/Library");
    expect(node.querySelector("input")).toBeNull();
    expect(node.textContent).not.toContain(WORDS.key);
  });

  it("names the cloud service and whether a key is saved, never the key", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, settings: CLOUD, key: "saved" });

    expect(node.textContent).toContain("In the cloud, at api.example.com.");
    expect(node.textContent).toContain(WORDS.keySaved);
    expect(button(node, WORDS.removeKey)).toBeDefined();
  });

  it("holds the model names behind a disclosure", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, settings: CLOUD });

    const details = node.querySelector("details") as HTMLDetailsElement;
    expect(details.querySelector("summary")?.textContent).toBe(WORDS.showDetails);
    expect(details.textContent).toContain("gpt-4o-mini");
  });

  it("opens the form on Change", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, READY);

    button(node, WORDS.change)?.click();

    expect(model.edit).toHaveBeenCalledOnce();
  });

  it("asks for the address, the models and the key of a cloud model", () => {
    const node = root();
    const model = fakeModel();
    const draft = { where: "cloud" as const, baseUrl: "https://a.example", model: "m", embeddingModel: "e", key: "" };
    renderSettings(node, model, { ...READY, editing: true, draft });

    expect(input(node, WORDS.address)?.value).toBe("https://a.example");
    expect(input(node, WORDS.chatModel)?.value).toBe("m");
    expect(input(node, WORDS.searchModel)?.value).toBe("e");
    const key = input(node, WORDS.key) as HTMLInputElement;
    expect(key.type).toBe("password");
    expect(key.value).toBe("");

    key.value = "sk-1";
    key.dispatchEvent(new Event("input"));
    expect(model.setField).toHaveBeenCalledWith("key", "sk-1");
    button(node, WORDS.save)?.click();
    expect(model.save).toHaveBeenCalledOnce();
    button(node, WORDS.cancel)?.click();
    expect(model.cancel).toHaveBeenCalledOnce();
  });

  it("asks for nothing more for this Mac", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, editing: true });

    expect(node.querySelectorAll("input")).toHaveLength(0);
    expect(node.textContent).toContain(WORDS.localText);
    expect(node.querySelector('[role="radio"][aria-checked="true"]')?.textContent).toBe(WORDS.local);
  });

  it("switches where the model runs from the choice", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, editing: true });

    (Array.from(node.querySelectorAll<HTMLElement>('[role="radio"]')).find((r) => r.textContent === WORDS.cloud) as HTMLElement).click();

    expect(model.choose).toHaveBeenCalledWith("cloud");
  });

  it("says when the Mac cannot keep a key, instead of a field", () => {
    const node = root();
    const draft = { where: "cloud" as const, baseUrl: "", model: "", embeddingModel: "", key: "" };
    renderSettings(node, fakeModel(), { ...READY, editing: true, draft, key: "unsupported" });

    expect(input(node, WORDS.key)).toBeNull();
    expect(node.textContent).toContain(WORDS.keyNotSupported);
  });

  it("shows the warning in a dialog that starts on keeping it on this Mac", () => {
    const node = root();
    const model = fakeModel();
    const lines = ["Who.", "What.", "Never.", "Cost."];
    renderSettings(node, model, { ...READY, editing: true, confirming: { lines } });

    const dialog = document.querySelector('[role="alertdialog"]') as HTMLElement;
    expect(dialog.querySelector("h2")?.textContent).toBe(WORDS.warningTitle);
    expect(Array.from(dialog.querySelectorAll("p"), (p) => p.textContent)).toEqual(lines);
    expect(document.activeElement?.textContent).toBe(WORDS.warningCancel);

    button(dialog, WORDS.warningConfirm)?.click();
    expect(model.confirm).toHaveBeenCalledOnce();
  });

  it("declining the warning dismisses it, and a redraw does not open a second one", () => {
    const node = root();
    const model = fakeModel();
    const state = { ...READY, editing: true, confirming: { lines: ["a"] } };
    renderSettings(node, model, state);
    renderSettings(node, model, state);
    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1);

    button(document, WORDS.warningCancel)?.click();
    expect(model.dismiss).toHaveBeenCalledOnce();

    renderSettings(node, model, { ...state, confirming: null });
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  });

  it("announces what a save did, and a refusal", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, note: "Saved.", problem: null });
    expect(node.querySelector('[role="status"]')?.textContent).toContain("Saved.");

    renderSettings(node, fakeModel(), { ...READY, note: null, problem: "No." });
    expect(node.querySelector('[role="alert"]')?.textContent).toContain("No.");
  });

  it("opens or creates another library", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, READY);

    button(node, WORDS.openAnother)?.click();
    button(node, WORDS.createNew)?.click();

    expect(model.openLibrary).toHaveBeenCalledOnce();
    expect(model.createLibrary).toHaveBeenCalledOnce();
  });

  it("offers no library choice when it was made outside the app", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, library: { source: "environment", path: "/x", valid: true } });

    expect(button(node, WORDS.openAnother)).toBeUndefined();
    expect(node.textContent).toContain(WORDS.libraryFromOutside);
  });

  it("says it is opening the library while the app switches", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, librarySwitching: true });

    expect(node.textContent).toContain(WORDS.switching);
    expect(button(node, WORDS.openAnother)).toBeUndefined();
  });

  it("waits, and offers to read again after a failure", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, phase: "loading", settings: null });
    expect(node.textContent).toContain(WORDS.loading);

    renderSettings(node, model, { ...READY, phase: "failed", settings: null });
    button(node, WORDS.tryAgain)?.click();
    expect(model.load).toHaveBeenCalledOnce();
  });

  it("shows no list of earlier decisions while there are none", () => {
    const node = root();
    renderSettings(node, fakeModel(), READY);

    expect(node.textContent).not.toContain("Pages you settled");
    expect(node.textContent).not.toContain("Disagreements you left as they are");
  });

  it("lists the pages you settled, each with Undo", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, settled: [SETTLED_ROW] });

    const titles = Array.from(node.querySelectorAll(".ui-card__title"), (t) => t.textContent);
    expect(titles).toEqual(["AI model", "Pages you settled", "Library"]);
    const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    expect(card.querySelector("li")?.textContent).toContain("'Old plan' and 'New plan'");
    button(card, "Undo")?.click();
    expect(model.undo).toHaveBeenCalledWith("settled", "a|b");
  });

  it("lists the disagreements you left as they are, each with Point this out again", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, declined: [DECLINED_ROW] });

    const titles = Array.from(node.querySelectorAll(".ui-card__title"), (t) => t.textContent);
    expect(titles).toEqual(["AI model", "Disagreements you left as they are", "Library"]);
    const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    expect(card.textContent).toContain("'Tea' and 'Coffee'");
    button(card, "Point this out again")?.click();
    expect(model.undo).toHaveBeenCalledWith("declined", "c|d|");
  });

  it("offers to watch a folder, saying what happens first and what else the watch does", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, inbox: NO_FOLDER, watch: OFF });

    const titles = Array.from(node.querySelectorAll(".ui-card__title"), (t) => t.textContent);
    expect(titles).toEqual(["AI model", WATCH.title, "Library"]);
    const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    for (const line of [WATCH.intro, ...WATCH.what, WATCH.also, WATCH.stops]) expect(card.textContent).toContain(line);
    expect(button(card, WATCH.stop)).toBeUndefined();
    button(card, WATCH.choose)?.click();
    expect(model.chooseWatchFolder).toHaveBeenCalledOnce();
  });

  it("draws no watch card until the engine has said what the folder is", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, inbox: null, watch: OFF });

    expect(node.textContent).not.toContain(WATCH.title);
  });

  it("asks before it starts, naming the folder, with Start watching and Cancel", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, inbox: NO_FOLDER, watch: OFF, watchConfirm: { folder: "/Users/ana/Inbox" } });

    const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    expect(card.textContent).toContain(WATCH.confirmTitle("/Users/ana/Inbox"));
    expect(button(card, WATCH.choose)).toBeUndefined();
    button(card, WATCH.start)?.click();
    expect(model.confirmWatch).toHaveBeenCalledOnce();
    button(card, WATCH.cancel)?.click();
    expect(model.cancelWatch).toHaveBeenCalledOnce();
  });

  it("shows the watched folder with Stop watching and Choose another folder", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, inbox: WITH_FOLDER, watch: ON });

    const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    expect(card.querySelector("code")?.textContent).toBe("/Users/ana/Inbox");
    button(card, WATCH.stop)?.click();
    expect(model.stopWatch).toHaveBeenCalledOnce();
    button(card, WATCH.chooseAnother)?.click();
    expect(model.chooseWatchFolder).toHaveBeenCalledOnce();
    expect(button(card, WATCH.start)).toBeUndefined();
  });

  it("offers Start watching for a saved folder that is not being watched", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, inbox: WITH_FOLDER, watch: OFF });

    const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    button(card, WATCH.start)?.click();
    expect(model.startWatch).toHaveBeenCalledOnce();
    expect(button(card, WATCH.stop)).toBeUndefined();
  });

  it("says in the card whether watching is on, off, paused or stopped", () => {
    const failed = { state: "failed", reason: "exited", detail: "the daemon exited (1).\nboom", wanted: true } as const;
    const spent = { last_job: null, budget_exhausted: true, jobs_unreadable: false };
    const cases = [
      [{ inbox: WITH_FOLDER, watch: ON }, WATCH_STATUS.on],
      [{ inbox: WITH_FOLDER, watch: OFF }, WATCH_STATUS.stopped],
      [{ inbox: NO_FOLDER, watch: OFF, stoppedByYou: true }, WATCH_STATUS.stopped],
      [{ inbox: WITH_FOLDER, watch: ON, activity: spent }, WATCH_STATUS.budget],
      [{ inbox: WITH_FOLDER, watch: failed }, WATCH_STATUS.failed],
    ] as const;
    for (const [patch, line] of cases) {
      const node = root();
      renderSettings(node, fakeModel(), { ...READY, ...patch });
      const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
      expect(card.querySelector("[data-watch-status]")?.textContent).toContain(line);
    }
  });

  it("says nothing of the status before the watch was ever turned on", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, inbox: NO_FOLDER, watch: OFF });

    expect(node.querySelector("[data-watch-status]")).toBeNull();
  });

  it("offers Start again after a failure, with the details behind a disclosure", () => {
    const node = root();
    const model = fakeModel();
    const failed = { state: "failed", reason: "exited", detail: "the daemon exited (1).\nboom", wanted: true } as const;
    renderSettings(node, model, { ...READY, inbox: WITH_FOLDER, watch: failed });

    const card = node.querySelectorAll(".ui-card")[1] as HTMLElement;
    const details = card.querySelector("details") as HTMLDetailsElement;
    expect(details.querySelector("summary")?.textContent).toBe(WORDS.showDetails);
    expect(details.textContent).toContain("boom");
    expect(card.querySelector("[data-watch-status]")?.textContent).not.toContain("boom");
    button(card, WATCH_STATUS.startAgain)?.click();
    expect(model.startWatch).toHaveBeenCalledOnce();
  });

  it("shows what is being done while it is done", () => {
    for (const [busy, label] of [
      ["choosing", WATCH.choosing],
      ["starting", WATCH.starting],
      ["stopping", WATCH.stopping],
    ] as const) {
      const node = root();
      const state =
        busy === "choosing" ? { inbox: NO_FOLDER, watch: OFF } : { inbox: WITH_FOLDER, watch: busy === "starting" ? OFF : ON };
      renderSettings(node, fakeModel(), { ...READY, ...state, watchBusy: busy });

      const busyButton = Array.from(node.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.getAttribute("aria-busy") === "true");
      expect(busyButton?.textContent).toBe(label);
    }
  });

  it("shows the row being undone as busy and holds the other rows", () => {
    const node = root();
    const other = { ...SETTLED_ROW, key: "e|f", text: "'Tea' and 'Coffee'" };
    renderSettings(node, fakeModel(), { ...READY, settled: [SETTLED_ROW, other], undoing: "a|b" });

    const [busy, held] = Array.from(node.querySelectorAll<HTMLButtonElement>("li button"));
    expect(busy?.textContent).toBe("Undoing…");
    expect(busy?.getAttribute("aria-busy")).toBe("true");
    expect(held?.textContent).toBe("Undo");
    expect(held?.disabled).toBe(true);
  });

  it("says whether the app checks for updates, and lets the person turn it off or check now", () => {
    const node = root();
    const model = fakeModel();
    renderSettings(node, model, { ...READY, updates: { check: true }, update: { state: "up_to_date" } });

    const titles = Array.from(node.querySelectorAll(".ui-card__title"), (t) => t.textContent);
    expect(titles).toEqual(["AI model", UPDATES.title, "Library"]);
    expect(node.textContent).toContain(UPDATES.intro);
    expect(node.textContent).toContain(UPDATES.on);
    expect(node.textContent).toContain(UPDATES.upToDate);
    button(node, UPDATES.turnOff)?.click();
    expect(model.setUpdateCheck).toHaveBeenCalledWith(false);
    button(node, UPDATES.checkNow)?.click();
    expect(model.checkForUpdate).toHaveBeenCalledOnce();

    renderSettings(node, model, { ...READY, updates: { check: false }, update: { state: "off" } });
    expect(node.textContent).toContain(UPDATES.off);
    button(node, UPDATES.turnOn)?.click();
    expect(model.setUpdateCheck).toHaveBeenCalledWith(true);
  });

  it("shows a newer version and its notes, and asks before installing", () => {
    const node = root();
    const model = fakeModel();
    const update = { state: "available", version: "0.7.0", current_version: "0.6.0", notes: "What changed." } as const;
    renderSettings(node, model, { ...READY, updates: { check: true }, update });

    expect(node.textContent).toContain(UPDATES.available("0.7.0", "0.6.0"));
    expect(node.textContent).toContain("What changed.");
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
    button(node, UPDATES.install)?.click();
    expect(model.askInstall).toHaveBeenCalledOnce();
    expect(model.confirmInstall).not.toHaveBeenCalled();
  });

  it("confirms the install in a dialog that starts on not now", () => {
    const node = root();
    const model = fakeModel();
    const update = { state: "available", version: "0.7.0", current_version: "0.6.0", notes: null } as const;
    const state = { ...READY, updates: { check: true }, update, updateConfirm: true };
    renderSettings(node, model, state);
    renderSettings(node, model, state);

    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1);
    const dialog = document.querySelector('[role="alertdialog"]') as HTMLElement;
    expect(dialog.querySelector("h2")?.textContent).toBe(UPDATES.confirmTitle("0.7.0"));
    expect(Array.from(dialog.querySelectorAll("p"), (p) => p.textContent)).toEqual([...UPDATES.confirmLines]);
    expect(document.activeElement?.textContent).toBe(UPDATES.cancel);

    button(dialog, UPDATES.confirm)?.click();
    expect(model.confirmInstall).toHaveBeenCalledOnce();
  });

  it("closes the install dialog on not now", () => {
    const node = root();
    const model = fakeModel();
    const update = { state: "available", version: "0.7.0", current_version: "0.6.0", notes: null } as const;
    renderSettings(node, model, { ...READY, updates: { check: true }, update, updateConfirm: true });

    button(document, UPDATES.cancel)?.click();
    expect(model.cancelInstall).toHaveBeenCalledOnce();
    expect(model.confirmInstall).not.toHaveBeenCalled();

    renderSettings(node, model, { ...READY, updates: { check: true }, update, updateConfirm: false });
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  });

  it("says quietly when a check failed, and why an install was refused", () => {
    const node = root();
    renderSettings(node, fakeModel(), {
      ...READY,
      updates: { check: true },
      update: { state: "failed" },
      updateProblem: UPDATES.installFailed,
    });
    expect(node.textContent).toContain(UPDATES.failed);
    expect(node.querySelector('[role="alert"]')?.textContent).toContain(UPDATES.installFailed);
  });

  it("keeps the updates card when the engine is not running", () => {
    const node = root();
    renderSettings(node, fakeModel(), { ...READY, phase: "failed", settings: null, updates: { check: true }, update: null });
    expect(Array.from(node.querySelectorAll(".ui-card__title"), (t) => t.textContent)).toEqual([UPDATES.title]);
  });
});
