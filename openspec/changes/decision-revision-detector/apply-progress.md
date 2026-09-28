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
