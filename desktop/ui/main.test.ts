// @vitest-environment jsdom
// The page's entry point against a fake shell: it draws the app shell, the
// status screen and the sidebar's engine badge, and switches views.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

const text = (selector: string) => document.querySelector(selector)?.textContent;
const navButtons = () => Array.from(document.querySelectorAll<HTMLButtonElement>(".ui-nav-item"));

beforeAll(async () => {
  vi.useFakeTimers();
  const page = readFileSync(join(import.meta.dirname, "index.html"), "utf8");
  document.body.innerHTML = page.slice(page.indexOf("<body"), page.indexOf("</body>")).replace(/^<body[^>]*>/, "");
  Object.assign(window, { __TAURI__: { core: { invoke } } });
  invoke.mockResolvedValue({ state: "failed", reason: "r", detail: "trace" });
  await import("./main.ts");
  await settle();
});

afterAll(() => vi.useRealTimers());

describe("the app", () => {
  it("draws the shell with Ask offered, Settings at the foot, and Status open", () => {
    expect(navButtons().map((n) => n.textContent)).toEqual(["Ask", "Add files", "To review", "Status", "Settings"]);
    expect(document.querySelector(".ui-sidebar__footer .ui-nav-item")?.textContent).toBe("Settings");
    expect(navButtons()[0]?.hasAttribute("aria-disabled")).toBe(false);
    expect(navButtons()[3]?.getAttribute("aria-current")).toBe("page");
    expect(text("#engine-status")).toBe("The engine could not be started.");
    expect(text(".ui-sidebar__footer .ui-badge__word")).toBe("Engine not running");
  });

  it("opens To review in the main region, and back", () => {
    (navButtons()[2] as HTMLButtonElement).click();
    const review = document.getElementById("review")?.parentElement as HTMLElement;
    expect(review.hidden).toBe(false);
    expect(review.closest("main")).not.toBeNull();
    expect(review.querySelector(".ui-loading")?.textContent).toBe("Waiting for OpenKOS to start…");
    expect(document.querySelector(".ui-legacy")).toBeNull();
    expect(navButtons()[2]?.getAttribute("aria-current")).toBe("page");
    (navButtons()[3] as HTMLButtonElement).click();
    expect(review.hidden).toBe(true);
    expect(document.activeElement?.textContent).toBe("Status");
  });

  it("opens Ask on its title, waiting for the engine, and back", () => {
    (navButtons()[0] as HTMLButtonElement).click();
    const ask = document.querySelector('[data-view="ask"]') as HTMLElement;
    expect(ask.hidden).toBe(false);
    expect(ask.closest("main")).not.toBeNull();
    expect(navButtons()[0]?.getAttribute("aria-current")).toBe("page");
    expect(document.activeElement?.textContent).toBe("Ask");
    expect(text("#ask-waiting")).toBe("Waiting for OpenKOS to start…");
    expect((document.querySelector(".ask-form .ui-button--primary") as HTMLButtonElement).disabled).toBe(true);
    (navButtons()[3] as HTMLButtonElement).click();
    expect(ask.hidden).toBe(true);
    expect(navButtons()[3]?.getAttribute("aria-current")).toBe("page");
  });

  it("opens Add files on its title, waiting for the engine, and back", () => {
    (navButtons()[1] as HTMLButtonElement).click();
    const add = document.querySelector('[data-screen="add"]') as HTMLElement;
    expect(add.hidden).toBe(false);
    expect(add.closest("main")).not.toBeNull();
    expect(navButtons()[1]?.getAttribute("aria-current")).toBe("page");
    expect(document.activeElement?.textContent).toBe("Add files");
    expect(text("#add-waiting")).toBe("Waiting for OpenKOS to start…");
    (navButtons()[3] as HTMLButtonElement).click();
    expect(add.hidden).toBe(true);
  });

  it("says when the shell cannot report the engine", async () => {
    invoke.mockRejectedValueOnce(new Error("no shell"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(text("#engine-status")).toBe("The shell could not report the engine's state.");
    expect(text(".ui-sidebar__footer .ui-badge__word")).toBe("Engine state unknown");
  });

  it("offers Ask once the engine is ready, and waits again when it stops", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    invoke.mockResolvedValue({ state: "ready", attempt: 1, base_url: "http://127.0.0.1:4321/v1", token: "t".repeat(64), pid: 7 });
    await vi.advanceTimersByTimeAsync(1000);
    const button = () => document.querySelector(".ask-form .ui-button--primary") as HTMLButtonElement;
    expect(button().disabled).toBe(false);
    expect(document.getElementById("ask-waiting")).toBeNull();
    invoke.mockResolvedValue({ state: "stopped" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(button().disabled).toBe(true);
    vi.unstubAllGlobals();
  });
});
