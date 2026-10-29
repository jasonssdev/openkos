import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable } from "../../client/index.ts";
import { ENGINE_WORDS } from "../review/wording.ts";
import { LABEL_WORDS, changedLine, labelOptions, lowerWarning, problemFor, undoneAllLine, undoneLine } from "./wording.ts";

function apiError(code: string): ApiError {
  return new ApiError({ status: 409, code, retryable: false, message: "engine words: concept sensitivity", retryAfterSeconds: null });
}

describe("the label wording", () => {
  it("offers the three labels in the brief's words, from the least to the most kept", () => {
    expect(labelOptions().map((o) => [o.value, o.label])).toEqual([
      ["public", "Shareable"],
      ["private", "Private"],
      ["confidential", "Only on this Mac"],
    ]);
    expect(LABEL_WORDS.group).toBe("Who can see it");
    expect(LABEL_WORDS.keepHere).toBe("Keep only on this Mac");
  });

  it("confirms a change in the past tense, and names pages raised with it", () => {
    expect(changedLine("Garden", "confidential", 0)).toBe("“Garden” is now Only on this Mac.");
    expect(changedLine("Notes", "confidential", 1)).toBe(
      "“Notes” is now Only on this Mac, and so is 1 page made from it. To change that page back, change it on its own.",
    );
    expect(changedLine("Notes", "confidential", 3)).toContain("and so are 3 pages made from it. To change those pages back");
    expect(undoneLine("Garden", "private")).toBe("“Garden” is Private again.");
  });

  it("drops the manual chore where the engine can undo the whole change", () => {
    expect(changedLine("Notes", "confidential", 1, true)).toBe("“Notes” is now Only on this Mac, and so is 1 page made from it.");
    expect(changedLine("Notes", "confidential", 3, true)).toBe("“Notes” is now Only on this Mac, and so are 3 pages made from it.");
    expect(changedLine("Notes", "confidential", 0, true)).toBe("“Notes” is now Only on this Mac.");
    expect(LABEL_WORDS.undoAll).toBe("Undo this change");
  });

  it("confirms an undo of the whole change, and says which pages stayed and why", () => {
    expect(undoneAllLine("Notes", "private", 0, 0)).toBe("“Notes” is Private again.");
    expect(undoneAllLine("Notes", "private", 1, 0)).toBe("“Notes” is Private again, and so is 1 page made from it.");
    expect(undoneAllLine("Notes", "private", 3, 0)).toBe("“Notes” is Private again, and so are 3 pages made from it.");
    expect(undoneAllLine("Notes", "private", 2, 1)).toBe(
      "“Notes” is Private again, and so are 2 pages made from it. 1 page stays as it was, to keep it at least as private as the pages it is made from.",
    );
    expect(undoneAllLine("Notes", "private", 0, 2)).toBe(
      "“Notes” is Private again. 2 pages stay as they were, to keep them at least as private as the pages they are made from.",
    );
  });

  it("says plainly when an undo is refused", () => {
    expect(problemFor(apiError("sensitivity_changed_since"), true)).toBe(
      "Some of these pages changed after this change, so it can't be undone in one step. Nothing was changed.",
    );
    expect(problemFor(apiError("sensitivity_no_record"), true)).toBe("This change can't be undone in one step. Nothing was changed.");
  });

  it("warns before a page marked Only on this Mac is let out while a cloud AI model is on", () => {
    const warning = lowerWarning("Garden", "private", "api.example.com");
    expect(warning.title).toBe("Let “Garden” be sent to api.example.com?");
    expect(warning.description).toBe(
      "While a cloud AI model is on, pages marked Private are sent to api.example.com whenever the AI model needs them. “Garden” will be too.",
    );
    expect(warning.confirm).toBe("Mark it Private");
    expect(warning.cancel).toBe("Keep it only on this Mac");
    expect(lowerWarning("Garden", "public", "h").confirm).toBe("Mark it Shareable");
  });

  it("words each failure from its code, saying whether anything changed", () => {
    expect(problemFor(apiError("workspace_busy"), false)).toBe("OpenKOS is finishing something else. Nothing was changed.");
    expect(problemFor(apiError("stale"), false)).toBe("This page changed while you were looking. Nothing was changed. Try again.");
    expect(problemFor(apiError("page_not_found"), false)).toBe("This page no longer exists, so nothing was changed.");
    expect(problemFor(new EngineUnreachable({ path: "/v1/x" }), false)).toBe("OpenKOS isn't responding. Nothing was changed.");
    expect(problemFor(new Error("x"), false)).toBe("Something went wrong. Nothing was changed.");
    // After the change was sent, an unknown failure never says nothing changed.
    expect(problemFor(new Error("x"), true)).toBe("Something went wrong. Look at the page again to see whether it changed.");
    expect(problemFor(new EngineUnreachable({ path: "/v1/x" }), true)).toBe(
      "OpenKOS stopped responding. Look at the page again to see whether it changed.",
    );
  });

  it("uses no engine words", () => {
    const all = [
      ...labelOptions().map((o) => o.label),
      ...Object.values(LABEL_WORDS),
      changedLine("t", "confidential", 2),
      changedLine("t", "confidential", 2, true),
      undoneAllLine("t", "private", 2, 1),
      ...["sensitivity_changed_since", "sensitivity_no_record"].map((c) => problemFor(apiError(c), true)),
      undoneLine("t", "public"),
      ...Object.values(lowerWarning("t", "private", "h")),
      ...["workspace_busy", "stale", "write_drift", "page_not_found", "x"].map((c) => problemFor(apiError(c), false)),
    ].join(" ").toLowerCase();
    for (const word of ENGINE_WORDS) expect(all).not.toMatch(new RegExp(`\\b${word}\\b`));
  });
});

describe("docs/decision-wording.md", () => {
  const brief = readFileSync(new URL("../../../docs/decision-wording.md", import.meta.url), "utf8");
  const section = brief.slice(brief.indexOf("## Who can see a page"), brief.indexOf("## Setup"));

  it("states the action, the warning and every failure word for word", () => {
    expect(section).toContain(`**${LABEL_WORDS.keepHere}**`);
    const warning = lowerWarning("{title}", "private", "{host}");
    expect(section).toContain(`“{title}” will be too.`);
    expect(section).toContain(warning.title);
    expect(section).toContain(`**${warning.cancel}**`);
    for (const code of ["workspace_busy", "stale", "page_not_found"]) expect(section).toContain(problemFor(apiError(code), false));
    expect(section).toContain(problemFor(new Error("x"), true));
    expect(section).toContain(`**${LABEL_WORDS.undoAll}**`);
    expect(section).toContain(changedLine("{title}", "confidential", 3, true).replace("3 pages", "{n} pages"));
    for (const code of ["sensitivity_changed_since", "sensitivity_no_record"]) expect(section).toContain(problemFor(apiError(code), true));
    const [restoredSentence, keptSentence] = undoneAllLine("{title}", "private", 2, 1).split(/(?<=\.) /);
    expect(section).toContain(restoredSentence!.replace("2 pages", "{n} pages").replace("“{title}” is Private", "“{title}” is {label}"));
    expect(section).toContain(keptSentence!);
    expect(section).toContain(undoneAllLine("{title}", "private", 0, 2).split(/(?<=\.) /)[1]!.replace("2 pages", "{n} pages"));
    expect(section).toContain(problemFor(new EngineUnreachable({ path: "/v1/x" }), true));
  });
});
