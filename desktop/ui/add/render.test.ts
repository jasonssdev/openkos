// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fileQuote, job, outcome, quote, result } from "./fixtures.ts";
import type { AddState } from "./model.ts";
import { renderAdd, type AddActions } from "./render.ts";

function state(patch: Partial<AddState> = {}): AddState {
  return { ready: true, phase: "idle", quote: null, job: null, outcome: null, problem: null, queue: [], incoming: null, before: 0, elapsed: null, ...patch };
}

function actions() {
  return {
    choose: vi.fn(() => {}),
    agree: vi.fn(() => {}),
    decline: vi.fn(() => {}),
    stop: vi.fn(() => {}),
    close: vi.fn(() => {}),
    resolveProblem: vi.fn(() => {}),
    agreeIncoming: vi.fn(() => {}),
    dismissIncoming: vi.fn(() => {}),
    remove: vi.fn((_path: string) => {}),
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

  it("offers dropping as well as the button, in words", () => {
    const root = draw(state());
    const zone = root.querySelector(".add-drop") as HTMLElement;
    expect(zone.textContent).toContain("Drop files here");
    expect(zone.textContent).toContain("Choose files");
    expect(zone.getAttribute("role")).toBe("group");
    expect(zone.getAttribute("aria-label")).toBe("Drop files");
    expect(buttons(root)).toEqual(["Choose files…"]);
  });

  it("keeps the drop hint off the screens that follow", () => {
    expect(draw(state({ phase: "quoting" })).querySelector(".add-drop")).toBeNull();
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
    expect(buttons(root)).toEqual(["Stop", "Choose files…"]);
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

  describe("while files are being added", () => {
    const q = quote([fileQuote("a.md"), fileQuote("b.md")]);
    const waiting = (name: string) => {
      const one = quote([fileQuote(name)]);
      return { path: `/Users/me/${name}`, name, quote: one };
    };
    const run = (patch: Partial<AddState> = {}) =>
      state({ phase: "running", quote: q, job: job({ index: 1, total: 2, phase: "extracting" }), ...patch });

    it("keeps the drop zone, worded for more files, with a working Choose files button", () => {
      const a = actions();
      const root = draw(run(), a);
      const zone = root.querySelector(".add-drop") as HTMLElement;
      expect(zone.textContent).toContain("Drop more files here");
      expect(zone.textContent).toContain("They are added after these.");
      button(root, "Choose files…").click();
      expect(a.choose).toHaveBeenCalledOnce();
    });

    it("lists the files waiting, in order, each with a Remove that names its file", () => {
      const a = actions();
      const root = draw(run({ queue: [waiting("c.md"), waiting("d.md")] }), a);
      const section = root.querySelector(".add-queue") as HTMLElement;
      expect(section.querySelector("h2")?.textContent).toBe("Waiting");
      expect(Array.from(section.querySelectorAll("li"), (li) => li.firstChild?.textContent)).toEqual(["c.md", "d.md"]);
      const remove = Array.from(section.querySelectorAll("button"));
      expect(remove.map((b) => b.getAttribute("aria-label"))).toEqual(["Remove c.md", "Remove d.md"]);
      remove[1]!.click();
      expect(a.remove).toHaveBeenCalledWith("/Users/me/d.md");
    });

    it("shows no waiting list when nothing waits", () => {
      expect(draw(run()).querySelector(".add-queue")).toBeNull();
    });

    it("counts the files before it and the ones waiting in the heading", () => {
      const root = draw(run({ before: 2, queue: [waiting("c.md")], job: job({ index: 1, total: 1, phase: "extracting" }), quote: quote([fileQuote("b.md")]) }));
      expect(root.querySelector(".ui-card__title")?.textContent).toBe("Adding 3 of 4: b.md");
    });

    it("asks about files put in during the run, under the run, without touching it", () => {
      const a = actions();
      const incoming = quote([fileQuote("c.md"), fileQuote("d.md")]);
      const root = draw(run({ incoming: { quote: incoming, problem: null } }), a);
      expect(root.textContent).toContain("Add 2 files?");
      expect(button(root, "Stop")).toBeTruthy();
      button(root, "Add 2 files").click();
      button(root, "Not now").click();
      expect(a.agreeIncoming).toHaveBeenCalledOnce();
      expect(a.dismissIncoming).toHaveBeenCalledOnce();
      expect(a.agree).not.toHaveBeenCalled();
      expect(a.decline).not.toHaveBeenCalled();
    });

    it("says it is looking at files put in during the run, and what went wrong if it cannot", () => {
      expect(draw(run({ incoming: { quote: null, problem: null } })).textContent).toContain("Looking at your files…");
      const a = actions();
      const root = draw(run({ incoming: { quote: null, problem: "Your library is busy right now. Try again in a moment." } }), a);
      expect(root.querySelector(".ui-callout--error")?.textContent).toContain("busy");
      button(root, "OK").click();
      expect(a.dismissIncoming).toHaveBeenCalledOnce();
    });

    it("asks about files put in during the run on the result too", () => {
      const a = actions();
      const root = draw(state({ phase: "done", outcome: outcome([result("a.md")]), incoming: { quote: quote([fileQuote("c.md")]), problem: null } }), a);
      button(root, "Add 1 file").click();
      expect(a.agreeIncoming).toHaveBeenCalledOnce();
      expect(button(root, "OK")).toBeTruthy();
    });

    it("keeps the drop zone and the waiting list while it starts a waiting file", () => {
      const root = draw(state({ phase: "starting", quote: q, queue: [waiting("c.md")] }));
      expect(root.textContent).toContain("Starting…");
      expect(root.querySelector(".add-drop")).not.toBeNull();
      expect(root.querySelector(".add-queue")).not.toBeNull();
    });

    it("takes no more files once Stop is pressed, and says the waiting ones are left", () => {
      const root = draw(run({ phase: "stopping", queue: [waiting("c.md")] }));
      expect(root.querySelector(".add-drop")).toBeNull();
      expect(root.querySelector(".add-queue")?.textContent).toContain("These will be left. Add them again later.");
      expect(root.querySelector(".add-queue button")).toBeNull();
    });

    it("shows how far into a file the AI model is, as a bar and in words", () => {
      const root = draw(run({ job: job({ index: 1, total: 2, phase: "extracting", parts_done: 2, parts_total: 5 }) }));
      const bar = root.querySelector("progress") as HTMLProgressElement;
      expect([bar.value, bar.max]).toEqual([2, 5]);
      expect(root.textContent).toContain("2 of 5 parts read");
    });

    it("shows a bar that only says it is working until the model has counted parts", () => {
      const root = draw(run());
      const bar = root.querySelector("progress") as HTMLProgressElement;
      expect(bar.hasAttribute("value")).toBe(false);
      expect(root.textContent).not.toContain("parts read");
    });

    it("says how long it has been on this file and that a file can take minutes", () => {
      const root = draw(run({ elapsed: 125 }));
      expect(root.textContent).toContain("On this file for 2 min 5 s.");
      expect(root.textContent).toContain("This can take several minutes per file on this Mac.");
    });

    it("does not say a file can take minutes before the model is reading it", () => {
      const root = draw(run({ job: job({ index: 1, total: 2, phase: "writing" }), elapsed: 2 }));
      expect(root.textContent).not.toContain("several minutes");
    });
  });

  it("says before the files are added that reading them can take minutes", () => {
    const local = draw(state({ phase: "consent", quote: quote([fileQuote("a.md")]) }));
    expect(local.textContent).toContain("This can take several minutes per file on this Mac.");
    const away = draw(state({ phase: "consent", quote: quote([fileQuote("a.md")], { is_local: false, host: "api.example.com" }) }));
    expect(away.textContent).toContain("This can take several minutes per file.");
    expect(away.textContent).not.toContain("on this Mac.");
    const kept = draw(state({ phase: "consent", quote: quote([fileQuote("a.md", { status: "source_only", source_only: "not_text", calls: 0 })]) }));
    expect(kept.textContent).not.toContain("several minutes");
  });
});
