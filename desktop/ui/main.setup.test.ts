// @vitest-environment jsdom
// The first run against a fake shell and a fake engine: no library yet, so the
// welcome; a library is created, the engine starts, setup is not ready, so the
// guided setup opens on its own, and Status offers it again later.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const TOKEN = "f".repeat(64);
const READY = { state: "ready", attempt: 1, base_url: "http://127.0.0.1:4321/v1", token: TOKEN, pid: 7 };

let engine: unknown = { state: "no_workspace" };
const calls: { command: string; args: unknown }[] = [];
const invoke = vi.fn(async (command: string, args?: Record<string, unknown>) => {
  calls.push({ command, args });
  switch (command) {
    case "engine_state":
      return engine;
    case "workspace_choice_status":
      return { source: "none", path: null, valid: false };
    case "pick_workspace_folder":
      return "/Users/me/library";
    case "create_workspace":
      engine = { state: "starting", attempt: 1 };
      return { source: "remembered", path: "/Users/me/library", valid: true };
    case "open_runtime_download":
      return null;
    default:
      throw new Error(`unexpected ${command}`);
  }
});

const READINESS = {
  runtime: { backend: "ollama", host: "localhost:11434", is_local: true, reachable: false, version: null, download_url: "https://ollama.com/download" },
  models: [{ tag: "qwen3:8b", roles: ["chat"], required: true, installed: null, pullable: true }],
  memory: { total_bytes: 64 * 1024 ** 3, floor_bytes: 32 * 1024 ** 3, meets_floor: true },
  ready: false,
};

const fetched: string[] = [];
const fetchMock = vi.fn(async (request: Request) => {
  const path = new URL(request.url).pathname;
  fetched.push(path);
  const body =
    path === "/v1/setup/readiness"
      ? READINESS
      : path === "/v1/status"
        ? { root: "/Users/me/library", sources: 0, concepts: 0, open_decisions_by_kind: {} }
        : { decisions: [] };
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
});

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

const view = (name: string) => document.querySelector(`[data-screen="${name}"]`) as HTMLElement;
const button = (root: Element, label: string) =>
  Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.textContent === label) as HTMLButtonElement;

beforeAll(async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", fetchMock);
  const page = readFileSync(join(import.meta.dirname, "index.html"), "utf8");
  document.body.innerHTML = page.slice(page.indexOf("<body"), page.indexOf("</body>")).replace(/^<body[^>]*>/, "");
  Object.assign(window, { __TAURI__: { core: { invoke } } });
  await import("./main.ts");
  await settle();
});

afterAll(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the first run", () => {
  it("welcomes a person with no library instead of an error, with the navigation held back", () => {
    expect(view("welcome").hidden).toBe(false);
    expect(view("status").hidden).toBe(true);
    expect(view("welcome").querySelector("h1")?.textContent).toBe("Welcome to OpenKOS");
    expect((document.querySelector(".ui-nav") as HTMLElement).hidden).toBe(true);
    expect(document.querySelector(".ui-sidebar__footer .ui-badge__word")?.textContent).toBe("No library open");
  });

  it("creates a library in the chosen folder through the shell", async () => {
    button(view("welcome"), "Create a new library").click();
    await settle();
    expect(calls.map((c) => c.command)).toContain("pick_workspace_folder");
    expect(calls.find((c) => c.command === "create_workspace")?.args).toEqual({ path: "/Users/me/library" });
    expect(view("welcome").querySelector(".ui-loading")?.textContent).toBe("Opening your library…");
  });

  it("leaves the welcome once the engine starts, and opens setup when it is not ready", async () => {
    await vi.advanceTimersByTimeAsync(1000);
    expect(view("welcome").hidden).toBe(true);
    expect((document.querySelector(".ui-nav") as HTMLElement).hidden).toBe(false);
    engine = READY;
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    expect(fetched).toContain("/v1/setup/readiness");
    expect(view("setup").hidden).toBe(false);
    expect(view("setup").querySelector(".ui-card__title")?.textContent).toBe("Install Ollama");
  });

  it("opens the download page through the shell, naming no address", async () => {
    button(view("setup"), "Get Ollama").click();
    await settle();
    expect(calls.find((c) => c.command === "open_runtime_download")?.args).toBeUndefined();
  });

  it("offers Check setup on Status while setup is not ready", async () => {
    const status = Array.from(document.querySelectorAll<HTMLButtonElement>(".ui-nav-item")).find((n) => n.textContent === "Status");
    status?.click();
    await vi.advanceTimersByTimeAsync(1000);
    expect(view("setup").hidden).toBe(true);
    button(view("status"), "Check setup").click();
    expect(view("setup").hidden).toBe(false);
  });

  it("does not open setup on its own again after the engine restarts", async () => {
    const status = Array.from(document.querySelectorAll<HTMLButtonElement>(".ui-nav-item")).find((n) => n.textContent === "Status");
    status?.click();
    engine = { state: "restarting", attempt: 1, delay_ms: 500, reason: "exited", detail: "x" };
    await vi.advanceTimersByTimeAsync(1000);
    engine = { ...READY, pid: 8 };
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    expect(view("setup").hidden).toBe(true);
    expect(button(view("status"), "Check setup")).toBeDefined();
  });
});
