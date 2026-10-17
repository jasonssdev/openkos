import { describe, expect, it, vi } from "vitest";
import { ApiError, EngineUnreachable, type Schemas } from "../../client/index.ts";
import { ReviewModel, offersCheckAll, type ReviewClient } from "./model.ts";
import { CHANGED, CHANGED_CHECK, DECIDE_LATER } from "./wording.ts";

type Decision = Schemas["Decision"];
type Page = Schemas["Page"];

const NOW = new Date("2026-10-16T12:00:00Z");

function apiError(code: string, status = 409, retryAfterSeconds: number | null = null): ApiError {
  return new ApiError({ status, code, retryable: code === "workspace_busy", message: "engine text", retryAfterSeconds });
}

function page(id: string, title: string, okf_type = "Person"): Page {
  return { concept_id: id, title, okf_type, excerpt: "", sensitivity: "private", sources: [] };
}

const PAGES: Record<string, Page> = {
  "people/a": page("people/a", "Marguerite Osei"),
  "people/b": page("people/b", "M. Osei"),
  "people/c": page("people/c", "Maggie Osei"),
  "orgs/acme": page("orgs/acme", "Acme", "Organization"),
  "orgs/acme2": page("orgs/acme2", "Acme Ltd", "Organization"),
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
    detail: { member_ids: members, okf_type: "Person", tier: "exact", verdict: "same", rationale: null },
    ...over,
  };
}

const PAIR = identity("pair", ["people/a", "people/b"]);
const GROUP = identity("group", ["people/a", "people/b", "people/c"], {
  actions: ["merge_group", "keep_distinct", "skip"],
  withheld: [{ action: "merge", reason: "more_than_two_members" }],
});
const KINDS = identity("kinds", ["people/a", "orgs/acme", "orgs/acme2"], {
  actions: ["merge_group", "keep_distinct", "skip"],
  withheld: [{ action: "merge", reason: "cross_type" }],
});
const UNJUDGED = identity("unjudged", ["people/a", "people/c"], {
  evaluation: "pending_evaluation",
  actions: ["keep_distinct", "skip"],
  withheld: [{ action: "merge", reason: "pending_evaluation" }],
});

const VERSION = { decision_key: "pair", content_digest: "d1", payload_digest: "p1" };

function mergePlan(survivor = "people/a", absorbed = "people/b"): Schemas["MergePlan"] {
  return {
    kind: "merge",
    survivor_id: survivor,
    absorbed_id: absorbed,
    survivor_criterion: "chosen",
    survivor_type: "Person",
    cross_source_same_pair: false,
    cross_type_concern: null,
    files_rewritten: 2,
    kept_distinct_ids: [],
    steps: [{ survivor_id: survivor, absorbed_id: absorbed, absorbed_type: "Person", files_rewritten: 2, type_outcome: "same_type" }],
  };
}

const RELATE_PLAN: Schemas["RelatePlan"] = {
  kind: "relate",
  source_id: "people/c",
  target_id: "orgs/acme",
  relation_type: "part_of",
};
const TIER_PLAN: Schemas["TierPlan"] = { kind: "set_tier", type_name: "Person", tier: "volatile", configured_tier: null };

function planFor(action: Schemas["Action"]): Schemas["PreviewPlan"] {
  if (action === "keep_distinct") return { kind: "keep_distinct", member_ids: ["people/a", "people/b"] };
  if (action === "relate" || action === "relate_reversed") return RELATE_PLAN;
  if (action === "set_tier") return TIER_PLAN;
  return mergePlan();
}

function preview(action: Schemas["Action"], version = VERSION): Schemas["DecisionPreview"] {
  return { decision_key: version.decision_key, action, plan: planFor(action), version };
}

const COMBINED: Schemas["MergedConcept"][] = [
  {
    concept_id: "people/a",
    merges: [{ survivor_id: "people/a", absorbed_id: "people/b", absorbed_title: "M. Osei", merged_at: "2026-10-16T12:00:00Z" }],
  },
];
const COMBINED_LINES = [
  { survivor: "people/a", title: "Marguerite Osei", lines: [{ text: "Combined from 'M. Osei' on 16 October", absorbed: "people/b" }] },
];

function refreshed(): Schemas["KindRefresh"] {
  return { kind: "identity", inserted: 0, amended: 0, unchanged: 0, replaced: 0, suppressed: 0, retired: 0, complete: true, ambiguous: 0 };
}
const QUEUE_REFRESH: Schemas["QueueRefresh"] = {
  identity: refreshed(),
  relation_type: { ...refreshed(), kind: "relation_type" },
  volatility: { ...refreshed(), kind: "volatility" },
  identity_truncation_notice: null,
};

const BINDING = { backend: "ollama", model: "m", is_local: true, kind: "identity" as const, effective_confidential: false, rubric_digest: null };
const TOKEN = { binding: BINDING, calls: 2, include_confidential: false, include_uncertain: false, work: [] };
const QUOTE: Schemas["JudgingQuote"] = {
  kind: "identity",
  total: 2,
  served: 0,
  uncertain: 0,
  to_judge: 2,
  calls: 2,
  binding: BINDING,
  notices: [],
  token: TOKEN,
};

function run(state: Schemas["RunStatus"]["state"], over: Partial<Schemas["RunStatus"]> = {}): Schemas["RunStatus"] {
  return { run_id: "r1", kind: "identity", state, index: null, total: 2, persisted: 0, outcome: null, error: null, ...over };
}

function outcome(judged: number, status: Schemas["JudgingOutcomeBody"]["status"] = "completed"): Schemas["JudgingOutcomeBody"] {
  return {
    kind: "identity",
    status,
    judged,
    calls: judged,
    deferred: 0,
    dropped: 0,
    failed_at: null,
    failure: null,
    not_in_quote: [],
    observer_failed: false,
    persisted: judged,
    settled_since_quote: 0,
    unpersisted: 0,
    unpersisted_cause: null,
  };
}

function fake(decisions: Decision[] = [PAIR]) {
  const client = {
    decisions: vi.fn(async () => ({ decisions })),
    readPage: vi.fn(async (id: string) => {
      const found = PAGES[id];
      if (!found) throw apiError("not_found", 404);
      return found;
    }),
    previewDecision: vi.fn(async (_key: string, body: Schemas["PreviewRequest"]) => preview(body.action)),
    applyDecision: vi.fn(
      async (_key: string, body: Schemas["ApplyRequest"]): Promise<Schemas["ApplyResponse"]> => ({
        applied: {
          action: body.action,
          commit_sha: "c",
          decision_key: body.version.decision_key,
          merged: body.action === "merge" || body.action === "merge_group" ? [["people/a", "people/b"]] : [],
        },
        warnings: [],
      }),
    ),
    quoteJudging: vi.fn(async () => QUOTE),
    startJudgingRun: vi.fn(async () => run("running")),
    judgingRun: vi.fn(async () => run("finished", { index: 2, outcome: outcome(2) })),
    stopJudgingRun: vi.fn(async () => run("stopping")),
    mergedConcepts: vi.fn(async (): Promise<Schemas["MergedConceptList"]> => ({ concepts: [] })),
    refreshQueue: vi.fn(async () => QUEUE_REFRESH),
    previewReopen: vi.fn(async (body: Schemas["ReopenPreviewRequest"]) => ({
      member_ids: body.member_ids,
      version: { member_ids: body.member_ids, content_digest: "r1" },
    })),
    applyReopen: vi.fn(async (body: Schemas["ReopenApplyRequest"]) => ({
      reopened: { member_ids: body.version.member_ids, commit_sha: "c" },
      warnings: [],
    })),
    previewUnrelate: vi.fn(async (body: Schemas["UnrelatePreviewRequest"]) => ({
      ...body,
      withdraws_deprecation: false,
      version: { ...body, content_digest: "n1" },
    })),
    applyUnrelate: vi.fn(async (body: Schemas["UnrelateApplyRequest"]) => ({
      unrelated: {
        source_id: body.version.source_id,
        relation_type: body.version.relation_type,
        target_id: body.version.target_id,
        commit_sha: "c",
        committed_confidential: false,
      },
      warnings: [],
    })),
    previewTierChange: vi.fn(async (body: Schemas["TierChangePreviewRequest"]) => ({
      ...body,
      configured_tier: "volatile",
      version: { ...body, content_digest: "t1" },
    })),
    applyTierChange: vi.fn(async (body: Schemas["TierChangeApplyRequest"]) => ({
      changed: { type_name: body.version.type_name, tier: body.version.tier, commit_sha: "c" },
      warnings: [],
    })),
    previewUnmerge: vi.fn(async (body: Schemas["UnmergePreviewRequest"]) => unmergePreview(body.absorbed_id)),
    applyUnmerge: vi.fn(async () => ({
      unmerged: { survivor_id: "people/a", restored_ids: ["people/b"], commit_shas: ["c"] },
      warnings: [],
    })),
  } satisfies ReviewClient;
  const sleep = vi.fn(async (_ms: number) => {});
  let clock = 0;
  const model = new ReviewModel(client, { sleep, now: () => clock, today: () => NOW });
  const advance = (ms: number) => {
    clock += ms;
  };
  return { client, model, sleep, advance };
}

function unmergePreview(absorbed: string, steps = [absorbed]): Schemas["UnmergePreview"] {
  return {
    survivor_id: "people/a",
    absorbed_id: absorbed,
    survivor_edits_discarded: false,
    steps: steps.map((id) => ({
      survivor_id: "people/a",
      absorbed_id: id,
      absorbed_title: PAGES[id]?.title ?? null,
      merged_at: "2026-10-16T12:00:00Z",
      files_rewritten: 0,
      survivor_edits_unverifiable: false,
      restores_catalog_snapshot: false,
    })),
    version: { survivor_id: "people/a", absorbed_id: absorbed, content_digest: "u1" },
  };
}

async function opened(decisions: Decision[], key: string) {
  const f = fake(decisions);
  await f.model.load();
  f.model.open(key);
  return f;
}

describe("loading To review", () => {
  it("lists the decisions and reads each page they name once", async () => {
    const { model, client } = fake([PAIR, GROUP]);
    await model.load();
    expect(model.state.decisions.map((d) => d.decision_key)).toEqual(["pair", "group"]);
    expect(client.readPage.mock.calls.map(([id]) => id).sort()).toEqual(["people/a", "people/b", "people/c"]);
    expect(model.state.pages.get("people/a")?.title).toBe("Marguerite Osei");
    expect(model.state.loaded).toBe(true);
  });

  it("reads a connection's pages too", async () => {
    const relation: Decision = {
      ...PAIR,
      decision_key: "rel",
      kind: "relation_type",
      targets: [],
      detail: {
        source_id: "people/a",
        target_id: "orgs/acme",
        effective_source_id: "people/c",
        effective_target_id: "orgs/acme",
        suggested_type: "part_of",
        rationale: "r",
      },
    };
    const { model, client } = fake([relation]);
    await model.load();
    expect(client.readPage.mock.calls.map(([id]) => id).sort()).toEqual(["orgs/acme", "people/c"]);
  });

  it("keeps listing when a page cannot be read", async () => {
    const { model } = fake([identity("ghost", ["people/a", "people/ghost"])]);
    await model.load();
    expect(model.state.pages.has("people/ghost")).toBe(false);
    expect(model.state.decisions).toHaveLength(1);
  });

  it("words a list that cannot be read", async () => {
    const { model, client } = fake();
    client.decisions.mockRejectedValueOnce(new EngineUnreachable({ path: "/v1/decisions" }));
    await model.load();
    expect(model.state.listProblem).toEqual({ message: "OpenKOS isn't responding. Nothing was changed.", action: "try_again" });
  });

  it("lists every combined page with its lines, reading its title", async () => {
    const { model, client } = fake([]);
    client.mergedConcepts.mockResolvedValueOnce({ concepts: COMBINED });
    await model.load();
    expect(client.readPage).toHaveBeenCalledWith("people/a");
    expect(model.state.combined).toEqual(COMBINED_LINES);
  });

  it("keeps the decisions and the last combined list when that list cannot be read", async () => {
    const { model, client } = fake([PAIR]);
    client.mergedConcepts.mockResolvedValueOnce({ concepts: COMBINED });
    await model.load();
    client.mergedConcepts.mockRejectedValueOnce(apiError("internal_error", 500));
    await model.load();
    expect(model.state.decisions).toHaveLength(1);
    expect(model.state.listProblem).toBeNull();
    expect(model.state.combined).toEqual(COMBINED_LINES);
  });

  it("tells subscribers about every change", async () => {
    const { model } = fake();
    const seen: boolean[] = [];
    model.subscribe((state) => seen.push(state.loading));
    await model.load();
    expect(seen).toEqual([true, false]);
  });
});

describe("what the detail shows", () => {
  it("shows the combined pages in place of a decision, and a decision in place of them", async () => {
    const { model, client } = await opened([PAIR], "pair");
    model.showCombined();
    expect(model.state.combinedOpen).toBe(true);
    expect(model.state.open).toBeNull();
    model.open("pair");
    expect(model.state.combinedOpen).toBe(false);
    expect(model.state.open?.key).toBe("pair");
    expect(client.previewDecision).not.toHaveBeenCalled();
  });

  it("shows nothing when a decision is closed", async () => {
    const { model } = await opened([PAIR], "pair");
    model.close();
    expect(model.state.open).toBeNull();
    expect(model.state.combinedOpen).toBe(false);
  });
});

describe("one decision", () => {
  it("decides later without sending anything", async () => {
    const { model, client } = await opened([PAIR], "pair");
    model.choose(DECIDE_LATER);
    expect(model.state.open).toBeNull();
    expect(model.state.notice).toEqual({ tone: "info", text: "Nothing changes. This stays in To review." });
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(client.applyDecision).not.toHaveBeenCalled();
    expect(model.state.decisions).toHaveLength(1);
  });

  it("previews, then applies the previewed version unchanged", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    expect(client.previewDecision).toHaveBeenCalledWith("pair", { action: "merge" });
    expect(model.state.open?.step).toMatchObject({ name: "preview", action: "merge" });
    await model.confirm();
    expect(client.applyDecision).toHaveBeenCalledWith("pair", { action: "merge", version: VERSION });
    expect(model.state.open?.step).toEqual({
      name: "done",
      lines: ["Combined into 'Marguerite Osei'."],
      undo: { kind: "split", survivor: "people/a", absorbed: "people/b" },
    });
    expect(client.decisions).toHaveBeenCalledTimes(2);
  });

  it("lists the combine for Split again from the engine's list", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    client.mergedConcepts.mockResolvedValueOnce({ concepts: COMBINED });
    await model.confirm();
    expect(client.mergedConcepts).toHaveBeenCalledTimes(2);
    expect(model.state.combined).toEqual(COMBINED_LINES);
  });

  it("offers Undo after keeping separate, reopening exactly the plan's pages", async () => {
    const { model } = await opened([PAIR], "pair");
    await model.choose({ choice: "keep_distinct", label: "", does: "" });
    await model.confirm();
    expect(model.state.open?.step).toEqual({
      name: "done",
      lines: ["Kept separate."],
      undo: { kind: "reopen", members: ["people/a", "people/b"] },
    });
    expect(model.state.combined).toEqual([]);
  });

  it("goes back from a preview to the choice without applying", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    model.back();
    expect(model.state.open?.step).toEqual({ name: "choosing" });
    await model.confirm();
    expect(client.applyDecision).not.toHaveBeenCalled();
  });

  it("never sends skip", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "skip", label: "", does: "" });
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(model.state.open).toBeNull();
  });

  it("starts the check from an unjudged decision without previewing", async () => {
    const { model, client } = await opened([UNJUDGED], "unjudged");
    await model.choose({ choice: "check", label: "", does: "" });
    expect(client.quoteJudging).toHaveBeenCalledWith({ kind: "identity" });
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(model.state.check?.phase).toBe("consent");
  });
});

const SAME = { choice: "same", label: "Same thing", does: "" } as const;

describe("asking whether they are the same, first", () => {
  it("previews nothing of a group before Same thing, and only the answer after it", async () => {
    const { model, client } = await opened([GROUP], "group");
    expect(model.state.open?.selection).toEqual({ same: false, marked: [], name: null, okfType: null });
    model.toggleMark("people/a");
    model.toggleMark("people/c");
    model.chooseName("people/c");
    await model.choose({ choice: "merge_group", label: "", does: "" });
    expect(client.previewDecision).not.toHaveBeenCalled();
    await model.choose(SAME);
    expect(model.state.open?.selection.same).toBe(true);
    expect(model.state.open?.step).toEqual({ name: "choosing" });
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(client.applyDecision).not.toHaveBeenCalled();
  });

  it("previews nothing of different kinds before Same thing", async () => {
    const { model, client } = await opened([KINDS], "kinds");
    await model.choose({ choice: "merge_group", label: "", does: "", okfType: "Person" });
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(model.state.open?.selection).toMatchObject({ same: false, okfType: null, name: null });
  });

  it("previews keeping them separate without asking anything else", async () => {
    const { model, client } = await opened([KINDS], "kinds");
    await model.choose({ choice: "keep_distinct", label: "", does: "" });
    expect(client.previewDecision).toHaveBeenCalledWith("kinds", { action: "keep_distinct" });
  });

  it("goes back to the first question, forgetting the answers after it", async () => {
    const { model } = await opened([GROUP], "group");
    await model.choose(SAME);
    model.toggleMark("people/a");
    model.askAgain();
    expect(model.state.open?.selection).toEqual({ same: false, marked: [], name: null, okfType: null });
    expect(model.state.open?.step).toEqual({ name: "choosing" });
  });

  it("keeps Same thing for a pair the combine itself", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose(SAME);
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(model.state.open?.selection.same).toBe(false);
  });
});

describe("marking a group", () => {
  async function marking() {
    const f = await opened([GROUP], "group");
    await f.model.choose(SAME);
    return f;
  }

  it("marks nothing for the person and refuses fewer than two", async () => {
    const { model, client } = await marking();
    expect(model.state.open?.selection).toEqual({ same: true, marked: [], name: null, okfType: null });
    model.toggleMark("people/a");
    model.chooseName("people/a");
    expect(model.state.open?.selection.name).toBe("people/a");
    expect(model.canCombineMarked()).toBe(false);
    await model.choose({ choice: "merge_group", label: "", does: "" });
    expect(client.previewDecision).not.toHaveBeenCalled();
  });

  it("asks for the name among the marked pages only", async () => {
    const { model } = await marking();
    model.toggleMark("people/a");
    model.toggleMark("people/c");
    expect(model.canCombineMarked()).toBe(false);
    model.chooseName("people/b");
    expect(model.state.open?.selection.name).toBeNull();
    model.chooseName("people/c");
    expect(model.canCombineMarked()).toBe(true);
  });

  it("clears the name when its page is unmarked", async () => {
    const { model } = await marking();
    model.toggleMark("people/a");
    model.toggleMark("people/c");
    model.chooseName("people/c");
    model.toggleMark("people/c");
    expect(model.state.open?.selection).toEqual({ same: true, marked: ["people/a"], name: null, okfType: null });
  });

  it("sends the marked pages and the chosen name", async () => {
    const { model, client } = await marking();
    model.toggleMark("people/a");
    model.toggleMark("people/c");
    model.chooseName("people/c");
    await model.choose({ choice: "merge_group", label: "", does: "" });
    expect(client.previewDecision).toHaveBeenCalledWith("group", {
      action: "merge_group",
      selection: { survivor_id: "people/c", member_ids: ["people/a", "people/c"] },
    });
  });
});

describe("choosing what something is", () => {
  async function choosing() {
    const f = await opened([KINDS], "kinds");
    await f.model.choose(SAME);
    return f;
  }

  it("takes the name from the only page of the chosen kind", async () => {
    const { model, client } = await choosing();
    expect(model.state.open?.selection.marked).toEqual(["people/a", "orgs/acme", "orgs/acme2"]);
    await model.choose({ choice: "merge_group", label: "", does: "", okfType: "Person" });
    expect(client.previewDecision).toHaveBeenCalledWith("kinds", {
      action: "merge_group",
      selection: { survivor_id: "people/a", member_ids: ["people/a", "orgs/acme", "orgs/acme2"] },
    });
  });

  it("asks which keeps its name when two pages share the kind", async () => {
    const { model, client } = await choosing();
    await model.choose({ choice: "merge_group", label: "", does: "", okfType: "Organization" });
    expect(client.previewDecision).not.toHaveBeenCalled();
    expect(model.namesForKind()).toEqual(["orgs/acme", "orgs/acme2"]);
    model.chooseName("people/a");
    expect(model.state.open?.selection.name).toBeNull();
    model.chooseName("orgs/acme2");
    await model.choose({ choice: "merge_group", label: "", does: "", okfType: "Organization" });
    expect(client.previewDecision).toHaveBeenCalledWith("kinds", {
      action: "merge_group",
      selection: { survivor_id: "orgs/acme2", member_ids: ["people/a", "orgs/acme", "orgs/acme2"] },
    });
  });
});

describe("stale, busy and refused", () => {
  it("re-previews a stale apply and never applies twice", async () => {
    const fresh = { ...VERSION, content_digest: "d2" };
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    client.applyDecision.mockRejectedValueOnce(apiError("stale"));
    client.previewDecision.mockResolvedValueOnce(preview("merge", fresh));
    await model.confirm();
    expect(client.applyDecision).toHaveBeenCalledTimes(1);
    expect(client.decisions).toHaveBeenCalledTimes(2);
    expect(client.previewDecision).toHaveBeenCalledTimes(2);
    expect(model.state.open?.changed).toBe(CHANGED);
    expect(model.state.open?.step).toMatchObject({ name: "preview", preview: { version: fresh } });
  });

  it("re-previews on write drift and on a stale preview", async () => {
    const { model, client } = await opened([PAIR], "pair");
    client.previewDecision.mockRejectedValueOnce(apiError("stale"));
    await model.choose({ choice: "merge", label: "", does: "" });
    expect(client.previewDecision).toHaveBeenCalledTimes(2);
    client.applyDecision.mockRejectedValueOnce(apiError("write_drift"));
    await model.confirm();
    expect(client.previewDecision).toHaveBeenCalledTimes(3);
    expect(model.state.open?.step.name).toBe("preview");
  });

  it("returns to the list when a stale decision is gone", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    client.applyDecision.mockRejectedValueOnce(apiError("stale"));
    client.decisions.mockResolvedValueOnce({ decisions: [] });
    await model.confirm();
    expect(model.state.open).toBeNull();
    expect(model.state.notice).toEqual({ tone: "warning", text: "These pages changed, and this question no longer applies." });
  });

  it("shows a decision listed as changed with the changed notice", async () => {
    const { model } = await opened([{ ...PAIR, fresh: false }], "pair");
    expect(model.state.open?.changed).toBe(CHANGED_CHECK);
  });

  it("shows a stale apply whose decision is now changed as changed, never previewing it again", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    client.applyDecision.mockRejectedValueOnce(apiError("stale"));
    client.decisions.mockResolvedValue({ decisions: [{ ...PAIR, fresh: false }] });
    await model.confirm();
    expect(client.previewDecision).toHaveBeenCalledTimes(1);
    expect(model.state.open?.changed).toBe(CHANGED_CHECK);
    expect(model.state.open?.step).toEqual({ name: "choosing" });
    expect(model.state.open?.problem).toBeNull();
  });

  it("retries busy for the bounded time, then offers Try again that repeats the apply", async () => {
    const { model, client, sleep, advance } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    client.applyDecision.mockImplementation(async () => {
      advance(2000);
      throw apiError("workspace_busy", 503, 2);
    });
    await model.confirm();
    expect(sleep).toHaveBeenCalledWith(2000);
    expect(client.applyDecision.mock.calls.length).toBeGreaterThan(1);
    expect(model.state.open?.problem).toEqual({
      message: "OpenKOS is finishing something else. Nothing was changed.",
      action: "try_again",
    });
    client.applyDecision.mockReset();
    client.applyDecision.mockResolvedValue({
      applied: { action: "merge", commit_sha: null, decision_key: "pair", merged: [["people/a", "people/b"]] },
      warnings: [],
    });
    await model.resolveProblem();
    expect(client.applyDecision).toHaveBeenCalledWith("pair", { action: "merge", version: VERSION });
    expect(model.state.open?.step.name).toBe("done");
  });

  it("does not retry a busy preview blindly", async () => {
    const { model, client } = await opened([PAIR], "pair");
    client.previewDecision.mockRejectedValueOnce(apiError("workspace_busy", 503));
    await model.choose({ choice: "merge", label: "", does: "" });
    expect(client.previewDecision).toHaveBeenCalledTimes(1);
    expect(model.state.open?.problem?.action).toBe("try_again");
    await model.resolveProblem();
    expect(client.previewDecision).toHaveBeenCalledTimes(2);
    expect(model.state.open?.step.name).toBe("preview");
  });

  it("closes a decision whose page is gone after OK", async () => {
    const { model, client } = await opened([PAIR], "pair");
    client.previewDecision.mockRejectedValueOnce(apiError("member_missing"));
    await model.choose({ choice: "merge", label: "", does: "" });
    expect(model.state.open?.problem?.message).toBe("One of these pages no longer exists, so there's nothing to decide.");
    client.decisions.mockResolvedValueOnce({ decisions: [] });
    await model.resolveProblem();
    expect(model.state.open).toBeNull();
    expect(model.state.decisions).toEqual([]);
  });

  it("explains an interrupted group and reloads", async () => {
    const { model, client } = await opened([GROUP], "group");
    await model.choose(SAME);
    model.toggleMark("people/a");
    model.toggleMark("people/b");
    model.chooseName("people/a");
    await model.choose({ choice: "merge_group", label: "", does: "" });
    client.applyDecision.mockRejectedValueOnce(apiError("merge_group_interrupted", 500));
    await model.confirm();
    expect(model.state.open?.problem).toEqual({
      message: "Only some of these were combined. Nothing else changed, and each combine can be split again.",
      action: "reload",
    });
    client.decisions.mockResolvedValueOnce({ decisions: [] });
    await model.resolveProblem();
    expect(client.applyDecision).toHaveBeenCalledTimes(1);
    expect(model.state.open).toBeNull();
  });

  it("never claims nothing changed after an unknown failure of a write", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    client.applyDecision.mockRejectedValueOnce(apiError("write_failed", 500));
    await model.confirm();
    expect(model.state.open?.problem).toEqual({
      message: "Something went wrong. Look at To review to see what changed.",
      action: "reload",
    });
  });
});

describe("the check", () => {
  it("runs only after agreement, with the quote's token, and reports what became decidable", async () => {
    const { model, client, sleep } = fake([UNJUDGED, PAIR]);
    await model.load();
    await model.startCheck();
    expect(client.startJudgingRun).not.toHaveBeenCalled();
    expect(model.state.check?.consent?.heading).toBe("Check 2 pairs?");
    client.decisions.mockResolvedValue({ decisions: [{ ...UNJUDGED, evaluation: "judged_same" }, PAIR] });
    await model.agreeCheck();
    expect(client.startJudgingRun).toHaveBeenCalledWith({ token: TOKEN });
    expect(client.judgingRun).toHaveBeenCalledWith("r1");
    expect(sleep).toHaveBeenCalledWith(1000);
    expect(model.state.check).toMatchObject({ phase: "done", text: "Checked 2. 1 is ready for you to decide." });
  });

  it("shows progress while it runs and stops when asked", async () => {
    const { model, client } = fake([UNJUDGED]);
    await model.load();
    await model.startCheck();
    const seen: string[] = [];
    model.subscribe((s) => {
      if (s.check?.text) seen.push(s.check.text);
    });
    client.judgingRun
      .mockImplementationOnce(async () => {
        await model.stopCheck();
        return run("running", { index: 1 });
      })
      .mockResolvedValueOnce(run("finished", { index: 1, outcome: outcome(1, "stopped") }));
    await model.agreeCheck();
    expect(client.stopJudgingRun).toHaveBeenCalledWith("r1");
    expect(seen).toContain("Checking 0 of 2…");
    expect(seen).toContain("Stopping after the current one. Everything already checked is kept.");
    expect(seen).not.toContain("Checking 1 of 2…");
    expect(model.state.check?.text).toBe("Checked 1. 0 are ready for you to decide.");
  });

  it("re-quotes a stale quote and never runs the old one", async () => {
    const { model, client } = fake([UNJUDGED]);
    await model.load();
    await model.startCheck();
    client.startJudgingRun.mockRejectedValueOnce(apiError("quote_stale"));
    await model.agreeCheck();
    expect(client.quoteJudging).toHaveBeenCalledTimes(2);
    expect(client.judgingRun).not.toHaveBeenCalled();
    expect(model.state.check).toMatchObject({
      phase: "consent",
      text: "Something changed since you agreed — the AI model or the pages. Please look again.",
    });
  });

  it("words a run that is already going and a model that is not running", async () => {
    const { model, client } = fake([UNJUDGED]);
    await model.load();
    await model.startCheck();
    client.startJudgingRun.mockRejectedValueOnce(apiError("run_active"));
    await model.agreeCheck();
    expect(model.state.check?.problem?.message).toBe("A check is already running.");
    client.quoteJudging.mockRejectedValueOnce(apiError("backend_unavailable", 503));
    await model.startCheck();
    expect(model.state.check?.problem).toEqual({
      message: "The AI model isn't running. OpenKOS needs it to check pages. Check setup, on Status, helps you start it.",
      action: "try_again",
    });
    await model.resolveCheckProblem();
    expect(model.state.check?.phase).toBe("consent");
  });

  it("words a run that failed and keeps what was checked", async () => {
    const { model, client } = fake([UNJUDGED]);
    await model.load();
    await model.startCheck();
    client.judgingRun.mockResolvedValueOnce(
      run("failed", { error: { code: "backend_unavailable", retryable: true, message: "x" } }),
    );
    await model.agreeCheck();
    expect(model.state.check?.problem?.message).toBe("The AI model isn't running. OpenKOS needs it to check pages. Check setup, on Status, helps you start it.");
    // Listed on load, again before the quote, and again after the run.
    expect(client.decisions).toHaveBeenCalledTimes(3);
  });

  it("refreshes the queue before it quotes and after the run, so changed pairs come back decidable", async () => {
    const { model, client } = fake([UNJUDGED]);
    await model.load();
    const calls: string[] = [];
    client.refreshQueue.mockImplementation(async () => {
      calls.push("refresh");
      return QUEUE_REFRESH;
    });
    client.quoteJudging.mockImplementation(async () => {
      calls.push("quote");
      return QUOTE;
    });
    client.decisions.mockImplementation(async () => {
      calls.push("list");
      return { decisions: [UNJUDGED] };
    });
    await model.startCheck();
    await model.agreeCheck();
    expect(calls).toEqual(["refresh", "list", "quote", "refresh", "list"]);
  });

  it("counts a changed pair the check made decidable as ready", async () => {
    const changed = { ...PAIR, fresh: false };
    const { model, client } = fake([changed]);
    await model.load();
    await model.startCheck();
    client.decisions.mockResolvedValue({ decisions: [PAIR] });
    await model.agreeCheck();
    expect(model.state.check?.text).toBe("Checked 2. 1 is ready for you to decide.");
  });
});

describe("a changed decision", () => {
  const CHANGED_PAIR = { ...PAIR, fresh: false };

  it("checks again for free when the refresh makes it decidable", async () => {
    const { model, client } = await opened([CHANGED_PAIR], "pair");
    client.decisions.mockResolvedValue({ decisions: [PAIR] });
    await model.choose({ choice: "recheck", label: "", does: "" });
    expect(client.refreshQueue).toHaveBeenCalledTimes(1);
    expect(client.quoteJudging).not.toHaveBeenCalled();
    expect(model.state.check).toBeNull();
    expect(model.state.open).toMatchObject({ decision: PAIR, changed: null, step: { name: "choosing" } });
  });

  it("asks agreement to check its kind when it is still changed", async () => {
    const relation: Decision = { ...CHANGED_PAIR, decision_key: "rel", kind: "relation_type", evaluation: "not_applicable" };
    const { model, client } = await opened([relation], "rel");
    await model.choose({ choice: "recheck", label: "", does: "" });
    expect(client.refreshQueue).toHaveBeenCalledTimes(1);
    expect(client.quoteJudging).toHaveBeenCalledWith({ kind: "relation_type" });
    expect(model.state.check?.phase).toBe("consent");
    expect(client.startJudgingRun).not.toHaveBeenCalled();
  });

  it("says the question no longer applies when the refresh retired it", async () => {
    const { model, client } = await opened([CHANGED_PAIR], "pair");
    client.decisions.mockResolvedValue({ decisions: [] });
    await model.choose({ choice: "recheck", label: "", does: "" });
    expect(model.state.open).toBeNull();
    expect(model.state.check).toBeNull();
    expect(model.state.notice).toEqual({ tone: "warning", text: "These pages changed, and this question no longer applies." });
    expect(client.quoteJudging).not.toHaveBeenCalled();
  });

  it("checks again even when the refresh fails", async () => {
    const { model, client } = await opened([CHANGED_PAIR], "pair");
    client.refreshQueue.mockRejectedValueOnce(apiError("workspace_busy", 503));
    await model.choose({ choice: "recheck", label: "", does: "" });
    expect(client.quoteJudging).toHaveBeenCalledWith({ kind: "identity" });
  });
});

describe("Check all", () => {
  it("is offered for an unjudged pair and for a changed one, and not otherwise", () => {
    expect(offersCheckAll([UNJUDGED])).toBe(true);
    expect(offersCheckAll([{ ...PAIR, fresh: false }])).toBe(true);
    expect(offersCheckAll([PAIR])).toBe(false);
    expect(offersCheckAll([{ ...PAIR, kind: "relation_type", fresh: false }])).toBe(false);
  });
});

describe("a visit", () => {
  it("refreshes the queue before it lists the decisions", async () => {
    const { model, client } = fake([PAIR]);
    const calls: string[] = [];
    client.refreshQueue.mockImplementation(async () => {
      calls.push("refresh");
      return QUEUE_REFRESH;
    });
    client.decisions.mockImplementation(async () => {
      calls.push("list");
      return { decisions: [PAIR] };
    });
    await model.visit();
    expect(calls).toEqual(["refresh", "list"]);
    expect(model.state.loaded).toBe(true);
  });

  it("still lists when the refresh fails", async () => {
    const { model, client } = fake([PAIR]);
    client.refreshQueue.mockRejectedValueOnce(apiError("workspace_busy", 503));
    await model.visit();
    expect(model.state.decisions).toEqual([PAIR]);
  });

  it("shows the open decision as it is after the check", async () => {
    const { model, client } = await opened([UNJUDGED], "unjudged");
    await model.choose({ choice: "check", label: "", does: "" });
    const judged = { ...UNJUDGED, evaluation: "judged_same" as const, actions: ["merge", "keep_distinct", "skip"] as Schemas["Action"][] };
    client.decisions.mockResolvedValue({ decisions: [judged] });
    await model.agreeCheck();
    expect(model.state.open?.decision).toEqual(judged);
    expect(model.state.open?.step).toEqual({ name: "choosing" });
  });

  it("closes the consent without running", async () => {
    const { model, client } = fake([UNJUDGED]);
    await model.load();
    await model.startCheck();
    model.closeCheck();
    expect(model.state.check).toBeNull();
    expect(client.startJudgingRun).not.toHaveBeenCalled();
  });
});

describe("split again", () => {
  it("asks first, then splits with the previewed version and reads the history again", async () => {
    const { model, client } = fake();
    await model.load();
    await model.openSplit("people/a", "people/b");
    expect(client.previewUnmerge).toHaveBeenCalledWith({ survivor_id: "people/a", absorbed_id: "people/b" });
    expect(model.state.split).toMatchObject({
      phase: "confirm",
      text: "Split 'M. Osei' out again? Both pages go back exactly as they were.",
    });
    await model.confirmSplit();
    expect(client.applyUnmerge).toHaveBeenCalledWith({
      version: { survivor_id: "people/a", absorbed_id: "people/b", content_digest: "u1" },
    });
    expect(model.state.split).toMatchObject({ phase: "done", text: "Split. 'M. Osei' is back as it was." });
    expect(client.mergedConcepts).toHaveBeenCalledTimes(2);
    expect(client.decisions).toHaveBeenCalledTimes(2);
  });

  it("lists the later combines first", async () => {
    const { model, client } = fake();
    client.previewUnmerge.mockResolvedValueOnce(unmergePreview("people/b", ["people/c", "people/b"]));
    await model.openSplit("people/a", "people/b");
    expect(model.state.split?.text).toBe("To split this, the later combines on this page are split first: 'Maggie Osei'. Continue?");
  });

  it("refuses an edited page with OK only and never asks to discard", async () => {
    const { model, client } = fake();
    client.previewUnmerge.mockRejectedValueOnce(apiError("survivor_edited"));
    await model.openSplit("people/a", "people/b");
    expect(model.state.split).toMatchObject({
      phase: "problem",
      problem: { message: "This page was changed after it was combined, so it can't be split here.", action: "ok" },
    });
    expect(JSON.stringify(client.previewUnmerge.mock.calls)).not.toContain("discard");
    await model.resolveSplitProblem();
    expect(model.state.split).toBeNull();
    expect(client.previewUnmerge).toHaveBeenCalledTimes(1);
  });

  it("explains an interrupted split and reads the history again", async () => {
    const { model, client } = fake();
    await model.openSplit("people/a", "people/b");
    client.applyUnmerge.mockRejectedValueOnce(apiError("unmerge_interrupted", 500));
    await model.confirmSplit();
    expect(model.state.split?.problem?.message).toBe(
      "Only some of these were split. Each page that was split is back as it was.",
    );
    await model.resolveSplitProblem();
    expect(client.mergedConcepts).toHaveBeenCalledTimes(1);
    expect(model.state.split).toBeNull();
  });

  it("re-previews a stale split", async () => {
    const { model, client } = fake();
    await model.openSplit("people/a", "people/b");
    client.applyUnmerge.mockRejectedValueOnce(apiError("stale"));
    await model.confirmSplit();
    expect(client.previewUnmerge).toHaveBeenCalledTimes(2);
    expect(model.state.split?.phase).toBe("confirm");
    expect(client.applyUnmerge).toHaveBeenCalledTimes(1);
  });

  it("takes the Undo away once the combine is split", async () => {
    const { model } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    await model.confirm();
    await model.undo();
    await model.confirmSplit();
    expect(model.state.open?.step).toEqual({ name: "done", lines: ["Combined into 'Marguerite Osei'."], undo: null });
  });

  it("splits from a combine's Undo", async () => {
    const { model, client } = await opened([PAIR], "pair");
    await model.choose({ choice: "merge", label: "", does: "" });
    await model.confirm();
    await model.undo();
    expect(client.previewUnmerge).toHaveBeenCalledWith({ survivor_id: "people/a", absorbed_id: "people/b" });
    expect(model.state.split?.phase).toBe("confirm");
  });
});

const RELATION: Decision = {
  ...PAIR,
  decision_key: "rel",
  kind: "relation_type",
  targets: [],
  actions: ["relate", "skip"],
  detail: {
    source_id: "people/c",
    target_id: "orgs/acme",
    effective_source_id: "people/c",
    effective_target_id: "orgs/acme",
    suggested_type: "part_of",
    rationale: "r",
  },
};
const TIER: Decision = {
  ...PAIR,
  decision_key: "tier",
  kind: "volatility",
  targets: [],
  actions: ["set_tier", "skip"],
  detail: { type_name: "Person", current_default: "slow", suggested_tier: "volatile", rationale: "r" },
};

async function decided(decision: Decision, action: Schemas["Action"]) {
  const f = await opened([decision], decision.decision_key);
  await f.model.choose({ choice: action, label: "", does: "" });
  await f.model.confirm();
  return f;
}

describe("undo", () => {
  it("reopens a pair kept separate, asks the queue again and says so", async () => {
    const { model, client } = await decided(PAIR, "keep_distinct");
    await model.undo();
    expect(client.previewReopen).toHaveBeenCalledWith({ member_ids: ["people/a", "people/b"] });
    expect(client.applyReopen).toHaveBeenCalledWith({
      version: { member_ids: ["people/a", "people/b"], content_digest: "r1" },
    });
    expect(client.refreshQueue).toHaveBeenCalledTimes(1);
    expect(client.decisions).toHaveBeenCalledTimes(3);
    expect(model.state.open?.step).toEqual({ name: "done", lines: ["Undone. We'll ask you about these again."], undo: null });
  });

  it("still reloads when the queue cannot be refreshed after a reopen", async () => {
    const { model, client } = await decided(PAIR, "keep_distinct");
    client.refreshQueue.mockRejectedValueOnce(apiError("workspace_busy", 503));
    await model.undo();
    expect(client.decisions).toHaveBeenCalledTimes(3);
    expect(model.state.open?.problem).toBeNull();
  });

  it("removes exactly the connection the plan wrote", async () => {
    const { model, client } = await decided(RELATION, "relate");
    expect(model.state.open?.step).toMatchObject({
      name: "done",
      undo: { kind: "unrelate", source: "people/c", relationType: "part_of", target: "orgs/acme" },
    });
    await model.undo();
    expect(client.previewUnrelate).toHaveBeenCalledWith({
      source_id: "people/c",
      relation_type: "part_of",
      target_id: "orgs/acme",
    });
    expect(client.applyUnrelate).toHaveBeenCalledWith({
      version: { source_id: "people/c", relation_type: "part_of", target_id: "orgs/acme", content_digest: "n1" },
    });
    expect(client.refreshQueue).not.toHaveBeenCalled();
    expect(model.state.open?.step).toEqual({ name: "done", lines: ["Undone. That connection is removed."], undo: null });
  });

  it("restores the setting the plan replaced, removing it when there was none", async () => {
    const { model, client } = await decided(TIER, "set_tier");
    await model.undo();
    expect(client.previewTierChange).toHaveBeenCalledWith({ type_name: "Person", tier: null });
    expect(client.applyTierChange).toHaveBeenCalledWith({
      version: { type_name: "Person", tier: null, content_digest: "t1" },
    });
    expect(model.state.open?.step).toEqual({ name: "done", lines: ["Undone. People are treated as before."], undo: null });
  });

  it("restores an earlier setting by name", async () => {
    const f = await opened([TIER], "tier");
    f.client.previewDecision.mockResolvedValueOnce({
      decision_key: "tier",
      action: "set_tier",
      plan: { ...TIER_PLAN, configured_tier: "static" },
      version: VERSION,
    });
    await f.model.choose({ choice: "set_tier", label: "", does: "" });
    await f.model.confirm();
    await f.model.undo();
    expect(f.client.previewTierChange).toHaveBeenCalledWith({ type_name: "Person", tier: "static" });
  });

  it("offers no undo for a setting the app cannot name", async () => {
    const f = await opened([TIER], "tier");
    f.client.previewDecision.mockResolvedValueOnce({
      decision_key: "tier",
      action: "set_tier",
      plan: { ...TIER_PLAN, configured_tier: "sometimes" },
      version: VERSION,
    });
    await f.model.choose({ choice: "set_tier", label: "", does: "" });
    await f.model.confirm();
    expect(f.model.state.open?.step).toMatchObject({ name: "done", undo: null });
  });

  it("retries a busy undo, then applies once", async () => {
    const { model, client } = await decided(PAIR, "keep_distinct");
    client.applyReopen.mockRejectedValueOnce(apiError("workspace_busy", 503, 1));
    await model.undo();
    expect(client.applyReopen).toHaveBeenCalledTimes(2);
    expect(client.previewReopen).toHaveBeenCalledTimes(1);
    expect(model.state.open?.step).toMatchObject({ name: "done", undo: null });
  });

  it("words a refused undo and takes the Undo away", async () => {
    const { model, client } = await decided(RELATION, "relate");
    client.previewUnrelate.mockRejectedValueOnce(apiError("not_related"));
    await model.undo();
    expect(client.applyUnrelate).not.toHaveBeenCalled();
    expect(model.state.open?.problem).toEqual({ message: "These pages aren't connected any more.", action: "ok" });
    expect(model.state.open?.step).toMatchObject({ name: "done", undo: null });
    await model.resolveProblem();
    expect(model.state.open).toBeNull();
  });

  it("never applies a stale undo again blindly", async () => {
    const { model, client } = await decided(PAIR, "keep_distinct");
    client.applyReopen.mockRejectedValueOnce(apiError("stale"));
    await model.undo();
    expect(client.applyReopen).toHaveBeenCalledTimes(1);
    expect(model.state.open?.problem?.action).toBe("reload");
    await model.resolveProblem();
    expect(client.applyReopen).toHaveBeenCalledTimes(1);
    expect(model.state.open).toBeNull();
  });

  it("tries a failed undo again from its preview", async () => {
    const { model, client } = await decided(PAIR, "keep_distinct");
    client.previewReopen.mockRejectedValueOnce(new EngineUnreachable({ path: "/v1/reopen/preview" }));
    await model.undo();
    expect(model.state.open?.problem).toEqual({ message: "OpenKOS isn't responding. Nothing was changed.", action: "try_again" });
    expect(model.state.open?.step).toMatchObject({ name: "done", undo: { kind: "reopen" } });
    await model.resolveProblem();
    expect(client.applyReopen).toHaveBeenCalledTimes(1);
    expect(model.state.open?.step).toMatchObject({ name: "done", undo: null });
  });
});
