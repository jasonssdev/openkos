# Tasks: Resolve Identity Groups Through The Decision Surface

Tracking issue: #1430. Inputs: `proposal.md`, `design.md` (authoritative),
`specs/curate-decisions/spec.md`, `specs/stable-python-api/spec.md`,
`specs/local-http-api/spec.md`, `specs/pending-work/spec.md` (deltas) and
ADR-0063 (Proposed, written with this plan). Paths are under `src/openkos` unless
they start with `tests/`, `docs/` or `openspec/`.

PR text names the change in prose ("the `identity-groups-decision` change") and
uses `Refs #1430` on every slice but the last, which uses `Closes #1430`. Never
write a close keyword in any other form (a negated one still closes).

Spec shorthand. Curate-decisions: `C1` Each Kind Carries Its Own Action Set
(MODIFIED); `C2` Only Judged-SAME Identity Items Are Mergeable (MODIFIED); `C3`
Apply Is A Two-Phase Preview And Commit Against A Version Token (MODIFIED); `C4`
A Group Selection Names The Marked Members And The Survivor; `C5` A Group
Decision Merges The Marked Members Pairwise Into The Survivor; `C6` Members Not
Marked Are Kept Distinct From The Survivor; `C7` A Cross-Type Group Merges Only
Into The Survivor The Selection Names; `C8` A Group Decision Is Checked Once,
Holds The Lock Once And Commits Once. `P1` stable-python-api: Group Decisions
Extend v1 Additively. `H1` local-http-api: Responses Are The Python API's Result
Types (MODIFIED); `H2` Outcomes And Errors Map To Status Codes (MODIFIED); `H3`
The Group Selection Travels In The Preview Body And The Echoed Version. `W1`
pending-work: Only Human-Facing Write Paths Resolve A Row (MODIFIED). `D1`..`D13`
are the design decisions.

## Review Workload Forecast

Authored changed lines are additions plus deletions. The generated
`tests/unit/api/api_surface.json` and `tests/unit/localapi/openapi.json` are
excluded from estimates (they still ship in their PR). The ~400-line figure is an
advisory heuristic: no test or doc is cut and no slice is split artificially to
fit it.

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,050 authored across 6 slices (generated snapshots excluded) |
| 400-line budget risk | High in total; per slice below (S2a and S3 at the advisory line) |
| Chained PRs recommended | Yes |
| Suggested split | S1 -> S2a -> S2b -> S3 -> S4 -> S5 (6 PRs, merged in this order) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Per-slice forecast

| Slice | Title | Forecast (authored) | Depends on | Over ~400? |
|-------|-------|---------------------|------------|------------|
| S1 | Selection, group preview and version (application) | ~380 | none | No |
| S2a | Group commit: steps, one lock, one commit, row resolution | ~410 | S1 | At the line (accepted) |
| S2b | Unmarked rulings and the interrupted group | ~330 | S2a | No |
| S3 | Offer `merge_group`: listing, dispatch, `openkos.api` facade | ~400 | S2b | At the line (accepted) |
| S4 | Local HTTP API: selection body, error row, OpenAPI | ~280 | S3 | No |
| S5 | Decision wording, CHANGELOG, spec merge, ADR-0063 Accepted, archive | ~250 | S4 | No |

S2a and S3 sit at the line because their matrices are the point of the slice: a
group commit without its lock, staleness and byte-parity proofs, or an offered
action without its list, dispatch and additivity proofs, would ship without its
evidence. Neither needs a decision before apply.

**Serialization.** S1, S2a, S3 and S4 regenerate the committed snapshots
(`python -m tests.unit.api.surface --write`,
`python -m tests.unit.localapi.openapi_snapshot --write`). Each slice branches
from `main` after the previous one is squash-merged and regenerates; snapshot JSON
is never hand-merged.

### Suggested Work Units

| Unit | Goal | Focused test command | Rollback boundary |
|------|------|----------------------|-------------------|
| S1 | Validate a selection, preview the steps, bind the selection in the version | `uv run pytest tests/unit/application/test_curate_decisions_group.py tests/unit/api/test_api_surface.py tests/unit/localapi/test_errors.py` | Nothing offers the action; reverting removes unreachable code plus public names (see proposal rollback) |
| S2a | Commit a group under one section and one commit; resolve its row | `uv run pytest tests/unit/application/test_curate_decisions_group_commit.py tests/unit/application/test_queue_resolution.py` | Unreachable until S3 |
| S2b | Rulings for unmarked members; `MergeGroupInterrupted` | `uv run pytest tests/unit/application/test_curate_decisions_group_commit.py` | Unreachable until S3 |
| S3 | List, dispatch and expose through `openkos.api` | `uv run pytest tests/unit/application/test_curate_decisions.py tests/unit/api` | One-line withdrawal in `_identity_actions` |
| S4 | HTTP selection body, interrupted row, round trip, OpenAPI | `uv run pytest tests/unit/localapi` | Reverts the request field and one row |
| S5 | Docs, spec merge, ADR acceptance, archive | `uv run pytest tests/unit/test_adr_index.py` | Docs only |

## Conventions For Every Slice

- **Strict TDD.** Write the slice's RED tests first and run them to see them fail
  for the stated reason, then GREEN, then REFACTOR. A test that passes on first
  run is mutated on the line it guards before it is trusted; purge `__pycache__`
  before re-running a same-size mutation.
- **Gates, run directly (never piped):** `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy .`, `uv run pytest --cov` (90%
  branch floor). `uv run pre-commit run --all-files` is not a substitute for the
  three linters.
- **Snapshots.** When a re-exported type changes, regenerate both snapshots in the
  same commit and confirm `tests/unit/api/test_api_surface.py` classifies every
  difference additive with `API_VERSION` 1.
- **Layering.** `application/curate_decisions.py` imports nothing from `typer`,
  `rich`, `openkos.cli` or `openkos.vcs`; `localapi` imports only `openkos.api`.
- **Commit.** One Conventional Commit per slice (squash-merged), message as given,
  no AI attribution. Branch `feat/identity-groups-<slice>` off `main`.

## S1: Selection, group preview and version (application)

Covers C3 (selection in the token), C4, the preview half of C5 and C7. D1, D2
(application side), D3.

- [x] 1.1 RED `tests/unit/application/test_curate_decisions_group.py`:
  - [x] 1.1.1 `validate_selection` returns `NotApplied(MERGE_GROUP, "invalid_selection")`
    for: no selection; survivor not marked; a member outside the group; one
    marked member; and returns the canonical selection (deduplicated, ascending)
    for a valid one (C4 scenarios).
  - [x] 1.1.2 `preview_merge_group` over a SAME group of three, all marked,
    survivor B: two steps in ascending absorbed order; no step names an unmarked
    member; nothing on disk changes (snapshot of every file's bytes).
  - [x] 1.1.3 A verdict other than SAME is `NotApplied("not_mergeable")`; a
    member that does not resolve is `NotApplied("member_missing")`.
  - [x] 1.1.4 Type outcomes over A (Person), B (Organization), C (Place), all
    marked, survivor A with no `type_alternative`: B `recorded`, C `discarded`;
    survivor that already records one: both `discarded`; same types:
    `same_type`; an unreadable type: `unknown` (C5, C7).
  - [x] 1.1.5 The preview's cross-type concern is computed over the marked
    members only (an unmarked member of another type does not raise it).
  - [x] 1.1.6 `version_of` for a group preview: `selection` is set; reordering or
    duplicating marked members gives the same `content_digest` and `token()`;
    editing an unmarked member changes `content_digest`; a different selection
    changes it (C3, C8 content half).
  - [x] 1.1.7 `DecisionVersion.token()` for a version with `selection=None` equals
    the value computed by the current formula (pin a literal hex digest of a fixed
    version before the change).
- [x] 1.2 RED `tests/unit/localapi/test_errors.py`: the pinned `NotApplied`
  reason set gains `invalid_selection`; it maps to 422 `invalid_selection`, not
  retryable, fixed message; the spec-code set gains `invalid_selection`.
- [x] 1.3 GREEN `application/curate_decisions.py`: `Action.MERGE_GROUP`;
  `NotApplied` reason `invalid_selection`; `GroupSelection`;
  `DecisionVersion.selection` (defaulted, last field) and the `token()` branch;
  `validate_selection`; `MergeGroupDecisionPreview` (survivor, absorbed ids,
  kept-distinct ids, per-step `PreparedMerge` and type outcome, survivor type,
  cross-source and cross-type over the marked members, group, selection,
  `as_proposed`); `preview_merge_group` (each step through
  `lifecycle.prepare_one_merge(..., ordered_pair=(survivor, absorbed))`; the type
  simulation reads frontmatter through `okf`, never `yaml`); `version_of` branch
  (selection lines, every step's `_merge_content_lines`, `keep` lines).
- [x] 1.4 GREEN `api/__init__.py`: export `GroupSelection` (the leak guard rejects
  `DecisionVersion` naming an unexported type).
- [x] 1.5 GREEN `localapi/_errors.py`: the `invalid_selection` row and constant.
- [x] 1.6 Regenerate both snapshots; confirm the surface diff is additive
  (`Action` member, `NotApplied.reason` Literal widened, `DecisionVersion` field
  with default, `GroupSelection` name added).
- [x] 1.7 Gates. Commit: `feat(api): preview an identity group merge against a chosen survivor`

## S2a: Group commit: steps, one lock, one commit, row resolution

Covers C5 (commit half), C8 (except interruption), W1. D3, D4, D5, D7.

- [x] 2.1 RED `tests/unit/application/test_curate_decisions_group_commit.py`:
  - [x] 2.1.1 Commit a group of three, all marked, survivor B: A and C are gone,
    B holds two ledger entries in ascending absorbed order, `Applied.merged` is
    `(("B","A"), ("B","C"))`, `Applied.kept_distinct` is empty.
  - [x] 2.1.2 Byte parity: `unmerge` the second step then the first (through the
    unmerge service) restores every bundle file to its pre-group bytes (C5).
  - [x] 2.1.3 Exactly one `autocommit` port call, holding both steps' paths and
    the ledger sidecar; message
    `openkos: merge group into B (A, C)` (D5). `committed_confidential` is true
    when a marked member is confidential.
  - [x] 2.1.4 Staleness, each writing nothing: an edit to an unmarked member
    before the section is `Stale("content_changed")`; an altered selection is
    `Stale("content_changed")`; a resolved row is `Stale("row_resolved")`; a
    superseded row `row_superseded`; changed inputs `row_inputs_changed`; a
    replayed token after a commit `row_resolved` (C3, C5, C8).
  - [x] 2.1.5 Inside the section: an unmarked member deleted is
    `NotApplied("member_missing")` and nothing is written; a first-step target
    edited after the token check raises `DriftDetected` and nothing is written
    (C8).
  - [x] 2.1.6 The lock is entered once: a counting `commit_section` port sees
    one entry for a group of four; with the real `lock_wait.locked_commit_section`
    held by another process and `wait_seconds=0`, `WorkspaceBusyError` is raised
    and no file changed (C8).
  - [x] 2.1.7 Later steps are prepared against the earlier steps' bytes: the
    second step's plan sees the survivor as written by the first (assert the
    written `type_alternative` equals the previewed outcome from 1.1.4).
- [x] 2.2 RED `tests/unit/application/test_queue_resolution.py`: with a
  populated queue, a group of three all marked resolves the group row `applied`
  as proposed; marking three of four resolves it `applied` as modified; neither is
  `stale`; a relation row naming an absorbed member is `stale` (W1). A pairwise
  `merge_core` with no `group_row` behaves as before (existing tests unchanged).
- [x] 2.3 GREEN `application/queue_resolution.py`: `GroupRow`;
  `resolve_merged(..., group_row=None)` resolves the group key before
  `_retire_naming`.
- [x] 2.4 GREEN `application/lifecycle.py`: `merge_core(..., group_row=None)`
  forwards it; the registry test still passes unchanged.
- [x] 2.5 GREEN `application/merge_service.py`: `merge_group_commit_message`.
- [x] 2.6 GREEN `application/curate_decisions.py`: `Applied.merged` and
  `Applied.kept_distinct` (defaulted); `commit_merge_group_decision` in D4's
  order with a mandatory `expected`.
- [x] 2.7 Measure the lock hold for a group of four on a 300-concept generated
  fixture (median of 5); record the number in the PR description and in
  `verify` notes. No assertion on time in the suite.
- [x] 2.8 Regenerate both snapshots (`Applied` fields); confirm additive.
- [x] 2.9 Gates. Commit: `feat(api): commit an identity group as pairwise merges under one lock`

## S2b: Unmarked rulings and the interrupted group

Covers C6 and C8's interruption. D6, D8.

- [x] 3.1 RED (same test module as S2a):
  - [x] 3.1.1 Group of A, B, C, D marking A and B, survivor A: rulings over
    {A, C}, {A, D} and {A, C, D} exist and are `declined`; C and D are
    byte-identical; `Applied.kept_distinct` lists the three sets; open identity
    rows over exactly those sets are `declined` (C6).
  - [x] 3.1.2 One unmarked member: exactly one ruling, over {survivor, it}.
  - [x] 3.1.3 No ruling over two unmarked members alone ({C, D} is not recorded).
  - [x] 3.1.4 All marked: no ruling and no ruling path in the commit.
  - [x] 3.1.5 A `record_identity_ruling` port that raises `OSError` for one set:
    the merges are written and committed, `on_warning` is called once,
    `Applied.kept_distinct` omits that set (fail-open).
  - [x] 3.1.6 Interrupted: `merge_core` patched (through the module attribute)
    to raise `OSError` on its second call. `MergeGroupInterrupted` is raised with
    `completed == (("A","B"),)`, `failed_absorbed_id == "C"`, `commit_sha` from
    the port; one `autocommit` call holds only step 1's paths; C is unchanged; no
    ruling is written. Mutate the "commit what landed" call away and see the test
    fail.
  - [x] 3.1.7 The second step's prepare fails (C deleted after the section's
    presence check, simulated by patching `prepare_one_merge`): the same
    `MergeGroupInterrupted`.
  - [x] 3.1.8 A first-step write failure raises plain `WriteFailed`, not the
    subclass; `isinstance(MergeGroupInterrupted(...), WriteFailed)` and
    `OpenKosError` hold.
- [x] 3.2 GREEN `application/write_gate.py`: `MergeGroupInterrupted(WriteFailed)`
  with `completed`, `commit_sha`, `failed_absorbed_id`, `operation="merge_group"`.
- [x] 3.3 GREEN `commit_merge_group_decision`: the rulings after the last step
  (D8 sets, ascending, through `ports.record_identity_ruling`); the interruption
  path (D6).
- [x] 3.4 Gates (no public surface change: the exception is exported in S3).
  Commit: `feat(api): keep unmarked group members distinct and report an interrupted group`

## S3: Offer `merge_group`: listing, dispatch, `openkos.api` facade

Covers C1, C2, C7 (bulk), P1, and the dispatch half of C3. D1, D2, D9, D10.

- [x] 4.1 RED `tests/unit/application/test_curate_decisions.py` (list and
  dispatch):
  - [x] 4.1.1 Judged SAME group of three: actions `(MERGE_GROUP, KEEP_DISTINCT, SKIP)`,
    withheld `MERGE` with `more_than_two_members`. Judged SAME cross-type pair:
    the same with `cross_type`. Judged SAME same-type pair: unchanged, no
    `MERGE_GROUP` anywhere (C1, C2).
  - [x] 4.1.2 Pending, DIFFERENT and UNCERTAIN groups of three, and a pending
    cross-type pair: `MERGE` and `MERGE_GROUP` both withheld with the evaluation
    reason; a pending same-type pair withholds only `MERGE` (C2).
  - [x] 4.1.3 `preview_decision` and `apply_decision` with `MERGE_GROUP`:
    withheld item is `NotApplied("not_mergeable")`; same-type pair
    `NotApplied("not_offered")`; missing selection `invalid_selection`; a
    selection with `MERGE` is `invalid_selection`; a valid one previews and
    applies end to end and the row is `applied` (C2, C3, C4).
  - [x] 4.1.4 `BULK_ELIGIBILITY["identity"]` is still `NEVER` and
    `item_consent("identity", accepted=True, ...)` is `ask` (C7).
  - [x] 4.1.5 End to end with judging: a pending group of three judged SAME
    through `api.run_judging` with a recording chat factory becomes
    `MERGE_GROUP`-offered with the same `decision_key` (D10, Finding 7).
- [x] 4.2 RED `tests/unit/api/test_decisions_apply.py` and
  `tests/unit/api/test_list_decisions.py`: a merge-group preview's plan is a
  `MergePlan` with `kind == "merge"`, `survivor_criterion == "chosen"`, two
  `MergeStep`s, `kept_distinct_ids`, `survivor_type`; a pairwise merge plan
  lists its one step equal to `survivor_id`/`absorbed_id`; `except WriteFailed`
  catches an interrupted group through `api.apply_decision` (P1).
- [x] 4.3 RED `tests/unit/api/test_api_surface.py`: classification against the
  merge base is additive only (`Action` member, `MergeStep`,
  `MergeGroupInterrupted`, `MergePlan` fields, `preview_decision` keyword) and
  `API_VERSION == 1`; `DecisionPreview.plan` and `ApplyOutcome` are unchanged.
- [x] 4.4 GREEN `application/curate_decisions.py`: `_identity_actions` offers or
  withholds `MERGE_GROUP` (shape: more than two members or a cross-type
  concern); `_refusal` maps a withheld `MERGE_GROUP` to `not_mergeable`;
  `preview_decision`/`apply_decision` take and validate the selection and
  dispatch to `preview_merge_group`/`commit_merge_group_decision`.
- [x] 4.5 GREEN `api/_decisions.py`: `MergeStep`; `MergePlan` fields after
  `kind`; `_plan_of` fills `steps` for both previews; `preview_decision(...,
  selection=None)`. `api/__init__.py`: export `MergeStep`, `MergeGroupInterrupted`.
- [x] 4.6 Regenerate both snapshots; confirm additive.
- [x] 4.7 Gates. Commit: `feat(api): offer merge_group on identity groups through openkos.api`

## S4: Local HTTP API: selection body, error row, OpenAPI

Covers H1, H2, H3. D12.

- [x] 5.1 RED `tests/unit/localapi/test_decisions_write.py`:
  - [x] 5.1.1 Preview `merge_group` with a selection, then apply the echoed
    version: 200, `applied.merged` lists both pairs (H3).
  - [x] 5.1.2 An echoed version with one marked member removed from
    `selection.member_ids`: 409 `stale`, nothing written (H3).
  - [x] 5.1.3 `selection.member_ids` as a string: 422 `invalid_request`, the
    API function not called; an unknown top-level key: 422; a well-formed invalid
    selection: 422 `invalid_selection` (H2, H3).
  - [x] 5.1.4 A preview without `selection` behaves as today for every other
    action (existing tests unchanged).
- [x] 5.2 RED `tests/unit/localapi/test_errors.py`: `MergeGroupInterrupted` maps
  to 500 `merge_group_interrupted`, not `write_failed`, not retryable, fixed
  message containing no id; the spec-code set gains it (H2).
- [x] 5.3 RED round trip (`tests/unit/localapi/test_reads.py` or the write test
  module): a populated merge-group plan over a cross-type group of three with one
  unmarked member parses back equal to the Python preview; its `kind` is `merge`
  (H1).
- [x] 5.4 RED `tests/unit/localapi/test_openapi_snapshot.py`: `GroupSelection`
  appears once, with no `-Input`/`-Output` variant (H3).
- [x] 5.5 GREEN `localapi/_models.py`: `PreviewRequest` onto `_EchoRequest`,
  `selection: api.GroupSelection | None = None`. `_routes.py`: pass
  `selection=body.selection`. `_errors.py`: the `merge_group_interrupted` row
  before `WriteFailed`.
- [x] 5.6 Regenerate `tests/unit/localapi/openapi.json`.
- [x] 5.7 Gates. Commit: `feat(api): carry the group selection over the local HTTP API`

## S5: Decision wording, CHANGELOG, spec merge, ADR-0063 Accepted, archive

- [x] 6.1 `docs/decision-wording.md`, Identity section:
  - [x] 6.1.1 Remove the "Not combinable in the app" paragraph and its "These
    can't be combined here yet." line; the actions line names `merge_group`
    beside `merge`.
  - [x] 6.1.2 Add **"Which of these are the same?"** (more than two pages,
    `merge_group`) **(H)**: the pages side by side, each with a mark; "Which one
    should the combined page keep its name from?" over the marked pages. Options:
    **Combine the ones I marked** ("We'll make one page from the {n} pages you
    marked. It keeps the name '{survivor title}'. The pages you didn't mark stay
    as they are, and we won't suggest combining them with '{survivor title}'
    again."), **Keep all separate**, **Decide later**. The combine button stays
    disabled with the line "Mark at least two pages" until two are marked **(H)**.
  - [x] 6.1.3 Add **"What is {title}?"** (different kinds, `merge_group`) **(H)**:
    one option per kind among the pages, worded with the type nouns ("It's a
    person", "It's an organization"), plus **They're different things** (keep
    separate) and **Decide later**. What it does: "We'll keep one page, as a
    {person}, named '{survivor title}', and remember it was also described as
    {an organization}." When the plan says the type is discarded: "It was already
    noted as possibly {a place}, so we won't note {an organization}." When two
    pages share the chosen kind, ask which one keeps its name **(H)**. The
    "different files" line and the cross-type warning line are shown as for a
    pair.
  - [x] 6.1.4 Undo: a group is undone step by step with **Split again**, newest
    first, using the existing "the later combines on this page are split first"
    wording; note that the app needs an API unmerge for it.
  - [x] 6.1.5 Busy/refusal table gains `invalid_selection` ("Something about your
    choice didn't match these pages. Please choose again." → reload) and
    `merge_group_interrupted` ("Only some of these were combined. Nothing else
    changed, and each combine can be split again." → reload) **(H)**.
  - [x] 6.1.6 "Open questions": remove the "Groups the app cannot combine"
    bullet.
- [x] 6.2 `CHANGELOG.md` `[Unreleased] / Added`: one entry naming `merge_group`,
  the selection in the version, the additive surface (`API_VERSION` unchanged)
  and the two HTTP codes, with `(#1430)`.
- [x] 6.3 Spec merge: apply each delta to `openspec/specs/{curate-decisions,stable-python-api,local-http-api,pending-work}/spec.md`.
  Name-match every MODIFIED heading against the living spec before replacing
  (`grep -n "^### Requirement: <heading>$"` must hit exactly once); append every
  ADDED requirement; keep the "(Previously: ...)" lines as written, as the living
  specs already do.
  Diff the merged specs against the shipped code, not against the task list.
- [x] 6.4 ADR-0063: flip `status` and the body **Status:** line to Accepted, and
  its index row; nothing else in the ADR changes. Run
  `uv run pytest tests/unit/test_adr_index.py`.
- [x] 6.5 Archive: move `openspec/changes/identity-groups-decision/` to
  `openspec/changes/archive/2026-MM-DD-identity-groups-decision/` (date of the
  merge) with `git mv`, add `archive-report.md` (slices, PRs, measured lock hold,
  deviations), and confirm the original folder is gone and every artifact moved
  (diff against a sibling archive's file list).
- [x] 6.6 Gates. Commits: `docs(docs): word group and kind choices in the decision brief`
  and `docs(sdd): archive identity-groups-decision` (one PR, `Closes #1430`).

## Follow-ups (not in this change)

- A `curate` Identity stage group walk reusing `commit_merge_group_decision`
  (design D11). Open an issue once S5 lands.
- An `openkos.api` unmerge operation, needed for the app's "Split again" on any
  combine, pairwise or group.
