// @vitest-environment jsdom
// Settings against a fake shell and a fake engine: the quiet entry at the foot
// of the sidebar, the calm line while a cloud AI model is on, and the screen it
// opens, read through the local API and the shell's key command.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const TOKEN = "f".repeat(64);
const READY = { state: "ready", attempt: 1, base_url: "http://127.0.0.1:4321/v1", token: TOKEN, pid: 7 };

let watchState: Record<string, unknown> = { state: "on", attempt: 1, pid: 9, wanted: true };
let spent = false;

const invoke = vi.fn(async (command: string) => {
  switch (command) {
    case "engine_state":
      return READY;
    case "api_key_status":
      return true;
    case "workspace_choice_status":
      return { source: "remembered", path: "/Users/me/library", valid: true };
    case "watch_state":
      return watchState;
    case "update_settings":
      return { check: true };
    case "check_for_update":
      return { state: "available", version: "0.7.0", current_version: "0.6.0", notes: "What changed." };
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
      : path === "/v1/settings/inbox"
        ? { inbox: "/Users/me/Inbox", quiet_seconds: 30, max_calls_per_day: 500 }
        : path === "/v1/unattended"
          ? { last_job: null, budget_exhausted: spent, jobs_unreadable: false }
      : path === "/v1/reconciled"
        ? { pairs: [{ first_id: "b/x", second_id: "b/y", relation_type: "reconciled_with", holder_id: null, target_id: null }] }
        : path === "/v1/declined-contradictions"
          ? { contradictions: [] }
          : path === "/v1/pages"
            ? { concept_id: new URL(request.url).searchParams.get("concept_id"), title: new URL(request.url).searchParams.get("concept_id") === "b/x" ? "Tea" : "Coffee", excerpt: "", okf_type: null, sensitivity: "private", sources: [] }
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

describe("the watch status in the app", () => {
  it("keeps a quiet line at the foot of the sidebar saying watching is on", () => {
    expect(button(footer(), "Watching: on")).toBeDefined();
  });

  it("changes that line when the daemon stops on the AI model's limit, without a visit to Settings", async () => {
    spent = true;
    await vi.advanceTimersByTimeAsync(31_000);
    await settle();
    expect(button(footer(), "Watching: paused")).toBeDefined();
    expect(button(footer(), "Watching: on")).toBeUndefined();
  });

  it("changes it again when the shell gives up on the daemon", async () => {
    watchState = { state: "failed", reason: "exited", detail: "boom", wanted: true };
    await vi.advanceTimersByTimeAsync(31_000);
    await settle();
    expect(button(footer(), "Watching: stopped")).toBeDefined();
    // Leaves the state as the rest of the file expects it.
    watchState = { state: "on", attempt: 1, pid: 9, wanted: true };
    spent = false;
    await vi.advanceTimersByTimeAsync(31_000);
    await settle();
    expect(button(footer(), "Watching: on")).toBeDefined();
  });

  it("opens Settings from that line, where the card says the same", async () => {
    button(footer(), "Watching: on")?.click();
    await settle();
    expect(view("settings").hidden).toBe(false);
    expect(view("settings").textContent).toContain("Watch a folder");
    expect(view("settings").textContent).toContain("/Users/me/Inbox");
    expect(view("settings").textContent).toContain("Watching is on.");
    const status = Array.from(document.querySelectorAll<HTMLButtonElement>(".ui-nav-item")).find((n) => n.textContent === "Status");
    status?.click();
  });
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

  it("lists the pages you settled, each with Undo, and nothing for what is empty", () => {
    const text = view("settings").textContent ?? "";
    expect(text).toContain("Pages you settled");
    expect(text).toContain("'Tea' and 'Coffee'");
    expect(text).not.toContain("Disagreements you left as they are");
    expect(button(view("settings"), "Undo")).toBeDefined();
  });

  it("goes back to Status from the navigation", () => {
    const status = Array.from(document.querySelectorAll<HTMLButtonElement>(".ui-nav-item")).find((n) => n.textContent === "Status");
    status?.click();
    expect(view("settings").hidden).toBe(true);
    expect(button(footer(), "Settings")?.getAttribute("aria-current")).toBeNull();
  });
});

describe("the app's updates", () => {
  it("checks once at launch, and offers what it found at the foot of the sidebar", () => {
    const checks = invoke.mock.calls.filter(([command]) => command === "check_for_update");
    expect(checks).toHaveLength(1);
    expect(button(footer(), "Update available: 0.7.0")).toBeDefined();
  });

  it("opens Settings from that line, showing the version and its notes, and installs nothing", async () => {
    button(footer(), "Update available: 0.7.0")?.click();
    await settle();
    expect(view("settings").hidden).toBe(false);
    expect(view("settings").textContent).toContain("OpenKOS 0.7.0 is available. You have 0.6.0.");
    expect(view("settings").textContent).toContain("What changed.");
    expect(invoke.mock.calls.some(([command]) => command === "install_update")).toBe(false);
  });
});
