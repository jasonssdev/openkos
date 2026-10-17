import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable } from "../../client/index.ts";
import { fileQuote, job, outcome, quote, result } from "./fixtures.ts";
import { ENGINE_WORDS, describeOutcome, describeQuote, problemFor, progressLine, refusalLine } from "./wording.ts";

const api = (code: string, retryable = false) => new ApiError({ status: 409, code, retryable, message: "x", retryAfterSeconds: null });

describe("describeQuote", () => {
  it("asks about one file by its name", () => {
    const view = describeQuote(quote([fileQuote("notes.md")]));
    expect(view.heading).toBe("Add notes.md?");
    expect(view.agree).toBe("Add 1 file");
    expect(view.decline).toBe("Not now");
  });

  it("asks about several files by count", () => {
    const view = describeQuote(quote([fileQuote("a.md"), fileQuote("b.md"), fileQuote("c.md")]));
    expect(view.heading).toBe("Add 3 files?");
    expect(view.agree).toBe("Add 3 files");
    expect(view.files.map((f) => f.note)).toEqual([null, null, null]);
  });

  it("says a local model reads them and nothing leaves the Mac, with no number", () => {
    const view = describeQuote(quote([fileQuote("a.md"), fileQuote("b.md")]));
    expect(view.privacy).toBe("The AI model on this Mac will read both and make pages. Nothing leaves this Mac.");
    expect(view.ceiling).toBeNull();
  });

  it("counts the files the model reads, not the ones only kept", () => {
    const view = describeQuote(
      quote([fileQuote("a.md"), fileQuote("b.md"), fileQuote("c.md"), fileQuote("d.png", { status: "source_only", source_only: "not_text" })]),
    );
    expect(view.privacy).toContain("will read 3 of them");
  });

  it("says one file is read as 'it'", () => {
    expect(describeQuote(quote([fileQuote("a.md")])).privacy).toBe(
      "The AI model on this Mac will read it and make pages. Nothing leaves this Mac.",
    );
  });

  it("says a cloud model gets the text, may charge, and shows the most it will ask", () => {
    const view = describeQuote(quote([fileQuote("a.md"), fileQuote("b.md")], { is_local: false, host: "api.example.com", model: "gpt-x" }));
    expect(view.privacy).toBe(
      "The text of these files will be sent to api.example.com to make pages with gpt-x. The provider may charge you.",
    );
    expect(view.ceiling).toBe("At most 12 requests to gpt-x, usually fewer.");
  });

  it("says one cloud file as 'this file'", () => {
    const view = describeQuote(quote([fileQuote("a.md", { calls: 1 })], { is_local: false, host: "h.example", model: "m" }));
    expect(view.privacy).toBe("The text of this file will be sent to h.example to make pages with m. The provider may charge you.");
    expect(view.ceiling).toBe("At most 1 request to m, usually fewer.");
  });

  it("says nothing about the model when it reads nothing", () => {
    const view = describeQuote(quote([fileQuote("d.png", { status: "source_only", source_only: "not_text" })], { is_local: false }));
    expect(view.privacy).toBeNull();
    expect(view.ceiling).toBeNull();
    expect(view.addable).toBe(1);
  });

  it("words each way a file is only kept or skipped", () => {
    const view = describeQuote(
      quote([
        fileQuote("same.md", { status: "unchanged", calls: 0 }),
        fileQuote("pic.png", { status: "source_only", source_only: "not_text" }),
        fileQuote("empty.md", { status: "source_only", source_only: "blank" }),
        fileQuote("secret.md", { status: "source_only", source_only: "confidential_floor" }),
      ]),
    );
    expect(view.files.map((f) => f.note)).toEqual([
      "Already in your library. Nothing to do.",
      "We'll keep this file, but the AI model can't read it, so no pages will be made from it.",
      "This file is empty. We'll keep it, but there is nothing to make pages from.",
      "Your library keeps new files Only on this Mac, so the AI model won't read this one. We'll keep the file without making pages.",
    ]);
    expect(view.addable).toBe(3);
  });

  it("lists a refused file with the one thing the person can do", () => {
    const view = describeQuote(quote([fileQuote("a.md"), fileQuote("old.md", { status: "refused", refusal: "changed_after_import", calls: 0 })]));
    expect(view.files[1]).toMatchObject({ name: "old.md", refused: true });
    expect(view.files[1]?.note).toBe(refusalLine("old.md", "changed_after_import"));
    expect(view.heading).toBe("Add a.md?");
    expect(view.addable).toBe(1);
  });

  it("has nothing to add when every file is refused or unchanged", () => {
    const view = describeQuote(quote([fileQuote("same.md", { status: "unchanged", calls: 0 }), fileQuote("gone.md", { status: "refused", refusal: "not_found", calls: 0 })]));
    expect(view.heading).toBe("None of these can be added.");
    expect(view.agree).toBeNull();
    expect(view.decline).toBe("OK");
    expect(view.privacy).toBeNull();
  });
});

describe("refusalLine", () => {
  it.each([
    ["changed_after_import", "A different version of f.md is already in your library, and files you added are never replaced. To add this version too, rename the file and add it again."],
    ["duplicate_name", "Two of the files you chose are called f.md. Rename one of them and add it again."],
    ["not_found", "f.md isn't there any more."],
    ["not_a_file", "f.md is a folder, not a file. Choose the files inside it."],
    ["inside_workspace", "f.md is part of your library's own storage, so it can't be added."],
    ["unreadable", "f.md couldn't be opened. Check that you can open it, then add it again."],
    ["inconsistent_workspace", "f.md can't be added from the app right now."],
    ["symlinked_destination", "f.md can't be added from the app right now."],
    ["not_absolute", "f.md can't be added from the app right now."],
  ] as const)("%s", (reason, text) => {
    expect(refusalLine("f.md", reason)).toBe(text);
  });
});

describe("progressLine", () => {
  const q = quote([fileQuote("a.md"), fileQuote("b.md")]);
  it("names the file and the step", () => {
    expect(progressLine(q, job({ index: 2, total: 2, phase: "extracting" }))).toEqual({
      heading: "Adding 2 of 2: b.md",
      step: "The AI model is reading it…",
    });
    expect(progressLine(q, job({ index: 1, total: 2, phase: "reading" })).step).toBe("Opening it…");
    expect(progressLine(q, job({ index: 1, total: 2, phase: "writing" })).step).toBe("Saving…");
  });
  it("says it is making the files searchable at the end", () => {
    expect(progressLine(q, job({ index: 2, total: 2, phase: "refreshing" }))).toEqual({ heading: "Almost done", step: "Making it searchable…" });
  });
  it("says it is getting started before the first step", () => {
    expect(progressLine(q, job())).toEqual({ heading: "Getting started", step: null });
  });
});

describe("describeOutcome", () => {
  it("lists each file", () => {
    const view = describeOutcome(
      outcome([
        result("a.md", { created: 3 }),
        result("b.md", { created: 2, revised: 1 }),
        result("c.md", { status: "updated" }),
        result("d.md", { status: "unchanged", created: 0 }),
        result("e.md", { extraction_degraded: true, created: 0 }),
      ]),
    );
    expect(view.heading).toBe("Added 4 files.");
    expect(view.lines).toEqual([
      "a.md: 3 new pages",
      "b.md: 2 new pages and 1 page updated",
      "c.md: finished adding.",
      "d.md: already in your library.",
      "e.md: kept, but the AI model couldn't finish reading it. Add it again later to try again.",
    ]);
  });

  it("says one page, not 1 pages", () => {
    expect(describeOutcome(outcome([result("a.md", { created: 1 })])).lines).toEqual(["a.md: 1 new page"]);
  });

  it("says when nothing new was added", () => {
    expect(describeOutcome(outcome([result("d.md", { status: "unchanged", created: 0 })])).heading).toBe("Nothing new was added.");
  });

  it("explains a stop and the files it left", () => {
    const view = describeOutcome(
      outcome([result("a.md"), result("b.md", { status: "not_reached", created: 0 })], { status: "stopped" }),
    );
    expect(view.heading).toBe("Stopped. Added 1 file; the rest were left.");
    expect(view.lines[1]).toBe("b.md: wasn't reached. Add it again later.");
  });

  it("explains a busy library", () => {
    const view = describeOutcome(
      outcome([result("a.md", { status: "busy", created: 0 })], { status: "busy" }),
    );
    expect(view.heading).toBe("Your library was busy, so adding stopped.");
    expect(view.lines).toEqual(["a.md: your library was busy. Add it again in a moment."]);
  });

  it("words a refused, failed and interrupted file", () => {
    const view = describeOutcome(
      outcome([
        result("a.md", { status: "refused", refusal: "not_found", created: 0 }),
        result("b.md", { status: "failed", created: 0 }),
        result("c.md", { status: "interrupted", created: 0 }),
      ]),
    );
    expect(view.lines).toEqual([
      "a.md isn't there any more.",
      "b.md: couldn't be added.",
      "c.md: stopped before it was finished. Add it again later.",
    ]);
  });

  it("warns when search is behind", () => {
    expect(describeOutcome(outcome([result("a.md")], { indexes: "stale" })).notes).toEqual([
      "Your files are added, but search isn't up to date with them yet, so answers may miss them for now.",
    ]);
    expect(describeOutcome(outcome([result("a.md")])).notes).toEqual([]);
  });
});

describe("problemFor", () => {
  it("offers a fresh look when something changed", () => {
    expect(problemFor(api("quote_stale"))).toEqual({
      text: "A file or your AI model changed since we looked. Look again before adding.",
      action: "requote",
    });
  });
  it("asks to wait when files are already being added", () => {
    expect(problemFor(api("ingest_active", true))).toEqual({
      text: "OpenKOS is already adding files. Wait for it to finish, then try again.",
      action: "try_again",
    });
  });
  it("asks to try again when the library is busy", () => {
    for (const code of ["workspace_busy", "workspace_lock_unavailable"]) {
      expect(problemFor(api(code, true))).toEqual({ text: "Your library is busy right now. Try again in a moment.", action: "try_again" });
    }
  });
  it("words the rest", () => {
    expect(problemFor(api("ingest_refused")).action).toBe("ok");
    expect(problemFor(api("run_refused")).action).toBe("requote");
    expect(problemFor(api("not_found")).text).toBe("OpenKOS restarted. Check To review and Status to see what was added.");
    expect(problemFor(api("request_too_large")).text).toBe("That is too many files at once. Choose fewer.");
    expect(problemFor(api("workspace_not_found")).action).toBe("ok");
    expect(problemFor(api("surprise"))).toEqual({ text: "Something went wrong.", action: "try_again" });
    expect(problemFor(new EngineUnreachable({ path: "/x" }))).toEqual({ text: "OpenKOS isn't responding.", action: "try_again" });
    expect(problemFor(new Error("secret detail")).text).toBe("Something went wrong.");
  });
});

describe("the brief's rule 1", () => {
  it("keeps every engine word off the screen", () => {
    const quoteView = describeQuote(
      quote([fileQuote("a.md"), fileQuote("p.png", { status: "source_only", source_only: "confidential_floor" })], { is_local: false, host: "api.example.com", model: "m" }),
    );
    const refusals = (["changed_after_import", "duplicate_name", "not_found", "not_a_file", "inside_workspace", "unreadable", "not_absolute"] as const).map((r) => refusalLine("f.md", r));
    const out = describeOutcome(outcome([result("a.md"), result("b.md", { status: "failed" })], { indexes: "stale" }));
    const text = [quoteView.heading, quoteView.privacy, quoteView.ceiling, ...quoteView.files.map((f) => f.note), ...refusals, out.heading, ...out.lines, ...out.notes]
      .filter((t): t is string => t !== null)
      .join(" ")
      .toLowerCase();
    for (const word of ENGINE_WORDS) expect(text).not.toMatch(new RegExp(`\\b${word}\\b`));
  });
});
