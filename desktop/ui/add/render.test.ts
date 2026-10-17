// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fileQuote, job, outcome, quote, result } from "./fixtures.ts";
import type { AddState } from "./model.ts";
import { renderAdd, type AddActions } from "./render.ts";

function state(patch: Partial<AddState> = {}): AddState {
  return { ready: true, phase: "idle", quote: null, job: null, outcome: null, problem: null, ...patch };
}

function actions() {
  return {
    choose: vi.fn(() => {}),
    agree: vi.fn(() => {}),
    decline: vi.fn(() => {}),
    stop: vi.fn(() => {}),
    close: vi.fn(() => {}),
    resolveProblem: vi.fn(() => {}),
  } satisfies AddActions;
}

function draw(s: AddState, a: AddActions = actions(), root: HTMLElement = document.createElement("div")): HTMLElement {
  if (!root.isConnected) document.body.replaceChildren(root);
  renderAdd(root, s, a);
  return root;
}

const buttons = (root: HTMLElement) => Array.from(root.querySelectorAll("button"), (b) => b.textContent);
const button = (root: HTMLElement, label: string) => Array.from(root.querySelectorAll("button")).find((b) => b.textContent === label) as HTMLButtonElement;

describe("the Add files screen", () => {
  it("opens on its title with one primary button", () => {
    const root = draw(state());
    const title = root.querySelector("h1") as HTMLElement;
    expect(title.textContent).toBe("Add files");
    expect(title.dataset["view"]).toBe("heading");
    expect(root.querySelectorAll(".ui-button--primary")).toHaveLength(1);
    expect(buttons(root)).toEqual(["Choose files…"]);
  });

  it("chooses files from the button", () => {
    const a = actions();
    button(draw(state(), a), "Choose files…").click();
    expect(a.choose).toHaveBeenCalledOnce();
  });

  it("waits for the engine, saying why the button is unavailable", () => {
    const root = draw(state({ ready: false }));
    const choose = button(root, "Choose files…");
    expect(choose.disabled).toBe(true);
    expect(choose.getAttribute("aria-describedby")).toBe("add-waiting");
    expect(root.querySelector("#add-waiting")?.textContent).toBe("Waiting for OpenKOS to start…");
  });

  it("shows a spinner while it looks at the files", () => {
    expect(draw(state({ phase: "quoting" })).querySelector(".ui-loading")?.textContent).toBe("Looking at your files…");
  });

  it("asks for consent: the files, where they go, and two buttons", () => {
    const a = actions();
    const q = quote([fileQuote("a.md"), fileQuote("b.md")]);
    const root = draw(state({ phase: "consent", quote: q }), a);
    expect(root.querySelector(".ui-card__title")?.textContent).toBe("Add 2 files?");
    expect(Array.from(root.querySelectorAll(".add-files li"), (li) => li.textContent)).toEqual(["a.md", "b.md"]);
    expect(root.textContent).toContain("The AI model on this Mac will read both and make pages. Nothing leaves this Mac.");
    expect(root.textContent).not.toContain("At most");
    expect(buttons(root)).toEqual(["Add 2 files", "Not now"]);
    expect(root.querySelectorAll(".ui-button--primary")).toHaveLength(1);
    button(root, "Add 2 files").click();
    button(root, "Not now").click();
    expect(a.agree).toHaveBeenCalledOnce();
    expect(a.decline).toHaveBeenCalledOnce();
  });

  it("puts a note under a file that needs one, and names the cloud's ceiling", () => {
    const q = quote(
      [fileQuote("a.md"), fileQuote("old.md", { status: "refused", refusal: "changed_after_import", calls: 0 })],
      { is_local: false, host: "api.example.com", model: "gpt-x" },
    );
    const root = draw(state({ phase: "consent", quote: q }));
    const items = root.querySelectorAll(".add-files li");
    expect(items[0]?.textContent).toBe("a.md");
    expect(items[1]?.querySelector(".add-files__note")?.textContent).toContain("rename the file and add it again");
    expect(root.textContent).toContain("At most 6 requests to gpt-x, usually fewer.");
    expect(root.textContent).toContain("sent to api.example.com");
  });

  it("offers only OK when nothing can be added", () => {
    const q = quote([fileQuote("gone.md", { status: "refused", refusal: "not_found", calls: 0 })]);
    const a = actions();
    const root = draw(state({ phase: "consent", quote: q }), a);
    expect(root.querySelector(".ui-card__title")?.textContent).toBe("None of these can be added.");
    expect(buttons(root)).toEqual(["OK"]);
    button(root, "OK").click();
    expect(a.decline).toHaveBeenCalledOnce();
  });

  it("shows which file is being added, the step, a Stop button and what Stop does", () => {
    const q = quote([fileQuote("a.md"), fileQuote("b.md")]);
    const a = actions();
    const root = draw(state({ phase: "running", quote: q, job: job({ index: 2, total: 2, phase: "extracting" }) }), a);
    expect(root.querySelector(".ui-card__title")?.textContent).toBe("Adding 2 of 2: b.md");
    expect(root.textContent).toContain("The AI model is reading it…");
    expect(root.querySelector("progress")).not.toBeNull();
    expect(buttons(root)).toEqual(["Stop"]);
    button(root, "Stop").click();
    expect(a.stop).toHaveBeenCalledOnce();
  });

  it("says it is stopping, and that the file being read finishes first", () => {
    const q = quote([fileQuote("a.md")]);
    const root = draw(state({ phase: "stopping", quote: q, job: job({ index: 1, total: 1, phase: "extracting" }) }));
    const stop = button(root, "Stopping…");
    expect(stop.getAttribute("aria-busy")).toBe("true");
    expect(root.textContent).toContain("Stopping after this file. Files already added are kept.");
  });

  it("keeps focus on Stop across progress redraws", () => {
    const q = quote([fileQuote("a.md"), fileQuote("b.md")]);
    const root = draw(state({ phase: "running", quote: q, job: job({ index: 1, total: 2, phase: "reading" }) }));
    button(root, "Stop").focus();
    draw(state({ phase: "running", quote: q, job: job({ index: 1, total: 2, phase: "writing" }) }), actions(), root);
    expect(document.activeElement).toBe(button(root, "Stop"));
    expect(root.textContent).toContain("Saving…");
  });

  it("shows the result: a headline, a line per file, and OK", () => {
    const a = actions();
    const root = draw(state({ phase: "done", outcome: outcome([result("a.md", { created: 3 }), result("b.md", { status: "unchanged", created: 0 })]) }), a);
    expect(root.querySelector(".ui-callout--success")).not.toBeNull();
    expect(root.textContent).toContain("Added 1 file.");
    expect(Array.from(root.querySelectorAll(".add-files li"), (li) => li.textContent)).toEqual(["a.md: 3 new pages", "b.md: already in your library."]);
    button(root, "OK").click();
    expect(a.close).toHaveBeenCalledOnce();
  });

  it("warns, not celebrates, after a stop or a busy library, and when search is behind", () => {
    const stopped = draw(state({ phase: "done", outcome: outcome([result("a.md")], { status: "stopped", indexes: "stale" }) }));
    expect(stopped.querySelector(".ui-callout--warning")).not.toBeNull();
    expect(stopped.textContent).toContain("Stopped. Added 1 file; the rest were left.");
    expect(stopped.textContent).toContain("search isn't up to date with them yet");
    const busy = draw(state({ phase: "done", outcome: outcome([result("a.md", { status: "busy" })], { status: "busy" }) }));
    expect(busy.querySelector(".ui-callout--warning")).not.toBeNull();
  });

  it("shows a problem with its one action, and a way out", () => {
    for (const [action, label] of [
      ["try_again", "Try again"],
      ["requote", "Look again"],
    ] as const) {
      const a = actions();
      const root = draw(state({ phase: "problem", problem: { text: "It did not work.", action } }), a);
      expect(root.querySelector(".ui-callout--error")?.textContent).toContain("It did not work.");
      expect(buttons(root)).toEqual([label, "Not now"]);
      button(root, label).click();
      expect(a.resolveProblem).toHaveBeenCalledOnce();
      button(root, "Not now").click();
      expect(a.close).toHaveBeenCalledOnce();
    }
    const ok = draw(state({ phase: "problem", problem: { text: "No.", action: "ok" } }));
    expect(buttons(ok)).toEqual(["OK"]);
  });

  it("writes names as text, never markup", () => {
    const q = quote([fileQuote("<img src=x onerror=alert(1)>.md")]);
    const root = draw(state({ phase: "consent", quote: q }));
    expect(root.querySelector("img")).toBeNull();
    expect(root.textContent).toContain("<img src=x onerror=alert(1)>.md");
  });
});
