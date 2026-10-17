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
    expect(answers(root).map(([label]) => label)).toEqual(["Combine them", "Keep separate", "Decide later"]);
    const variants = Array.from(detail(root).querySelectorAll(".ui-answer button"), (b) => b.className.match(/ui-button--(\w+)/)?.[1]);
    expect(variants).toEqual(["secondary", "secondary", "quiet"]);
    await click(detail(root), "Combine them");
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

  it("marks a group's pages with checkboxes and says why combining waits", async () => {
    const { root, model } = await loaded([GROUP]);
    model.open("group");
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
    await click(detail(root), "Keep separate");
    await click(detail(root), "Confirm");
    expect(live.status.textContent).toBe("Kept separate.");
    expect(live.alert.textContent).toBe("");
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
    const { root, model } = await loaded([PAIR, GROUP, REVISION], COMBINED);
    for (const key of ["pair", "group", "rev"]) {
      model.open(key);
      for (const node of Array.from(root.querySelectorAll("[class]"))) {
        for (const name of Array.from(node.classList)) expect(name, name).toMatch(/^ui-/);
      }
      const words = (root.textContent ?? "").toLowerCase();
      for (const word of ENGINE_WORDS) expect(words, word).not.toContain(word);
    }
  });
});
