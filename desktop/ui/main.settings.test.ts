// @vitest-environment jsdom
// Settings against a fake shell and a fake engine: the quiet entry at the foot
// of the sidebar, the calm line while a cloud AI model is on, and the screen it
// opens, read through the local API and the shell's key command.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const TOKEN = "f".repeat(64);
const READY = { state: "ready", attempt: 1, base_url: "http://127.0.0.1:4321/v1", token: TOKEN, pid: 7 };

const invoke = vi.fn(async (command: string) => {
  switch (command) {
    case "engine_state":
      return READY;
    case "api_key_status":
      return true;
    case "workspace_choice_status":
      return { source: "remembered", path: "/Users/me/library", valid: true };
    default:
      throw new Error(`unexpected ${command}`);
  }
});

const CLOUD = {
  backend: "openai-compatible",
  base_url: "https://api.example.com/v1",
  embedding_base_url: null,
  model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  remote_hosts: ["api.example.com"],
  is_local: false,
  takes_api_key: true,
};

const READINESS = {
  runtime: { backend: "openai-compatible", host: "api.example.com", is_local: false, reachable: true, version: null, download_url: null },
  models: [],
  memory: { total_bytes: null, floor_bytes: 32 * 1024 ** 3, meets_floor: null },
  ready: true,
};

const fetchMock = vi.fn(async (request: Request) => {
  const path = new URL(request.url).pathname;
  const body =
    path === "/v1/settings/model"
      ? CLOUD
      : path === "/v1/setup/readiness"
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
const footer = () => document.querySelector(".ui-sidebar__footer") as HTMLElement;
const button = (root: Element, label: string) =>
  Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.textContent === label);

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

describe("settings in the app", () => {
  it("says calmly, at the foot of the sidebar, that a cloud AI model is on", () => {
    expect(button(footer(), "Cloud AI model on")).toBeDefined();
    expect(view("settings").hidden).toBe(true);
  });

  it("opens Settings from that line, naming the service and that a key is saved", async () => {
    button(footer(), "Cloud AI model on")?.click();
    await settle();
    expect(view("settings").hidden).toBe(false);
    expect(view("status").hidden).toBe(true);
    expect(view("settings").textContent).toContain("In the cloud, at api.example.com.");
    expect(view("settings").textContent).toContain("API key: Key saved");
    expect(view("settings").textContent).toContain("/Users/me/library");
    expect(button(footer(), "Settings")?.getAttribute("aria-current")).toBe("page");
  });

  it("goes back to Status from the navigation", () => {
    const status = Array.from(document.querySelectorAll<HTMLButtonElement>(".ui-nav-item")).find((n) => n.textContent === "Status");
    status?.click();
    expect(view("settings").hidden).toBe(true);
    expect(button(footer(), "Settings")?.getAttribute("aria-current")).toBeNull();
  });
});
