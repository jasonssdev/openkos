// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { Schemas } from "../../client/index.ts";
import type { SetupModel, SetupState, WelcomeModel, WelcomeState } from "./model.ts";
import { renderSetup, renderWelcome } from "./render.ts";

const GiB = 1024 ** 3;

const WELCOME: WelcomeState = { busy: null, started: false, problem: null, moved: null };

function welcomeModel() {
  return { create: vi.fn(), open: vi.fn(), openPath: vi.fn() } as unknown as WelcomeModel & {
    create: ReturnType<typeof vi.fn>;
    open: ReturnType<typeof vi.fn>;
    openPath: ReturnType<typeof vi.fn>;
  };
}

function setupModel() {
  return {
    load: vi.fn(),
    openDownload: vi.fn(),
    download: vi.fn(),
    downloadAll: vi.fn(),
    stop: vi.fn(),
  } as unknown as SetupModel & Record<"load" | "openDownload" | "download" | "downloadAll" | "stop", ReturnType<typeof vi.fn>>;
}

function root(): HTMLElement {
  const node = document.createElement("div");
  document.body.replaceChildren(node);
  return node;
}

const buttons = (node: Element) => Array.from(node.querySelectorAll("button"), (b) => b.textContent);
const button = (node: Element, label: string) =>
  Array.from(node.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.textContent === label) as HTMLButtonElement;

describe("renderWelcome", () => {
  it("asks for a folder with two paths, creating one first", () => {
    const node = root();
    const model = welcomeModel();
    renderWelcome(node, model, WELCOME);
    const title = node.querySelector("h1") as HTMLElement;
    expect(title.textContent).toBe("Welcome to OpenKOS");
    expect(title.dataset["view"]).toBe("heading");
    expect(node.querySelector(".ui-page-header__description")?.textContent).toBe("Choose a folder for your library.");
    expect(buttons(node)).toEqual(["Create a new library", "Open an existing library"]);
    expect(button(node, "Create a new library").className).toContain("ui-button--primary");
    expect(button(node, "Open an existing library").className).toContain("ui-button--secondary");
    expect(node.textContent).toContain("A new library needs an empty folder.");
    button(node, "Create a new library").click();
    button(node, "Open an existing library").click();
    expect(model.create).toHaveBeenCalledOnce();
    expect(model.open).toHaveBeenCalledOnce();
    expect(node.querySelector(".ui-callout")).toBeNull();
  });

  it("keeps the running choice busy and the other one unavailable", () => {
    const node = root();
    renderWelcome(node, welcomeModel(), { ...WELCOME, busy: "create" });
    const create = node.querySelector<HTMLButtonElement>("button") as HTMLButtonElement;
    expect(create.textContent).toBe("Setting up your library…");
    expect(create.getAttribute("aria-busy")).toBe("true");
    expect(button(node, "Open an existing library").disabled).toBe(true);
  });

  it("says the library is opening once one is chosen, and offers nothing else", () => {
    const node = root();
    renderWelcome(node, welcomeModel(), { ...WELCOME, started: true });
    expect(node.querySelector(".ui-loading")?.textContent).toBe("Opening your library…");
    expect(buttons(node)).toEqual([]);
  });

  it("words a refusal, with Open it and the details when it has them", () => {
    const node = root();
    const model = welcomeModel();
    const problem = { text: "That folder already holds an OpenKOS library.", openPath: "/Users/me/kb", detail: "the report" };
    renderWelcome(node, model, { ...WELCOME, problem });
    const callout = node.querySelector(".ui-callout--error") as HTMLElement;
    expect(callout.getAttribute("role")).toBe("alert");
    expect(callout.querySelector(".ui-callout__text")?.textContent).toBe(problem.text);
    button(callout, "Open it").click();
    expect(model.openPath).toHaveBeenCalledWith("/Users/me/kb");
    const details = callout.querySelector("details") as HTMLDetailsElement;
    expect(details.querySelector("summary")?.textContent).toBe("Show details");
    expect(details.querySelector("pre")?.textContent).toBe("the report");
  });

  it("says where the library chosen before was", () => {
    const node = root();
    renderWelcome(node, welcomeModel(), { ...WELCOME, moved: "/old/kb" });
    expect(node.querySelector("p code")?.textContent).toBe("/old/kb");
    expect(node.textContent).toContain("The library you used before isn't at /old/kb any more.");
  });
});

function readiness(over: { reachable?: boolean; installed?: Record<string, boolean>; local?: boolean; meets?: boolean } = {}) {
  const installed = over.installed ?? { "qwen3:8b": false, "bge-m3": false };
  const reachable = over.reachable ?? true;
  const models = Object.entries(installed).map(([tag, value]) => ({ tag, roles: ["chat"], required: true, installed: value, pullable: true }));
  return {
    runtime: {
      backend: "ollama",
      host: "localhost:11434",
      is_local: over.local ?? true,
      reachable,
      version: null,
      download_url: "https://ollama.com/download",
    },
    models,
    memory: { total_bytes: (over.meets === false ? 16 : 64) * GiB, floor_bytes: 32 * GiB, meets_floor: over.meets ?? true },
    ready: reachable && models.every((m) => m.installed),
  } as Schemas["SetupReadiness"];
}

const STATE: SetupState = { readiness: null, failed: false, checking: true, pull: null, waiting: [], notes: {}, browserFailed: false };

function draw(state: Partial<SetupState>, model = setupModel(), onDone = vi.fn()) {
  const node = root();
  renderSetup(node, model, { ...STATE, ...state }, { onDone });
  return { node, model, onDone };
}

describe("renderSetup", () => {
  it("shows a placeholder while it first checks", () => {
    const { node } = draw({});
    expect(node.querySelector("h1")?.textContent).toBe("Set up OpenKOS");
    expect(node.querySelector(".ui-page-header__description")?.textContent).toBe(
      "OpenKOS needs the internet only for these steps. Nothing you add is sent.",
    );
    expect(node.querySelector(".ui-skeleton")?.textContent).toBe("Checking your setup.");
  });

  it("says when it could not check, with Try again", () => {
    const { node, model } = draw({ checking: false, failed: true });
    const callout = node.querySelector(".ui-callout--error") as HTMLElement;
    expect(callout.querySelector(".ui-callout__text")?.textContent).toBe("OpenKOS couldn't check its setup.");
    button(callout, "Try again").click();
    expect(model.load).toHaveBeenCalledOnce();
  });

  it("sends a person with no Ollama to its download, and checks again", () => {
    const { node, model } = draw({ checking: false, readiness: readiness({ reachable: false }) });
    expect(node.querySelector(".ui-card__title")?.textContent).toBe("Install Ollama");
    expect(node.querySelector(".ui-card__meta")?.textContent).toBe("Step 1 of 2");
    expect(buttons(node)).toEqual(["Get Ollama", "Check again"]);
    expect(button(node, "Get Ollama").className).toContain("ui-button--primary");
    button(node, "Get Ollama").click();
    button(node, "Check again").click();
    expect(model.openDownload).toHaveBeenCalledOnce();
    expect(model.load).toHaveBeenCalledOnce();
    expect(node.textContent).not.toMatch(/cloud|provider|API key/i);
  });

  it("keeps Check again busy while it checks, and says where to go when the browser did not open", () => {
    const { node } = draw({ checking: true, browserFailed: true, readiness: readiness({ reachable: false }) });
    expect(button(node, "Checking…").getAttribute("aria-busy")).toBe("true");
    const callout = node.querySelector(".ui-callout--warning") as HTMLElement;
    expect(callout.querySelector(".ui-callout__text")?.textContent).toBe(
      "Your browser didn't open. You can go to https://ollama.com/download yourself.",
    );
  });

  it("offers no download for an AI model elsewhere", () => {
    const { node } = draw({ checking: false, readiness: readiness({ reachable: false, local: false }) });
    expect(node.querySelector(".ui-card__title")?.textContent).toBe("Reach the AI model");
    expect(buttons(node)).toEqual(["Check again"]);
    expect(button(node, "Check again").className).toContain("ui-button--primary");
  });

  it("lists the models with one download each, and Download all", () => {
    const { node, model } = draw({ checking: false, readiness: readiness() });
    expect(node.querySelector(".ui-card__title")?.textContent).toBe("Download the AI models");
    expect(node.querySelector(".ui-card__meta")?.textContent).toBe("Step 2 of 2");
    expect(Array.from(node.querySelectorAll("dt code"), (n) => n.textContent)).toEqual(["qwen3:8b", "bge-m3"]);
    expect(buttons(node)).toEqual(["Download", "Download", "Download all"]);
    expect(button(node, "Download all").className).toContain("ui-button--primary");
    (node.querySelectorAll("dd button")[1] as HTMLButtonElement).click();
    button(node, "Download all").click();
    expect(model.download).toHaveBeenCalledWith("bge-m3");
    expect(model.downloadAll).toHaveBeenCalledOnce();
  });

  it("makes the only missing model's download the primary action", () => {
    const { node } = draw({ checking: false, readiness: readiness({ installed: { "qwen3:8b": true, "bge-m3": false } }) });
    expect(buttons(node)).toEqual(["Download"]);
    expect(button(node, "Download").className).toContain("ui-button--primary");
    expect(node.querySelector("dd .ui-badge--success")?.textContent).toBe("✓Installed");
  });

  it("shows a running download's progress with Stop, and the waiting one", () => {
    const { node, model } = draw({
      checking: false,
      readiness: readiness(),
      pull: { model: "qwen3:8b", id: "p1", view: { state: "running", phase: "downloading", completed: 1_300_000_000, total: 5_200_000_000 } },
      waiting: ["bge-m3"],
    });
    const bar = node.querySelector("progress") as HTMLProgressElement;
    expect(bar.getAttribute("aria-label")).toBe("Downloading qwen3:8b");
    expect(bar.value).toBe(1_300_000_000);
    expect(node.textContent).toContain("1.3 GB of 5.2 GB (25%)");
    expect(node.textContent).toContain("Waiting…");
    expect(buttons(node)).toEqual(["Stop"]);
    button(node, "Stop").click();
    expect(model.stop).toHaveBeenCalledOnce();
  });

  it("words a stopped and a failed download, each with its own way on", () => {
    const { node, model } = draw({
      checking: false,
      readiness: readiness(),
      notes: {
        "qwen3:8b": { kind: "stopped", text: "Stopped at 1.3 GB of 5.2 GB. Downloading again continues from there." },
        "bge-m3": { kind: "failed", text: "Another download is already running. Try again in a moment." },
      },
    });
    expect(buttons(node)).toEqual(["Continue download", "Try again", "Download all"]);
    expect(node.textContent).toContain("Stopped at 1.3 GB of 5.2 GB.");
    expect(node.querySelector("dd .ui-field__error, dd [role='alert']")?.textContent).toContain("Another download is already running.");
    button(node, "Try again").click();
    expect(model.download).toHaveBeenCalledWith("bge-m3");
  });

  it("adds an honest note below the memory floor, without blocking", () => {
    const { node } = draw({ checking: false, readiness: readiness({ meets: false }) });
    const note = node.querySelector(".ui-callout--info") as HTMLElement;
    expect(note.querySelector(".ui-callout__text")?.textContent).toMatch(/^This Mac has 16 GB of memory\./);
    expect(buttons(node)).toContain("Download all");
  });

  it("says it is ready and works offline, then goes on", () => {
    const { node, onDone } = draw({ checking: false, readiness: readiness({ installed: { "qwen3:8b": true, "bge-m3": true } }) });
    const header = node.querySelector(".ui-page-header") as HTMLElement;
    expect(`${header.querySelector("h1")?.textContent} ${header.querySelector("p")?.textContent}`).toBe(
      "You're ready. OpenKOS works offline from now on.",
    );
    expect(buttons(node)).toEqual(["Start using OpenKOS"]);
    button(node, "Start using OpenKOS").click();
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("keeps focus on Stop across a progress redraw", () => {
    const pull = (completed: number) => ({
      model: "qwen3:8b",
      id: "p1",
      view: { state: "running" as const, phase: "downloading" as const, completed, total: 10 },
    });
    const model = setupModel();
    const node = root();
    renderSetup(node, model, { ...STATE, checking: false, readiness: readiness(), pull: pull(1) }, { onDone: vi.fn() });
    button(node, "Stop").focus();
    renderSetup(node, model, { ...STATE, checking: false, readiness: readiness(), pull: pull(2) }, { onDone: vi.fn() });
    expect(document.activeElement?.textContent).toBe("Stop");
  });
});
