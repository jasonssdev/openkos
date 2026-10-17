// @vitest-environment jsdom
// The review screen drawn from a real model over a fake client: a list of what
// waits on the left, the open decision on the right, all from the design system.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "../../client/index.ts";
import { ReviewModel, type ReviewClient } from "./model.ts";
import { renderReview } from "./render.ts";
import { ENGINE_WORDS } from "./wording.ts";

type Decision = Schemas["Decision"];
type Page = Schemas["Page"];

function page(id: string, title: string, okf_type = "Person", over: Partial<Page> = {}): Page {
  return {
    concept_id: id,
    title,
    okf_type,
    excerpt: `About ${title}.`,
    sensitivity: "private",
    sources: [{ source_id: "team.md", file_name: "team.md", added_at: null }],
    ...over,
  };
}

const PAGES: Record<string, Page> = {
  "people/a": page("people/a", "Marguerite Osei"),
  "people/b": page("people/b", "M. Osei"),
  "people/c": page("people/c", "Maggie Osei"),
  "concepts/idea": page("concepts/idea", "Osei method", "Concept"),
};

function identity(key: string, members: string[], over: Partial<Decision> = {}): Decision {
  return {
    decision_key: key,
    kind: "identity",
    targets: members,
    fresh: true,
    evaluation: "judged_same",
    actions: ["merge", "keep_distinct", "skip"],
    withheld: [],
    resolves_via: "openkos duplicates",
    detail: { member_ids: members, okf_type: "Person", tier: "exact", verdict: "same", rationale: "Same email." },
    ...over,
  };
}

const PAIR = identity("pair", ["people/a", "people/b"]);
const CHANGED = identity("changed", ["people/a", "people/c"], { fresh: false });
const GROUP = identity("group", ["people/a", "people/b", "people/c"], {
  actions: ["merge_group", "keep_distinct", "skip"],
  withheld: [{ action: "merge", reason: "more_than_two_members" }],
});
const KINDS = identity("kinds", ["people/a", "concepts/idea"], {
  actions: ["merge_group", "keep_distinct", "skip"],
  withheld: [{ action: "merge", reason: "cross_type" }],
});
const UNJUDGED = identity("unjudged", ["people/b", "people/c"], {
  evaluation: "pending_evaluation",
  actions: ["keep_distinct", "skip"],
});
const REVISION: Decision = {
  ...identity("rev", ["people/a", "people/b"]),
  kind: "revision",
  actions: [],
  evaluation: "not_applicable",
  detail: null,
};

const CONTRADICTION: Decision = {
  decision_key: "c1",
  kind: "contradiction",
  targets: ["people/a", "people/b"],
  fresh: true,
  evaluation: "not_applicable",
  actions: ["decline", "reconcile", "supersede", "supersede_reversed", "skip"],
  withheld: [],
  resolves_via: "openkos reconcile",
  detail: null,
  finding: {
    first_id: "people/a",
    second_id: "people/b",
    merged_absorbed_id: null,
    conflicting_claims: ["Lives in Accra.", "Lives in Kumasi."],
    rationale: "Two homes.",
    // A confidence the engine might carry is never drawn.
    confidence: 0.93,
  } as Schemas["ContradictionDetail"],
};
const MERGED_BODY: Decision = {
  ...CONTRADICTION,
  decision_key: "c2",
  actions: ["decline", "skip"],
  withheld: [{ action: "reconcile", reason: "merged_content" }],
  finding: { ...(CONTRADICTION.finding as Schemas["ContradictionDetail"]), merged_absorbed_id: "people/b" },
};
const DATED_REVISION: Decision = {
  ...CONTRADICTION,
  decision_key: "r1",
  kind: "revision",
  actions: ["supersede_reversed", "reconcile", "skip"],
  finding: {
    first_id: "people/a",
    second_id: "people/b",
    change: "reverses",
    later_id: "people/b",
    first_date: "2026-03-02",
    second_date: null,
    first_quote: "We move.",
    second_quote: "We stay.",
    rationale: "Taken back.",
  },
};
const REFUSED_FILE: Decision = {
  ...CONTRADICTION,
  decision_key: "w1",
  kind: "watch_refusal",
  targets: ["sources/notes"],
  fresh: null,
  actions: [],
  finding: { file_name: "notes.md", reason: "changed_after_import" },
};

const VERSION = { decision_key: "pair", content_digest: "d", payload_digest: "p" };
const MERGE_PLAN: Schemas["MergePlan"] = {
  kind: "merge",
  survivor_id: "people/a",
  absorbed_id: "people/b",
  survivor_criterion: "chosen",
  survivor_type: "Person",
  cross_source_same_pair: false,
  cross_type_concern: null,
  files_rewritten: 0,
  kept_distinct_ids: [],
  steps: [{ survivor_id: "people/a", absorbed_id: "people/b", absorbed_type: "Person", files_rewritten: 0, type_outcome: "same_type" }],
};
const QUOTE = {
  kind: "identity",
  total: 1,
  served: 0,
  uncertain: 0,
  to_judge: 1,
  calls: 1,
  binding: { backend: "ollama", model: "m", is_local: true, kind: "identity", effective_confidential: false, rubric_digest: null },
  notices: [],
  token: {},
} as unknown as Schemas["JudgingQuote"];
const COMBINED: Schemas["MergedConcept"][] = [
  {
    concept_id: "people/a",
    merges: [{ survivor_id: "people/a", absorbed_id: "people/b", absorbed_title: "M. Osei", merged_at: "2026-10-16T12:00:00Z" }],
  },
];

function fakeClient(decisions: Decision[], combined: Schemas["MergedConcept"][] = []) {
  return {
    decisions: vi.fn(async () => ({ decisions })),
    readPage: vi.fn(async (id: string) => {
      const found = PAGES[id];
      if (!found) throw new Error("not found");
      return found;
    }),
    previewDecision: vi.fn(async (key: string, body: Schemas["PreviewRequest"]) => ({
      decision_key: key,
      action: body.action,
      plan: body.action === "keep_distinct" ? { kind: "keep_distinct", member_ids: ["people/a", "people/b"] } : MERGE_PLAN,
      version: VERSION,
    })),
    previewFinding: vi.fn(async (key: string, body: Schemas["FindingPreviewRequest"]) => ({
      decision_key: key,
      action: body.action,
      plan: { kind: "reconcile", first_id: "people/a", second_id: "people/b", relation_type: "supersedes", holder_id: "people/a", target_id: "people/b", deprecates_id: "people/b", pages_changed: [] },
      version: { decision_key: key, content_digest: "f", payload_digest: "q" },
    })),
    applyDecision: vi.fn(async (_key: string, body: Schemas["ApplyRequest"]) => ({
      applied: { action: body.action, commit_sha: "c", decision_key: "pair", merged: [["people/a", "people/b"]] },
      warnings: [],
    })),
    quoteJudging: vi.fn(async () => QUOTE),
    startJudgingRun: vi.fn(),
    judgingRun: vi.fn(),
    stopJudgingRun: vi.fn(),
    mergedConcepts: vi.fn(async () => ({ concepts: combined })),
    refreshQueue: vi.fn(async () => ({})),
    previewUnmerge: vi.fn(async () => ({
      survivor_id: "people/a",
      absorbed_id: "people/b",
      survivor_edits_discarded: false,
      steps: [
        {
          survivor_id: "people/a",
          absorbed_id: "people/b",
          absorbed_title: "M. Osei",
          merged_at: "2026-10-16T12:00:00Z",
          files_rewritten: 0,
          survivor_edits_unverifiable: false,
          restores_catalog_snapshot: false,
        },
      ],
      version: { survivor_id: "people/a", absorbed_id: "people/b", content_digest: "u" },
    })),
    applyUnmerge: vi.fn(async () => ({ unmerged: { survivor_id: "people/a", restored_ids: ["people/b"], commit_shas: [] }, warnings: [] })),
    previewReopen: vi.fn(),
    applyReopen: vi.fn(),
    previewUnrelate: vi.fn(),
    applyUnrelate: vi.fn(),
    previewTierChange: vi.fn(),
    applyTierChange: vi.fn(),
  };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mount(decisions: Decision[], combined: Schemas["MergedConcept"][] = []) {
  const client = fakeClient(decisions, combined);
  const model = new ReviewModel(client as unknown as ReviewClient, { sleep: async () => {}, today: () => new Date("2026-10-16T12:00:00Z") });
  const root = document.createElement("div");
  const live = { status: document.createElement("p"), alert: document.createElement("p") };
  document.body.replaceChildren(root, live.status, live.alert);
  model.subscribe((state) => renderReview(root, live, model, state));
  renderReview(root, live, model, model.state);
  return { client, model, root, live };
}

async function loaded(decisions: Decision[], combined: Schemas["MergedConcept"][] = []) {
  const mounted = mount(decisions, combined);
  await mounted.model.load();
  return mounted;
}

const list = (root: HTMLElement) => root.querySelector(".ui-split__list") as HTMLElement;
const detail = (root: HTMLElement) => root.querySelector(".ui-split__detail") as HTMLElement;
const rows = (root: HTMLElement) => Array.from(list(root).querySelectorAll<HTMLButtonElement>(".ui-row"));
const buttonNamed = (scope: HTMLElement, name: string) =>
  Array.from(scope.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.textContent === name);
const answers = (root: HTMLElement) =>
  Array.from(detail(root).querySelectorAll(".ui-answer"), (li) => [li.querySelector("button")?.textContent, li.querySelector("p")?.textContent]);

async function click(scope: HTMLElement, name: string): Promise<void> {
  const button = buttonNamed(scope, name);
  if (!button) throw new Error(`no button "${name}"`);
  // As a person does: focus lands on the button they press.
  button.focus();
  button.click();
  await settle();
}

afterEach(() => document.body.replaceChildren());

describe("the list", () => {
  it("waits with a placeholder, in the list pane", () => {
    const { root } = mount([PAIR]);
    expect(list(root).querySelector(".ui-skeleton")?.textContent).toBe("Looking for things to review…");
    expect(rows(root)).toEqual([]);
  });

  it("says why the list could not be read, with Try again", async () => {
    const { root, client, model } = mount([PAIR]);
    client.decisions.mockRejectedValueOnce(new Error("boom"));
    await model.load();
    const callout = list(root).querySelector(".ui-callout--error") as HTMLElement;
    expect(callout.querySelector(".ui-callout__text")?.textContent).toBe("Something went wrong. Nothing was changed.");
    await click(callout, "Try again");
    expect(rows(root)).toHaveLength(1);
  });

  it("lists each decision as a row: its pages, its question, and Changed when it changed", async () => {
    const { root } = await loaded([PAIR, CHANGED, REVISION]);
    expect(rows(root).map((r) => [r.querySelector(".ui-row__title")?.textContent, r.querySelector(".ui-row__meta")?.textContent])).toEqual([
      ["Marguerite Osei · M. Osei", "Are these the same person?"],
      ["Marguerite Osei · Maggie Osei", "Are these the same person?"],
      ["A newer file may replace an older one.", "This can't be settled in the app yet."],
    ]);
    expect(rows(root).map((r) => r.querySelector(".ui-badge")?.textContent ?? null)).toEqual([null, "!Changed", null]);
  });

  it("offers Check all at the top when a pair waits for a check, and not otherwise", async () => {
    expect(buttonNamed(list((await loaded([PAIR])).root), "Check all")).toBeUndefined();
    const { root, client } = await loaded([UNJUDGED]);
    await click(list(root), "Check all");
    expect(client.quoteJudging).toHaveBeenCalledWith({ kind: "identity" });
    expect(detail(root).querySelector("h2")?.textContent).toBe("Check these two?");
  });

  it("asks the person to choose from the list while nothing is open", async () => {
    const { root } = await loaded([PAIR]);
    expect(detail(root).querySelector(".ui-empty__title")?.textContent).toBe("Nothing open");
  });

  it("says when there is nothing to review", async () => {
    const { root } = await loaded([]);
    expect(rows(root)).toEqual([]);
    expect(detail(root).querySelector(".ui-empty__title")?.textContent).toBe("Nothing to review right now");
  });
});

describe("one decision", () => {
  it("opens beside the list, marks its row and moves focus to the question", async () => {
    const { root } = await loaded([PAIR, CHANGED]);
    rows(root)[0]?.click();
    await settle();
    expect(rows(root).map((r) => r.getAttribute("aria-current"))).toEqual(["true", null]);
    const question = detail(root).querySelector("h2") as HTMLElement;
    expect(question.textContent).toBe("Are these the same person?");
    expect(document.activeElement).toBe(question);
    expect(detail(root).querySelector(".ui-lead")?.textContent).toBe("The AI model thinks these are the same person.");
  });

  it("compares the two pages side by side, each with where it came from", async () => {
    const { root, model } = await loaded([PAIR]);
    model.open("pair");
    const cards = Array.from(detail(root).querySelectorAll(".ui-compare .ui-card"));
    expect(cards.map((c) => [c.querySelector(".ui-card__title")?.textContent, c.querySelector(".ui-card__meta")?.textContent])).toEqual([
      ["Marguerite Osei", "A person · Private"],
      ["M. Osei", "A person · Private"],
    ]);
    expect(cards[0]?.querySelector(".ui-card__body")?.textContent).toBe("About Marguerite Osei.");
    expect(cards[0]?.querySelector(".ui-card__footer")?.textContent).toBe("From: team.md");
  });

  it("keeps the model's reason behind a disclosure, in its own words", async () => {
    const { root, model } = await loaded([PAIR]);
    model.open("pair");
    const details = detail(root).querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")?.textContent).toBe("Why the AI model thinks so");
    expect(details.querySelector("blockquote")?.textContent).toBe("“Same email.”");
  });

  it("offers every answer with what it does, and previews before it applies", async () => {
    const { root, model, client } = await loaded([PAIR]);
    model.open("pair");
    expect(answers(root).map(([label]) => label)).toEqual(["Same thing", "Different things", "Decide later"]);
    const variants = Array.from(detail(root).querySelectorAll(".ui-answer button"), (b) => b.className.match(/ui-button--(\w+)/)?.[1]);
    expect(variants).toEqual(["secondary", "secondary", "quiet"]);
    await click(detail(root), "Same thing");
    expect(client.applyDecision).not.toHaveBeenCalled();
    expect(detail(root).querySelector(".ui-subtitle")?.textContent).toBe("What will happen");
    const confirm = buttonNamed(detail(root), "Confirm") as HTMLButtonElement;
    expect(confirm.classList).toContain("ui-button--primary");
    await click(detail(root), "Confirm");
    expect(client.applyDecision).toHaveBeenCalledWith("pair", { action: "merge", version: VERSION });
    const done = detail(root).querySelector(".ui-callout--success") as HTMLElement;
    expect(done.querySelector(".ui-callout__text")?.textContent).toBe("Combined into 'Marguerite Osei'.");
    expect(buttonNamed(done, "Undo")).toBeDefined();
  });

  it("decides later without sending anything, and says so", async () => {
    const { root, model, client } = await loaded([PAIR]);
    model.open("pair");
    await click(detail(root), "Decide later");
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(detail(root).querySelector(".ui-callout__text")?.textContent).toBe("Nothing changes. This stays in To review.");
    expect(rows(root)).toHaveLength(1);
  });

  it("offers a changed decision only Check again, under the changed notice", async () => {
    const { root, model } = await loaded([CHANGED]);
    model.open("changed");
    expect(detail(root).querySelector(".ui-callout--warning .ui-callout__text")?.textContent).toBe(
      "Something about these pages changed since this was suggested. Check them again before you decide.",
    );
    expect(answers(root).map(([label]) => label)).toEqual(["Check again", "Decide later"]);
  });

  it("lists a decision it cannot act on with a plain sentence and no answers", async () => {
    const { root, model } = await loaded([REVISION]);
    model.open("rev");
    expect(detail(root).querySelector("h2")?.textContent).toBe("A newer file may replace an older one.");
    expect(detail(root).querySelector(".ui-callout--info .ui-callout__text")?.textContent).toBe("This can't be settled in the app yet.");
    expect(detail(root).querySelector(".ui-answers")).toBeNull();
    expect(detail(root).textContent).not.toContain("openkos");
  });

  it("asks a group whether they are the same before it asks which, with no marks shown", async () => {
    const { root, model } = await loaded([GROUP]);
    model.open("group");
    expect(detail(root).querySelector("h2")?.textContent).toBe("Are these the same person?");
    expect(answers(root).map(([label]) => label)).toEqual(["Same thing", "Different things", "Decide later"]);
    expect(detail(root).querySelector("fieldset")).toBeNull();
    await click(detail(root), "Same thing");
    const question = detail(root).querySelector("h2") as HTMLElement;
    expect(question.textContent).toBe("Which of these are the same?");
    expect(document.activeElement).toBe(question);
    expect(detail(root).querySelector(".ui-lead")?.textContent).toBe("You said these are the same.");
    const marks = detail(root).querySelector("fieldset") as HTMLFieldSetElement;
    expect(marks.querySelector("legend")?.textContent).toBe("Mark the pages that are the same");
    expect(Array.from(marks.querySelectorAll("input"), (i) => [i.type, i.checked])).toEqual([
      ["checkbox", false],
      ["checkbox", false],
      ["checkbox", false],
    ]);
    const combine = buttonNamed(detail(root), "Combine the ones I marked") as HTMLButtonElement;
    expect(combine.disabled).toBe(true);
    expect(detail(root).querySelector(`#${combine.getAttribute("aria-describedby")}`)?.textContent).toBe("Mark at least two pages");
    await click(detail(root), "Back");
    expect(detail(root).querySelector("h2")?.textContent).toBe("Are these the same person?");
  });

  it("asks whether pages of two kinds are the same before it asks what it is", async () => {
    const { root, model, client } = await loaded([KINDS]);
    model.open("kinds");
    expect(detail(root).querySelector("h2")?.textContent).toBe("Are these the same thing?");
    expect(answers(root).map(([label]) => label)).toEqual(["Same thing", "Different things", "Decide later"]);
    await click(detail(root), "Same thing");
    expect(detail(root).querySelector("h2")?.textContent).toBe("What is Marguerite Osei?");
    expect(document.activeElement).toBe(detail(root).querySelector("h2"));
    expect(answers(root).map(([label]) => label)).toEqual(["It's a person", "It's an idea", "Decide later"]);
    expect(client.previewDecision).not.toHaveBeenCalled();
    await click(detail(root), "It's an idea");
    expect(client.previewDecision).toHaveBeenCalledWith("kinds", {
      action: "merge_group",
      selection: { survivor_id: "concepts/idea", member_ids: ["people/a", "concepts/idea"] },
    });
  });

  it("writes every value as text", async () => {
    PAGES["people/x"] = page("people/x", "<img src=x onerror=alert(1)>");
    const { root, model } = await loaded([identity("x", ["people/a", "people/x"])]);
    model.open("x");
    expect(root.querySelector("img")).toBeNull();
    expect(detail(root).textContent).toContain("<img src=x onerror=alert(1)>");
    delete PAGES["people/x"];
  });

  it("announces a confirmation politely", async () => {
    const { root, model, live } = await loaded([PAIR]);
    model.open("pair");
    await click(detail(root), "Different things");
    await click(detail(root), "Confirm");
    expect(live.status.textContent).toBe("Kept separate.");
    expect(live.alert.textContent).toBe("");
  });
});

describe("a disagreement, a newer decision and a refused file", () => {
  it("shows both pages, what doesn't match in the model's words, and never a confidence", async () => {
    const { root, model } = await loaded([CONTRADICTION]);
    model.open("c1");
    expect(detail(root).querySelector("h2")?.textContent).toBe("These pages say different things. Which is right?");
    expect(detail(root).querySelectorAll(".ui-compare .ui-card")).toHaveLength(2);
    const claims = Array.from(detail(root).querySelectorAll("section[aria-label=\"What doesn't match\"] blockquote"), (q) => q.textContent);
    expect(claims).toEqual(["“Lives in Accra.”", "“Lives in Kumasi.”"]);
    expect(detail(root).querySelector("details blockquote")?.textContent).toBe("“Two homes.”");
    expect(detail(root).textContent).not.toMatch(/0\.93|93/);
    expect(answers(root).map(([label]) => label)).toEqual([
      "'Marguerite Osei' is right",
      "'M. Osei' is right",
      "Both are right",
      "Leave them as they are",
      "Decide later",
    ]);
  });

  it("previews with the finding preview and confirms with no Undo", async () => {
    const { root, model, client } = await loaded([CONTRADICTION]);
    model.open("c1");
    await click(detail(root), "'Marguerite Osei' is right");
    expect(client.previewFinding).toHaveBeenCalledWith("c1", { action: "supersede" });
    expect(Array.from(detail(root).querySelectorAll("section[aria-labelledby=what-will-happen] p"), (p) => p.textContent)).toEqual([
      "We'll mark 'M. Osei' as out of date. It stays in your library, but answers won't use it.",
    ]);
    await click(detail(root), "Confirm");
    const done = detail(root).querySelector(".ui-callout--success") as HTMLElement;
    expect(done.querySelector(".ui-callout__text")?.textContent).toBe("Marked 'M. Osei' as out of date.");
    expect(buttonNamed(done, "Undo")).toBeUndefined();
  });

  it("found inside a combined page, offers leave it and points to Split again", async () => {
    const { root, model } = await loaded([MERGED_BODY]);
    model.open("c2");
    expect(detail(root).querySelector("h2")?.textContent).toBe("Part of 'Marguerite Osei' disagrees with what was combined into it.");
    expect(answers(root).map(([label]) => label)).toEqual(["Leave it as it is", "Decide later"]);
    const after = detail(root).querySelector(".ui-answers ~ .ui-lead");
    expect(after?.textContent).toBe("If combining them was a mistake, you can split it again under 'Pages you combined'.");
  });

  it("shows each page of a newer decision with its quote and its date", async () => {
    const { root, model } = await loaded([DATED_REVISION]);
    model.open("r1");
    const cards = Array.from(detail(root).querySelectorAll(".ui-compare .ui-card"));
    expect(cards.map((c) => [c.querySelector(".ui-card__meta")?.textContent, c.querySelector(".ui-card__body")?.textContent])).toEqual([
      ["A person · 2 March · Private", "“We move.”"],
      ["A person · No date · Private", "“We stay.”"],
    ]);
    expect(answers(root).map(([label]) => label)).toEqual(["Yes, it replaces it", "Both still stand", "Decide later"]);
  });

  it("says a changed finding can only wait, with no Check again", async () => {
    const { root, model } = await loaded([{ ...CONTRADICTION, fresh: false }]);
    model.open("c1");
    expect(detail(root).querySelector(".ui-callout--warning .ui-callout__text")?.textContent).toBe(
      "Something about these pages changed since this was found, so it can't be settled as it was. The app can't look at them again yet.",
    );
    expect(answers(root).map(([label]) => label)).toEqual(["Decide later"]);
  });

  it("says why a file wasn't added and what to do in the folder, with no action", async () => {
    const { root, model } = await loaded([REFUSED_FILE]);
    expect(rows(root)[0]?.textContent).toContain("'notes.md' wasn't added.");
    model.open("w1");
    expect(detail(root).querySelector("h2")?.textContent).toBe("'notes.md' wasn't added.");
    expect(detail(root).querySelector(".ui-statement")?.textContent).toBe(
      "A file with this name is already in your library, with different contents. To add this one too, give it a new name in your folder.",
    );
    expect(detail(root).querySelector(".ui-card")).toBeNull();
    expect(detail(root).querySelector(".ui-answers")).toBeNull();
    expect(detail(root).querySelector(".ui-callout--info .ui-callout__text")?.textContent).toBe("This can't be settled in the app yet.");
    expect(detail(root).textContent).not.toContain("openkos");
  });
});

describe("pages you combined", () => {
  it("is one row in the list that opens every combined page, each line with Split again", async () => {
    const { root, client } = await loaded([PAIR], COMBINED);
    const row = rows(root).find((r) => r.querySelector(".ui-row__title")?.textContent === "Pages you combined") as HTMLButtonElement;
    expect(row.querySelector(".ui-row__meta")?.textContent).toBe("1 page");
    row.click();
    await settle();
    expect(detail(root).querySelector("h2")?.textContent).toBe("Pages you combined");
    expect(detail(root).querySelector("h3")?.textContent).toBe("Marguerite Osei");
    await click(detail(root), "Split again");
    expect(detail(root).querySelector("p")?.textContent).toBe("Split 'M. Osei' out again? Both pages go back exactly as they were.");
    await click(detail(root), "Split again");
    expect(client.applyUnmerge).toHaveBeenCalledWith({ version: { survivor_id: "people/a", absorbed_id: "people/b", content_digest: "u" } });
    expect(detail(root).querySelector(".ui-callout--success .ui-callout__text")?.textContent).toBe("Split. 'M. Osei' is back as it was.");
  });

  it("is not listed when nothing was combined", async () => {
    const { root } = await loaded([PAIR]);
    expect(rows(root).map((r) => r.querySelector(".ui-row__title")?.textContent)).toEqual(["Marguerite Osei · M. Osei"]);
  });
});

describe("the screen", () => {
  it("is drawn from the design system only, with no engine word", async () => {
    const { root, model } = await loaded([PAIR, GROUP, REVISION, CONTRADICTION, MERGED_BODY, DATED_REVISION, REFUSED_FILE], COMBINED);
    for (const key of ["pair", "group", "rev", "c1", "c2", "r1", "w1"]) {
      model.open(key);
      for (const node of Array.from(root.querySelectorAll("[class]"))) {
        for (const name of Array.from(node.classList)) expect(name, name).toMatch(/^ui-/);
      }
      const words = (root.textContent ?? "").toLowerCase();
      for (const word of ENGINE_WORDS) expect(words, word).not.toContain(word);
    }
  });
});

describe("who can see each page", () => {
  it("puts the screen's label action on each page card in place of the label in its meta line", async () => {
    const client = fakeClient([PAIR]);
    const model = new ReviewModel(client as unknown as ReviewClient, { sleep: async () => {}, today: () => new Date("2026-10-16T12:00:00Z") });
    const root = document.createElement("div");
    const live = { status: document.createElement("p"), alert: document.createElement("p") };
    document.body.replaceChildren(root, live.status, live.alert);
    const seen: string[] = [];
    const extras = {
      pageAction: (page: { conceptId: string; title: string; level: string }) => {
        seen.push(`${page.conceptId}:${page.level}`);
        return document.createElement("aside");
      },
    };
    model.subscribe((state) => renderReview(root, live, model, state, extras));
    await model.load();
    rows(root)[0]?.click();
    await settle();
    expect(seen).toContain("people/a:private");
    expect(seen).toContain("people/b:private");
    const cards = Array.from(detail(root).querySelectorAll(".ui-card"));
    expect(cards.every((card) => card.querySelector("aside") !== null)).toBe(true);
    expect(cards.map((card) => card.querySelector(".ui-card__meta")?.textContent)).toEqual(["A person", "A person"]);
  });
});
