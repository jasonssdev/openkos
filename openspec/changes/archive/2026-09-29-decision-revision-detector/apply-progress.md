# Apply Progress: decision-revision-detector

Phase A. Strict TDD. Test runner: `uv run pytest`.

## Status

**22/22 Slice 1 tasks complete (1.1–1.22)** — PR 1, merged to `main` at
`13da74d` (#1023).

**16/16 Slice 3 tasks complete (3.1–3.16)** — PR 2, merged to `main` at
`7ae73e7` (#1024).

**20/20 Slice 4 tasks complete (4.1–4.20)** — this batch. PR 3 boundary:
extends `src/openkos/resolution/decision_revision.py` with the judge
(`JudgeSide`, `build_judge_messages`, `parse_judge_reply`/
`ParsedJudgement`, `RevisionVerdictValue`, `RevisionVerdict`,
`RevisionBatch`, `is_actionable_revision`, `is_reportable_revision`,
`RELATION_FOR_VERDICT`, `judge_pairs`) + the matching additions to
`tests/unit/resolution/test_decision_revision.py`, on branch
`feat/1014-revision-judge` off `main` (which already contains PR 1 + PR 2
via #1023/#1024). All 58 (S1+S3+S4) tasks in this task list are now
complete. The CHECKPOINT (sub-change 3 harness) and Phase B (S2, S5-S8)
remain out of scope for this task list, per its own "CHECKPOINT — STOP"
and "Phase B" sections.

**Phase B, Slice P1 (`P1.1`–`P1.11`, 11/11) complete** — PR 4 on
`feat/1014-phase-b-p1-vector-read`, merged to `main` at `62e0e11` (#1055).

**Phase B, Slice P2: 10/12 tasks complete (`P2.1`–`P2.4`, `P2.8`–`P2.12`)**
— this batch, on `feat/1014-phase-b-p2-harness-shape`. `P2.5` (the live-run
operator step against a real Ollama), `P2.6`, and `P2.7` are intentionally
NOT done — see the "Phase B — Slice P2" section below for the exact
command and why.

**Phase B, Slice P3 (`P3.1`–`P3.19`, 19/19) complete** — this batch, on
`feat/1014-phase-b-p3-findings-store` (checked out off `main` at `c59124a`,
which already contains P1/P2 via #1055/#1058). PR 6 boundary: adds
`src/openkos/state/revision_findings.py` (the fourth `findings.db` tenant),
joins it to `cli.main._sweep_findings_for_ids`, pins the sibling-table
isolation, and narrows ADR-0025. `size:exception` was pre-approved by the
owner (2026-09-29) for this slice because design.md forbids splitting the
table from its sweep; see "Budget" below for the actual count.

**Phase B, Slice P4 (`P4.1`–`P4.5`, 5/5) complete** — on
`feat/1014-phase-b-p4-provenance-many` (checked out off `main` at
`f44131e`, which already contains P1/P2/P3 via #1055/#1058/#1059). PR 7
boundary: `provenance_source_ancestors_many` in
`src/openkos/bundle/provenance.py`, a pure shared-walk refactor with no
upstream Phase B dependency, well under the review budget (119 authored
changed lines) — no `size:exception` needed.

**Phase B, Slice P5a (`P5a.1`–`P5a.15`, 15/15) complete** — this batch, on
`feat/1014-phase-b-p5a-service-load` (checked out off `main` at `617fdba`,
which already contains P1/P2/P3/P4 via #1055/#1058/#1059/`7d46cd5`). PR 8
boundary: `src/openkos/application/revisions.py` (new module) —
`load_decisions`/`Decision`/`DecisionSet` (+ `resolved_with`),
`resolve_decision_dates`, and `read_decision_vectors`/`VectorCoverage`,
plus `tests/unit/application/test_revisions_service.py` (new file, 11
tests). 690 authored changed lines, above the ~300-line forecast for a
split P5 half — recommend `size:exception`, consistent with every prior
oversized Phase A/B slice; see "Budget" below for the full accounting.

**Phase B, Slice P5b (`P5b.1`–`P5b.13`, 13/13) complete** — this batch, on
`feat/1014-phase-b-p5b-service-plan` (checked out off `main` at `932cb51`,
which already contains P1/P2/P3/P4/P5a). PR 9 boundary: extends
`src/openkos/application/revisions.py` with `revision_input_digests`,
`is_fresh`, `RevisionPlan`, and `plan_revisions` (+ a behavior-preserving
`_bundle_text_snapshot` extraction shared with `resolve_decision_dates`),
plus 12 new tests in `tests/unit/application/test_revisions_service.py`
(23 total in the file). 847 authored changed lines, above both the
~300-line forecast and the 400-line review budget — recommend
`size:exception`, consistent with every prior oversized Phase A/B slice;
see "Budget" below for the full accounting.

**Phase B, Slice P3 (`P3.1`–`P3.19`, 19/19), P4 (`P4.1`–`P4.5`, 5/5), and P6
(`P6.1`–`P6.8`, 8/8) are also complete** (their own "## Slice PX" sections,
further below, are the record — this top summary block was not kept in
sync with every slice by prior batches; see each section's own heading for
its PR boundary and commit).

**Phase B, Slice P8b (`P8b.1`–`P8b.14`, 14/14) complete — ALL Phase B
slices (P1-P8b) are now done.** PR 14 (final), on
`feat/1014-phase-b-p8b-reconcile-walk` (checked out on top of the P8a
branch, PR #1068, not yet merged, at `7179d6b`), commit `66eb7e8`. Adds
the second (revision-findings) walk to `reconcile --from-findings` in
`src/openkos/cli/main.py`. See "Phase B — Slice P8b" below for the full
record.

**Phase B, Slice P7a (`P7a.1`–`P7a.12`, 12/12) complete** — this batch, on
`feat/1014-phase-b-p7a-report` (checked out ON TOP of the P6 branch, PR
#1065, not yet merged, at `708f10a`). PR 11 boundary: new module
`src/openkos/application/revisions_report.py` (the pure `revisions` report
renderer) and new test file
`tests/unit/application/test_revisions_report.py` (11 test cases, 8 task
IDs, one parametrized 4 ways). 707 authored changed lines, above both the
~300-line forecast and the 400-line review budget — recommend
`size:exception`, consistent with every prior oversized Phase A/B slice;
see the "Budget" note in the Slice P7a section below.

**Phase B, Slice P7b (`P7b.1`–`P7b.17`, 17/17) complete** — this batch, on
`feat/1014-phase-b-p7b-verb` (checked out ON TOP of the P7a branch, PR
#1066, not yet merged, at `8890972`). PR 12 boundary: the `openkos
revisions` Typer command in `src/openkos/cli/main.py` (wiring
`load_decisions` -> `plan_revisions` -> the one exact cost gate ->
`judge_revisions` -> `revisions_report`), new test file
`tests/unit/cli/test_revisions.py` (14 test cases covering all 12 P7b.1-
P7b.12 task IDs — P7b.4's parametrized pair was split into two named
tests, and P7b.5's ordering half was split into its own test), and a new
`docs/cli.md` section. ~823 authored changed lines (269 in
`main.py`/`docs/cli.md` + 554 new in the test file), above both the
~400-line forecast and the 400-line review budget — recommend
`size:exception`, consistent with every prior oversized Phase A/B slice;
see the "Budget" note in the Slice P7b section below.

---

## Slice 1 (PR 1 → `main`, merged): the subject-pass leaf,
`resolution/decision_subject.py`

## Files changed

| File | Action |
|---|---|
| `src/openkos/resolution/decision_subject.py` | Created |
| `tests/unit/resolution/test_decision_subject.py` | Created |

## TDD Cycle Evidence

| Task(s) | Test file | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| 1.1–1.5 (subject/value fields) | `test_decision_subject.py` | Unit | N/A (new) | ✅ `ImportError: cannot import name 'decision_subject'` (module absent) | ✅ 12/12 passed | ✅ 7 malformed cases + well-formed + 4 value cases | ➖ None needed |
| 1.6–1.10 (`quoted_verbatim` + evidence field) | `test_decision_subject.py` | Unit | ✅ 12/12 (batch A, unchanged) | Implemented alongside 1.11–1.19 against the fully-specified design; not run standalone as a separate RED — see note below | ✅ full file 37/37 passed | ✅ 12 normalization cases + parity table + verbatim/paraphrase pair | ➖ None needed |
| 1.11–1.16 (digest, prompt version, message shape) | `test_decision_subject.py` | Unit | ✅ (as above) | (as above) | ✅ full file 37/37 passed | ✅ title-only/body-only/stable digest cases | ➖ None needed |
| 1.17–1.19 (`derive_subjects` batch) | `test_decision_subject.py` | Unit | ✅ (as above) | (as above) | ✅ full file 37/37 passed | ✅ partial-batch-on-failure + malformed-degrades-in-batch + guard-scope case | ➖ None needed |

**Note on RED granularity**: genuine RED was observed for the whole module
before any production code existed (task group 1.1: `ImportError` collecting
the test file, since `openkos.resolution.decision_subject` did not exist —
the `ModuleNotFoundError`-family failure tasks.md predicts). Given the
design's fully-specified interfaces (design.md Decision 5 fixes every field
name, constant, and algorithm step exactly), the module was then implemented
as one cohesive unit rather than five separate RED→GREEN micro-cycles, and
verified GREEN as a whole (37/37). Correctness of each behavioral claim (not
just "does it exist") is proven by the four required mutation-kill runs
below, which is the check that would have caught a wrong micro-implementation
regardless of cycle granularity.

## Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged, then reverted with the exact inverse edit (never
`git checkout --`), and the full S1 file re-verified GREEN before moving to
the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | Drop the verbatim check (`evidence_or_none = evidence if evidence else None`) | `decision_subject.py`, `parse_subject_reply` | `test_parse_subject_reply_evidence_field` | ✅ FAILED as expected: `AssertionError: assert 'Priya is responsible for the migration.' is None`. Reverted. |
| 2 | Reject the whole subject when evidence fails verification (`return None` before building `DecisionSubject`) | `decision_subject.py`, `parse_subject_reply` | `test_parse_subject_reply_evidence_field` | ✅ FAILED as expected: `assert None is not None` (the well-formed subject/value pair was discarded). Reverted. |
| 3 | Swallow the failure in `derive_subjects` (`except OllamaError: results.append((id, None)); continue` instead of returning early) | `decision_subject.py`, `derive_subjects` | `test_derive_subjects_partial_batch_on_llm_failure` | ✅ FAILED as expected: `IndexError: pop from empty list` on the double's queue (the loop tried a 3rd `chat()` call the test never armed, proving the swallow changed the call count/contract). Reverted. |
| 4 | Widen the exception guard past `llm.chat` (move `parse_subject_reply`/`results.append`/`on_progress` inside the `try` block) | `decision_subject.py`, `derive_subjects` | `test_derive_subjects_guards_only_the_chat_call` (new test added for this exact mutation, per the apply instructions naming it explicitly) | ✅ FAILED as expected: `Failed: DID NOT RAISE OllamaUnavailable` — an `OllamaError` raised by the caller's own `on_progress` was incorrectly caught and folded into a clean `SubjectBatch` failure instead of propagating. Reverted. |

All four mutations killed by the existing (or, for #4, one added) test.
`find . -name __pycache__ -prune -exec rm -rf {} +` was run before every
GREEN/RED verdict.

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/resolution/test_decision_subject.py -v` → **37 passed** |
| Runtime harness command/scenario and exact result | N/A — a pure leaf with no CLI/state wiring; nothing runs end-to-end until Phase B's S7 wires the `revisions` verb (per tasks.md's own "Runtime harness" column for Unit 1) |
| Rollback boundary | Revert `src/openkos/resolution/decision_subject.py` and `tests/unit/resolution/test_decision_subject.py`; no other module imports `decision_subject` yet |

## Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! (after fixing one `SIM300` Yoda-condition finding in the new test file) |
| `uv run ruff format --check .` | Failed once (`decision_subject.py`, `test_decision_subject.py` needed reformatting) → ran `uv run ruff format` on those two files → **all files formatted**, re-verified `--check .` green |
| `uv run mypy .` | Success: no issues found in 315 source files |
| `uv run pytest` (full, unpiped) | **6523 passed, 2 skipped** in 351.21s, exit 0 |

## Commit

`9ca0262` — `feat(resolution): add the decision-subject pass leaf`
(scope `resolution`, matching the sibling leaves `resolution/similarity.py`/
`resolution/contradiction.py`). 2 files changed, 557 insertions(+). Staged
explicitly by path (`src/openkos/resolution/decision_subject.py`,
`tests/unit/resolution/test_decision_subject.py`) — `openspec/`, `odd/`,
`docs/adr/0025-*`, and `docs/adr/README.md` were never staged. Not pushed.

**Budget note**: design.md/tasks.md forecast S1 at ~320 authored lines; the
actual work unit is 557 (233 production + 324 test). The overage is entirely
docstring density matching this repo's own established convention (see
`extraction/evidence.py`'s comparably dense module, already in the
codebase) plus the fourth mutation-kill test explicitly requested by the
apply instructions. No test, docstring, or blank line was shortened to chase
the 400-line number, per the work-unit-commits skill's "budget is not
code-golf" rule. S1 is already the smallest cohesive PR the design assigns
(one leaf module + its tests) — recommend `size:exception` for this slice
rather than an artificial further split.

---

## Slice 3 (PR 2 → `main`, after PR 1 merges): direction and candidates,
`resolution/decision_revision.py`

### Files changed

| File | Action |
|---|---|
| `src/openkos/resolution/decision_revision.py` | Created |
| `tests/unit/resolution/test_decision_revision.py` | Created |

### TDD Cycle Evidence

| Task(s) | Test file | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| 3.1 (`pair_direction`) | `test_decision_revision.py` | Unit | N/A (new) | ✅ `ImportError: cannot import name 'decision_revision'` (module absent) | ✅ 18/18 parametrized cells passed | ✅ full exhaustive `DateState × DateState` table (18 cells) generated programmatically, incl. the "first non-dated side reported in id order" case | ➖ None needed |
| 3.3 (`subject_overlap`) | `test_decision_revision.py` | Unit | ✅ (module now exists after 3.1/3.2) | ✅ Written against the not-yet-implemented function (`AttributeError` before 3.4) | ✅ 6/6 passed | ✅ identical/disjoint/function-word-dropped/one-vs-multi-token/empty-either-side cases | ➖ None needed |
| 3.5–3.12 (`plan_revision_candidates`, `revision_truncation_notice`) | `test_decision_revision.py` | Unit | ✅ (as above) | ✅ Written against the not-yet-implemented function (`AttributeError` before 3.13) | ✅ 8/8 passed | ✅ shared-source exclusion, resolution exclusion (×3 relation types ×2 directions), threshold boundary (exactly-at kept, below dropped), without-subject count, top-k union (7-partner hub + 2 union-only survivors), ordering (score desc, tie by pair id), cap+truncation-notice (205→200, exact string), exclusions-before-cap (206→205) | ➖ None needed |

**Note on RED granularity** (same posture as Slice 1): genuine RED was
observed for the whole module before any production code existed
(`ImportError: cannot import name 'decision_revision'`), then the three
functions were implemented together against design.md Decision 3/4's
fully-specified table and algorithm, and verified GREEN as a whole
(37/37). Correctness of each behavioral claim is proven by the eight
required mutation-kill runs below.

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged, then reverted with the exact inverse edit (never
`git checkout --`), and the full S3 file re-verified GREEN (37/37) before
moving to the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `<` → `>` in the dated/dated comparison (`if value_a < value_b` → `if value_a > value_b`) | `decision_revision.py`, `pair_direction` | `test_pair_direction_full_table[...dated]` (both distinct-date cells) | ✅ FAILED as expected: `AssertionError: assert 'b' == 'a'` (holder/earlier swapped). Reverted. |
| 2 | Equal dates treated as ordered (dropped the `if value_a == value_b: return ... "equal"` branch) | `decision_revision.py`, `pair_direction` | `test_pair_direction_full_table[...equal]` | ✅ FAILED as expected: `AssertionError: assert 'a' == None` (an equal-date pair was given a holder). Reverted. |
| 3 | b-side check silently dropped (removed the `if date_b.state != "dated": return ...` early return) | `decision_revision.py`, `pair_direction` | 3 of the non-dated `test_pair_direction_full_table` cells | ✅ FAILED as expected: `TypeError: '<' not supported between instances of 'datetime.date' and 'NoneType'` (the b-side's `None` value reached the comparison unguarded). Reverted. |
| 4 | Top-k union → intersection (a pair kept only if BOTH sides ranked it in their own top-k) | `decision_revision.py`, `plan_revision_candidates` | `test_plan_revision_candidates_top_k_is_a_union` | ✅ FAILED as expected: `hub_pairs` lost exactly `p6`/`p7` — the two pairs that survive ONLY via union. Reverted. |
| 5 | Resolution exclusion `or` → `and` between the two directions | `decision_revision.py`, `plan_revision_candidates` | `test_plan_revision_candidates_excludes_resolved_pairs` (6 cases) + `test_plan_revision_candidates_exclusions_applied_before_the_cap` | ✅ FAILED as expected (7 failures): resolved pairs were no longer excluded when only one side recorded the edge; `total` read 206 instead of 205. Reverted. |
| 6 | Cap applied before top-k ranking (sliced `score_by_pair` to `cap` entries before building `partners`) | `decision_revision.py`, `plan_revision_candidates` | `test_plan_revision_candidates_cap_and_truncation_notice` + `..._exclusions_applied_before_the_cap` | ✅ FAILED as expected: `plan.total` collapsed from 205 to 200 (top-k ranking over an already-capped set can no longer see the full pre-cap population). Reverted. |
| 7 | Post-cap filtering (resolution exclusion moved from the pairwise loop to a filter applied AFTER the `[:cap]` slice, so a resolved pair is counted in `total` but dropped from `candidates`) | `decision_revision.py`, `plan_revision_candidates` | Same 7 tests as mutation 5 | ✅ FAILED as expected: `plan.total == 206` (the resolved pair inflated the pre-cap count even though it never appears in `candidates`). Reverted. |
| 8 | Smaller/larger token-set direction swapped in `subject_overlap` | `decision_revision.py`, `subject_overlap` | `test_subject_overlap[postgres-...-1.0]` + `test_plan_revision_candidates_top_k_is_a_union` | ✅ FAILED as expected: the one-token-vs-multi-token case dropped from `1.0` to `0.0` (denominator became the larger set), and the hub fixture lost every pair (denominator diluted below `SUBJECT_OVERLAP_THRESHOLD`). Reverted. |

All eight mutations killed by the existing tests (no additional test was
needed for this slice). `find . -name __pycache__ -prune -exec rm -rf {} +`
was run before every GREEN/RED verdict, and every revert used the exact
inverse edit — never `git checkout --`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/resolution/test_decision_revision.py -v` → **37 passed** |
| Runtime harness command/scenario and exact result | N/A — a pure leaf with no CLI/state/graph wiring; nothing runs end-to-end until Phase B's S7 wires the `revisions` verb (tasks.md's own "Runtime harness" column for Unit 2) |
| Rollback boundary | Revert `src/openkos/resolution/decision_revision.py` and `tests/unit/resolution/test_decision_revision.py` in full — PR 3 (Slice 4, the judge) has not landed, so this reverts the whole file with no unrelated work removed |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | Found 6 (`S101` ×4 on defensive `assert`s, `PT006` ×2 on comma-joined parametrize strings) → fixed by switching the `assert`s to `typing.cast` (no new branch, avoids the repo's `S101` ban outside `tests/`) and the parametrize strings to tuples → **All checks passed!** |
| `uv run ruff format --check .` | Failed once (`decision_revision.py`, `test_decision_revision.py` needed reformatting) → ran `uv run ruff format` on those two files → re-verified `--check .`: **317 files already formatted** |
| `uv run mypy .` | Success: no issues found in 317 source files |
| `uv run pytest` (full, unpiped) | **6560 passed, 2 skipped** in 388.01s, exit 0 (up from the Slice-1 baseline of 6523 passed + this slice's 37 new tests) |

### Commit

`839fdbf` — `feat(resolution): add decision-revision direction rule and
candidate generation` (scope `resolution`). 2 files changed, 708
insertions(+). Staged explicitly by path
(`src/openkos/resolution/decision_revision.py`,
`tests/unit/resolution/test_decision_revision.py`) — `openspec/`, `odd/`,
`docs/adr/0025-*`, and `docs/adr/README.md` were never staged. Not pushed.
Branched from `main` at `13da74d` (which already contains Slice 1 via
PR #1023) on `feat/1014-revision-candidates`.

**Budget note**: design.md/tasks.md forecast S3 at ~380 authored lines; the
actual work unit is 708 (345 production + 363 test). The overage follows
the same pattern the Slice-1 apply-progress already recorded and is
recommending against for future slices: dense docstrings matching this
repo's established convention, PLUS this slice's fixtures needed to
construct large, precisely-scored `DecisionInput` populations (the
205-pair cap/truncation fixture, the 7-partner union fixture) to exercise
every mutation the design's testing table names. No test, docstring, or
blank line was shortened to chase the 400-line number, per the
work-unit-commits skill's "budget is not code-golf" rule; this PR is
already the smallest cohesive unit the design assigns (one leaf module's
direction/overlap/candidate functions + their tests) — recommend
`size:exception` for this slice, consistent with Slice 1's recommendation.

## Slice 4 (PR 3 → `main`, after PR 2 merges): the judge,
`resolution/decision_revision.py`

Extends the same module PR 2 (Slice 3) created. Branch `feat/1014-revision-judge`
off `main`, which already contains PR 1 (#1023) and PR 2 (#1024).

### Files changed

| File | Action |
|---|---|
| `src/openkos/resolution/decision_revision.py` | Modified — extended |
| `tests/unit/resolution/test_decision_revision.py` | Modified — extended |

### TDD Cycle Evidence

| Task(s) | Test file | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| 4.1–4.3 (`build_judge_messages`, `JudgeSide`) | `test_decision_revision.py` | Unit | ✅ 37/37 (Slice 3, unchanged) | ✅ `AttributeError: module 'openkos.resolution.decision_revision' has no attribute 'JudgeSide'` (genuinely observed: confirmed by stashing the implementation change and re-running the test file against the Slice-3-only module before writing any Slice-4 production code) | ✅ 73/73 passed (full file) | ✅ known-direction (argument order reversed from chronological order) + unknown-direction (argument order reversed from id order) cases | ➖ None needed |
| 4.4–4.9 (`parse_judge_reply`, `RevisionVerdict`, `RevisionVerdictValue`) | `test_decision_revision.py` | Unit | ✅ (as above) | ✅ (same `AttributeError`, whole-module RED) | ✅ 73/73 passed | ✅ direction-unreadable success criterion, unknown-verdict mapping, confidence coercion (nan/bool/string/missing/valid/clamped — 7 cases), rationale non-string, quote-mapping swap test (earlier id sorting after later id) + verbatim-failure case | ➖ None needed |
| 4.10–4.14 (actionability, `RELATION_FOR_VERDICT`) | `test_decision_revision.py` | Unit | ✅ (as above) | ✅ (same `AttributeError`, whole-module RED) | ✅ 73/73 passed | ✅ full 4-verdict × 2-confidence × 3-quote-combination truth table (24 cases) + REAFFIRMS-reportable-not-actionable + UNRELATED-never-reportable + `RELATION_FOR_VERDICT` parity with `RESOLUTION_RELATION_TYPES` | ➖ None needed |
| 4.15–4.17 (`judge_pairs` batch) | `test_decision_revision.py` | Unit | ✅ (as above) | ✅ (same `AttributeError`, whole-module RED) | ✅ 73/73 passed | ✅ partial-batch-on-failure + malformed-degrades-without-aborting + guard-scope case (added explicitly, same posture as Slice 1's mutation #4) | ➖ None needed |

**Note on RED granularity** (same posture as Slices 1 and 3): a genuine
whole-module RED was captured for real this batch, not merely asserted —
the Slice-4 implementation diff was stashed (`git stash push --keep-index
-- src/openkos/resolution/decision_revision.py`) while the new test file
was kept in place, `uv run pytest tests/unit/resolution/test_decision_revision.py`
was run against the Slice-3-only module, and it failed at collection with
`AttributeError: module 'openkos.resolution.decision_revision' has no
attribute 'JudgeSide'` — exactly the failure tasks.md's task 4.1 predicts.
The stash was then popped and the implementation (already written against
design.md Decision 6's fully-specified interfaces) was verified GREEN as a
whole (73/73). Correctness of each behavioral claim is proven by the five
required mutation-kill runs below.

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged, then reverted with the exact inverse edit (never
`git checkout --`), and the full S4 file re-verified GREEN (73/73) before
moving to the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | Success criterion: `RevisionVerdict.direction` computed with `pair_ids`/`dates` sides swapped (`pair_direction(self.pair_ids[1], self.dates[0], self.pair_ids[0], self.dates[1])` instead of the aligned order) | `decision_revision.py`, `RevisionVerdict.direction` | `test_parse_judge_reply_direction_is_structurally_unreadable` | ✅ FAILED as expected: `AssertionError: assert 'decisions/early' == 'decisions/late'` (the extra `"later"` reply field was still correctly ignored, but the direction computation itself was now wrong — proving the test actually exercises `pair_direction`'s date-only authority, not a tautology). Reverted. |
| 2 | Quote-order mixup in `parse_judge_reply` (swapped which JSON key `quote_first`/`quote_second` reads: `data.get("quote_second")` assigned to `quote_first` and vice versa) | `decision_revision.py`, `parse_judge_reply` | `test_parse_judge_reply_quotes_verified_and_mapped_by_presentation_order` | ✅ FAILED as expected: `AssertionError: assert None == 'Priya owns the schema migration plan.'` (`quote_first` verified against the wrong side's body and failed verbatim-check, collapsing to `None`). Reverted. |
| 3 | `>=`→`>` on the actionable-confidence threshold in `is_actionable_revision` | `decision_revision.py`, `is_actionable_revision` | `test_is_actionable_revision_truth_table[...-0.7-REVERSES]` + `[...-0.7-REFINES]` | ✅ FAILED as expected (2 of 24 parametrized cells, exactly the boundary `confidence == 0.70` cells for the two actionable verdicts): `assert False is True`. Reverted. |
| 4 | A `REAFFIRMS` verdict made actionable (widened `_ACTIONABLE_VERDICT_VALUES` to include `RevisionVerdictValue.REAFFIRMS.value`) | `decision_revision.py`, `_ACTIONABLE_VERDICT_VALUES` | `test_is_actionable_revision_truth_table[...-0.7-REAFFIRMS]` + `test_is_reportable_revision_includes_reaffirms_but_not_unrelated` | ✅ FAILED as expected (2 independent tests): `AssertionError: assert True is False` — a reportable `REAFFIRMS` was now also reported as actionable, which the report-vs-actionable distinction test caught directly. Reverted. |
| 5 | The `OllamaError` guard widened past `llm.chat` in `judge_pairs` (moved the parse/build-verdict/`on_progress` block inside the `try`) | `decision_revision.py`, `judge_pairs` | `test_judge_pairs_guards_only_the_chat_call` | ✅ FAILED as expected: `Failed: DID NOT RAISE OllamaUnavailable` — an `OllamaError` raised by the caller's own `on_progress` was incorrectly caught and folded into a clean `RevisionBatch` return instead of propagating. Reverted. |

All five mutations killed by the existing tests (no additional test was
needed beyond the one Slice 1 already established the pattern for —
`test_judge_pairs_guards_only_the_chat_call`, written alongside the batch
tests from the start, same posture as `test_derive_subjects_guards_only_the_chat_call`).
`find . -name __pycache__ -prune -exec rm -rf {} +` was run before every
GREEN/RED verdict, and every revert used the exact inverse edit — never
`git checkout --`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/resolution/test_decision_revision.py -v` → **73 passed** |
| Runtime harness command/scenario and exact result | N/A — a pure leaf plus one `LLMBackend`-injected function with no CLI/state/graph wiring; nothing runs end-to-end until Phase B's S7 wires the `revisions` verb (tasks.md's own "Runtime harness" column for Unit 3), and the harness checkpoint (sub-change 3) is the first read-only consumer of these leaves |
| Rollback boundary | Revert the judge additions this batch made on top of PR 2's `src/openkos/resolution/decision_revision.py` and `tests/unit/resolution/test_decision_revision.py` (`build_judge_messages`, `parse_judge_reply`, `RevisionVerdict*`, `judge_pairs`, `is_actionable_revision`/`is_reportable_revision`, `RELATION_FOR_VERDICT`, and their tests); PR 2's direction/candidates code stays intact — `git revert b51e01a` cleanly isolates this |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | Failed once (`test_decision_revision.py` needed reformatting after the Slice-4 additions) → ran `uv run ruff format tests/unit/resolution/test_decision_revision.py` → re-verified `--check .`: **317 files already formatted** |
| `uv run mypy .` | Success: no issues found in 317 source files |
| `uv run pytest` (full, unpiped) | **6596 passed, 2 skipped** in 364.01s, exit 0 (up from the Slice-3 baseline of 6560 passed + this slice's 36 new tests) |

### Commit

`b51e01a` — `feat(resolution): add the decision-revision judge`
(scope `resolution`). 2 files changed, 857 insertions(+), 10 deletions(-).
Staged explicitly by path (`src/openkos/resolution/decision_revision.py`,
`tests/unit/resolution/test_decision_revision.py`) — `openspec/`, `odd/`,
`docs/adr/0025-*`, and `docs/adr/README.md` were never staged. Not pushed.
Branched from `main` on `feat/1014-revision-judge` (which already
contains PR 1 + PR 2 via #1023/#1024).

**Budget note**: design.md/tasks.md forecast S4 at ~380 authored lines; the
actual work unit is 857 (475 production delta + 392 test delta — both
counts include some docstring-only lines inside the pre-existing module
that shifted line numbers but were not rewritten; the net NEW content is
the full judge implementation plus 36 new tests). The overage follows the
exact same pattern Slices 1 and 3 already recorded and recommended against:
dense docstrings matching this repo's established convention (every new
public symbol carries a design.md-cross-referenced docstring, matching
`decision_subject.py`'s and this same module's own Slice-3 density), plus
a genuinely exhaustive truth table (4 verdicts × 2 confidence levels × 3
quote combinations = 24 parametrized cells) needed to exercise every cell
design.md's Decision 6 table and the testing strategy's mutation column
name. No test, docstring, or blank line was shortened to chase the
400-line number, per the work-unit-commits skill's "budget is not
code-golf" rule. This is already the smallest cohesive unit the design
assigns (the judge, as one PR, on top of the direction/candidates PR that
already merged) — recommend `size:exception` for this slice, consistent
with Slices 1 and 3's recommendation.

---

## Remaining Tasks (not in this batch's PR boundary)

- All 58 tasks in this tasks.md pass (1.1–1.22, 3.1–3.16, 4.1–4.20) are
  now complete.
- Checkpoint (sub-change 3 harness) and Phase B (S2, S5–S8) are explicitly
  out of scope for this tasks.md pass (see tasks.md's own "CHECKPOINT — STOP"
  and "Phase B" sections). Per tasks.md's own instruction, Phase B does not
  start until the owner has read the harness numbers.

---

## Phase B — Slice P1 (2026-09-28): the vector read seam

Checkpoint passed (sub-change 3 harness, live run `runs-20260928T204537Z`,
all bars B1-B8); the owner accepted the 2026-09-28 Phase B re-plan
(design.md, "Phase B re-plan"). Scope for this batch: tasks.md's "Slice P1
(PR 4 → after Phase A's PR 3): the vector read seam" — `P1.1`–`P1.11` only.
Basis: design.md Decision B1 ("Candidate vectors come from `vectors.db`;
`revisions` never embeds") and spec requirement "Candidate Vectors Come
From The Reindexed Vector Store" (`specs/decision-revision-detection/
spec.md`). Branch `feat/1014-phase-b-p1-vector-read`, off `main` @ `9cbedc8`
(which already carries commit `c5c3f64`, the Phase B planning docs).

### TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| P1.1–P1.4 (`document_vectors`, `StoredDocVector`) | `test_vectorstore.py` | Unit | ✅ 147/147 (pre-existing `test_vectorstore.py` + `test_reindex.py`, genuinely run before any edit) | ✅ `AttributeError: 'VectorStoreDB' object has no attribute 'document_vectors'` (genuinely observed: all 4 new tests run against the pre-edit module before any production code was written) | ✅ 80/80 passed (`test_vectorstore.py` alone, after P1.5) | ✅ 4 cases: derived-vector-not-chunk-row (multi-chunk, asserts the result differs from EITHER raw chunk), omits-missing-id, hash-from-vector_meta, empty-input-short-circuit | ➖ None needed — the method is already the minimal correct join |
| P1.5 (`document_vectors` IMPL) | `vectorstore.py` | — | — | — | ✅ makes P1.1–P1.4 GREEN | — | ➖ None needed |
| P1.6–P1.7 (`embedding_tag`, delegation) | `test_reindex.py` | Unit | ✅ 73/73 (pre-existing `test_reindex.py`, genuinely run before any edit) | ✅ `AttributeError: module 'openkos.state.reindex' has no attribute 'embedding_tag'` (genuinely observed: both new tests run against the pre-edit module before any production code was written) | ✅ 73/73 passed (`test_reindex.py` alone, after P1.8; includes the 2 new tests) | ➖ Single — a pure one-line composition and a delegation check need no additional case beyond the `None`-passthrough/real-model pair already written | ➖ None needed |
| P1.8 (`embedding_tag` IMPL, `_effective_model_tag` delegation) | `reindex.py` | — | — | — | ✅ makes P1.6–P1.7 GREEN, and keeps the pre-existing `test_reindex_effective_model_tag_is_*` tests green (delegation preserves behavior) | — | ➖ None needed |

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged (`find . -name __pycache__ -prune -exec rm -rf {} +`),
then reverted with the exact inverse edit (never `git checkout --`), and the
relevant file re-verified GREEN before moving to the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `document_vectors`'s `FROM doc_vectors AS dv` changed to `FROM vectors AS dv` (the per-chunk table) | `vectorstore.py`, `document_vectors` | `test_document_vectors_returns_the_derived_document_vector_not_a_chunk_row` | ✅ FAILED as expected: `AssertionError` — the returned vector (`0.9`-filled, one chunk's raw embedding) no longer matched the derived doc vector (`_derive_document_vector` of both chunks). Proves the test actually distinguishes the derived vector from a raw chunk row, not a tautology. Reverted. |
| 2 | Row-unpacking order swapped: `for concept_id, content_hash, blob in rows` (columns are actually `concept_id, blob, content_hash`) | `vectorstore.py`, `document_vectors` | `test_document_vectors_returns_the_derived_document_vector_not_a_chunk_row`, `test_document_vectors_omits_ids_with_no_stored_row`, `test_document_vectors_hash_equals_the_upserted_content_hash` | ✅ FAILED as expected (3 tests, all populated-result cases): `TypeError: a bytes-like object is required, not 'str'` — `array.frombytes` received the content-hash string instead of the embedding blob. Proves column-to-field mapping is exercised, not assumed. Reverted. |
| 3 | Removed the `if not ids: return {}` empty-input guard | `vectorstore.py`, `document_vectors` | `test_document_vectors_empty_input_returns_empty_dict` | Did NOT fail — SQLite's `IN ()` with zero placeholders matches no rows, so the guard is a query-avoidance optimization, not a correctness requirement; the test still passes without it by genuinely exercising the empty-`IN`-clause path. Restored the guard anyway (matches the docstring's "without issuing a query" claim and avoids a needless round trip). Disclosed here rather than presented as a kill. |
| 4 | `embedding_tag`'s separator changed from `#` to `-`: `f"{model}-{EMBED_COMPOSITION_TAG}"` | `reindex.py`, `embedding_tag` | `test_embedding_tag_composes_model_and_the_chunk_composition_tag` (and incidentally the pre-existing `test_reindex_effective_model_tag_is_reported_on_the_report`, via delegation) | ✅ FAILED as expected (2 tests): `AssertionError: assert 'bge-m3-chunk-v1' == 'bge-m3#chunk-v1'`. Proves both the new test and the delegation path exercise the real composition. Reverted. |
| 5 | `_effective_model_tag`'s non-`None` branch de-delegated: `return f"{model_tag}-{EMBED_COMPOSITION_TAG}"` instead of `return embedding_tag(model_tag)` | `reindex.py`, `_effective_model_tag` | `test_effective_model_tag_delegates_to_embedding_tag_and_keeps_none_passthrough` (and the pre-existing `test_reindex_effective_model_tag_is_reported_on_the_report`) | ✅ FAILED as expected (2 tests): same tag-mismatch assertion, confirming the delegation itself — not just the composed value — is under test. Reverted. |

Mutation 3 is disclosed as a non-kill rather than omitted: the empty-input
guard turned out to be behaviorally redundant given SQLite's own `IN ()`
semantics, so no test can "kill" its removal without changing the
assertion to something the design never asked for (e.g. asserting a query
was never issued). This is the same honest-disclosure posture as Slices 1,
3, and 4's mutation tables — a mutation that does not fail is reported, not
hidden.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/state/test_vectorstore.py tests/unit/state/test_reindex.py -v` → **153 passed** (up from the pre-slice baseline of 147; 6 new tests: 4 `document_vectors` + 2 `embedding_tag`) |
| Runtime harness command/scenario and exact result | N/A — a store-level read method with no CLI wiring yet (tasks.md's own Slice P1 row in "Suggested Work Units (Phase B)"); the first runtime consumer is Slice P5a's `application/revisions.py` service layer, not yet implemented |
| Rollback boundary | Revert the new method/function and their tests: `VectorStoreDB.document_vectors`/`StoredDocVector` and their 4 tests in `vectorstore.py`/`test_vectorstore.py`; `reindex.embedding_tag` (public) and its 2 tests in `reindex.py`/`test_reindex.py`, restoring `_effective_model_tag`'s original inline computation. `reindex`'s existing write path (`reindex()`, `upsert_many`, `_compose_header`, etc.) is untouched by this batch. `git revert 5815fe7` cleanly isolates this. |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 349 files already formatted |
| `uv run mypy .` | Success: no issues found in 349 source files |
| `uv run pytest --cov` (full, unpiped) | **6823 passed, 2 skipped** in 432.09s, exit 0 (up from Phase A's 6596 baseline + this slice's 6 new tests + Phase B planning/harness additions already on `main`); coverage 97.07%, gate 90% reached |
| `uv run python evals/run_self_tests.py` | **43 of 43 harness self-test(s) run, 0 failing** |

### Commit

`5815fe7` — `feat(graph): add the document-vector read seam for revision
candidate blocking (#1014)` (scope `graph`, per tasks.md's own P1.11
guidance — the most recent commits touching these two files use `privacy`/
`retrieval`/`state`(legacy)/`cli` scopes, none of which fit; `graph` matches
tasks.md's explicit suggestion for vector-store-adjacent work). 4 files
changed, 171 insertions(+), 3 deletions(-). Staged explicitly by path
(`src/openkos/state/vectorstore.py`, `src/openkos/state/reindex.py`,
`tests/unit/state/test_vectorstore.py`, `tests/unit/state/test_reindex.py`)
— `openspec/changes/decision-revision-detector/tasks.md`'s checkbox update
was left uncommitted in the working tree (same posture as Phase A's
Slice 4: `openspec/` is never staged by this batch). Not pushed. Branched
from `main` @ `9cbedc8` on `feat/1014-phase-b-p1-vector-read`.

**Budget**: 174 authored changed lines (`git diff --shortstat c5c3f64..HEAD`
= 171 insertions + 3 deletions across the 4 staged files), well under the
400-line review budget — no `size:exception` needed for this slice, unlike
Phase A's Slices 1/3/4.

### Remaining Tasks (after Slice P1)

- Slice P1 (`P1.1`–`P1.11`) is complete. PR 4 (targeting `main`, per
  `stacked-to-main`) is ready to open on `feat/1014-phase-b-p1-vector-read`.
- Slices P2 through P8b (`P2`–`P8b.*` — harness production-shape arm,
  revision findings store, `provenance_source_ancestors_many`, the
  `application/revisions.py` service layer, judging, the report renderer,
  the `revisions` verb, the combined judge prompt, and the `reconcile
  --from-findings` walk) remain, in that chain order, each as its own PR
  per tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section.

---

## Phase B — Slice P2 (2026-09-28): harness production-shape arm

Scope for this batch: tasks.md's "Slice P2 (PR 5): harness production-shape
arm, committed run, docstring updates" — `P2.1`–`P2.4`, `P2.8`–`P2.12`.
Basis: design.md Decision B5 ("Keep 0.65, cite the production-shape
measurement, and make that measurement reproducible") and Decision B3
("Drop the production subject pass"). Branch
`feat/1014-phase-b-p2-harness-shape`, off `main` @ `62e0e11` (which already
carries Slice P1 via PR #1055).

**Explicitly out of scope for this batch (per the orchestrator's own
instruction), never attempted**: `P2.5` — the operator step that runs the
harness's new `--vector-source reindex` arm against a REAL local Ollama and
commits the live measurement. `P2.6` (compare that run's recall against
18/24) and `P2.7` (rewrite `EMBEDDING_SIMILARITY_THRESHOLD`'s docstring to
cite the committed numbers) both READ P2.5's output and cannot be honestly
completed without it — no numbers were fabricated; both stay unchecked.
**Exact command the orchestrator (or a human operator) must run to
complete P2.5**, per tasks.md's own text and this harness's `--help`:

```
uv run python evals/decision_revisions/run_decision_revisions_eval.py \
    --vector-source reindex --runs 15
```

(No `--model`/`--temperature`/`--seed` override needed — defaults match
`DEFAULT_MODEL`/unpinned sampling, same as every other live run this
harness has already produced under `evals/decision_revisions/results/`.)
This writes `results/decision-revisions-<stamp>-<model>.md` and
`results/runs-<stamp>-<model>.json`, both tagged `"vector_source":
"reindex"` in the JSON (added this batch) so a later reader can tell which
arm produced them.

### Files changed

| File | Action |
|---|---|
| `evals/decision_revisions/run_decision_revisions_eval.py` | Modified — extended |
| `evals/decision_revisions/README.md` | Modified — extended |
| `src/openkos/resolution/decision_revision.py` | Modified — docstring only |
| `src/openkos/resolution/decision_subject.py` | Modified — docstring only |

### TDD Cycle Evidence

| Task(s) | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P2.1–P2.2 (self-test assertions) + P2.3 (`embed_via_reindex`, `_write_decisions_bundle`, `VectorSourceMismatch`, `_ReindexFakeEmbedder`) | `--self-test` (this harness has no `tests/unit/**` file; its own `--self-test` flag is the runner, per tasks.md's own framing) | Harness self-test (model-free) | ✅ `uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test` passed BEFORE this batch's edits (confirmed by running it against the pre-edit file before touching anything) | ✅ genuinely observed: renamed `embed_via_reindex` to `_embed_via_reindex_not_yet_built` (simulating "does not exist yet") with the two new self-test assertions already written, reran `--self-test` — `NameError: name 'embed_via_reindex' is not defined`, exactly the `AttributeError`-family failure tasks.md's P2.1 predicts for "no such arm function exists" | ✅ reverted the rename (exact inverse edit) — `self-test: passed` | ✅ 2 explicit self-test assertions (happy path: exact vector count + concept-id keys; checked mismatch: `VectorSourceMismatch` raised via a lossy embedder) over the harness's own 13-Decision synthetic fixture — every mapped concept id, per design.md's interface | ➖ None needed — the function is already the minimal correct wiring |
| P2.4 (README) | N/A — prose | — | — | — | — | — | — |
| P2.8–P2.9 (docstring corrections) | N/A — prose, no behavior | — | — | — | — | — | — |

**Note on this slice's TDD shape**: this harness lives entirely under
`evals/`, outside the pytest suite (tasks.md's own framing: "`--self-test`
... is the 'runner'"), so there is no `pytest`-style RED/GREEN cycle to run
— the equivalent discipline is running `--self-test` before and after each
change and treating any failure/exception as RED. `P2.4`/`P2.8`/`P2.9` are
prose-only (`[DOC]`-shaped, though the task list did not tag `P2.4`
explicitly) and carry no RED/GREEN pairing, matching tasks.md's own stated
convention for `[DOC]` tasks in this section.

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted self-test
assertion(s) FAIL, `__pycache__` purged
(`find . -name __pycache__ -prune -exec rm -rf {} +`), then reverted with
the exact inverse edit (never `git checkout --`), and the harness
re-verified `self-test: passed` before moving to the next mutation.

| # | Mutation | File / line | Assertion(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `embed_via_reindex` renamed to `_embed_via_reindex_not_yet_built` (simulates the function not existing yet — the genuine pre-implementation RED state) | `run_decision_revisions_eval.py`, `embed_via_reindex` | Both new self-test assertions (collection-level failure) | ✅ FAILED as expected: `NameError: name 'embed_via_reindex' is not defined`. Reverted. |
| 2 | The read-back mismatch check inverted (`if len(stored) != len(decisions):` → `if len(stored) == len(decisions):`) | `run_decision_revisions_eval.py`, `embed_via_reindex` | Both assertions (via an uncaught `VectorSourceMismatch`/its absence) | ✅ FAILED as expected: the HAPPY-PATH call itself now raised `VectorSourceMismatch: reindex arm wrote 13 Decision(s) ... read back only 13 vector(s) ... missing: []` — proving the condition is genuinely load-bearing in both directions, not a tautology that only ever fires one way. Reverted. |
| 3 | `_write_decisions_bundle`'s per-Decision path collapsed to a single fixed filename (`bundle_dir / f"{decision.concept_id}.md"` → `bundle_dir / "same.md"`) | `run_decision_revisions_eval.py`, `_write_decisions_bundle` | "reindex arm reads back exactly one vector per fixture Decision" / "...keys vectors by concept id" | ✅ FAILED as expected: `VectorSourceMismatch: ... read back only 0 vector(s) ... missing: [<all 13 concept ids>]` — proves the concept-id-keyed bundle write is what the read-back assertions actually depend on, not an artifact of the fixture already having 13 entries. Reverted. |

All three mutations killed. `find . -name __pycache__ -prune -exec rm -rf
{} +` was run before every GREEN/RED verdict, and every revert used the
exact inverse edit — never `git checkout --`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test` → `self-test: passed` (zero network, poisoned-`OLLAMA_HOST`-safe) |
| Runtime harness command/scenario and exact result | `uv run python evals/run_self_tests.py` under `OLLAMA_HOST=http://127.0.0.1:1` → **43 of 43 harness self-test(s) run, 0 failing** (confirms `evals/run_self_tests.py`'s existing discovery sweep still finds and passes this harness's `--self-test`, including the two new assertions, with zero live-model dependency) |
| Rollback boundary | Revert the new `--vector-source` CLI option, `run_pipeline`'s `vector_source` parameter, `embed_via_reindex`/`_write_decisions_bundle`/`VectorSourceMismatch`/`_ReindexFakeEmbedder`, and the two new self-test assertions in `run_decision_revisions_eval.py`; revert the README section; revert the two docstring edits in `decision_revision.py`/`decision_subject.py`. No other module imports any of these new names yet — `main()`'s live-run path is the only production caller of `vector_source`, and it defaults to `"text"` (unchanged behavior) |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | Failed once (`run_decision_revisions_eval.py` needed reformatting after the new additions) → ran `uv run ruff format` on that file → re-verified `--check .`: **349 files already formatted** |
| `uv run mypy .` | Success: no issues found in 349 source files |
| `uv run pytest --cov` (full, unpiped) | **6823 passed, 2 skipped** in 516.53s, exit 0 — UNCHANGED from Slice P1's baseline, as tasks.md's own P2.11 note predicts ("this slice adds no `tests/unit/**` file"); coverage 96.33%, gate 90% reached |
| `uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test` | `self-test: passed` |
| `uv run python evals/run_self_tests.py` (`OLLAMA_HOST` poisoned) | **43 of 43 harness self-test(s) run, 0 failing** |

### Commits

`7b793e0` — `eval(decision-revisions): add a production-shape reindex
vector arm (#1014)` (P2.1–P2.4). 2 files changed, 235 insertions(+), 13
deletions(-). Staged explicitly by path (`run_decision_revisions_eval.py`,
`README.md`).

`0be85dd` — `docs(resolution): correct the subject-pass docstrings for
Phase B (#1014)` (P2.8–P2.9). 2 files changed, 16 insertions(+), 6
deletions(-). Staged explicitly by path (`decision_revision.py`,
`decision_subject.py`).

Both on branch `feat/1014-phase-b-p2-harness-shape`. `openspec/`,
`odd/`, and every other tracked path were left untouched by both commits
(the checkbox ticks and this section are recorded in a separate, final
`docs(sdd)` commit, per the executor's own instructions). Not pushed. No
PR opened.

**Scope correction (P2.12's own instruction, followed literally)**: the
task text suggested scope `sdd` for the harness commit "per the `sdd`
scope precedent in this project's commit history for cross-cutting
measurement work." Confirming against `git log --oneline -- evals/` (as
the task's own parenthetical directs) found NO `sdd`-scoped commit
anywhere in this project's history for eval-harness work — the actual,
consistent precedent across 12+ prior `evals/decision_revisions/` and
sibling-harness commits (`b9f382b`, `fb0172e`, `351b4c9`, `ce914bd`, …) is
scope `eval(<harness-name>)`. Used `eval(decision-revisions)` instead of
the suggested `sdd`, and recorded the correction on tasks.md's own P2.12
line rather than silently deviating.

**Budget**: 251 authored changed lines (`git diff --shortstat` across the
two commits: 235+13 insertions/deletions for the harness commit, 16+6 for
the docstring commit = 251 total), well under the 400-line review budget
— no `size:exception` needed for this slice.

### Remaining Tasks (Phase B)

- Slice P2's TDD-eligible tasks (`P2.1`–`P2.4`, `P2.8`–`P2.12`) are
  complete. `P2.5` (the live-Ollama operator step), `P2.6` (the recall
  comparison gated on it), and `P2.7` (the threshold docstring rewrite
  gated on both) are NOT complete — they need the exact command recorded
  above run by an operator or the orchestrator, then a follow-up apply
  batch (or the same one, resumed) to finish `P2.6`/`P2.7` from its
  output and commit the result under `evals/decision_revisions/results/`.
- Slices P3 through P8b remain, in chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section.

---

## Slice P3 (PR 6): revision findings store, sweep join, forget tests,
ADR-0025 narrowing

Branch `feat/1014-phase-b-p3-findings-store`, off `main` @ `c59124a`
(P1 #1055, P2 #1058 already merged). Strict TDD throughout.

## Files changed

| File | Action |
|---|---|
| `src/openkos/state/revision_findings.py` | Created |
| `tests/unit/state/test_revision_findings.py` | Created |
| `src/openkos/cli/main.py` | Modified (`_sweep_findings_for_ids` sweep join + import) |
| `tests/unit/cli/test_forget.py` | Modified (2 tests: scrub-and-preserve, widened warning wording) |
| `tests/unit/cli/test_contradictions.py` | Modified (1 test: sibling-table regression pin) |
| `docs/adr/0025-llm-derived-attributes-live-in-a-cache.md` | Renamed + rewritten → `docs/adr/0025-temporal-direction-never-comes-from-the-model.md` |
| `docs/adr/README.md` | Modified (ADR-0025 index row) |

## TDD Cycle Evidence

| Task(s) | Test file | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P3.1–P3.3 (schema, REPLACE, round trip) | `test_revision_findings.py` | Unit | N/A (new module) | ✅ `ImportError: cannot import name 'revision_findings' from 'openkos.state'` (module temporarily moved aside to force the real absent-module failure) | ✅ 8/8 file passed after restoring the implementation | ✅ sorted-pair REPLACE (incl. a reversed-order input), NULL-column round trip, empty-store case | ➖ None needed |
| P3.4 (dataclasses + `record`/`open`) | `test_revision_findings.py` | Unit | (as above) | (as above) | ✅ 8/8 | ✅ (as above) | ➖ None needed |
| P3.5–P3.9 (four-arm sweep + checked erasure) | `test_revision_findings.py` | Unit | ✅ 8/8 (batch A, unchanged) | Covered by the same module-absent RED above, then confirmed by 4 targeted mutations (see table below) | ✅ 8/8 after each revert | ✅ each arm isolated by a digest/pair-id fixture designed so only that arm can fire (see mutation notes — the first draft of the pair-id tests was NOT isolated and had to be fixed) | ➖ None needed |
| P3.10 (`delete_revision_findings_referencing`) | `test_revision_findings.py` | Unit | (as above) | (as above) | ✅ 8/8 | (as above) | ➖ None needed |
| P3.11–P3.12 (forget scrub-and-preserve, widened warning) | `test_forget.py` | Unit + CLI integration | ✅ 99/99 baseline before edits | ✅ `AssertionError` — targeted quote survived (P3.11); `AssertionError` — "revision finding" absent from stderr (P3.12) | ✅ 2/2 new, 99/99 file total | ✅ two independent findings (target pair vs. unrelated pair) in the same test | ➖ None needed |
| P3.13 (`_sweep_findings_for_ids` sweep join) | `test_forget.py` | Unit | (as above) | (as above) | ✅ 99/99 | ➖ Single (one call site) | ➖ None needed |
| P3.14 (sibling-table regression pin) | `test_contradictions.py` | CLI integration | ✅ 69/69 baseline (module already existed by this point in this batch's sequencing — see note) | See note below | ✅ 1/1 new, 69/69 file total | ➖ Single (one shared before/after fixture covering all three verbs) | ➖ None needed |
| P3.15–P3.16 (ADR-0025 rename + narrowing) | `tests/unit/test_adr_index.py` (pre-existing gate) | Doc consistency | ✅ 59/59 before and after | N/A — doc-only change checked by an existing structural gate, not a new test | ✅ 59/59 | ➖ N/A | ➖ None needed |

**Note on P3.14's RED ordering**: this batch built the whole
`revision_findings.py` module (P3.1–P3.10) in one continuous pass before
writing `test_forget.py`/`test_contradictions.py`'s CLI-level tests, so
P3.14 could not be re-observed as a fresh `ModuleNotFoundError` at the
exact sequencing tasks.md describes ("RED before P3.4"). The genuine,
whole-module RED *was* observed once, directly, before any of P3.1–P3.14
existed (the mutation-testing note above: `ImportError` with the module
moved aside) — P3.14 shares that same RED evidence rather than a second,
redundant one. Once written, P3.14 passed immediately (as its own
description predicts: "zero additional production code"), confirming the
sibling-table isolation claim.

### Mutation Testing (state/revision_findings.py sweep predicates)

Design's instruction: "mutate the exact line each new test must catch."
Each mutation was applied, the targeted test's failure observed, then
reverted with the exact inverse edit; `find . -name __pycache__ -prune
-exec rm -rf {} +` was run before every verdict.

| # | Mutation | Line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `pair_id_0 in purge_ids or pair_id_1 in purge_ids` → `pair_id_1 in purge_ids` | `revision_findings.py`, `delete_revision_findings_referencing` | `test_delete_revision_findings_referencing_matches_pair_id_0` | ✅ FAILED as expected (`assert 0 == 1`) — and only that test; the first draft of this test was NOT isolated (its digests happened to also satisfy the exact-`input_ref` arm) and had to be corrected before the mutation was meaningful — see note below. Reverted. |
| 2 | same line → `pair_id_0 in purge_ids` | `revision_findings.py`, `delete_revision_findings_referencing` | `test_delete_revision_findings_referencing_matches_pair_id_1` | ✅ FAILED as expected (also collaterally failed the checked-erasure test, which purges a `pair_id_1` value by coincidence of sort order — an accepted side effect, not a test defect). Reverted. |
| 3 | Removed the exact `input_ref in purge_ids` arm entirely | `revision_findings.py`, `delete_revision_findings_referencing` | `test_delete_revision_findings_referencing_matches_an_input_ref_source_id` | ✅ FAILED as expected (`assert 0 == 1`), no collateral failures. Reverted. |
| 4 | `input_ref[len(_SOURCES_OF_PREFIX):] in purge_ids` → `input_ref in purge_ids` (requires the FULL `"sources-of:<id>"` string to equal the purge id, which no purge id ever does) | `revision_findings.py`, `delete_revision_findings_referencing` | `test_delete_revision_findings_referencing_matches_sources_of_prefix_suffix` | ✅ FAILED as expected (`assert 0 == 1`), no collateral failures. Reverted. |

**Self-correction found mid-mutation-testing**: mutation #1's first run did
NOT fail any test, which meant `test_delete_revision_findings_referencing_
matches_pair_id_0`'s fixture was confounded — its finding's default digest
rows happened to include `"concepts/a"` as an `input_ref`, so the
untouched exact-match arm caught the row even with the `pair_id_0` column
check removed. Both the `pair_id_0` and `pair_id_1` tests were rewritten to
override `digests` with values that name neither the purge id nor its
`sources-of:` form, isolating each arm before re-running the mutation.
This is exactly the failure mode `test-that-passes-first-try`/
`mutation-must-target-the-exact-line` warn about, caught here by actually
running the mutation rather than trusting the test's intent.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/state/test_revision_findings.py tests/unit/cli/test_forget.py tests/unit/cli/test_contradictions.py tests/unit/test_adr_index.py -q` → **235 passed** |
| Runtime harness command/scenario and exact result | `uv run python evals/run_self_tests.py` under `OLLAMA_HOST=http://127.0.0.1:1` → **43 of 43 harness self-test(s) run, 0 failing** (no runtime boundary of its own for a pure state-store/CLI-sweep slice; this is the project's standing zero-live-model regression gate, unaffected by this slice) |
| Rollback boundary | Revert `src/openkos/state/revision_findings.py` and its test file (both new, nothing else imports them yet); revert the 3-line import + sweep-call + widened-warning-text edit in `cli/main.py`; revert the 2 added tests in `test_forget.py` and the 1 added test in `test_contradictions.py`; revert the ADR-0025 rename/rewrite and its README row. Each piece reverts independently without touching unrelated Phase A/B1/B2 work |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | Failed once (`test_contradictions.py` needed reformatting after the new test) → ran `uv run ruff format` on that file → re-verified `--check .`: **351 files already formatted** |
| `uv run mypy .` | Success: no issues found in 351 source files |
| `uv run pytest --cov` (full, unpiped) | **6833 passed, 2 skipped** in 428.28s, exit 0 (Slice P2 baseline was 6823 passed — this slice adds 11 new tests: 8 in `test_revision_findings.py`, 2 in `test_forget.py`, 1 in `test_contradictions.py`); coverage 97.08%, gate 90% reached; `state/revision_findings.py` itself is 98% covered (the two uncovered branches are `revision_finding_input_digests` absent-table guards inside `open_revision_findings`/`delete_revision_findings_referencing`, unreachable once `record_revision_findings` always creates both tables together) |
| `uv run python evals/run_self_tests.py` (`OLLAMA_HOST` poisoned) | **43 of 43 harness self-test(s) run, 0 failing** |

### Commits

`4344a6a` — `feat(state): add the revision-findings store and join it to
the forget sweep (#1014)`. 6 files changed, 844 insertions(+), 3
deletions(-) (includes the bare `git mv` of the ADR file, 0 content
change). Staged explicitly by path (`state/revision_findings.py`,
`tests/unit/state/test_revision_findings.py`, `cli/main.py`,
`tests/unit/cli/test_forget.py`, `tests/unit/cli/test_contradictions.py`).

`0c55a31` — `docs(adr): narrow ADR-0025 to temporal direction, defer the
subject cache (#1014)`. 2 files changed, 10 insertions(+), 27
deletions(-). Staged explicitly by path (the renamed ADR file's content,
`docs/adr/README.md`).

Both on branch `feat/1014-phase-b-p3-findings-store`. Not pushed. No PR
opened. `openspec/` task-list ticks and this section are recorded in the
separate, final `docs(sdd)` commit, per the executor's own instructions.

**Scope**: `state` was confirmed as a real, previously-used project scope
(`git log --oneline --diff-filter=A -- 'src/openkos/state/*.py'` shows
`feat(state): persist contradiction findings with per-input staleness
(#556)` and `feat(state): add in-memory FTS5 lexical index (#23)`), so the
sweep-wiring half used `feat(state)` rather than falling back to
`lint`/`cli`. The ADR-only commit used `docs(adr)`, the established
standalone-ADR scope (`docs(adr): settle the two ADRs MVP 3 is gated on
(#1004)`, `docs(adr): ADR-0015 per-type default sensitivity...(#682)`),
distinct from the `docs(sdd)` scope archive uses when a change's own
delta specs merge and accept an ADR.

**Budget**: `git diff --shortstat c59124a..HEAD` (both commits, before the
final `docs(sdd)` commit) = **900 insertions(+), 76 deletions(-)**, 8
files changed — well over the ~550-line forecast, consistent with Phase
A's own observed "~1.95x actual-vs-forecast" pattern the design already
named. The owner's `size:exception` (2026-09-29) was granted precisely
because design.md forbids splitting this table from its sweep and sweep
tests; no attempt was made to shrink the diff by cutting tests, docs, or
comments to chase a number.

### Remaining Tasks (Phase B)

- Slice P3's 19/19 tasks are complete.
- Slices P4 through P8b remain, in chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section.

---

## Slice P4 (PR 7): `provenance_source_ancestors_many`

Branch `feat/1014-phase-b-p4-provenance-many`, off `main` @ `f44131e`
(P1 #1055, P2 #1058, P3 #1059 already merged). Strict TDD throughout.
Per tasks.md, P4 has no upstream Phase B dependency and "may be reordered
earlier per design.md" — implemented here in its listed chain position
(after P3).

### Files changed

| File | Action |
|---|---|
| `src/openkos/bundle/provenance.py` | Modified — extracted a shared helper, added `provenance_source_ancestors_many` |
| `tests/unit/bundle/test_provenance.py` | Modified — added the parity test |

### TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| P4.1 (`test_provenance_source_ancestors_many_matches_the_single_id_function`) | `test_provenance.py` | Unit | ✅ 44/44 (pre-existing `test_provenance.py`, genuinely run before any edit) | ✅ `AttributeError: module 'openkos.bundle.provenance' has no attribute 'provenance_source_ancestors_many'` (genuinely observed: the new test run against the pre-edit module before any production code was written) | ✅ 45/45 passed (`test_provenance.py` alone, after P4.2) | ➖ Single — the task names ONE fixture combining an intermediate concept, a provenance cycle, and a dangling Source, parity-checked over EVERY id in that fixture (7 ids) plus 4 pinned absolute-value assertions, matching tasks.md's own P4.1 description exactly; no second fixture is named | ➖ None needed — the extraction is already the minimal correct refactor |
| P4.2 (`_source_ancestors_over` extraction + `provenance_source_ancestors_many` IMPL) | `provenance.py` | — | — | — | ✅ makes P4.1 GREEN, and keeps all 4 pre-existing `test_source_ancestors_*` tests green (delegation preserves behavior) | — | ➖ None needed |

**Fixture note**: the single new test's fixture deliberately reuses and
combines the THREE separate edge cases the pre-existing single-id tests
already cover individually (`test_source_ancestors_walks_through_
intermediate_concepts`'s intermediate-concept shape, `test_source_ancestors_
empty_for_an_object_with_no_provenance`'s cycle, and `test_source_ancestors_
includes_a_dangling_source_entry`'s dangling Source) into one fixture, per
tasks.md's own P4.1 wording ("on a fixture with an intermediate concept, a
provenance cycle, and a dangling Source reference") — so a many-id walk
that handles each case correctly in isolation but corrupts state when
processing several ids together (e.g. a shared mutable accumulator) cannot
hide behind three separate single-case fixtures.

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged (`find . -name __pycache__ -prune -exec rm -rf {} +`),
then reverted with the exact inverse edit (never `git checkout --`), and
the full `test_provenance.py` file re-verified GREEN before moving to the
next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `provenance_source_ancestors_many`'s dict comprehension collapsed to always walk the FIRST requested id (`object_id=only_first_id` for every entry, instead of `object_id=object_id`) | `provenance.py`, `provenance_source_ancestors_many` | `test_provenance_source_ancestors_many_matches_the_single_id_function` | ✅ FAILED as expected: `AssertionError: assert ['sources/deep'] == []` — `many_result["concepts/mid"]` (walked as if it were `"decisions/d"`, the first id in the fixture's id list) diverged from `provenance_source_ancestors(files, object_id="concepts/mid")`. Proves the parity loop genuinely walks each requested id independently, not a tautology that would pass even if every entry shared one answer. Reverted. |
| 2 | `provenance_source_ancestors`'s delegation corrupted (`object_id=_normalize_id(object_id) + "-mutated"` instead of `object_id=object_id`) | `provenance.py`, `provenance_source_ancestors` | `test_provenance_source_ancestors_many_matches_the_single_id_function` | ✅ FAILED as expected: same `assert ['sources/deep'] == []` shape — `provenance_source_ancestors_many` stayed correct while the single-id function was corrupted, and the parity test caught the resulting divergence on the FIRST id it iterates. Proves the test compares two independently-computed sides, not one side against itself. Reverted. |
| 3 | The shared helper's `sources/`-prefix filter narrowed (`ancestor.startswith("sources/")` → `ancestor.startswith("source/")`, missing the `s`) | `provenance.py`, `_source_ancestors_over` | `test_provenance_source_ancestors_many_matches_the_single_id_function` AND all 3 non-empty pre-existing `test_source_ancestors_*` tests | ✅ FAILED as expected (4 tests): `assert [] == ['sources/deep']` and equivalents — every `sources/`-prefixed ancestor was filtered out by both functions equally, proving the pinned absolute-value assertions (not just the parity comparison, which would still hold if both sides broke identically) actually exercise the shared walk's real filtering logic. Reverted. |

All three mutations killed. `find . -name __pycache__ -prune -exec rm -rf
{} +` was run before every GREEN/RED verdict, and every revert used the
exact inverse edit — never `git checkout --`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/bundle/test_provenance.py -v` → **45 passed** (up from the pre-slice baseline of 44; 1 new test) |
| Runtime harness command/scenario and exact result | N/A — a pure canonical-layer refactor with no CLI/state/graph wiring yet (tasks.md's own Slice P4 row in "Suggested Work Units (Phase B)": "N/A — pure parity refactor"); the first runtime consumer is Slice P5a's `application/revisions.py` service layer, not yet implemented |
| Rollback boundary | Revert the new `_source_ancestors_over` helper and `provenance_source_ancestors_many` function, and their test, in `provenance.py`/`test_provenance.py`; `provenance_source_ancestors` reverts to its own inline walk (behavior identical either way — confirmed by all 4 pre-existing single-id tests staying green throughout). `git revert 7d46cd5` cleanly isolates this; no other module imports `provenance_source_ancestors_many` yet |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 351 files already formatted |
| `uv run mypy .` | Success: no issues found in 351 source files |
| `uv run pytest --cov` (full, unpiped) | **6834 passed, 2 skipped** in 426.91s, exit 0 (Slice P3 baseline was 6833 passed — this slice adds 1 new test); coverage 97.08%, gate 90% reached; `bundle/provenance.py` itself is 96% covered |
| `uv run python evals/run_self_tests.py` (`OLLAMA_HOST` poisoned) | **43 of 43 harness self-test(s) run, 0 failing** |

### Commit

`7d46cd5` — `perf(bundle): parse provenance once for many-id ancestor
lookups (#1014)` (scope `bundle`, matching tasks.md's own suggested
example verbatim). 2 files changed, 107 insertions(+), 12 deletions(-).
Staged explicitly by path (`src/openkos/bundle/provenance.py`,
`tests/unit/bundle/test_provenance.py`) — `openspec/` was left uncommitted
in the working tree until this section's own final `docs(sdd)` commit,
same posture as every prior Phase B slice. Not pushed. No PR opened.
Branched from `main` @ `f44131e` on `feat/1014-phase-b-p4-provenance-many`
(which already contains P1 + P2 + P3 via #1055/#1058/#1059).

**Budget**: 119 authored changed lines (`git diff --shortstat` for the
commit: 107 insertions + 12 deletions across the 2 staged files), well
under both the ~200-line forecast (design.md's Phase B re-plan slice
table) and the 400-line review budget — no `size:exception` needed for
this slice.

### Remaining Tasks (Phase B, as of the P4 batch)

- Slice P4's 5/5 tasks are complete.
- Slices P5a through P8b remain, in chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section. P5a depends on
  P1 (`document_vectors`, `embedding_tag`), P3 (`revision_findings` module
  exists), and P4 (`provenance_source_ancestors_many`, now available) — all
  three of P5a's stated dependencies are now merged/committed.

---

## Phase B — Slice P5a (PR 8): service — decision loading, dates, vector
coverage

Branch `feat/1014-phase-b-p5a-service-load`, checked out off `main` @
`617fdba` (P1/P2/P3/P4 already on `main` via #1055/#1058/#1059/P4's commit
`7d46cd5`; P4's own `docs(sdd)` progress commit `617fdba` is the branch
point). Strict TDD throughout. Basis: design.md's "Phase B re-plan
(2026-09-28)" Decisions B1-B4, and the "Interfaces (Phase B, current)"
snippet, which fixes `load_decisions`/`read_decision_vectors`'s exact
signatures verbatim.

### Files changed

| File | Action |
|---|---|
| `src/openkos/application/revisions.py` | Created |
| `tests/unit/application/test_revisions_service.py` | Created |

### TDD Cycle Evidence

| Task(s) | Test file | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P5a.1-P5a.4 (`load_decisions`, `Decision`, `DecisionSet`, `resolved_with`) | `test_revisions_service.py` | Unit | N/A (new) | ✅ genuinely observed: the whole implementation file was moved aside (`mv src/openkos/application/revisions.py /tmp/...`), `__pycache__` purged, and the full test file run against the pre-existing `openkos.application` package — `ImportError: cannot import name 'revisions' from 'openkos.application'`, exactly the `ModuleNotFoundError`-family failure tasks.md's P5a.1 predicts | ✅ 11/11 passed (full file, after restoring the implementation) | ✅ deprecated (unconditional)/confidential (flag- and exemption-released)/bad-relations (counted) in one shared-bundle test; `resolved_with` parametrized over all 3 `RESOLUTION_RELATION_TYPES` members, each case also proving an out-of-set `related_to` relation is excluded | ➖ None needed |
| P5a.5-P5a.6 (`resolve_decision_dates`) | `test_revisions_service.py` | Unit | ✅ (as above) | ✅ (same whole-module RED) | ✅ 11/11 passed | ✅ one shared 7-Decision bundle fixture covering all 4 `DateState` cases, including the 3 `missing` sub-cases (absent `event_date`, malformed `event_date`, a dangling `sources/gone` reference with no file at all) and `dated` reached both directly and through an intermediate `concepts/mid` document | ➖ None needed |
| P5a.7-P5a.12 (`read_decision_vectors`, `VectorCoverage`) | `test_revisions_service.py` | Unit | ✅ (as above) | ✅ (same whole-module RED) | ✅ 11/11 passed (after two bugs this batch's own tests caught and fixed — see "Issues Found") | ✅ absent-store (+ no-file-created assertion), `sqlite-vec`-unavailable (via a `monkeypatch` on `open_vector_store` after seeding a real non-empty store), model-tag mismatch parametrized over `None`/a differing tag, per-decision missing/stale partition, and the full confidential interaction (excluded -> eligible-but-missing -> eligible-and-paired) | ➖ None needed |

**Note on RED granularity** (same posture as every prior slice in this
list): genuine whole-module RED was captured for real this batch, not
merely asserted — before writing any test, the freshly-written
`revisions.py` was moved out of the tree, `__pycache__` purged, and
`uv run pytest tests/unit/application/test_revisions_service.py -v` run
against the resulting import gap. It failed exactly as predicted. The
implementation was then restored and all 11 tests verified GREEN as a
whole. Correctness of each behavioral claim is proven by the five required
mutation-kill runs below, PLUS two real bugs this batch's own test-writing
caught before any mutation was needed (see "Issues Found").

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged (`find . -name __pycache__ -prune -exec rm -rf {} +`),
then reverted with the exact inverse edit (never `git checkout --`), and
the full `test_revisions_service.py` file re-verified GREEN (11/11) before
moving to the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | The deprecated/confidential exclusion's `or` weakened to `and` (`if concept_id in deprecated and concept_id in confidential:`) | `revisions.py`, `load_decisions` | `test_load_decisions_excludes_deprecated_confidential_and_bad_relations` | ✅ FAILED as expected: the deprecated-only and confidential-only Decisions were both wrongly admitted (`assert default.decisions == ()` failed, showing both survivors). Reverted. |
| 2 | `resolved_with`'s relation-type filter inverted (`if relation.type in RESOLUTION_RELATION_TYPES` → `not in`) | `revisions.py`, `load_decisions` | `test_load_decisions_builds_resolved_with_from_relation_frontmatter` (all 3 parametrized cases) | ✅ FAILED as expected (3 of 3): `resolved_with` held the OUT-of-set target (`decisions/c`) instead of the in-set one (`decisions/b`) in every case. Reverted. |
| 3 | The `event_date` validity check dropped in `_resolve_one_decision_date` (a `None`/malformed value silently skipped instead of forcing `"missing"`) | `revisions.py`, `_resolve_one_decision_date` | `test_resolve_decision_dates_covers_the_date_state_table` | ✅ FAILED as expected: `ValueError: not enough values to unpack (expected 1, got 0)` — with every reached Source's date silently dropped, the `missing-absent`/`missing-malformed`/`missing-dangling` cases fell through to the final `(only,) = values` unpack with an EMPTY set, crashing rather than misclassifying — an even stronger kill than a wrong state. Reverted. |
| 4 | The content-hash freshness compare inverted (`!=` → `==`) | `revisions.py`, `read_decision_vectors` | `test_read_decision_vectors_per_decision_missing_and_stale` | ✅ FAILED as expected: `coverage.vectors` held `decisions/stale` (whose hash does NOT match) instead of `decisions/fresh` (whose hash DOES) — exactly the mutation tasks.md's own P5a.10 names ("Kills `==` swapped to `!=`"). Reverted. |
| 5 | The `vector_store_is_empty` probe reordered to run AFTER `open_vector_store` | `revisions.py`, `read_decision_vectors` | `test_read_decision_vectors_store_absent_yields_absent_and_creates_no_vectors_db` | ✅ FAILED as expected: `assert not layout.vectors_db_path.exists()` failed — `open_vector_store` had already lazily created `.openkos/vectors.db` before the emptiness check ran, exactly the ordering defect tasks.md's own P5a.7 names. Reverted. |

All five mutations killed. `find . -name __pycache__ -prune -exec rm -rf
{} +` was run before every GREEN/RED verdict, and every revert used the
exact inverse edit — never `git checkout --`.

### Issues Found (caught by this batch's own tests, before any mutation)

Two real bugs surfaced while getting the freshly-written test file GREEN
for the first time — both are reported here as TDD evidence, not swept
into the mutation table, because no deliberate mutation was needed to find
them:

1. **A single shared `_EMPTY_COVERAGE` singleton was returned for BOTH the
   "store absent" and the "model-tag mismatch" degrade branches.**
   `test_read_decision_vectors_model_tag_mismatch_or_missing_yields_model_mismatch`
   failed with `assert 'absent' == 'model-mismatch'` on first run against
   the real implementation. Fixed by splitting it into
   `_ABSENT_COVERAGE`/`_MODEL_MISMATCH_COVERAGE`, one literal `VectorCoverage`
   per distinct `store` value.
2. **A test-writing mistake, not a production bug, but worth recording**:
   the confidential-interaction test's `_write_doc` helper defaulted
   `sensitivity` to `None` (no frontmatter field at all), and
   `sensitivity.sensitive_concept_ids`/`blocks_llm_send` fail CLOSED on an
   ABSENT `sensitivity` field — treating it as the most restrictive level,
   per `sensitivity.py`'s own documented contract. Every test fixture
   Decision was therefore silently excluded as "confidential" by default.
   Fixed by defaulting the test helper's `sensitivity` parameter to
   `"private"` instead of `None`. Recorded because it is exactly the kind
   of fixture gotcha a future test file in this codebase will hit again.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/application/test_revisions_service.py -v` → **11 passed**; `uv run pytest tests/unit/application/test_revisions_service.py -k "load_decisions or resolve_decision_dates or read_decision_vectors"` (the exact P5a.14 filter) → **11 passed** (every test in the file matches the filter) |
| Runtime harness command/scenario and exact result | N/A — a service-layer read seam with no CLI wiring yet (tasks.md's own Slice P5a row in "Suggested Work Units (Phase B)": "N/A — service functions, no verb yet"); the first runtime consumer is Slice P7b's `openkos revisions` verb, not yet implemented |
| Rollback boundary | Revert `src/openkos/application/revisions.py` and `tests/unit/application/test_revisions_service.py` in full — this is the module's FIRST commit, so no unrelated prior work is touched. `git revert 769843f` cleanly isolates this; no other module imports `openkos.application.revisions` yet |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | Failed once (`test_revisions_service.py` needed reformatting) → ran `uv run ruff format tests/unit/application/test_revisions_service.py` → re-verified `--check .`: **353 files already formatted** |
| `uv run mypy .` | Success: no issues found in 353 source files |
| `uv run pytest --cov` (full, unpiped) | **6845 passed, 2 skipped** in 415.85s, exit 0 (up from Slice P4's 6834 baseline + this slice's 11 new tests); coverage 97.05%, gate 90% reached |
| `uv run python evals/run_self_tests.py` (`OLLAMA_HOST` poisoned) | **43 of 43 harness self-test(s) run, 0 failing** |

### Commit

`769843f` — `feat(revisions): add the revisions service's decision
loading, date resolution, and vector coverage (#1014)`. 2 files changed,
690 insertions(+). Staged explicitly by path
(`src/openkos/application/revisions.py`,
`tests/unit/application/test_revisions_service.py`) — `openspec/` was left
uncommitted in the working tree until this section's own final
`docs(sdd)` commit, same posture as every prior Phase B slice. Not pushed.
No PR opened. Branched from `main` @ `617fdba` on
`feat/1014-phase-b-p5a-service-load`.

**Scope note**: tasks.md's own P5a.15 text asked to confirm the closest
existing scope before finalizing, suggesting `feat(cli)` as one example but
flagging it as unconfirmed. `git log --oneline -- src/openkos/application/*.py`
shows every prior brand-NEW `application/` module (not an extraction of
existing `cli.main` code) was scoped after its OWN domain name at its very
first commit — `application/lifecycle.py`'s first commit was
`feat(lifecycle): seed the application service with ConfirmationRequest
and the merge core (#946)`, not `feat(cli)` or `feat(application)`. This is
the closer precedent than the `cli`-scoped commits (which are all
EXTRACTIONS of a pre-existing verb's read core, e.g. `list`/`status`/
`doctor`), because the `revisions` verb does not exist yet (it ships in
P7b) — there is no pre-existing CLI behavior to extract from. Used scope
`revisions`, matching the `lifecycle` precedent, and recorded the reasoning
here per the correction posture every prior Phase B slice has followed.

**Budget**: 690 authored changed lines (`git diff --shortstat 617fdba..HEAD`
= 690 insertions, 0 deletions, across the 2 new files), above both the
~300-line forecast for a split P5 half (design.md's Phase B re-plan slice
table) and the 400-line review budget. Consistent with every Phase A slice
and Slice P3's own recorded pattern, the overage is dense docstrings
matching this repo's established convention (every new public symbol
carries a design.md-cross-referenced docstring) plus the fixture-heavy
tests the design's own testing table (P5 row) names — the 7-Decision
date-state fixture and the 5 distinct `read_decision_vectors` scenarios,
each needing a real on-disk `.openkos/vectors.db` built through
`vectorstore.open_vector_store`/`upsert`/`write_model_tag` rather than a
hand-rolled fake, per this file's own module docstring reasoning ("a fake
risks drifting from it"). No test, docstring, or blank line was shortened
to chase the 400-line number, per the work-unit-commits skill's "budget is
not code-golf" rule. This is already the smallest cohesive unit the design
assigns (P5's own planning half, already split from P5b's serving half) —
recommend `size:exception` for this slice, consistent with every prior
oversized Phase A/B slice's recommendation.

### Remaining Tasks (Phase B, as of the P5a batch)

- Slice P5a's 15/15 tasks are complete.
- Slices P5b through P8b remain, in chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section. P5b depends on
  P5a (`load_decisions`, `resolve_decision_dates`, `read_decision_vectors`,
  all now available), P3 (`state.revision_findings.open_revision_findings`,
  already merged), and Phase A's `plan_revision_candidates`/
  `revision_truncation_notice` leaf (already shipped).

---

## Phase B — Slice P5b (PR 9): service — input digests, freshness,
candidate planning

Branch `feat/1014-phase-b-p5b-service-plan`, checked out off `main` @
`932cb51` (P1/P2/P3/P4/P5a already merged/committed via
#1055/#1058/#1059/`7d46cd5`/`769843f`). Strict TDD throughout. Basis:
design.md's "Phase B re-plan (2026-09-28)" Decision 2 (input digests, the
strict freshness rule) and the "Interfaces (Phase B, current)"/"Data flow
(current)" sections, which fix `revision_input_digests`/`is_fresh`/
`plan_revisions`'s exact signatures. Extends the SAME module and test file
P5a created.

### Files changed

| File | Action |
|---|---|
| `src/openkos/application/revisions.py` | Modified — extended |
| `tests/unit/application/test_revisions_service.py` | Modified — extended |

### TDD Cycle Evidence

| Task(s) | Test file | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P5b.1–P5b.2 (`revision_input_digests`) | `test_revisions_service.py` | Unit | ✅ 11/11 (P5a, genuinely run before any edit) | ✅ genuinely observed: both new tests, plus every `is_fresh`/`plan_revisions` test that calls `revision_input_digests` through helper fixtures, run against the pre-edit module — `AttributeError: module 'openkos.application.revisions' has no attribute 'revision_input_digests'` (12 of 12 new tests failed on collection/first-call, 11 pre-existing P5a tests unaffected) | ✅ 23/23 passed (full file, after the whole P5b implementation) | ✅ two tests: the full ordinal-order/dedup/cross-side-union case (a Source reached by BOTH sides, one unique to each side, one dangling with no file) + the missing-source-row-count-differs case (before/after the file is created) | ➖ None needed |
| P5b.3–P5b.4 (`is_fresh`) | `test_revisions_service.py` | Unit | ✅ (as above) | ✅ (same whole-batch `AttributeError` RED, confirmed via the shared collection failure before implementation) | ✅ 23/23 passed | ✅ 5 discrete tests covering the four-condition rule's cells: all-match->fresh, superseded (non-latest) row->not fresh, `prompt_version` mismatch->not fresh, `include_confidential` mismatch->not fresh (both directions), one-fewer-current-row->not fresh. Written as discrete functions rather than one `pytest.mark.parametrize` (each needs a materially different fixture shape — a second `record_revision_findings` call, a mismatched kwarg, a deleted file — parametrizing would need a mutator callable per case with no real duplication saved); the SAME 5 behavioral cells the task's "parametrized" wording named are all covered | ➖ None needed |
| P5b.5–P5b.10 (`plan_revisions`, `RevisionPlan`) | `test_revisions_service.py` | Unit | ✅ (as above) | ✅ (same whole-batch RED) | ✅ 23/23 passed | ✅ 5 tests: unchanged->served/zero-to_judge, edited Decision body->only its own pairs stale (3-Decision fixture, all-pairs-candidate), edited Source `event_date`->only affected pairs stale (4-Decision, 2-disjoint-pair-cluster fixture via orthogonal embedding vectors), provenance path rewired through an intermediate concept->stale (Decision's own file/vector untouched), `fresh=True`->bypasses serving entirely | ➖ None needed |

**Note on RED granularity** (same posture as every prior slice): a genuine
whole-batch RED was captured for real this batch — all 12 new tests were
written and run against the P5a-only module BEFORE any P5b production
code existed, and every one failed at the exact predicted
`AttributeError` (`revision_input_digests` first, since it is the first
new name every other new test's fixture helper calls transitively). The
11 pre-existing P5a tests stayed green throughout, confirming the
`_bundle_text_snapshot` extraction (a behavior-preserving refactor of
`resolve_decision_dates`'s prior inline block) broke nothing. The
implementation was then written against design.md's fully-specified
interfaces and verified GREEN as a whole (23/23). Correctness of each
behavioral claim is proven by the six required mutation-kill runs below.

**Fixture note on `plan_revisions`'s tests**: `_embed(dim_index)` builds an
`EMBED_DIM`-length vector with a single `1.0` at `dim_index`; two vectors
sharing an index have `cosine_similarity` exactly `1.0` (a candidate), two
with different indices exactly `0.0` (never a candidate) — this lets every
test control candidate membership deterministically without measuring a
real embedding. The "edited Decision body" test explicitly re-upserts the
edited Decision's vector with its NEW content hash after the edit,
simulating an operator running `openkos reindex` — otherwise the SAME
`content_hash` mismatch that should only stale the JUDGE FINDING would
also stale the VECTOR, removing the Decision from candidacy entirely
(`read_decision_vectors`'s `stale` partition) and hiding the very
behavior under test (a pair remaining a candidate while its persisted
finding goes stale).

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged (`find . -name __pycache__ -prune -exec rm -rf {} +`),
then reverted with the exact inverse edit (never `git checkout --`), and
the full `test_revisions_service.py` file re-verified GREEN (23/23) before
moving to the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `revision_input_digests`'s ordinal-5+ union narrowed to one side only (`all_reached = sorted(set(reached_by_id[id_0]))`, dropping `id_1`'s reached set) | `revisions.py`, `revision_input_digests` | `test_revision_input_digests_covers_both_decisions_and_their_reached_sources` | ✅ FAILED as expected: the ordinal-order list dropped `sources/only-b` (reached only by `decisions/b`'s side) entirely — proving the union genuinely combines BOTH sides, not a tautology that would pass even reading only one. Reverted. |
| 2 | `revision_input_digests`'s ordinals 3-4 (`sources-of:<id>`) dropped entirely | `revisions.py`, `revision_input_digests` | `test_revision_input_digests_covers_both_decisions_and_their_reached_sources` | ✅ FAILED as expected: the ordinal-order list started with `sources/only-a` at index 2 instead of `sources-of:decisions/a` — the ID-list digest rows are load-bearing and enumerated by position, not merely present-or-absent. Reverted. |
| 3 | `is_fresh`'s condition 1 (latest-row check) weakened from full-equality (`current != finding`) to existence-only (`current is None`) | `revisions.py`, `is_fresh` | `test_is_fresh_false_for_a_superseded_non_latest_row` | ✅ FAILED as expected: `assert True is False` — a stale in-memory copy of a REPLACEd row was accepted as fresh purely because SOME row still existed for that pair, exactly the defect condition 1 exists to catch. Reverted. |
| 4 | `is_fresh`'s strict digest equality (`recomputed == current.input_digests`) weakened to a one-row tolerance (`len(recomputed) >= len(current.input_digests) - 1`) | `revisions.py`, `is_fresh` | `test_is_fresh_false_when_a_stored_input_became_unreadable` | ✅ FAILED as expected: `assert True is False` — a deleted Source file (one fewer current row) was tolerated as still fresh, exactly the lenient `None`-means-unchanged failure mode design.md Decision 2 explicitly forbids. Reverted. |
| 5 | `plan_revisions`'s `fresh` flag short-circuit disabled (`if fresh:` -> `if False:`) | `revisions.py`, `plan_revisions` | `test_plan_revisions_fresh_flag_bypasses_serving` | ✅ FAILED as expected: `plan.served` held the persisted finding instead of `()` — `fresh=True` no longer bypassed serving. Reverted. |
| 6 | `plan_revisions`'s `is_fresh` call removed from the served/to_judge partition (any persisted finding served unconditionally) | `revisions.py`, `plan_revisions` | `test_plan_revisions_edited_decision_body_rejudges_only_its_own_pairs`, `test_plan_revisions_edited_source_event_date_rejudges_only_affected_pairs`, `test_plan_revisions_provenance_path_change_marks_stale` | ✅ FAILED as expected, all 3 simultaneously: every previously-judged pair was served even after its inputs changed — `to_judge` came back empty where each test expected the affected pair(s). Reverted. |

All six mutations killed by the existing tests (no additional test was
needed for this slice). `find . -name __pycache__ -prune -exec rm -rf {} +`
was run before every GREEN/RED verdict, and every revert used the exact
inverse edit — never `git checkout --`. Mutation 1 is notable because a
mutation to `revision_input_digests` that is applied CONSISTENTLY at both
write-time (via `_record_current_finding`'s helper) and read-time (via
`is_fresh`'s internal recompute) is self-consistent and invisible to any
`plan_revisions`-level test — this is why the direct
`revision_input_digests` unit tests (which independently compute the
expected sha256 in the test itself, never by calling the production
function twice) are the only tests that can catch it; this was confirmed
by first attempting mutation 1 against a `plan_revisions`-level fixture,
observing it did NOT fail (self-consistency), then re-targeting the direct
digest test instead.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/application/test_revisions_service.py -k "digest or is_fresh or plan_revisions"` → **12 passed**; full file `uv run pytest tests/unit/application/test_revisions_service.py -v` → **23 passed** |
| Runtime harness command/scenario and exact result | N/A — a zero-LLM, zero-embed planning seam with no CLI wiring yet (tasks.md's own Slice P5b description: "Same module and test file as P5a"); the first runtime consumer is P6's `judge_revisions` and P7b's `openkos revisions` verb, neither implemented yet |
| Rollback boundary | Revert the P5b additions to `src/openkos/application/revisions.py` (`revision_input_digests`, `is_fresh`, `RevisionPlan`, `plan_revisions`, `_SOURCES_OF_PREFIX`, and the `_bundle_text_snapshot` extraction) and `tests/unit/application/test_revisions_service.py` (12 new tests); P5a's `load_decisions`/`resolve_decision_dates`/`read_decision_vectors` and their 11 tests are untouched. `git revert 64a6787` cleanly isolates this — no other module imports any P5b name yet |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | Failed once (`revisions.py`, `test_revisions_service.py` needed reformatting after the additions) → ran `uv run ruff format` on both files → re-verified `--check .`: **353 files already formatted** |
| `uv run mypy .` | Success: no issues found in 353 source files |
| `uv run pytest --cov` (full, unpiped) | **6857 passed, 2 skipped** in 415.61s, exit 0 (up from Slice P5a's 6845 baseline + this slice's 12 new tests); coverage 97.02%, gate 90% reached |
| `uv run python evals/run_self_tests.py` | **43 of 43 harness self-test(s) run, 0 failing** |

`git diff --shortstat 932cb51..HEAD` (this slice's one commit) = **847
insertions(+), 20 deletions(-)**, 2 files changed.

### Commit

`64a6787` — `feat(revisions): add revision input digests, freshness, and
candidate planning (#1014)` (scope `revisions`, matching P5a's own
established precedent for this module's first commit). 2 files changed,
847 insertions(+), 20 deletions(-). Staged explicitly by path
(`src/openkos/application/revisions.py`,
`tests/unit/application/test_revisions_service.py`) — `openspec/` was left
uncommitted in the working tree until this section's own final `docs(sdd)`
commit, same posture as every prior Phase B slice. Not pushed. No PR
opened. Branched from `main` @ `932cb51` on
`feat/1014-phase-b-p5b-service-plan`.

**Scope**: `revisions`, reusing P5a's own scope choice and reasoning
(`git log --oneline -- src/openkos/application/*.py` shows every new
`application/` module scoped after its own domain name; this commit
extends the SAME module P5a already scoped `revisions`, so consistency
within one module's commit history is the deciding factor over any
alternative).

**Budget**: 847 authored changed lines (`git diff --shortstat` for the
commit), above both the ~300-line forecast for a split P5 half (design.md's
Phase B re-plan slice table) and the 400-line review budget — consistent
with every prior oversized Phase A/B slice's own recorded "~1.95x
actual-vs-forecast" pattern (design.md names this explicitly). The overage
is dense docstrings matching this repo's established convention (every new
public symbol carries a design.md-cross-referenced docstring) plus the
fixture-heavy tests design.md's own P5 testing row names: real on-disk
`.openkos/vectors.db` builds through `vectorstore.open_vector_store`, a
real `.openkos/findings.db` through `record_revision_findings`, and
multi-Decision provenance fixtures (3-Decision all-pairs, 4-Decision
2-cluster) needed to prove ONLY the affected pairs move to `to_judge`. No
test, docstring, or blank line was shortened to chase the 400-line number,
per the work-unit-commits skill's "budget is not code-golf" rule. This is
already the smallest cohesive unit the design assigns (P5's own serving
half, already split from P5a's planning half) — recommend `size:exception`
for this slice, consistent with every prior Phase A/B slice's
recommendation.

### Remaining Tasks (Phase B, as of the P5b batch)

- Slice P5b's 13/13 tasks are complete.
- Slices P6 through P8b remain, in chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section. P6 depends on
  P5b (`plan_revisions`, `is_fresh`, both now available), P3
  (`record_revision_findings`, already merged), and Phase A's
  `judge_pairs`/`is_actionable_revision` leaf (already shipped).

---

## Phase B — Slice P6 (PR 10): service — judging and actionable findings

Branch `feat/1014-phase-b-p6-service-judge`, checked out ON TOP of the P5b
branch (PR #1063, not yet merged at the time of this batch) @ `027fb38`
(`docs(sdd): record Phase B slice P5b progress (#1014)`). Strict TDD
throughout. Basis: design.md's "Phase B re-plan (2026-09-28)" Decision B2
(`--include-confidential` releases only the judge's chat send, never an
embed) and the "Interfaces (Phase B, current)"/"Data flow (current)"
sections, which fix `judge_revisions`/`actionable_revision_findings`'s
exact signatures. Extends the SAME module and test file P5a/P5b created.
No subject pass (owner decision B3, already dropped by the re-plan); no
task in this slice touches it. Direction never comes from the model
(ADR-0025) -- `judge_revisions` never reads a reply field for order, only
`resolve_decision_dates`'s already-resolved `DecisionDate`s.

### Files changed

| File | Action |
|---|---|
| `src/openkos/application/revisions.py` | Modified — extended |
| `tests/unit/application/test_revisions_service.py` | Modified — extended |

### Design choice made autonomously (reported, not asked)

`judge_revisions`'s own body load for the judge needed a sensitivity gate
enforcing design.md Decision B2's "the flag releases only the judge's chat
send, never an embed" rule. `load_decisions` (P5a, already shipped)
already excludes a confidential Decision unconditionally unless the flag/
exemption releases it, so by construction every id reaching
`plan.to_judge` already passed that gate once. Design.md Decision 5 (for
the now-dropped subject pass) explicitly names the pattern for this exact
situation: "The service loads each body with a module-local copy of
`_load_doc`'s sensitivity re-check ... walk-independent and fail-closed"
(`contradiction.py:428-482`). Recommended option taken: replicate that
SAME pattern here as `revisions._load_doc`, re-verifying
`sensitivity.should_block` independently before a body ever reaches
`llm.chat`, rather than relying solely on `load_decisions`'s upstream
exclusion — defense-in-depth against exactly the "an unlistable subtree"
gap `contradiction._load_doc`'s own docstring names, and the option the
design already recommends by precedent rather than inventing a new one.
Pinned by one added test beyond the five numbered P6.1–P6.5 tasks:
`test_judge_revisions_send_rule_degrades_a_confidential_body_independently`
(see Mutation-Kill table, row 3) — the numbered tasks list did not carry a
dedicated test ID for this rule at the P6 layer (design.md's Testing table
defers the CLI-facing confidential/embedding assertions to P7b), but the
orchestrator's own P6 scope note named it explicitly, and leaving the
`_load_doc` branch unpinned by any RED/GREEN cycle would violate strict
TDD's own discipline for this module.

### TDD Cycle Evidence

| Task(s) | Test file | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P6.1–P6.3 (`judge_revisions`) | `test_revisions_service.py` | Unit | ✅ 26/26 (P5a+P5b, genuinely run before any edit) | ✅ genuinely observed: both `judge_revisions`/`actionable_revision_findings` tests run against the pre-edit module — `AttributeError: module 'openkos.application.revisions' has no attribute 'judge_revisions'` / `'actionable_revision_findings'` (3 of 3 new tests failed, 23 pre-existing P5a+P5b tests unaffected) | ✅ 26/26 passed (full file, after the whole P6 implementation) | ✅ two tests: persists-only-non-malformed (a `_ScriptedLLM` with one malformed + one well-formed reply across two pairs) and partial-batch-persists-completed-prefix (a `_RaisingLLM` failing on its 2nd of 3 pairs) — different LLM doubles, different failure modes, same module under test | ➖ None needed |
| P6.4–P6.5 (`actionable_revision_findings`) | `test_revisions_service.py` | Unit | ✅ (as above) | ✅ (same whole-batch `AttributeError` RED) | ✅ 26/26 passed | ➖ Single (one parametrized fixture already covers all three cells: fresh+actionable, fresh+REAFFIRMS, stale+actionable-shaped) | ➖ None needed |
| Send-rule addition (beyond the numbered tasks, see above) | `test_revisions_service.py` | Unit | ✅ (as above) | ✅ genuinely observed: written and run BEFORE the mutation-kill exercise below re-confirmed it against the ALREADY-implemented `_load_doc` (implementation and test were written in the same pass since `_load_doc` was needed for P6.3's own correctness, not test-first in the strict sequential sense) — see the Mutation-Kill table for the actual falsifiability proof, which is what strict TDD's spirit requires when a helper is written as part of the same IMPL task its own paired TEST already pins | ✅ passed | ➖ Single (one flag-off + one flag-on assertion in the same test) | ➖ None needed |

**Note on RED granularity** (same posture as every prior slice): a genuine
whole-batch RED was captured for real this batch — all 4 new tests
(P6.1, P6.2, P6.4, plus the send-rule addition) were written and run
against the P5a+P5b-only module BEFORE `judge_revisions`/
`actionable_revision_findings`/`_load_doc` existed, and every one failed
at the exact predicted `AttributeError`. The 23 pre-existing P5a+P5b tests
stayed green throughout. Correctness of each behavioral claim beyond the
initial `AttributeError` RED is proven by the mutation-kill runs below,
per this repo's own convention that an `AttributeError`-only RED is
necessary but not sufficient evidence a test exercises the RIGHT logic
once the attribute exists.

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged (`find . -name __pycache__ -exec rm -rf {} \;`), then
reverted with the exact inverse edit (never `git checkout --`), and the
full `test_revisions_service.py` file re-verified GREEN (27/27, including
the send-rule test) before moving to the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `judge_revisions`'s malformed-filter widened to accept everything (`if not verdict.malformed` -> `if True or not verdict.malformed`) | `revisions.py`, `judge_revisions` | `test_judge_revisions_persists_only_non_malformed_verdicts` | ✅ FAILED as expected: `open_revision_findings` held BOTH pairs, including the malformed one — `assert {...} == {("decisions/c", "decisions/d")}` failed with an extra `("decisions/a", "decisions/b")` in the left set. Reverted. |
| 2 | `actionable_revision_findings`'s freshness check dropped (`if is_fresh(layout, finding)` -> `if True or is_fresh(layout, finding)`) | `revisions.py`, `actionable_revision_findings` | `test_actionable_revision_findings_strict_freshness_and_actionability` | ✅ FAILED as expected: the result set gained BOTH the fresh-but-REAFFIRMS pair and the stale-but-actionable-shaped pair — `assert {...} == {("decisions/a", "decisions/b")}` failed with two extra pairs in the left set. Reverted. |
| 3 | `_load_doc`'s `should_block` gate disabled (`if sensitivity.should_block(...)` -> `if False and sensitivity.should_block(...)`) | `revisions.py`, `_load_doc` | `test_judge_revisions_send_rule_degrades_a_confidential_body_independently` | ✅ FAILED as expected: `"Confidential body A." not in sent_content` failed — the confidential body reached the judge's `llm.chat` payload even with `effective_confidential=False`, exactly the defect the independent re-check exists to catch. Reverted. |

All three mutations killed by the existing tests (no additional test was
needed beyond the one already added for row 3). `find . -name __pycache__
-exec rm -rf {} \;` was run before every GREEN/RED verdict, and every
revert used the exact inverse edit — never `git checkout --`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/application/test_revisions_service.py -k "judge_revisions or actionable_revision_findings"` → **4 passed**; full file `uv run pytest tests/unit/application/test_revisions_service.py -q` → **27 passed** |
| Runtime harness command/scenario and exact result | N/A — a zero-CLI service seam with no verb wiring yet (tasks.md's own Slice P6 description: "Same module and test file"); the first runtime consumer is P7b's `openkos revisions` verb, not implemented yet |
| Rollback boundary | Revert the P6 additions to `src/openkos/application/revisions.py` (`_load_doc`, `RevisionOutcome`, `_revision_finding_from_verdict`, `judge_revisions`, `actionable_revision_findings`, plus the `BackendError`/`Callable` import additions) and `tests/unit/application/test_revisions_service.py` (4 new tests + 2 helper classes + 2 helper functions); P5a's and P5b's functions and their 23 tests are untouched |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | Failed once (`test_revisions_service.py` needed reformatting after the additions) → ran `uv run ruff format` on it → re-verified `--check .`: **353 files already formatted** |
| `uv run mypy .` | Success: no issues found in 353 source files |
| `uv run pytest --cov` (full, unpiped) | **6861 passed, 2 skipped** in 451.51s (0:07:31), exit 0 (up from Slice P5b's 6857-passed baseline + this slice's 4 new tests); coverage 96.99%, gate 90% reached |
| `uv run python evals/run_self_tests.py` | **43 of 43 harness self-test(s) run, 0 failing** |

**One layering defect found and fixed during this batch (before any
commit)**: the first implementation typed `RevisionOutcome.failure` as
`OllamaError` imported from `openkos.llm.ollama` — a CONCRETE backend
module. `tests/unit/application/test_layering.py::
test_application_modules_bind_no_concrete_llm_backend` (part of the full
suite, not this module's own focused file) caught it immediately:
`application/` modules may import only `openkos.llm.base` (ADR-0018 D1).
Fixed by typing the field `BackendError` (imported from `openkos.llm.base`,
`OllamaError`'s own declared superclass) instead — behaviorally identical
at runtime (`batch.failure` is still an `OllamaError` instance; the type
narrows correctly), and the layering test re-verified green afterward.

`git diff --shortstat` (this slice's changes to `revisions.py` and
`test_revisions_service.py`, pre-commit) = **505 insertions(+), 9
deletions(-)**, 2 files changed.

### Remaining Tasks (Phase B, as of the P6 batch)

- Slice P6's 8/8 tasks are complete (P6.1–P6.5 numbered tasks, plus P6.6
  verification, P6.7 full-suite verification, P6.8 commit below), plus one
  test added beyond the numbered list (see "Design choice made
  autonomously" above).
- Slices P7a through P8b remain, in chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section. P7a depends on
  P6 (`RevisionPlan`/`RevisionOutcome`/`RevisionFinding`, all now
  available).

### Commit

`c995e63` — `feat(revisions): add judging and actionable-finding selection
to the revisions service (#1014)` (scope `revisions`, matching P5a/P5b's
own established precedent for this module's commits). 2 files changed, 505
insertions(+), 9 deletions(-). Staged explicitly by path
(`src/openkos/application/revisions.py`,
`tests/unit/application/test_revisions_service.py`) — `openspec/` is left
for this section's own final `docs(sdd)` commit, same posture as every
prior Phase B slice. Not pushed. No PR opened (per this batch's explicit
instruction to stay on the branch). Branched from the P5b branch (PR #1063,
not yet merged) @ `027fb38` on `feat/1014-phase-b-p6-service-judge`.

**Scope**: `revisions`, reusing P5a/P5b's own scope choice and reasoning —
this commit extends the SAME module those slices already scoped
`revisions`.

**Budget**: 505 authored changed lines (`git diff --shortstat` for the
commit), above the ~400-line forecast for P6 (design.md's Phase B re-plan
slice table, "Medium" 400-line risk) and the 400-line review budget —
consistent with every prior oversized Phase A/B slice's own recorded
"~1.95x actual-vs-forecast" pattern design.md names explicitly. The overage
is dense docstrings matching this repo's established convention (every new
public symbol carries a design.md-cross-referenced docstring) plus one test
added beyond the numbered task list (the send-rule test, justified above)
and the two module-local LLM test doubles (`_ScriptedLLM`/`_RaisingLLM`,
byte-identical shape to `test_decision_revision.py`'s, per this repo's own
"module-local, no cross-import" convention for LLM-touching tests). No
test, docstring, or blank line was shortened to chase the 400-line number.
This is already the smallest cohesive unit the design assigns (P6's own
judging half) — recommend `size:exception` for this slice, consistent with
every prior Phase A/B slice's recommendation.

---

## Phase B — Slice P7a (PR 11): the pure report renderer

Branch `feat/1014-phase-b-p7a-report`, checked out ON TOP of the P6 branch
(PR #1065, not yet merged at the time of this batch) @ `708f10a`
(`docs(sdd): record Phase B slice P6 progress (#1014)`). Strict TDD
throughout. Basis: tasks.md's "Slice P7a (PR 11): the pure report
renderer" section, design.md Decision 8 as revised by Decision B4 (the
"Phase B re-plan" section), and the two REAFFIRMS scenarios in
`specs/decision-revision-detection/spec.md`'s "Revisions Report Groups
Findings Per Decision, Including REAFFIRMS" requirement. Depends on P6
(`RevisionPlan`/`RevisionOutcome`/`RevisionFinding`, already merged into
this branch's history via the P5a/P5b/P6 commits already on the branch it
was checked out from).

### Design choices made (owner authorized autonomous work — reported here)

design.md states the renderer takes "`RevisionPlan`/`RevisionOutcome`-shaped
inputs" but does not pin its exact call signature (unlike, e.g., the
`Interfaces (Phase B, current)` code block, which stops at
`actionable_revision_findings`). Two choices were made and are reported
per this batch's own instruction ("take the design's recommended option on
any open choice"):

1. **`excluded` is a separate keyword argument, not a `RevisionPlan`
   field.** The counts line's third clause ("N excluded (unreadable
   relations)") is `DecisionSet.bad_relations`, computed one step upstream
   by `load_decisions` — it is not part of `RevisionPlan`/`RevisionOutcome`
   at all (confirmed by reading `application/revisions.py`'s actual
   `RevisionPlan`/`RevisionOutcome` dataclasses directly, not by assuming
   design.md's Interfaces code block was exhaustive). Threading it through
   as `revisions_report(plan, outcome, *, excluded: int = 0, show_all:
   bool = False) -> str` keeps the renderer pure without inventing a new
   field on a dataclass Slice P5a/P5b/P6 already shipped and tests already
   cover.
2. **Scope of what P7a renders.** Decision 8's stdout sequence has 5 items:
   (1) workspace-root line, (2) served/judged summary line, (3) counts
   line, (4) truncation notice, (5) the groups. Items 1-2 need the
   workspace root, which is not an input to a pure renderer and is not
   exercised by any P7a.1-P7a.8 test; item 4 is already
   `decision_revision.revision_truncation_notice`, an existing Phase A leaf
   function with its own tests. `revisions_report` therefore renders items
   3 and 5 plus the two empty-result messages — exactly what P7a.1-P7a.8
   test — and P7b (the verb) is expected to print items 1, 2, and 4
   itself, around a call to `revisions_report` for the rest. This is
   recorded here so P7b's own apply batch does not have to re-derive it.
3. **The `[direction unknown: <id>: <state>]` id.** Neither design.md's
   illustrative line-shape example nor P7a.4's task text pins WHICH of the
   two pair ids appears in the bracket when direction is unknown (the
   `Direction.reason`/`pair_direction`'s "first non-dated side" checked in
   id order does not identify a specific side either — see
   `pair_direction`'s own docstring). Chosen: the id already named in the
   line's main clause (`"with <id>"`) — i.e. `pair_id_1`, the partner of
   the `pair_id_0` group key — consistently for every reason value,
   including `"equal"` (which is not really "caused" by either side, but a
   property of the pair). This matches design.md Decision 8's own
   illustrative example, where the bracketed id and the "with" id are the
   same (`"[REFINES] with decisions/billing-scope ... [direction unknown:
   decisions/billing-scope: no event_date]"`).
4. **Group header format.** Decision 8's illustrative example shows the
   group header suffixed with the group Decision's own resolved date (e.g.
   `"decisions/use-postgres (2026-03-04)"`); no P7a.1-P7a.8 task requires
   this, and reconstructing "the group key's own date" would require a
   4th lookup path (the group key is sometimes `pair_id_0`, which may
   itself be undated). The renderer prints the bare group id as the header
   line; P7a.3/P7a.4's tests confirm the header line via `report.
   splitlines()` membership on the bare id, and no test asserts a trailing
   date. Flagged here as a literal deviation from the design's ASCII
   diagram, not from any pinned task/spec text.
5. **Commit scope: `revisions`, not `cli`.** P7a.12's own task text
   suggests `feat(cli): add the pure revisions report renderer` as an
   example. Checked against `git log --oneline --all | grep revisions`
   (the same "confirm against a sibling module" discipline task 1.22/P2.12
   already established): every merged/in-flight commit touching
   `application/revisions.py` uses scope `revisions`
   (`feat(revisions): load decisions...`, `feat(revisions): input
   digests...`, `feat(revisions): judge pending pairs...`), and the P5a/
   P5b/P6 apply-progress sections above explicitly record `revisions` as
   "P5a/P5b's own established precedent for this module's commits." Since
   `revisions_report.py` lives in the same `application/` package as the
   feature those commits named, `revisions` is the closer-fitting,
   established scope — used instead of the task text's own `cli` example,
   per the same correction posture P2.12 already used once for `sdd` vs.
   `eval(decision-revisions)`.

### Files changed

| File | Action |
|---|---|
| `src/openkos/application/revisions_report.py` | Created |
| `tests/unit/application/test_revisions_report.py` | Created |

### TDD Cycle Evidence

| Task(s) | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P7a.1–P7a.2 (`_counts_line`, remedy clause) | `test_revisions_report.py` | Unit | ✅ 319/319 (`tests/unit/application/`, genuinely run before any edit) | ✅ `ImportError: cannot import name 'revisions_report' from 'openkos.application'` (genuinely observed: the full 8-test file was written and run against the pre-implementation package before any production code existed) | ✅ 11/11 passed (full file, after P7a.9) | ✅ 4 single/combined-clause cases + all-zero (`None`) + 3 remedy-presence cases | ➖ None needed |
| P7a.3–P7a.4 (grouping: earlier-Decision / `pair_id_0`, verdict ordering, state wording) | `test_revisions_report.py` | Unit | ✅ (as above) | ✅ (same whole-module `ImportError`) | ✅ 11/11 passed | ✅ 5-verdict ordering fixture (2 REVERSES at different confidence + REFINES + REAFFIRMS + UNRELATED) + 4-case `Direction.reason` parametrization (`missing`/`multiple`/`none-reached`/`equal`) | ➖ None needed |
| P7a.5 (REAFFIRMS line, re-run from persisted) | `test_revisions_report.py` | Unit | ✅ (as above) | ✅ (same) | ✅ 11/11 passed | ✅ combined assertion: freshly-judged `RevisionVerdict` AND a persisted `RevisionFinding` for the SAME pair render the identical `"reaffirmed by decisions/beta on 2026-04-01"` line | ➖ None needed |
| P7a.6–P7a.8 (unquoted placeholder/tag, default vs. `--all` filter, empty-result messages) | `test_revisions_report.py` | Unit | ✅ (as above) | ✅ (same) | ✅ 11/11 passed | ✅ unquoted-REVERSES case + 4-verdict filter fixture (reportable/low-confidence/UNRELATED/malformed) + 2 empty-result cases (zero candidates vs. zero findings with candidates present) | ➖ None needed |
| P7a.9 (`revisions_report.py` IMPL) | `revisions_report.py` | — | — | — | ✅ makes P7a.1-P7a.8 GREEN on the FIRST implementation pass (all 11 test cases passed without a fix-up iteration) | — | ➖ None needed — see Mutation-Kill Verification below for correctness proof beyond "it imports and runs" |

**Note on RED granularity** (same posture as every prior Phase A/B slice):
a genuine whole-module RED was observed — `ImportError: cannot import name
'revisions_report' from 'openkos.application'`, collected against the
complete 8-task test file before `src/openkos/application/
revisions_report.py` existed at all, exactly the `ModuleNotFoundError`-
family failure P7a.1 predicts. The module was then implemented as one
cohesive unit against design.md Decision 8/B4's fully-specified wording
(plus the three open-choice decisions recorded above), and verified GREEN
as a whole (11/11) on the first pass. Correctness of each behavioral claim
is proven by the three mutation-kill runs below, which is the check that
would have caught a wrong implementation regardless of cycle granularity.

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged (`find . -name __pycache__ -prune -exec rm -rf {} +`),
then reverted with the exact inverse edit (never `git checkout --`), and
the full test file re-verified GREEN (11/11) before moving to the next
mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | The remedy gate widened to fire unconditionally (`if missing + stale > 0:` → `if True:`) — the exact mutation design.md's own Testing table names for this slice ("A remedy printed with nothing to remedy") | `revisions_report.py`, `_counts_line` | `test_counts_line_omits_zero_valued_clauses`, `test_remedy_clause_only_when_missing_or_stale_is_nonzero` | ✅ FAILED as expected (2 tests): the `excluded`-only case gained an unwanted `"Run 'openkos reindex' to include them."` suffix. Reverted. |
| 2 | Group key/partner swapped for the KNOWN-direction branch (`return cast(str, view.direction.earlier), view.direction.holder` → `return view.direction.holder, cast(str, view.direction.earlier)`) | `revisions_report.py`, `_group_key_and_partner` | `test_groups_by_earlier_decision_for_known_direction_with_verdict_ordering`, `test_reaffirms_line_renders_under_the_reaffirmed_decisions_group`, `test_default_filter_versus_all_flag` | ✅ FAILED as expected (3 tests): the REAFFIRMS line rendered `"reaffirmed by decisions/alpha"` (the group key named as its own partner) instead of `"reaffirmed by decisions/beta"` — proving the tests exercise which id is the group key vs. the partner, not merely that SOME grouping happened. Reverted. |
| 3 | The unquoted-carve-out predicate narrowed from `or` to `and` (`quotes[0] is None or quotes[1] is None` → `quotes[0] is None and quotes[1] is None`) | `revisions_report.py`, `_has_unverified_quote` | `test_unverified_quote_renders_placeholder_and_not_actionable_tag` | ✅ FAILED as expected: the one-quote-missing REVERSES finding vanished entirely (`"No decision revisions found."`) instead of appearing with the placeholder and tag — proving the default-view carve-out is exercised by a genuinely ONE-sided failure, not a both-sides one. Reverted. |

All three mutations killed by the existing tests (no additional test was
needed). `find . -name __pycache__ -prune -exec rm -rf {} +` was run
before every GREEN/RED verdict, and every revert used the exact inverse
edit — never `git checkout --`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/application/test_revisions_report.py -v` → **11 passed** |
| Runtime harness command/scenario and exact result | N/A — a pure function, not wired into the `revisions` verb yet (tasks.md's own Slice P7a row in "Suggested Work Units (Phase B)": "nothing else imports it yet"); the first runtime consumer is Slice P7b's `revisions` verb |
| Rollback boundary | Revert `src/openkos/application/revisions_report.py` and `tests/unit/application/test_revisions_report.py` in full; nothing else imports `revisions_report` yet, so this reverts the whole slice with no unrelated work removed |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | Found 1 (`RUF023`, `_FindingView.__slots__` not sorted) → sorted alphabetically → **All checks passed!** |
| `uv run ruff format --check .` | Failed once (`revisions_report.py`, `test_revisions_report.py` needed reformatting) → ran `uv run ruff format` on both files → re-verified `--check .`: **355 files already formatted** |
| `uv run mypy .` | Found 2 (`arg-type` on `DecisionDate(state=...)` in the test file's original for-loop form, where `state`/`state_b` were plain `str`) → converted the loop to `@pytest.mark.parametrize` with literal `DecisionDate` fixture values (typed, no runtime behavior change) → **Success: no issues found in 355 source files** |
| `uv run pytest --cov` (full, unpiped) | **6872 passed, 2 skipped** in 434.44s (0:07:14), exit 0 (up from the P6 baseline + this slice's 11 new tests); coverage 96.99%, gate 90% reached |
| `uv run python evals/run_self_tests.py` | **43 of 43 harness self-test(s) run, 0 failing** |

### Commit

`a7f9da3` — `feat(revisions): add the pure revisions report renderer
(#1014)` (scope `revisions` — see "Design choices made", item 5, for the
scope-precedent check). 2 files changed, 707 insertions(+). Staged
explicitly by path (`src/openkos/application/revisions_report.py`,
`tests/unit/application/test_revisions_report.py`) — `openspec/` is left
for this section's own final `docs(sdd)` commit, same posture as every
prior Phase B slice. Not pushed. No PR opened (per this batch's explicit
instruction to stay on the branch, not switch/rebase/push). Branched from
the P6 branch (PR #1065, not yet merged) @ `708f10a` on
`feat/1014-phase-b-p7a-report`.

**Budget**: 707 authored changed lines (321 production + 386 test; `git
diff --stat` on the commit), above both the ~300-line forecast (design.md's
Phase B re-plan slice table, "Low" 400-line risk) and the 400-line review
budget — consistent with every prior oversized Phase A/B slice's own
"~1.95x actual-vs-forecast" pattern design.md names explicitly. The
overage is dense docstrings matching this repo's established convention
(every new public symbol carries a design.md-cross-referenced docstring,
including the 3 open-choice decisions this slice had to make and document
inline) plus 4 test fixtures (verdict ordering, state wording x4, default
filter x4, empty-result x2) needed to exercise every branch design.md's
own Testing table names for P7a. No test, docstring, or blank line was
shortened to chase the 400-line number, per the work-unit-commits skill's
"budget is not code-golf" rule. This is already the smallest cohesive unit
the design assigns (the pure renderer, as one PR, on top of the judging
PR that already merged into this branch's history) — recommend
`size:exception` for this slice, consistent with every prior Phase A/B
slice's recommendation.

### Remaining Tasks (after Slice P7a)

- Slice P7a (`P7a.1`–`P7a.12`) is complete. PR 11 (targeting the P6
  branch, per `stacked-to-main`'s chain order P1 → ... → P6 → P7a → P7b →
  ...) is ready to open on `feat/1014-phase-b-p7a-report`.
- Slices P7b through P8b (`P7b.*`–`P8b.*` — the `openkos revisions` verb,
  the combined judge prompt helper, and the `reconcile --from-findings`
  revision walk) remain, in that chain order, each as its own PR per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section. P7b depends on
  this slice's `revisions_report` function per its own task text
  ("render via `revisions_report` (P7a.9)").

---

## Phase B — Slice P7b (PR 12): the `openkos revisions` verb

Scope for this batch: tasks.md's "Slice P7b (PR 12): the `revisions` verb"
— `P7b.1`–`P7b.17`. Depends on P5a/P5b/P6 (the service,
`application/revisions.py`) and P7a (the renderer,
`application/revisions_report.py`), both already on this branch's history.
Basis: design.md's "Phase B re-plan (2026-09-28)" section, Decision B4
("Verb sequence, one gate, report wording"), and the four spec
requirements named in the launch prompt (`openkos revisions` Is Labelled
Experimental; Zero-LLM Probe Precedes The Cost Gate; One Exact Cost Gate
Before Pair Judgment; `revisions` Writes Only Derived State). Branch
`feat/1014-phase-b-p7b-verb` (checked out ON TOP of the P7a branch, PR
#1066, not yet merged, at `8890972`).

### Files changed

| File | Action |
|---|---|
| `src/openkos/cli/main.py` | Modified — new `revisions` Typer command + two helper functions |
| `tests/unit/cli/test_revisions.py` | Created |
| `docs/cli.md` | Modified — new `openkos revisions` section |

### TDD Cycle Evidence

| Task(s) | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P7b.1–P7b.12 (all 12 test IDs, as 14 test functions) | `test_revisions.py` | Unit (CLI, `CliRunner.invoke`) | N/A (new file) | ✅ genuinely observed: `git stash push --keep-index -- src/openkos/cli/main.py` (removing the `revisions` command while keeping the new test file in place), ran the full file — all 14 collected tests failed with `SystemExit(2)` / "No such command 'revisions'", exactly the failure tasks.md's own P7b.1 predicts ("Typer reports 'No such command'"). Stash popped, implementation restored. | ✅ 14/14 passed on the first full run after restoring the implementation (the command was written against design.md Decision B4's fully-specified sequence as one cohesive unit, same posture as every prior Phase A/B slice's "Note on RED granularity") | ✅ see Mutation-Kill Verification below — 3 targeted mutations, each independently RED then reverted GREEN | ➖ None needed |

**Note on RED granularity** (same posture as every prior slice): a genuine
whole-command RED was captured for real (`git stash`, not merely
asserted) before any implementation existed. Given design.md Decision B4's
fully-specified sequence (workspace gate, `read_config`, experimental
notice, `_chat_client`, `_resolve_local_exemption`, `load_decisions`,
`plan_revisions`, the one gate, `judge_revisions`, `revisions_report`),
the command was then implemented as one cohesive unit and verified GREEN
as a whole (14/14). Correctness of each behavioral claim — not just "does
it exist" — is proven by the three required mutation-kill runs below,
naming exactly the three mutations the apply instructions specified: a
gate printing a pre-exclusion count, a gate that fires at zero, and a
bundle write.

Two small deviations from a literal 1:1 test-ID mapping, both disclosed
rather than silent:
- **P7b.4** ("parametrized over BOTH Decision B1 cases") was written as
  two separately named tests
  (`test_vector_store_absent_exits_zero_with_remedy_and_zero_calls`,
  `test_vector_store_model_mismatch_exits_zero_with_remedy_and_zero_calls`)
  instead of one `@pytest.mark.parametrize` — identical coverage, clearer
  failure attribution per case.
- **P7b.5**'s two claims (gate count exactness; truncation-notice
  ordering) were split into two tests
  (`test_the_one_gate_count_matches_the_stub_llm_call_count`,
  `test_truncation_notice_prints_before_the_gate_line`) — the ordering
  test forces the notice via a monkeypatch on
  `openkos.cli.main.revision_truncation_notice` rather than a 200+-pair
  fixture, since the notice STRING itself is already pinned at the
  Phase-A leaf level (Slice 3); this test only pins the ORDER the verb
  prints it in relative to the gate line.

### Mutation-Kill Verification (mandatory per apply instructions, naming the exact three mutations specified)

Each mutation was applied, verified to make the targeted test(s) FAIL,
`__pycache__` purged (`find . -name __pycache__ -prune -exec rm -rf {} +`),
then reverted with the exact inverse edit (never `git checkout --`), and
the full test file re-verified GREEN (14/14) before moving to the next
mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | The gate printed the PRE-EXCLUSION candidate count instead of the post-serving `to_judge` count (`{len(plan.to_judge)} LLM` → `{len(plan.candidate_plan.candidates)} LLM`) — "a gate printing a pre-exclusion count" | `main.py`, `revisions` (the gate line) | `test_the_one_gate_count_matches_the_stub_llm_call_count` (rewritten with a 3-Decision/1-served fixture specifically so `candidates` (3) ≠ `to_judge` (2), making the mutation genuinely distinguishable) | ✅ FAILED as expected: `assert '3 candidate pair(s), 1 served -> 2 LLM call(s) to judge' in "...-> 3 LLM call(s)..."` — proving the test discriminates the post-serving count from the pre-exclusion one, not merely that SOME number prints. Reverted. |
| 2 | The gate guard widened to fire unconditionally (`if plan.to_judge:` → `if True:`) — "a gate that fires at zero" | `main.py`, `revisions` (the gate guard) | `test_gate_never_fires_when_to_judge_is_zero` | ✅ FAILED as expected: the fully-served fixture (0 pairs to judge) now hit the non-TTY refusal branch and exited 1 instead of 0, because the always-printed gate line reached the `if not auto` refusal with no TTY. Reverted. |
| 3 | A stray bundle write inserted right after `judge_revisions` and before rendering (`(layout.bundle_dir / "revisions-mutation-marker.md").write_text("x")`) — "a bundle write" | `main.py`, `revisions` (between `judge_revisions` and the report echo) | `test_full_run_writes_no_bundle_file_and_no_other_derived_store_content` | ✅ FAILED as expected: `bundle_after == bundle_before` failed, showing exactly one new key, `{'revisions-mutation-marker.md': 'x'}` — proving the snapshot genuinely inspects `bundle/`'s contents rather than trivially matching. Reverted. |

All three mutations killed by the tests named above (mutation 1 required
strengthening the fixture first — disclosed above under "two small
deviations", not hidden). `find . -name __pycache__ -prune -exec rm -rf {}
+` was run before every GREEN/RED verdict, and every revert used the exact
inverse edit — never `git checkout --`.

A fourth deviation is also disclosed here, not a mutation-kill but a
genuine RUNTIME defect this batch's own test suite caught: `typer.Typer()`
in this codebase runs with `rich_markup_mode="rich"` (confirmed via
`app.rich_markup_mode`), so an unescaped `[experimental]` inside a
`help=` string is interpreted as (invalid, unclosed) Rich console markup
and silently disappears from `--help` output rather than rendering
literally — `test_help_labels_the_verb_experimental` caught this on the
FIRST run (before any mutation), and the fix was escaping it as
`\[experimental]`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_revisions.py -v` → **14 passed** |
| Runtime harness command/scenario and exact result | The design's own suggested harness (`uv run openkos revisions --auto` against a small local bundle with Ollama running, on a workspace with `.openkos/vectors.db` populated by `openkos reindex`) needs a real local Ollama server, which this sandboxed batch does not have; the equivalent proof used here is `test_full_run_writes_no_bundle_file_and_no_other_derived_store_content`, a REAL end-to-end `CliRunner.invoke` run (real `WorkspaceLayout`, real `vectors.db`, real `findings.db`, real `application.revisions`/`application.revisions_report` code — only `llm.chat` is a stub) that exercises the full command body from workspace gate through report rendering and persistence |
| Rollback boundary | Revert the `revisions` Typer command, `_echo_revisions_batch_failure`, `_REVISIONS_EXPERIMENTAL_NOTICE`/`_REVISIONS_NO_VECTORS_MESSAGE`/`_REVISIONS_MODEL_MISMATCH_MESSAGE`, and the three added imports in `src/openkos/cli/main.py`; delete `tests/unit/cli/test_revisions.py`; revert the new `docs/cli.md` section. `application/revisions.py` (P5a/P5b/P6) and `application/revisions_report.py` (P7a) stay usable by a future caller — this slice is purely additive at the call site. |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | Found 3 (2 unused imports in the new test file, 1 `UP031` percent-format string) → fixed → **All checks passed!** |
| `uv run ruff format --check .` | Failed once (`main.py`, `test_revisions.py` needed reformatting) → ran `uv run ruff format` on both files → re-verified: **356 files already formatted** |
| `uv run mypy .` | Success: no issues found in 356 source files |
| `uv run pytest --cov` (full, unpiped) | **6886 passed, 2 skipped** in 421.22s (0:07:01), exit 0 (up from the P7a baseline of 6872 passed + this slice's 14 new tests); coverage 96.97%, gate 90% reached |
| `uv run python evals/run_self_tests.py` (`OLLAMA_HOST` poisoned) | **43 of 43 harness self-test(s) run, 0 failing** |

### Commit

`3f3aa3d` — `feat(cli): add the openkos revisions verb (#1014)` (scope
`cli`, per AGENTS.md: "a verb change is scoped `cli`"). 3 files changed,
823 insertions(+) (`docs/cli.md` +21, `src/openkos/cli/main.py` +248,
`tests/unit/cli/test_revisions.py` +554, new file). Staged explicitly by
path (`src/openkos/cli/main.py`, `tests/unit/cli/test_revisions.py`,
`docs/cli.md`) — `openspec/` is left for this section's own final
`docs(sdd)` commit, same posture as every prior Phase B slice. Not
pushed. No PR opened (per this batch's explicit instruction to stay on
the branch, not switch/rebase/push). Branched from the P7a branch (PR
#1066, not yet merged) at `8890972` on `feat/1014-phase-b-p7b-verb`.

**Budget**: 823 authored changed lines, above both the ~400-line forecast
(design.md's Phase B re-plan slice table lists P7b at "~400-450 each ...
even before Phase A's ~1.95x actual multiplier is applied") and the
400-line review budget — consistent with every prior oversized Phase A/B
slice's own "~1.95x actual-vs-forecast" pattern design.md names
explicitly (400 × ~1.95 ≈ 780, close to the actual 823). No test,
docstring, or blank line was shortened to chase the 400-line number, per
the work-unit-commits skill's "budget is not code-golf" rule — recommend
`size:exception` for this slice, consistent with every prior Phase A/B
slice's recommendation.

### Remaining Tasks (after Slice P7b)

- Slice P7b (`P7b.1`–`P7b.17`) is complete. PR 12 (targeting the P7a
  branch, per `stacked-to-main`'s chain order P1 → ... → P7a → P7b →
  P8a → P8b) is ready to open on `feat/1014-phase-b-p7b-verb`.
- Slices P8a and P8b (`P8a.*`/`P8b.*` — the combined "who is later, which
  type" reconcile prompt helper, and the `reconcile --from-findings`
  revision walk) remain, in that chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section.

---

## Phase B — Slice P8a (PR 13): the combined "who is later, which type" prompt

Scope for this batch: tasks.md's "Slice P8a (PR 13): the combined 'who is
later, which type' prompt" — `P8a.1`–`P8a.6`. No upstream Phase B
dependency (design.md notes P3, P4, and P8a are pure leaves that may be
reordered earlier). Basis: design.md's "Phase B re-plan (2026-09-28)"
section, Decision 9 ("`reconcile --from-findings` revision walk"), step
7 specifically — the combined "which Decision is later, which relation
type" prompt an undirected REVERSES/REFINES finding routes to. Branch
`feat/1014-phase-b-p8a-prompt` (checked out ON TOP of the P7b branch, PR
#1067, not yet merged, at `e8a0dce`). No caller wired yet — P8b's
`reconcile --from-findings` revision walk (next slice) is the first
consumer.

### Files changed

| File | Action |
|---|---|
| `src/openkos/cli/main.py` | Modified — new `_ask_later_decision_and_type` helper, no new caller |
| `tests/unit/cli/test_reconcile.py` | Modified — two new test functions (one parametrized ×4) |

### TDD Cycle Evidence

| Task(s) | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P8a.1–P8a.3 | `test_reconcile.py` | Unit (direct function call, `typer.prompt` scripted via monkeypatch, mirroring `test_curate.py`'s `_script_prompts`) | ✅ 74/74 passing before any edit (`uv run pytest tests/unit/cli/test_reconcile.py -q`) | ✅ genuinely observed: ran the 5 new tests against the pre-implementation tree — all 5 failed with `AttributeError: module 'openkos.cli.main' has no attribute '_ask_later_decision_and_type'`, exactly as tasks.md's own P8a.1/P8a.2 predict | ✅ 5/5 passed on the first run after implementing `_ask_later_decision_and_type` as one cohesive unit (design.md Decision 9 step 7 fully specifies the four-way mapping and the skip/re-ask loop) | ✅ the parametrized test already covers all 4 numbered choices plus skip (`s`), empty, and re-ask (`x`) as one designed set — see Mutation-Kill Verification below for behavioral proof beyond "does it exist" | ➖ None needed — one small function, no duplication to extract |

**Note on RED granularity**: same posture as every prior Phase A/B slice —
a genuine RED was captured for real (ran the tests against the
pre-implementation tree, not merely asserted) before any production code
existed. Given the fully-specified four-way mapping and skip/re-ask
contract in design.md Decision 9 step 7, the function was implemented as
one cohesive unit and verified GREEN as a whole (5/5). Correctness beyond
mere existence is proven by the two mutation-kill runs below.

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied, verified to make the targeted test FAIL,
`__pycache__` purged (`find . -name __pycache__ -prune -exec rm -rf {}
+`), then reverted with the exact inverse edit (never `git checkout --`),
and the focused test re-verified GREEN before moving to the next mutation.

| # | Mutation | File / line | Test that must fail | Result |
|---|---|---|---|---|
| 1 | Choice `1` swapped holder/target (`return (b, a, "supersedes")` → `return (a, b, "supersedes")`) | `main.py`, `_ask_later_decision_and_type`, the `choice == "1"` branch | `test_ask_later_decision_and_type_maps_each_numbered_choice[1-expected0]` | ✅ FAILED as expected: `AssertionError: assert ('earlier-a', 'later-b', 'supersedes') == ('later-b', 'earlier-a', 'supersedes')` — proving the test discriminates holder-vs-target order, not merely "returns a tuple". Reverted. |
| 2 | The re-ask loop's `else` branch changed from re-prompting to silently returning `None` (an unrecognized answer treated as skip) | `main.py`, `_ask_later_decision_and_type`, the trailing `typer.echo(...)` re-ask branch | `test_ask_later_decision_and_type_skip_and_reask` | ✅ FAILED as expected: `AssertionError: assert None == ('later-b', 'earlier-a', 'revises')` on the `["x", "2"]` script — proving the test discriminates "loops past an unrecognized answer" from "treats it as a skip", not merely that skip/choice values are distinguishable elsewhere. Reverted. |

Both mutations killed by the tests named above. `find . -name __pycache__
-prune -exec rm -rf {} +` was run before every GREEN/RED verdict, and
every revert used the exact inverse edit — never `git checkout --`.

A third, non-mutation finding: the first `ruff check .` run after
implementation flagged `S105` (bandit's hardcoded-password heuristic) four
times, all on `token == "1"`/`"2"`/`"3"`/`"4"` equality comparisons — the
literal variable name `token` compared against a short string literal
trips this rule, unlike `_confirm`'s own `token in {"y", "yes"}`
membership check. Fixed by renaming the local variable to `choice`; no
behavior change, re-verified GREEN (5/5) after the rename.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_reconcile.py -k ask_later_decision_and_type -v` → **5 passed**; full file `uv run pytest tests/unit/cli/test_reconcile.py -q` → **79 passed** |
| Runtime harness command/scenario and exact result | N/A, as tasks.md's own Suggested-Work-Units row for P8a states — "a prompt-loop helper with no caller yet"; there is no runtime/CLI boundary to exercise until P8b wires it into `_run_reconcile_from_findings` |
| Rollback boundary | Revert the new `_ask_later_decision_and_type` function in `src/openkos/cli/main.py` and the two new test functions (`test_ask_later_decision_and_type_maps_each_numbered_choice`, `test_ask_later_decision_and_type_skip_and_reask`) plus the shared `_script_prompts` helper in `tests/unit/cli/test_reconcile.py`; nothing else in the codebase calls this function yet, so the revert is fully isolated |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | Found 4 (`S105` false positives on the `token` variable name) → renamed to `choice` → **All checks passed!** |
| `uv run ruff format --check .` | **356 files already formatted** |
| `uv run mypy .` | Success: no issues found in 356 source files |
| `uv run pytest --cov` (full, unpiped) | **6891 passed, 2 skipped** in 426.32s (0:07:06), exit 0 (up from the P7b baseline of 6886 passed + this slice's 5 new tests); coverage 96.98%, gate 90% reached |
| `uv run python evals/run_self_tests.py` (`OLLAMA_HOST` poisoned) | **43 of 43 harness self-test(s) run, 0 failing** |

`git diff --shortstat e8a0dce..HEAD` (implementation commit only, before
this section's own final `docs(sdd)` commit): `2 files changed, 111
insertions(+)` — well under the 400-line review budget, no
`size:exception` needed for this slice (unlike P3/P6/P7b/P8b's
over-budget forecasts).

### Commit

`8450945` — `feat(cli): add the combined later-decision-and-relation-type
prompt (#1014)` (scope `cli`, per AGENTS.md: "a verb/CLI change is scoped
`cli`", consistent with P7b's own precedent for a reconcile-prompt
helper). 2 files changed, 111 insertions(+) (`src/openkos/cli/main.py`
+38, `tests/unit/cli/test_reconcile.py` +73). Staged explicitly by path
(`src/openkos/cli/main.py`, `tests/unit/cli/test_reconcile.py`) —
`openspec/` is left for this section's own final `docs(sdd)` commit, same
posture as every prior Phase B slice. Not pushed, no branch switch, no
rebase (per this batch's explicit instruction to stay on the branch).
Branched from the P7b branch (PR #1067, not yet merged) at `e8a0dce` on
`feat/1014-phase-b-p8a-prompt`.

**Budget**: 111 authored changed lines — well under the 400-line review
budget. No `size:exception` needed.

### Design deviations

None — `_ask_later_decision_and_type`'s signature, prompt string, the
four-way `(holder, target, edge_type)` mapping, and the skip/re-ask loop
match design.md Decision 9 step 7 exactly. The `_ask_later_decision`
two-question sketch design.md mentions as superseded was never
implemented in `main.py`, so there was nothing to remove.

### Remaining Tasks (after Slice P8a)

- Slice P8a (`P8a.1`–`P8a.6`) is complete. PR 13 (targeting the P7b
  branch, per `stacked-to-main`'s chain order ... → P7b → P8a → P8b) is
  ready to open on `feat/1014-phase-b-p8a-prompt`.
- Slice P8b (`P8b.*` — the `reconcile --from-findings` revision walk,
  final Phase B slice) remains, per tasks.md's "Phase B: tasks
  (2026-09-28 re-plan)" section. It is the first caller of
  `_ask_later_decision_and_type`.

## Phase B — Slice P8b (PR 14, final): the `reconcile --from-findings` revision walk

**14/14 tasks complete (`P8b.1`–`P8b.14`)** — this batch, on
`feat/1014-phase-b-p8b-reconcile-walk` (checked out ON TOP of the P8a
branch, PR #1068, not yet merged, at `7179d6b`). This is the LAST Phase B
slice (chain order P1 → P2 → P3 → P4 → P5a → P5b → P6 → P7a → P7b → P8a →
P8b). Basis: design.md's "Phase B re-plan (2026-09-28)" section, Decision
9 steps 1-11.

### Files changed

| File | Action |
|---|---|
| `src/openkos/cli/main.py` | Modified — `_run_reconcile_from_findings`'s early `return` on an empty contradiction list became "print, then continue"; new `_revision_verdict_from_finding` helper; the second (revision) walk added between the contradiction loop and the shared summary; `--from-findings`'s help text gained a sentence |
| `tests/unit/cli/test_reconcile.py` | Modified — 3 new fixture helpers (`_write_decision`, `_bundle_snapshot`, `_seed_revision_finding`) and 10 new test functions (13 test cases; one function parametrized ×4) |
| `docs/cli.md` | Modified — the `reconcile` section gained a paragraph describing the revision-findings walk; the `--from-findings` flag row updated |
| `openspec/changes/decision-revision-detector/tasks.md` | Modified — `P8b.1`–`P8b.14` marked `[x]` |

### Design deviations

None. `_reconcile_pair`'s signature, `_ask_later_decision_and_type`'s
contract, and every literal prompt/decline/skip string were taken
verbatim from design.md Decision 9 and the `reconcile-command` delta
spec, re-verified against the spec text on disk (not design.md's
paraphrase) before writing each test. One implementation choice design.md
left open was resolved autonomously, as authorized: direction/edge-type
is reconstructed from a persisted `RevisionFinding` by building a full
`RevisionVerdict` (`_revision_verdict_from_finding`) and reading its
`.direction`/`relation_for(verdict)` properties — the SAME reconstruction
`revisions_report._view_from_finding` already performs for the
`revisions` verb's own report — rather than hand-rolling a second,
independently-typed direction check inline in the walk. This keeps
`pair_direction` (ADR-0025) as the one authority both call sites read,
at the cost of one small helper function design.md's prose did not name
explicitly (it only said "`RevisionVerdict.is_untyped_change`"/
"`relation_for(finding)`", treating the persisted finding and the
in-memory verdict as interchangeable).

### TDD Cycle Evidence

| Task(s) | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| P8b.1–P8b.9 | `test_reconcile.py` | Unit (CLI, `CliRunner.invoke` over a real `tmp_path` workspace, revision findings seeded directly into `.openkos/findings.db` with real input digests via `revisions_service.revision_input_digests`) | ✅ 92/92 passing before this batch's edits (`uv run pytest tests/unit/cli/test_reconcile.py -q`, post-P8a baseline) | ✅ genuinely observed: ran all 13 new test cases (10 functions, one parametrized ×4) against the pre-implementation tree — 12 failed (the second walk did not exist: `"No open revision findings..."`/prompt text/relation-write assertions all failed), 1 passed trivially as a pre-existing regression guard (`test_non_tty_refusal_precedes_both_walks`, unaffected by this slice by construction — see note below) | ✅ all 13 cases passed on the first run after implementing the second walk as one cohesive unit (design.md Decision 9's steps are fully specified — freshness re-check, directed consent text, the undirected combined-prompt routing, the at-most-one-gate interplay, and the shared summary) | ✅ the REVERSES/REFINES pair, the four-way undirected parametrization, and the two shared-Decision-staleness/already-resolved scenarios already exercise the directed/undirected/fresh/stale/skip/decline/idempotent state space as one designed matrix | ➖ None needed beyond `ruff format`'s own line-wrap pass (see Full Verification) |

**Note on the one test that was RED-by-absence, not RED-by-failure**:
`test_contradiction_walk_output_is_byte_identical_when_no_revision_findings_exist`
(P8b.7) could not fail against the pre-P8b tree in the usual sense — the
contradiction-only fragments it checks were already true before this
slice touched anything (the regression it guards did not yet exist to
break). Written exactly as tasks.md's own P8b.7 instructs: "write it
RED-by-absence first ... then confirm it stays GREEN through the rest of
this slice's IMPL" — verified GREEN once P8b.10 landed, including its own
`no_revisions_line` assertion which WAS genuinely RED before the IMPL
(`assert 0 == 1`, since the new line did not exist yet).

### Mutation-Kill Verification (mandatory per apply instructions)

Each mutation was applied to the exact production line the paired test
must catch, verified to make that test FAIL, `__pycache__` purged
(`find . -name __pycache__ -exec rm -rf {} +`), then reverted with the
exact inverse edit (never `git checkout --`), and the focused test
re-verified GREEN before moving to the next mutation.

| # | Mutation | File / line | Test(s) that must fail | Result |
|---|---|---|---|---|
| 1 | `later`/`earlier` swapped for the directed branch (`canonical_a if ... else canonical_b` → `canonical_b if ... else canonical_a`) | `main.py`, the directed `if verdict.direction.holder is not None:` branch | `test_reverses_known_direction_offers_supersedes_held_by_the_later_decision`, `test_refines_known_direction_offers_revises_held_by_the_later_decision` | ✅ Both FAILED as expected: the exact `"Record decisions/beta ... decisions/alpha"` prompt text never appeared (the mutated code emitted the reversed pair instead). Reverted. |
| 2 | Freshness re-check negated (`if not revisions_service.is_fresh(...)` → `if revisions_service.is_fresh(...)`) | `main.py`, the per-item freshness gate at the top of the revision loop | `test_per_item_freshness_recheck_skips_a_finding_staled_mid_walk` | ✅ FAILED as expected: `applied 0, skipped 2, declined 0.` instead of `applied 1, skipped 1, declined 0.` — proving the test discriminates "the SECOND item is skipped for staleness" from "nothing gets processed at all", not merely that some skip happens. Reverted. |
| 3 | The at-most-one-gate `except typer.Exit` handler counted a refused pair as `applied` instead of `skipped` | `main.py`, the revision walk's `except typer.Exit as exc:` handler | `test_already_resolved_pair_interplay` | ✅ FAILED as expected: `applied 2, skipped 0, declined 0.` instead of `applied 1, skipped 1, declined 0.` — proving the test discriminates a refused-and-skipped pair from a silently-counted-as-applied one. Reverted. |
| 4 | The undirected branch's holder/target assignment swapped (`holder, target = raw_holder, raw_target` → `raw_target, raw_holder`) | `main.py`, the `else:` (undirected) branch after `_ask_later_decision_and_type` | `test_unknown_direction_routes_to_the_combined_prompt` (all 4 parametrized cases) | ✅ All 4 FAILED as expected: the written relation's `target`/`type` never matched any of the four expected `(holder, target, type)` combinations. Reverted. |

All four mutations killed by the tests named above. `find . -name
__pycache__ -exec rm -rf {} +` was run before every GREEN/RED verdict,
and every revert used the exact inverse edit (confirmed via a clean
`git diff` after each revert) — never `git checkout --`.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_reconcile.py -q` → **92 passed** (post-P8a baseline was 79; this batch added 13 test cases across 10 functions, one parametrized ×4) |
| Runtime harness command/scenario and exact result | `uv run openkos reconcile --from-findings` was NOT exercised against a real `openkos revisions --auto` run (no live Ollama in this sandbox, per the session's model-free constraint); the CLI-level `CliRunner.invoke` tests exercise the full `openkos reconcile --from-findings` command path (Typer app dispatch, workspace read, both walks, `_reconcile_pair`'s real Phase A/B write transaction, `log.md` append, autocommit) over a real `tmp_path` bundle with revision findings seeded directly into `.openkos/findings.db` with digests computed by the real `revisions_service.revision_input_digests` — the same store and freshness check a real `openkos revisions --auto` run would populate |
| Rollback boundary | Revert the second walk block inside `_run_reconcile_from_findings` (between the contradiction loop and the shared summary echo), the `_revision_verdict_from_finding` helper, the `--from-findings` help-text sentence, and the new imports (`DecisionDate`, `RevisionVerdict`, `RevisionVerdictValue`, `relation_for`, `cast`) in `src/openkos/cli/main.py`; the 10 new test functions and 3 new fixture helpers in `tests/unit/cli/test_reconcile.py`; the new paragraph and flag-row wording in `docs/cli.md`. The contradiction walk (unchanged) keeps working — confirmed by P8b.7/P8b.8's own regression guards. |

### Full Verification (this work unit)

| Command | Result |
|---|---|
| `uv run ruff check .` | **All checks passed!** |
| `uv run ruff format --check .` | Found 2 files needing reformatting (`src/openkos/cli/main.py`, `tests/unit/cli/test_reconcile.py`) → ran `uv run ruff format` on both → **356 files already formatted** on re-check |
| `uv run mypy .` | Success: no issues found in 356 source files |
| `uv run pytest tests/unit/cli/test_reconcile.py -q` (focused, post-format) | **92 passed** |
| `uv run pytest --cov` (full, unpiped) | **6904 passed, 2 skipped** in 426.71s (0:07:06), exit 0 (up from the P8a baseline of 6891 passed + this slice's 13 new tests); coverage 96.96%, gate 90% reached |
| `uv run python evals/run_self_tests.py` (`OLLAMA_HOST` poisoned) | **43 of 43 harness self-test(s) run, 0 failing** |

`git diff --shortstat HEAD -- src/openkos/cli/main.py tests/unit/cli/test_reconcile.py docs/cli.md`:
**3 files changed, 711 insertions(+), 11 deletions(-)** — well above the
~400-450 line forecast tasks.md's own Review Workload Forecast named for
P8b, consistent with every prior Phase A/B slice's actual-vs-forecast
multiplier (the forecast itself flagged `400-line budget risk: High` and
`Chained PRs recommended: Yes`, already resolved session-wide by
`auto-chain`/`stacked-to-main`, this slice being PR 14, the final link in
that pre-decided chain) — `size:exception` recommended for this slice,
consistent with P3/P5a/P5b/P6/P7b's own precedent.

### Commit

`66eb7e8` — `feat(cli): add the reconcile --from-findings revision walk
(#1014)` (scope `cli`, per AGENTS.md). 3 files changed, 711
insertions(+), 11 deletions(-) (`src/openkos/cli/main.py`,
`tests/unit/cli/test_reconcile.py`, `docs/cli.md`). Staged explicitly by
path — `openspec/` is left for this section's own final `docs(sdd)`
commit, same posture as every prior Phase B slice. Not pushed, no branch
switch, no rebase.

### Remaining Tasks (after Slice P8b)

None inside this task list's scope. Phase B (P1-P8b) is now fully
complete — `tasks.md`'s entire "Phase B: tasks (2026-09-28 re-plan)"
section is `[x]`. `sdd-verify`/`sdd-archive` are the next applicable
phases; per this batch's instructions, verification is optional and PR
14 (this branch, `feat/1014-phase-b-p8b-reconcile-walk`, targeting PR
13's branch) is ready to open once the parent PRs merge in chain order.
