import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable, ProtocolError, type Schemas } from "../../client/index.ts";
import { ENGINE_WORDS, describeAnswer, phaseLine, problemFor, turnMeta } from "./wording.ts";

type Answer = Schemas["Answer"];

function answer(patch: Partial<Answer> = {}): Answer {
  return {
    question: "Who runs the allotment?",
    outcome: "answered",
    answer: "Maria runs it on Saturdays.",
    no_match_cause: "none",
    citations: [
      { concept_id: "people/maria", title: "Maria", confidential: false, excerpted: false, history: null },
      { concept_id: "places/allotment", title: "The allotment", confidential: false, excerpted: true, history: null },
    ],
    omitted: [],
    attribution: "reported",
    withheld: 0,
    sufficiency_checked: true,
    sufficiency_degraded: false,
    lexical_unavailable: false,
    dense_unavailable: false,
    stale_indexes: [],
    ...patch,
  };
}

const apiError = (code: string, retryable = false) =>
  new ApiError({ status: 409, code, retryable, message: "fixed text", retryAfterSeconds: null });

describe("phaseLine", () => {
  it.each([
    [null, "Looking through your pages…"],
    ["retrieving", "Looking through your pages…"],
    ["assembling", "Gathering what's relevant…"],
    ["checking", "Checking your pages hold an answer…"],
    ["synthesizing", "Writing the answer…"],
  ] as const)("words %s calmly", (phase, line) => {
    expect(phaseLine(phase)).toBe(line);
  });
});

describe("describeAnswer", () => {
  it("gives the answer and the titles of the pages it drew on", () => {
    expect(describeAnswer(answer())).toEqual({
      kind: "answered",
      text: "Maria runs it on Saturdays.",
      from: ["Maria", "The allotment"],
      notes: [],
    });
  });

  it("says so when the answer points to no particular page", () => {
    const told = describeAnswer(answer({ citations: [], attribution: "none" }));
    expect(told).toMatchObject({ kind: "answered", from: [] });
    expect(told.notes).toEqual(["This answer doesn't point to a particular page."]);
  });

  it("says when the answer was not checked against the pages", () => {
    expect(describeAnswer(answer({ sufficiency_degraded: true })).notes).toEqual([
      "This answer wasn't checked against your pages before it was written.",
    ]);
  });

  it("notes, gently, a search that may miss recent changes", () => {
    expect(describeAnswer(answer({ stale_indexes: ["fts"] })).notes).toEqual([
      "Some recent changes to your library may not be searched yet, so this answer could miss them.",
    ]);
  });

  it.each([{ lexical_unavailable: true }, { dense_unavailable: true }])("notes a limited search (%o), once", (patch) => {
    expect(describeAnswer(answer({ ...patch, stale_indexes: ["fts"] })).notes).toEqual([
      "The search was limited this time, so this answer could miss some pages.",
    ]);
  });

  it("counts the pages left out, never naming them", () => {
    expect(describeAnswer(answer({ withheld: 1 })).notes).toEqual(["1 page marked Only on this Mac was left out."]);
    expect(describeAnswer(answer({ withheld: 3 })).notes).toEqual(["3 pages marked Only on this Mac were left out."]);
  });

  it("withholds an answer that drew on private pages, with a count only", () => {
    const told = describeAnswer(answer({ outcome: "withheld", answer: null, citations: [], withheld: 2 }));
    expect(told).toEqual({
      kind: "empty",
      title: "This answer stays private",
      text: "It drew on 2 pages marked Only on this Mac, so it isn't shown here, and neither are their names. You can show it while your AI model runs on this Mac.",
      notes: [],
    });
  });

  it("withholds without a count when there is none to give", () => {
    const told = describeAnswer(answer({ outcome: "withheld", answer: null, citations: [], withheld: 0 }));
    expect(told).toMatchObject({ kind: "empty", title: "This answer stays private" });
    expect(told.kind === "empty" && told.text).toBe(
      "It drew on pages marked Only on this Mac, so it isn't shown here, and neither are their names. You can show it while your AI model runs on this Mac.",
    );
  });

  it("withholds with one page in the singular", () => {
    const told = describeAnswer(answer({ outcome: "withheld", answer: null, citations: [], withheld: 1 }));
    expect(told.kind === "empty" && told.text).toMatch(/^It drew on 1 page marked Only on this Mac,/);
  });

  it.each([
    ["zero_hits", "Nothing matched", "Nothing in your library matches this question. Try asking with other words."],
    ["all_unreadable", "Nothing could be read", "The pages that match this question couldn't be read."],
    ["empty_query", "Nothing to ask", "Type a question first."],
    ["none", "Nothing matched", "Nothing in your library matches this question. Try asking with other words."],
  ] as const)("words a question nothing answers (%s)", (cause, title, text) => {
    const told = describeAnswer(answer({ outcome: "no_match", answer: null, citations: [], no_match_cause: cause }));
    expect(told).toEqual({ kind: "empty", title, text, notes: [] });
  });

  it("words pages that are about the question but do not answer it", () => {
    const told = describeAnswer(
      answer({ outcome: "no_answer_in_context", answer: null, citations: [], no_match_cause: "insufficient_context" }),
    );
    expect(told).toEqual({
      kind: "empty",
      title: "No answer in your pages",
      text: "Your library has pages about this, but none of them answers the question.",
      notes: [],
    });
  });

  it("keeps the notes on a question nothing answers", () => {
    const told = describeAnswer(
      answer({ outcome: "no_match", answer: null, citations: [], no_match_cause: "zero_hits", withheld: 1, stale_indexes: ["fts"] }),
    );
    expect(told.notes).toEqual([
      "Some recent changes to your library may not be searched yet, so this answer could miss them.",
      "1 page marked Only on this Mac was left out.",
    ]);
  });
});

describe("problemFor", () => {
  it.each([
    ["ask_active", "OpenKOS is already answering another question. Wait a moment, then try again.", true],
    ["backend_unavailable", "The AI model isn't running. OpenKOS needs it to answer questions.", true],
    ["backend_model_not_found", "The AI model isn't installed. OpenKOS needs it to answer questions.", false],
    ["generation_capped", "The AI model stopped before the answer was finished. Try again, or ask a shorter question.", true],
    ["backend_error", "The AI model ran into a problem.", true],
    ["workspace_unreadable", "Your library couldn't be read.", true],
    ["workspace_not_found", "Your library couldn't be found.", false],
    ["not_found", "OpenKOS restarted while it was answering. Ask again.", true],
    ["invalid_request", "This question couldn't be asked. Try writing it another way.", false],
    ["request_too_large", "This question is too long. Try a shorter one.", false],
    ["internal_error", "Something went wrong.", true],
  ])("words %s", (code, text, retry) => {
    expect(problemFor(apiError(code))).toEqual({ text, retry });
  });

  it("words an engine that does not answer, and one it cannot read", () => {
    expect(problemFor(new EngineUnreachable({ path: "/v1/ask" }))).toEqual({ text: "OpenKOS isn't responding.", retry: true });
    expect(problemFor(new ProtocolError({ status: 500, path: "/v1/ask" }))).toEqual({ text: "Something went wrong.", retry: true });
    expect(problemFor(new Error("secret detail"))).toEqual({ text: "Something went wrong.", retry: true });
  });
});

describe("turnMeta", () => {
  it("says in a word how an earlier question went", () => {
    expect(turnMeta({ kind: "answer", answer: answer() })).toBe("Answered");
    expect(turnMeta({ kind: "answer", answer: answer({ outcome: "withheld" }) })).toBe("Kept private");
    expect(turnMeta({ kind: "answer", answer: answer({ outcome: "no_match" }) })).toBe("No answer");
    expect(turnMeta({ kind: "answer", answer: answer({ outcome: "no_answer_in_context" }) })).toBe("No answer");
    expect(turnMeta({ kind: "problem", problem: { text: "x", retry: true } })).toBe("Didn't finish");
    expect(turnMeta({ kind: "running", phase: null })).toBe("Answering…");
  });
});

describe("the words on screen", () => {
  it("use no engine word", () => {
    const texts = [
      ...[null, "retrieving", "assembling", "checking", "synthesizing"].map((p) => phaseLine(p as never)),
      ...["zero_hits", "all_unreadable", "empty_query"].map((cause) =>
        describeAnswer(answer({ outcome: "no_match", no_match_cause: cause as never, withheld: 2, stale_indexes: ["fts"] })),
      ),
      describeAnswer(answer({ outcome: "no_answer_in_context", lexical_unavailable: true })),
      describeAnswer(answer({ outcome: "withheld", withheld: 2 })),
      describeAnswer(answer({ citations: [], sufficiency_degraded: true, withheld: 1 })),
      ...["ask_active", "backend_unavailable", "backend_model_not_found", "generation_capped", "backend_error"].map((c) =>
        problemFor(apiError(c)),
      ),
      ...["workspace_unreadable", "workspace_not_found", "not_found", "invalid_request", "request_too_large"].map((c) =>
        problemFor(apiError(c)),
      ),
    ].map((value) => JSON.stringify(value).replaceAll(/"(kind)":"[^"]*"/g, "").replace(/"text":"Maria runs[^"]*"/, ""));
    for (const text of texts) {
      for (const word of ENGINE_WORDS) {
        expect(text.toLowerCase(), word).not.toMatch(new RegExp(`\\b${word}\\b`));
      }
    }
  });
});
