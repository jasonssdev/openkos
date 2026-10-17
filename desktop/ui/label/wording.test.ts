import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable } from "../../client/index.ts";
import { ENGINE_WORDS } from "../review/wording.ts";
import { LABEL_WORDS, changedLine, labelOptions, lowerWarning, problemFor, undoneLine } from "./wording.ts";

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
    expect(section).toContain(problemFor(new EngineUnreachable({ path: "/v1/x" }), true));
  });
});
