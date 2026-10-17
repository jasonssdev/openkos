import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable, ProtocolError, type Schemas } from "../../client/index.ts";
import {
  CHECK_NEVER_COMBINES,
  DECIDE_LATER,
  ENGINE_WORDS,
  checkConsent,
  checkProgress,
  checkResult,
  confirmation,
  describeDecision,
  formatDate,
  historyLine,
  identityShape,
  listEntry,
  previewLines,
  problemFor,
  sourceLine,
  splitQuestion,
  splitDone,
  undoneLine,
  COMBINED_HEADING,
  typeNoun,
  whoCanSee,
  type Pages,
} from "./wording.ts";

type Decision = Schemas["Decision"];
type Page = Schemas["Page"];

const NOW = new Date("2026-10-16T12:00:00Z");

function page(id: string, title: string, okf_type: string | null = "Person", extra: Partial<Page> = {}): Page {
  return { concept_id: id, title, okf_type, excerpt: `About ${title}.`, sensitivity: "private", sources: [], ...extra };
}

function pagesOf(...list: Page[]): Pages {
  return new Map(list.map((p) => [p.concept_id, p]));
}

const A = page("people/a", "Marguerite Osei");
const B = page("people/b", "M. Osei");
const C = page("people/c", "Maggie Osei");
const ORG = page("orgs/acme", "Acme", "Organization");

function identity(over: Partial<Decision> = {}, members = ["people/a", "people/b"]): Decision {
  return {
    decision_key: "k1",
    kind: "identity",
    targets: members,
    fresh: true,
    evaluation: "judged_same",
    actions: ["merge", "keep_distinct", "skip"],
    withheld: [],
    resolves_via: "openkos duplicates --merge",
    detail: { member_ids: members, okf_type: "Person", tier: "exact", verdict: "same", rationale: "Same email." },
    ...over,
  };
}

function relation(type: string, actions: Schemas["Action"][] = ["relate", "skip"]): Decision {
  return {
    decision_key: "r1",
    kind: "relation_type",
    targets: ["people/a", "orgs/acme"],
    fresh: true,
    evaluation: "not_applicable",
    actions,
    withheld: [],
    resolves_via: "openkos relations --apply",
    detail: {
      source_id: "people/a",
      target_id: "orgs/acme",
      effective_source_id: "people/a",
      effective_target_id: "orgs/acme",
      suggested_type: type,
      rationale: "She is on the board.",
    },
  };
}

const TIER: Decision = {
  decision_key: "t1",
  kind: "volatility",
  targets: [],
  fresh: true,
  evaluation: "not_applicable",
  actions: ["set_tier", "skip"],
  withheld: [],
  resolves_via: "openkos suggest-volatility",
  detail: { type_name: "Person", current_default: "static", suggested_tier: "slow", rationale: "Roles change." },
};

describe("typeNoun", () => {
  it.each([
    ["Person", "person", "people", "a person"],
    ["Organization", "organization", "organizations", "an organization"],
    ["Procedure", "how-to", "how-tos", "a how-to"],
    ["Concept", "idea", "ideas", "an idea"],
    ["Insight", "saved answer", "saved answers", "a saved answer"],
    ["Source", "file", "files", "a file"],
    ["Gadget", "thing", "things", "a thing"],
    [null, "thing", "things", "a thing"],
  ] as const)("names %s", (type, one, many, withArticle) => {
    expect(typeNoun(type)).toEqual({ one, many, withArticle });
  });
});

describe("dates and sources", () => {
  it("names a date in this year by day and month", () => {
    expect(formatDate("2026-10-12T09:00:00Z", NOW)).toBe("12 October");
  });

  it("adds the year when it is not this year", () => {
    expect(formatDate("2025-03-02T12:00:00Z", NOW)).toBe("2 March 2025");
  });

  it("gives nothing for a date it cannot read", () => {
    expect(formatDate("soon", NOW)).toBeNull();
  });

  it("words a source with and without its date", () => {
    expect(sourceLine({ source_id: "s", file_name: "notes.md", added_at: "2026-10-12T12:00:00Z" }, NOW)).toBe(
      "From: notes.md, added 12 October",
    );
    expect(sourceLine({ source_id: "s", file_name: "notes.md", added_at: null }, NOW)).toBe("From: notes.md");
  });

  it("words who can see a page, never the level's name", () => {
    expect(whoCanSee("public")).toEqual({ label: "Shareable", meaning: "Fine to share or export." });
    expect(whoCanSee("private")).toEqual({
      label: "Private",
      meaning: "Stays in your library. Sent to a cloud AI model only if you turn one on.",
    });
    expect(whoCanSee("confidential")).toEqual({
      label: "Only on this Mac",
      meaning: "Never sent anywhere, even with a cloud AI model on.",
    });
  });
});

describe("identity decisions", () => {
  const pages = pagesOf(A, B, C, ORG);

  it("asks whether a judged-same pair is the same, with both answers", () => {
    const view = describeDecision(identity(), pages);
    expect(view.question).toBe("Are these the same person?");
    expect(view.lead).toBe("The AI model thinks these are the same person.");
    expect(view.reason).toBe("“Same email.”");
    expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([
      ["merge", "Same thing", "We'll make one page with everything from both."],
      ["keep_distinct", "Different things", "We won't suggest combining these again."],
      ["later", "Decide later", "Nothing changes. This stays in To review."],
    ]);
    expect(view.options[2]).toEqual(DECIDE_LATER);
    expect(view.next).toBeNull();
  });

  it.each([
    ["judged_different", "The AI model thinks these are different people.", ["keep_distinct", "later"]],
    ["judged_uncertain", "The AI model couldn't tell.", ["keep_distinct", "later"]],
    ["pending_evaluation", "These haven't been checked yet.", ["check", "keep_distinct", "later"]],
  ] as const)("words %s and withholds the combine", (evaluation, lead, choices) => {
    const view = describeDecision(identity({ evaluation, actions: ["keep_distinct", "skip"] }), pages);
    expect(view.lead).toBe(lead);
    expect(view.options.map((o) => o.choice)).toEqual(choices);
  });

  it("offers the check with its own line", () => {
    const view = describeDecision(identity({ evaluation: "pending_evaluation", actions: ["keep_distinct", "skip"] }), pages);
    expect(view.options[0]).toEqual({
      choice: "check",
      label: "Check these",
      does: "Checking doesn't change anything. You'll still decide.",
    });
  });

  it("asks a group whether they are the same first, and which ones only after", () => {
    const members = ["people/a", "people/b", "people/c"];
    const decision = identity(
      { actions: ["merge_group", "keep_distinct", "skip"], withheld: [{ action: "merge", reason: "more_than_two_members" }] },
      members,
    );
    expect(identityShape(decision)).toBe("group");
    const view = describeDecision(decision, pages);
    expect(view.question).toBe("Are these the same person?");
    expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([
      ["same", "Same thing", "Next, you'll mark which ones are the same."],
      ["keep_distinct", "Different things", "We won't suggest combining any of these again."],
      ["later", "Decide later", "Nothing changes. This stays in To review."],
    ]);
    expect(view.next?.lead).toBe("You said these are the same.");
    expect(view.next?.question).toBe("Which of these are the same?");
    expect(view.next?.options.map((o) => [o.choice, o.label])).toEqual([
      ["merge_group", "Combine the ones I marked"],
      ["later", "Decide later"],
    ]);
  });

  it("asks whether things of two kinds are the same first, and what it is only after", () => {
    const decision = identity(
      { actions: ["merge_group", "keep_distinct", "skip"], withheld: [{ action: "merge", reason: "cross_type" }] },
      ["people/a", "orgs/acme"],
    );
    expect(identityShape(decision)).toBe("kinds");
    const view = describeDecision(decision, pages);
    expect(view.question).toBe("Are these the same thing?");
    expect(view.lead).toBe("The AI model thinks these are the same thing.");
    expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([
      ["same", "Same thing", "Next, you'll choose what it is."],
      ["keep_distinct", "Different things", "We won't suggest combining these again."],
      ["later", "Decide later", "Nothing changes. This stays in To review."],
    ]);
    expect(view.next?.question).toBe("What is Marguerite Osei?");
    expect(view.next?.options.map((o) => [o.choice, o.label, o.okfType])).toEqual([
      ["merge_group", "It's a person", "Person"],
      ["merge_group", "It's an organization", "Organization"],
      ["later", "Decide later", undefined],
    ]);
  });

  it("asks nothing after the first question for a changed group", () => {
    const decision = identity(
      { fresh: false, actions: ["merge_group", "keep_distinct", "skip"], withheld: [{ action: "merge", reason: "cross_type" }] },
      ["people/a", "orgs/acme"],
    );
    const view = describeDecision(decision, pages);
    expect(view.options.map((o) => o.choice)).toEqual(["recheck", "later"]);
    expect(view.next).toBeNull();
  });

  it("names a page it could not read without its id", () => {
    const view = describeDecision(identity(), pagesOf(A));
    expect(view.pages.map((p) => p.title)).toEqual(["Marguerite Osei", "A page that couldn't be read"]);
    expect(JSON.stringify(view)).not.toContain("people/b");
  });

  it("keeps a pair a pair when one page could not be read", () => {
    expect(identityShape(identity())).toBe("pair");
    expect(describeDecision(identity(), pagesOf(A)).question).toBe("Are these the same person?");
  });

  it("says when the decision changed since it was suggested", () => {
    expect(describeDecision(identity({ fresh: false }), pages).changed).toBe(
      "Something about these pages changed since this was suggested. Check them again before you decide.",
    );
    expect(describeDecision(identity(), pages).changed).toBeNull();
    expect(describeDecision(identity({ fresh: null }), pages).changed).toBeNull();
  });

  it("offers a changed decision only Check again and Decide later", () => {
    const again = ["recheck", "Check again", "We'll look at these pages as they are now. Checking doesn't change anything. You'll still decide."];
    const later = ["later", "Decide later", "Nothing changes. This stays in To review."];
    for (const decision of [identity({ fresh: false }), { ...relation("part_of"), fresh: false }]) {
      const view = describeDecision(decision, pagesOf(A, B, ORG));
      expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([again, later]);
      expect(view.cannotAct).toBeNull();
    }
  });
});

describe("previews", () => {
  const pages = pagesOf(A, B, C, ORG);
  const plan = (over: Partial<Schemas["MergePlan"]> = {}): Schemas["MergePlan"] => ({
    kind: "merge",
    survivor_id: "people/a",
    absorbed_id: "people/b",
    survivor_criterion: "chosen",
    survivor_type: "Person",
    cross_source_same_pair: false,
    cross_type_concern: null,
    files_rewritten: 3,
    kept_distinct_ids: [],
    steps: [{ survivor_id: "people/a", absorbed_id: "people/b", absorbed_type: "Person", files_rewritten: 3, type_outcome: "same_type" }],
    ...over,
  });

  it("says what combining a pair does", () => {
    expect(previewLines(identity(), plan(), pages)).toEqual([
      "We'll make one page with everything from both. The page keeps the name 'Marguerite Osei'. 3 other pages that mention it will point to the combined page.",
    ]);
  });

  it("counts one other page and leaves out none", () => {
    expect(previewLines(identity(), plan({ files_rewritten: 1 }), pages)[0]).toContain(
      "1 other page that mentions it will point to the combined page.",
    );
    expect(previewLines(identity(), plan({ files_rewritten: 0 }), pages)[0]).toBe(
      "We'll make one page with everything from both. The page keeps the name 'Marguerite Osei'.",
    );
  });

  it("warns when the pair came from different files or kinds", () => {
    const lines = previewLines(identity(), plan({ cross_source_same_pair: true }), pages);
    expect(lines.at(-1)).toBe("These came from different files: check that they really are the same person.");
    expect(previewLines(identity(), plan({ cross_type_concern: "x" }), pages).at(-1)).toBe(
      "These came from different files: check that they really are the same person.",
    );
  });

  it("says what combining the marked pages does", () => {
    const group = identity({ actions: ["merge_group", "keep_distinct", "skip"] }, ["people/a", "people/b", "people/c"]);
    const marked = plan({ kept_distinct_ids: ["people/c"], files_rewritten: 2 });
    expect(previewLines(group, marked, pages)[0]).toBe(
      "We'll make one page from the 2 pages you marked. It keeps the name 'Marguerite Osei'. 2 other pages that mention it will point to the combined page. The pages you didn't mark stay as they are, and we won't suggest combining them with 'Marguerite Osei' again.",
    );
    expect(previewLines(group, plan(), pages)[0]).not.toContain("didn't mark");
  });

  it("says what choosing a kind does, recorded or already noted", () => {
    const kinds = identity({ actions: ["merge_group"], withheld: [{ action: "merge", reason: "cross_type" }] }, ["people/a", "orgs/acme"]);
    const step = { survivor_id: "people/a", absorbed_id: "orgs/acme", absorbed_type: "Organization", files_rewritten: 0 };
    expect(previewLines(kinds, plan({ steps: [{ ...step, type_outcome: "recorded" }], files_rewritten: 0 }), pages)[0]).toBe(
      "We'll keep one page, as a person, named 'Marguerite Osei', and remember it was also described as an organization.",
    );
    expect(previewLines(kinds, plan({ steps: [{ ...step, type_outcome: "discarded" }], files_rewritten: 0 }), pages)[0]).toBe(
      "We'll keep one page, as a person, named 'Marguerite Osei'. It was already noted as possibly something else, so we won't note an organization.",
    );
  });

  it("says what keeping separate, connecting and a setting do", () => {
    expect(previewLines(identity(), { kind: "keep_distinct", member_ids: ["people/a", "people/b"] }, pages)).toEqual([
      "We won't suggest combining these again.",
    ]);
    expect(
      previewLines(relation("part_of"), { kind: "relate", relation_type: "part_of", source_id: "people/a", target_id: "orgs/acme" }, pages),
    ).toEqual(["We'll record that Marguerite Osei is part of Acme."]);
    expect(
      previewLines(relation("part_of"), { kind: "relate", relation_type: "part_of", source_id: "orgs/acme", target_id: "people/a" }, pages),
    ).toEqual(["We'll record that Acme is part of Marguerite Osei."]);
    expect(previewLines(TIER, { kind: "set_tier", tier: "slow", type_name: "Person", configured_tier: null }, pages)).toEqual([
      "From now on, people are treated as changing slowly.",
    ]);
  });
});

describe("connections", () => {
  const pages = pagesOf(A, ORG);

  it.each([
    ["related_to", "Marguerite Osei and Acme are related.", false],
    ["references", "Marguerite Osei mentions Acme.", false],
    ["part_of", "Marguerite Osei is part of Acme.", true],
    ["member_of", "Marguerite Osei is a member of Acme.", true],
    ["depends_on", "Marguerite Osei depends on Acme.", true],
    ["caused_by", "Marguerite Osei was caused by Acme.", true],
    ["produced_by", "Marguerite Osei was made by Acme.", true],
  ] as const)("words %s", (type, sentence, oneWay) => {
    const actions: Schemas["Action"][] = oneWay ? ["relate", "relate_reversed", "skip"] : ["relate", "skip"];
    const view = describeDecision(relation(type, actions), pages);
    expect(view.question).toBe("Is this right?");
    expect(view.statement).toBe(sentence);
    expect(view.options[0]).toEqual({
      choice: "relate",
      label: "Yes, connect them",
      does: `We'll record that ${sentence.slice(0, -1)}.`,
    });
    expect(view.options.some((o) => o.choice === "relate_reversed")).toBe(oneWay);
  });

  it("shows the reversed sentence itself as the other way round", () => {
    const view = describeDecision(relation("part_of", ["relate", "relate_reversed", "skip"]), pages);
    expect(view.options[1]).toEqual({
      choice: "relate_reversed",
      label: "No — Acme is part of Marguerite Osei.",
      does: "We'll record that instead.",
    });
    expect(view.reason).toBe("“She is on the board.”");
  });
});

describe("how quickly it goes out of date", () => {
  it("asks about the kind and shows the current setting and the suggestion", () => {
    const view = describeDecision(TIER, new Map());
    expect(view.question).toBe("How quickly do facts about people go out of date?");
    expect(view.facts).toEqual([
      ["Now", "Rarely — they stay true."],
      ["Suggested", "Slowly — over months or years."],
    ]);
    expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([
      ["set_tier", "Use this", "From now on, people are treated as changing slowly. We'll remind you to recheck them now and then."],
      ["later", "Decide later", "Nothing changes. This stays in To review."],
    ]);
  });
});

describe("decisions the app cannot act on", () => {
  const pages = pagesOf(A, B);

  it.each([
    ["contradiction", "Two pages say different things about Marguerite Osei."],
    ["revision", "A newer file may replace an older one."],
    ["watch_refusal", "A file was not added."],
  ] as const)("lists a %s with a plain sentence and no options", (kind, sentence) => {
    const decision: Decision = { ...identity(), kind, actions: [], detail: null, evaluation: "not_applicable" };
    const view = describeDecision(decision, pages);
    expect(view.question).toBe(sentence);
    expect(view.options).toEqual([]);
    expect(view.cannotAct).toBe("This can't be settled in the app yet.");
    expect(JSON.stringify(view)).not.toContain("openkos");
  });

  it("offers no action without the finding to word it, even when the engine lists one", () => {
    const decision = { ...identity(), kind: "contradiction", actions: ["decline", "skip"], detail: null } as unknown as Decision;
    const view = describeDecision(decision, pages);
    expect(view.options).toEqual([]);
    expect(view.cannotAct).toBe("This can't be settled in the app yet.");
  });
});

describe("listEntry", () => {
  it("names a pair by its pages' titles, with the question as its meta", () => {
    expect(listEntry(identity(), pagesOf(A, B))).toEqual({
      title: "Marguerite Osei · M. Osei",
      meta: "Are these the same person?",
      changed: false,
      canAct: true,
    });
  });

  it("names a connection by its sentence and a setting by its question", () => {
    expect(listEntry(relation("part_of"), pagesOf(A, ORG))).toMatchObject({ title: "Marguerite Osei is part of Acme.", meta: null });
    expect(listEntry(TIER, pagesOf())).toMatchObject({ title: "How quickly do facts about people go out of date?", meta: null });
  });

  it("marks a changed decision, and one the app cannot act on says so", () => {
    const entry = listEntry({ ...identity(), kind: "revision", actions: [], fresh: false }, pagesOf(A, B));
    expect(entry).toEqual({
      title: "A newer file may replace an older one.",
      meta: "This can't be settled in the app yet.",
      changed: true,
      canAct: false,
    });
  });
});

describe("confirmations", () => {
  const pages = pagesOf(A, B, ORG);
  const applied = (action: Schemas["Action"], over: Partial<Schemas["Applied"]> = {}): Schemas["Applied"] => ({
    action,
    commit_sha: "abc",
    decision_key: "k1",
    merged: [["people/a", "people/b"]],
    ...over,
  });

  it("confirms a combine and says when the page is only on this Mac", () => {
    expect(confirmation(identity(), applied("merge"), pages)).toEqual(["Combined into 'Marguerite Osei'."]);
    expect(confirmation(identity(), applied("merge", { committed_confidential: true }), pages)).toEqual([
      "Combined into 'Marguerite Osei'.",
      "The combined page is marked Only on this Mac.",
    ]);
  });

  it("confirms the other choices in the past tense", () => {
    expect(confirmation(identity(), applied("keep_distinct", { merged: [] }), pages)).toEqual(["Kept separate."]);
    expect(confirmation(relation("part_of"), applied("relate"), pages)).toEqual([
      "We recorded that Marguerite Osei is part of Acme.",
    ]);
    expect(confirmation(relation("part_of"), applied("relate_reversed"), pages)).toEqual([
      "We recorded that Acme is part of Marguerite Osei.",
    ]);
    expect(confirmation(TIER, applied("set_tier"), pages)).toEqual([
      "From now on, people are treated as changing slowly.",
    ]);
  });
});

describe("the check", () => {
  const quote = (over: Partial<Schemas["JudgingQuote"]> = {}): Schemas["JudgingQuote"] => {
    const binding = { backend: "ollama", model: "llama3", is_local: true, kind: "identity" as const, effective_confidential: false, rubric_digest: null };
    return {
      kind: "identity",
      total: 5,
      served: 2,
      uncertain: 0,
      to_judge: 3,
      calls: 3,
      binding,
      notices: [],
      token: { binding, calls: 3, include_confidential: false, include_uncertain: false, work: [] },
      ...over,
    };
  };

  it("asks once for the batch, on this Mac", () => {
    expect(checkConsent(quote())).toEqual({
      heading: "Check 3 pairs?",
      lines: [
        "The AI model on this Mac will compare them. Nothing leaves this Mac.",
        "2 are already checked and won't be checked again.",
        CHECK_NEVER_COMBINES,
      ],
      agree: "Check 3",
      decline: "Not now",
    });
  });

  it("names one pair, a cloud model and background work", () => {
    const cloud = quote({
      to_judge: 1,
      served: 1,
      notices: [{ code: "unattended_active" }],
      binding: { backend: "openai-compatible", model: "gpt-x", is_local: false, kind: "identity", effective_confidential: false, rubric_digest: null },
    });
    expect(checkConsent(cloud)).toEqual({
      heading: "Check these two?",
      lines: [
        "The text of these pages will be sent to the provider of gpt-x to compare them. Pages marked 'Only on this Mac' are not sent. The provider may charge you.",
        "1 is already checked and won't be checked again.",
        "OpenKOS is also working in the background, so this may be slower.",
        CHECK_NEVER_COMBINES,
      ],
      agree: "Check 1",
      decline: "Not now",
    });
  });

  it("leaves out the already-checked line when none are", () => {
    expect(checkConsent(quote({ served: 0 })).lines).not.toContain(
      "0 are already checked and won't be checked again.",
    );
    expect(checkConsent(quote({ served: 0 })).lines).toHaveLength(2);
  });

  it("names connections and suggestions in the heading by the quote's kind", () => {
    expect(checkConsent(quote({ kind: "relation_type" })).heading).toBe("Check 3 connections?");
    expect(checkConsent(quote({ kind: "relation_type", to_judge: 1 })).heading).toBe("Check this connection?");
    expect(checkConsent(quote({ kind: "volatility" })).heading).toBe("Check 3 suggestions?");
    expect(checkConsent(quote({ kind: "volatility", to_judge: 1 })).heading).toBe("Check this suggestion?");
  });

  it("says when there is nothing to check", () => {
    expect(checkConsent(quote({ to_judge: 0 }))).toEqual({
      heading: "These are already checked.",
      lines: [],
      agree: null,
      decline: "OK",
    });
  });

  it("words progress, stopping and the result", () => {
    expect(checkProgress(1, 3, false)).toBe("Checking 1 of 3…");
    expect(checkProgress(1, 3, true)).toBe("Stopping after the current one. Everything already checked is kept.");
    expect(checkResult(3, 2)).toBe("Checked 3. 2 are ready for you to decide.");
    expect(checkResult(1, 1)).toBe("Checked 1. 1 is ready for you to decide.");
  });
});

describe("splitting again", () => {
  const record: Schemas["MergeRecord"] = {
    survivor_id: "people/a",
    absorbed_id: "people/b",
    absorbed_title: "M. Osei",
    merged_at: "2026-10-14T12:00:00Z",
  };
  const step = (absorbed_id: string, absorbed_title: string | null): Schemas["UnmergeStep"] => ({
    survivor_id: "people/a",
    absorbed_id,
    absorbed_title,
    merged_at: "2026-10-14T12:00:00Z",
    files_rewritten: 0,
    survivor_edits_unverifiable: false,
    restores_catalog_snapshot: false,
  });

  it("words a history line", () => {
    expect(historyLine(record, NOW)).toBe("Combined from 'M. Osei' on 14 October");
    expect(historyLine({ ...record, absorbed_title: null }, NOW)).toBe("Combined from a page on 14 October");
  });

  it("asks before one split, and lists the later combines first", () => {
    expect(splitQuestion([step("people/b", "M. Osei")])).toBe(
      "Split 'M. Osei' out again? Both pages go back exactly as they were.",
    );
    expect(splitQuestion([step("people/c", "Maggie"), step("people/d", null), step("people/b", "M. Osei")])).toBe(
      "To split this, the later combines on this page are split first: 'Maggie', a page. Continue?",
    );
  });

  it("confirms what was split", () => {
    expect(splitDone(["M. Osei"])).toBe("Split. 'M. Osei' is back as it was.");
    expect(splitDone(["Maggie", "M. Osei"])).toBe("Split. 'Maggie' and 'M. Osei' are back as they were.");
  });
});

describe("undoing", () => {
  it("says what each undo did, in the past tense", () => {
    expect(undoneLine({ kind: "reopen", members: ["people/a", "people/b"] })).toBe(
      "Undone. We'll ask you about these again.",
    );
    expect(undoneLine({ kind: "unrelate", source: "a", relationType: "part_of", target: "b" })).toBe(
      "Undone. That connection is removed.",
    );
    expect(undoneLine({ kind: "tier", typeName: "Person", tier: null })).toBe("Undone. People are treated as before.");
    expect(undoneLine({ kind: "tier", typeName: "Concept", tier: "slow" })).toBe("Undone. Ideas are treated as before.");
  });

  it("heads the combined pages for every session", () => {
    expect(COMBINED_HEADING).toBe("Pages you combined");
  });
});

describe("problemFor", () => {
  const api = (code: string) =>
    new ApiError({ status: 409, code, retryable: false, message: "engine text", retryAfterSeconds: null });

  it.each([
    ["workspace_busy", "OpenKOS is finishing something else. Nothing was changed.", "try_again"],
    ["run_active", "A check is already running.", "ok"],
    ["stale", "These pages changed while you were looking. Here is the current version.", "reload"],
    ["write_drift", "These pages changed while you were looking. Here is the current version.", "reload"],
    ["member_missing", "One of these pages no longer exists, so there's nothing to decide.", "ok"],
    ["backend_unavailable", "The AI model isn't running. OpenKOS needs it to check pages. Check setup, on Status, helps you start it.", "try_again"],
    ["backend_model_not_found", "The AI model isn't installed. OpenKOS needs it to check pages. Check setup, on Status, can download it.", "ok"],
    ["record_failed", "Your choice couldn't be saved. Nothing was changed.", "try_again"],
    ["invalid_selection", "Something about your choice didn't match these pages. Please choose again.", "reload"],
    [
      "merge_group_interrupted",
      "Only some of these were combined. Nothing else changed, and each combine can be split again.",
      "reload",
    ],
    ["survivor_missing", "This page was combined into another one. Split that first.", "ok"],
    ["not_merged", "These pages are already separate.", "reload"],
    ["survivor_edited", "This page was changed after it was combined, so it can't be split here.", "ok"],
    ["unmerge_interrupted", "Only some of these were split. Each page that was split is back as it was.", "reload"],
    ["quote_stale", "Something changed since you agreed — the AI model or the pages. Please look again.", "requote"],
    ["not_kept_distinct", "These pages aren't kept separate any more.", "ok"],
    ["not_related", "These pages aren't connected any more.", "ok"],
    ["tier_unchanged", "This setting is already as it was.", "ok"],
  ] as const)("words %s", (code, message, action) => {
    expect(problemFor(api(code), "before")).toEqual({ message, action });
  });

  it("never shows the engine's text, a code or a command for an unnamed failure", () => {
    for (const error of [api("write_failed"), new ProtocolError({ status: 500, path: "/v1/x" }), new Error("openkos x")]) {
      const before = problemFor(error, "before");
      expect(before).toEqual({ message: "Something went wrong. Nothing was changed.", action: "try_again" });
      expect(problemFor(error, "after")).toEqual({
        message: "Something went wrong. Look at To review to see what changed.",
        action: "reload",
      });
    }
  });

  it("words an engine that does not answer", () => {
    const gone = new EngineUnreachable({ path: "/v1/x" });
    expect(problemFor(gone, "before")).toEqual({ message: "OpenKOS isn't responding. Nothing was changed.", action: "try_again" });
    expect(problemFor(gone, "after")).toEqual({
      message: "OpenKOS stopped responding. Look at To review to see what changed.",
      action: "reload",
    });
  });
});

describe("no engine words reach the screen", () => {
  it("leaves every rule-1 word out of every wording above", () => {
    const pages = pagesOf(A, B, C, ORG);
    const texts = [
      describeDecision(identity(), pages),
      describeDecision(identity({ evaluation: "pending_evaluation", actions: ["keep_distinct"] }), pages),
      describeDecision(
        identity({ actions: ["merge_group", "keep_distinct"], withheld: [{ action: "merge", reason: "cross_type" }] }, [
          "people/a",
          "orgs/acme",
        ]),
        pages,
      ),
      describeDecision(relation("part_of", ["relate", "relate_reversed"]), pages),
      describeDecision(TIER, pages),
      undoneLine({ kind: "reopen", members: [] }),
      undoneLine({ kind: "unrelate", source: "a", relationType: "part_of", target: "b" }),
      undoneLine({ kind: "tier", typeName: "Person", tier: null }),
      COMBINED_HEADING,
      ...["workspace_busy", "stale", "write_failed", "quote_stale", "not_kept_distinct", "not_related", "tier_unchanged"].map((code) =>
        problemFor(new ApiError({ status: 409, code, retryable: false, message: "m", retryAfterSeconds: null }), "after"),
      ),
    ].map((value) => JSON.stringify(value).replaceAll(/"(choice|okfType)":"[^"]*"/g, ""));
    for (const text of texts) {
      for (const word of ENGINE_WORDS) {
        expect(text.toLowerCase(), word).not.toMatch(new RegExp(`\\b${word}\\b`));
      }
    }
  });
});

// -- contradictions, revisions and refused files (#1476) ------------------------ //

const D1 = page("decisions/lisbon", "Move to Lisbon", "Decision");
const D2 = page("decisions/porto", "Stay in Porto", "Decision");
const FINDING_PAGES = pagesOf(D1, D2);

function contradiction(over: Partial<Schemas["ContradictionDetail"]> = {}, decision: Partial<Decision> = {}): Decision {
  const finding: Schemas["ContradictionDetail"] = {
    first_id: "decisions/lisbon",
    second_id: "decisions/porto",
    merged_absorbed_id: null,
    conflicting_claims: ["The team moves to Lisbon in May.", "The team stays in Porto."],
    rationale: "One says they move, the other that they stay.",
    ...over,
  };
  const merged = finding.merged_absorbed_id !== null;
  return {
    decision_key: "c1",
    kind: "contradiction",
    targets: [finding.first_id, finding.second_id],
    fresh: true,
    evaluation: "not_applicable",
    actions: merged ? ["decline", "skip"] : ["decline", "reconcile", "supersede", "supersede_reversed", "skip"],
    withheld: merged ? [{ action: "reconcile", reason: "merged_content" }] : [],
    resolves_via: "openkos contradictions --decline, openkos reconcile or openkos unmerge",
    detail: null,
    finding,
    ...decision,
  };
}

function revision(over: Partial<Schemas["RevisionDetail"]> = {}, actions?: Schemas["Action"][]): Decision {
  const finding: Schemas["RevisionDetail"] = {
    first_id: "decisions/lisbon",
    second_id: "decisions/porto",
    change: "reverses",
    later_id: "decisions/porto",
    first_date: "2026-03-02",
    second_date: "2026-09-14",
    first_quote: "We move to Lisbon.",
    second_quote: "We stay in Porto after all.",
    rationale: "The later one takes the move back.",
    ...over,
  };
  return {
    decision_key: "r1",
    kind: "revision",
    targets: [finding.first_id, finding.second_id],
    fresh: true,
    evaluation: "not_applicable",
    actions: actions ?? ["supersede_reversed", "reconcile", "skip"],
    withheld: [],
    resolves_via: "openkos reconcile --from-findings",
    detail: null,
    finding,
  };
}

function refused(reason: Schemas["RefusedFileDetail"]["reason"] | null): Decision {
  return {
    decision_key: "w1",
    kind: "watch_refusal",
    targets: ["sources/notes"],
    fresh: null,
    evaluation: "not_applicable",
    actions: [],
    withheld: [],
    resolves_via: "openkos ingest inbox/notes.md",
    detail: null,
    finding: reason === null ? null : { file_name: "notes.md", reason },
  };
}

const reconcilePlan = (over: Partial<Schemas["ReconcilePlan"]>): Schemas["ReconcilePlan"] => ({
  kind: "reconcile",
  first_id: "decisions/lisbon",
  second_id: "decisions/porto",
  relation_type: "reconciled_with",
  holder_id: null,
  target_id: null,
  deprecates_id: null,
  pages_changed: ["decisions/lisbon", "decisions/porto"],
  ...over,
});
const declinePlan = (merged: string | null = null): Schemas["DeclinePlan"] => ({
  kind: "decline",
  first_id: "decisions/lisbon",
  second_id: "decisions/porto",
  merged_absorbed_id: merged,
});
const appliedAs = (action: Schemas["Action"]): Schemas["Applied"] => ({ action, commit_sha: "c", decision_key: "c1" });

describe("two pages disagree", () => {
  it("asks which is right, with the pages, what doesn't match and the model's reason", () => {
    const view = describeDecision(contradiction(), FINDING_PAGES, NOW);
    expect(view.question).toBe("These pages say different things. Which is right?");
    expect(view.pages.map((p) => p.title)).toEqual(["Move to Lisbon", "Stay in Porto"]);
    expect(view.claims).toEqual(["“The team moves to Lisbon in May.”", "“The team stays in Porto.”"]);
    expect(view.reason).toBe("“One says they move, the other that they stay.”");
    expect(view.cannotAct).toBeNull();
    expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([
      ["supersede", "'Move to Lisbon' is right", "We'll mark 'Stay in Porto' as out of date. It stays in your library, but answers won't use it."],
      ["supersede_reversed", "'Stay in Porto' is right", "We'll mark 'Move to Lisbon' as out of date. It stays in your library, but answers won't use it."],
      ["reconcile", "Both are right", "Both pages stay as they are, and each gets a short note that you checked them against each other."],
      ["decline", "Leave them as they are", "Nothing on either page changes. We won't point this out again."],
      ["later", "Decide later", "Nothing changes. This stays in To review."],
    ]);
  });

  it("offers only what the engine lists", () => {
    const view = describeDecision(contradiction({}, { actions: ["decline", "skip"] }), FINDING_PAGES, NOW);
    expect(view.options.map((o) => o.choice)).toEqual(["decline", "later"]);
  });

  it("found inside a combined page, offers only leave it and points to Split again", () => {
    const view = describeDecision(contradiction({ merged_absorbed_id: "decisions/porto" }), FINDING_PAGES, NOW);
    expect(view.question).toBe("Part of 'Move to Lisbon' disagrees with what was combined into it.");
    expect(view.pages.map((p) => p.title)).toEqual(["Move to Lisbon"]);
    expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([
      ["decline", "Leave it as it is", "Nothing changes on the page. We won't point this out again."],
      ["later", "Decide later", "Nothing changes. This stays in To review."],
    ]);
    expect(view.after).toBe("If combining them was a mistake, you can split it again under 'Pages you combined'.");
  });

  it("previews each choice from its plan", () => {
    const d = contradiction();
    expect(previewLines(d, reconcilePlan({ relation_type: "supersedes", holder_id: "decisions/porto", target_id: "decisions/lisbon", deprecates_id: "decisions/lisbon" }), FINDING_PAGES)).toEqual([
      "We'll mark 'Move to Lisbon' as out of date. It stays in your library, but answers won't use it.",
    ]);
    expect(previewLines(d, reconcilePlan({}), FINDING_PAGES)).toEqual([
      "Both pages stay as they are, and each gets a short note that you checked them against each other.",
    ]);
    expect(previewLines(d, declinePlan(), FINDING_PAGES)).toEqual(["Nothing on either page changes. We won't point this out again."]);
    expect(previewLines(contradiction({ merged_absorbed_id: "decisions/porto" }), declinePlan("decisions/porto"), FINDING_PAGES)).toEqual([
      "Nothing changes on the page. We won't point this out again.",
    ]);
  });

  it("confirms in the past tense, naming the page marked out of date from the plan", () => {
    const d = contradiction();
    const supersedes = reconcilePlan({ relation_type: "supersedes", holder_id: "decisions/lisbon", target_id: "decisions/porto", deprecates_id: "decisions/porto" });
    expect(confirmation(d, appliedAs("supersede"), FINDING_PAGES, supersedes)).toEqual(["Marked 'Stay in Porto' as out of date."]);
    expect(confirmation(d, appliedAs("reconcile"), FINDING_PAGES, reconcilePlan({}))).toEqual(["Noted that both are right."]);
    expect(confirmation(d, appliedAs("decline"), FINDING_PAGES, declinePlan())).toEqual(["Left as they are."]);
    expect(confirmation(contradiction({ merged_absorbed_id: "x" }), appliedAs("decline"), FINDING_PAGES, declinePlan("x"))).toEqual(["Left as it is."]);
  });

  it("lists by the pages' titles, with the question", () => {
    expect(listEntry(contradiction(), FINDING_PAGES)).toEqual({
      title: "Move to Lisbon · Stay in Porto",
      meta: "These pages say different things. Which is right?",
      changed: false,
      canAct: true,
    });
  });
});

describe("a newer decision", () => {
  it("dated, asks the one direction the dates name, with both still stand", () => {
    const view = describeDecision(revision(), FINDING_PAGES, NOW);
    expect(view.lead).toBeNull();
    expect(view.question).toBe("Does 'Stay in Porto' replace 'Move to Lisbon'?");
    expect(view.pages.map((p) => [p.title, p.quote, p.date])).toEqual([
      ["Move to Lisbon", "“We move to Lisbon.”", "2 March"],
      ["Stay in Porto", "“We stay in Porto after all.”", "14 September"],
    ]);
    expect(view.reason).toBe("“The later one takes the move back.”");
    expect(view.options.map((o) => [o.choice, o.label, o.does])).toEqual([
      ["supersede_reversed", "Yes, it replaces it", "We'll mark 'Move to Lisbon' as out of date. It stays in your library, but answers won't use it."],
      ["reconcile", "Both still stand", "Both pages stay current, and each gets a short note that you checked them against each other. We won't ask about these two again."],
      ["later", "Decide later", "Nothing changes. This stays in To review."],
    ]);
  });

  it("dated and refining, asks whether it updates the earlier one", () => {
    const view = describeDecision(revision({ change: "refines", later_id: "decisions/lisbon" }, ["revise", "reconcile", "skip"]), FINDING_PAGES, NOW);
    expect(view.question).toBe("Does 'Move to Lisbon' update 'Stay in Porto'?");
    expect(view.options[0]).toMatchObject({
      choice: "revise",
      label: "Yes, it updates it",
      does: "Both stay current, and 'Move to Lisbon' is noted as an update to 'Stay in Porto'.",
    });
  });

  it("undated, says so and offers all four directions", () => {
    const view = describeDecision(
      revision({ later_id: null, first_date: null, second_date: null }, ["supersede", "supersede_reversed", "revise", "revise_reversed", "reconcile", "skip"]),
      FINDING_PAGES,
      NOW,
    );
    expect(view.lead).toBe("These have no dates we can compare, so we can't tell which came first.");
    expect(view.question).toBe("How do these two relate?");
    expect(view.pages.map((p) => p.date)).toEqual(["No date", "No date"]);
    expect(view.options.map((o) => [o.choice, o.label])).toEqual([
      ["supersede", "'Move to Lisbon' replaces 'Stay in Porto'"],
      ["revise", "'Move to Lisbon' updates 'Stay in Porto'"],
      ["supersede_reversed", "'Stay in Porto' replaces 'Move to Lisbon'"],
      ["revise_reversed", "'Stay in Porto' updates 'Move to Lisbon'"],
      ["reconcile", "Both still stand"],
      ["later", "Decide later"],
    ]);
  });

  it("previews and confirms from the plan", () => {
    const d = revision();
    const supersedes = reconcilePlan({ relation_type: "supersedes", holder_id: "decisions/porto", target_id: "decisions/lisbon", deprecates_id: "decisions/lisbon" });
    const revises = reconcilePlan({ relation_type: "revises", holder_id: "decisions/porto", target_id: "decisions/lisbon" });
    expect(previewLines(d, revises, FINDING_PAGES)).toEqual(["Both stay current, and 'Stay in Porto' is noted as an update to 'Move to Lisbon'."]);
    expect(previewLines(d, reconcilePlan({}), FINDING_PAGES)).toEqual([
      "Both pages stay current, and each gets a short note that you checked them against each other. We won't ask about these two again.",
    ]);
    expect(confirmation(d, appliedAs("supersede_reversed"), FINDING_PAGES, supersedes)).toEqual(["Marked 'Move to Lisbon' as out of date."]);
    expect(confirmation(d, appliedAs("revise_reversed"), FINDING_PAGES, revises)).toEqual(["Noted that 'Stay in Porto' updates 'Move to Lisbon'."]);
    expect(confirmation(d, appliedAs("reconcile"), FINDING_PAGES, reconcilePlan({}))).toEqual(["Noted that both still stand."]);
  });
});

describe("a changed disagreement or newer decision", () => {
  it.each([["contradiction", contradiction({}, { fresh: false })], ["revision", { ...revision(), fresh: false }]] as const)(
    "a changed %s offers only Decide later, never Check again",
    (_kind, decision) => {
      const view = describeDecision(decision, FINDING_PAGES, NOW);
      expect(view.changed).toBe(
        "Something about these pages changed since this was found, so it can't be settled as it was. The app can't look at them again yet.",
      );
      expect(view.options.map((o) => o.choice)).toEqual(["later"]);
      expect(listEntry(decision, FINDING_PAGES).changed).toBe(true);
    },
  );
});

describe("a file that wasn't added", () => {
  it.each([
    [
      "changed_after_import",
      "'notes.md' wasn't added.",
      "A file with this name is already in your library, with different contents. To add this one too, give it a new name in your folder.",
    ],
    ["exceeds_pass_budget", "'notes.md' wasn't added.", "It's too large to add automatically. To add it, split it into smaller files in your folder."],
  ] as const)("says what happened and what to do in the folder (%s), with no action", (reason, question, statement) => {
    const view = describeDecision(refused(reason), pagesOf(), NOW);
    expect(view.question).toBe(question);
    expect(view.statement).toBe(statement);
    expect(view.pages).toEqual([]);
    expect(view.options).toEqual([]);
    expect(view.cannotAct).toBe("This can't be settled in the app yet.");
    expect(JSON.stringify(view)).not.toContain("openkos");
    expect(listEntry(refused(reason), pagesOf())).toEqual({ title: question, meta: "This can't be settled in the app yet.", changed: false, canAct: false });
  });

  it("without its detail, says only that a file was not added", () => {
    const view = describeDecision(refused(null), pagesOf(), NOW);
    expect(view.question).toBe("A file was not added.");
    expect(view.cannotAct).toBe("This can't be settled in the app yet.");
  });
});

describe("settling findings is refused in plain words", () => {
  it("words a pair already settled another way", () => {
    expect(problemFor(new ApiError({ status: 409, code: "already_reconciled", retryable: false, message: "m", retryAfterSeconds: null }), "before")).toEqual({
      message: "You've already settled how these pages relate, in another way. Nothing was changed.",
      action: "reload",
    });
  });

  it("leaves every rule-1 word out of the findings' wording", () => {
    const texts = [
      describeDecision(contradiction(), FINDING_PAGES, NOW),
      describeDecision(contradiction({ merged_absorbed_id: "x" }), FINDING_PAGES, NOW),
      describeDecision(revision(), FINDING_PAGES, NOW),
      describeDecision(revision({ later_id: null }, ["supersede", "supersede_reversed", "revise", "revise_reversed", "reconcile"]), FINDING_PAGES, NOW),
      describeDecision({ ...revision(), fresh: false }, FINDING_PAGES, NOW),
      describeDecision(refused("changed_after_import"), FINDING_PAGES, NOW),
      describeDecision(refused("exceeds_pass_budget"), FINDING_PAGES, NOW),
    ].map((value) => JSON.stringify(value).replaceAll(/"(choice|okfType)":"[^"]*"/g, ""));
    for (const text of texts) {
      for (const word of ENGINE_WORDS) expect(text.toLowerCase(), word).not.toMatch(new RegExp(`\\b${word}\\b`));
    }
  });
});
