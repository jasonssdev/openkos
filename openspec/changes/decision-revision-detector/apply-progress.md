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

**Phase B, Slice P4 (`P4.1`–`P4.5`, 5/5) complete** — this batch, on
`feat/1014-phase-b-p4-provenance-many` (checked out off `main` at
`f44131e`, which already contains P1/P2/P3 via #1055/#1058/#1059). PR 7
boundary: `provenance_source_ancestors_many` in
`src/openkos/bundle/provenance.py`, a pure shared-walk refactor with no
upstream Phase B dependency, well under the review budget (119 authored
changed lines) — no `size:exception` needed.

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

### Remaining Tasks (Phase B)

- Slice P4's 5/5 tasks are complete.
- Slices P5a through P8b remain, in chain order, each as its own PR, per
  tasks.md's "Phase B: tasks (2026-09-28 re-plan)" section. P5a depends on
  P1 (`document_vectors`, `embedding_tag`), P3 (`revision_findings` module
  exists), and P4 (`provenance_source_ancestors_many`, now available) — all
  three of P5a's stated dependencies are now merged/committed.
