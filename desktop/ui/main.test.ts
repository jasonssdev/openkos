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
  it("draws the shell with Ask not offered yet, and Status open", () => {
    expect(navButtons().map((n) => n.textContent)).toEqual(["AskComing soon", "To review", "Status"]);
    expect(navButtons()[0]?.getAttribute("aria-disabled")).toBe("true");
    expect(navButtons()[2]?.getAttribute("aria-current")).toBe("page");
    expect(text("#engine-status")).toBe("The engine could not be started.");
    expect(text(".ui-sidebar__footer .ui-badge__word")).toBe("Engine not running");
  });

  it("opens To review in the main region, and back", () => {
    (navButtons()[1] as HTMLButtonElement).click();
    const review = document.getElementById("review")?.parentElement as HTMLElement;
    expect(review.hidden).toBe(false);
    expect(review.closest("main")).not.toBeNull();
    expect(text(".ui-legacy > p")).toBe("Waiting for OpenKOS to start…");
    expect(navButtons()[1]?.getAttribute("aria-current")).toBe("page");
    (navButtons()[2] as HTMLButtonElement).click();
    expect(review.hidden).toBe(true);
    expect(document.activeElement?.textContent).toBe("Status");
  });

  it("ignores Ask", () => {
    (navButtons()[0] as HTMLButtonElement).click();
    expect(navButtons()[2]?.getAttribute("aria-current")).toBe("page");
  });

  it("says when the shell cannot report the engine", async () => {
    invoke.mockRejectedValueOnce(new Error("no shell"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(text("#engine-status")).toBe("The shell could not report the engine's state.");
    expect(text(".ui-sidebar__footer .ui-badge__word")).toBe("Engine state unknown");
  });
});
