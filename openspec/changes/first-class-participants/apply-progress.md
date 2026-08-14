# Apply Progress: First-class Person/Organization participants

## Work Unit 1 (PR1) — Judge Re-Admission Set (D1/D3/D4)

Branch: `feat/668-judge-readmit-participants` (from `main`)
Scope: tasks 1.1–1.16 only. Phase 2/3/4 untouched.

### Completed Tasks

- [x] 1.1 RED: `test_judge_dropped_person_with_anchor_on_meeting_source_is_readmitted`
- [x] 1.2 RED: `test_person_title_twin_of_source_still_dropped`
- [x] 1.3 RED: `test_meeting_titled_person_still_dropped_by_framing_removal`
- [x] 1.4 RED: `test_procedure_behavior_unchanged_at_all_three_sites`
- [x] 1.5 RED: `test_person_without_anchor_not_readmitted`
- [x] 1.6 RED: `test_person_with_meeting_role_anchor_is_readmitted`
- [x] 1.7 RED: `test_person_not_readmitted_from_non_meeting_source`
- [x] 1.8 RED: `test_judge_readmit_types_subset_of_classifiable_types`
- [x] 1.9 Confirmed RED for the right reason (see TDD Cycle Evidence)
- [x] 1.10 GREEN: `_JUDGE_READMIT_TYPES: Final = frozenset({"Procedure", "Person", "Organization"})` added near `_TWIN_EXEMPT_TYPE`; `_TWIN_EXEMPT_TYPE = "Procedure"` and both deletion call sites (`_is_droppable_source_title_twin` line 891, `_drop_framing_objects` line 1080 post-insert) are byte-identical (verified via `git diff` — zero diff lines touch them)
- [x] 1.11 GREEN: `_PARTICIPANT_ANCHOR_RE` added — English + Spanish role/affiliation/relation lexicon, two-language limit documented in the constant's docstring mirroring `_MEETING_SHAPED_TITLE_RE`'s #522 precedent
- [x] 1.12 GREEN: `_has_participant_anchor(result) -> bool` added, reads `description`+`body`
- [x] 1.13 GREEN: judge re-admission conjunct composed exactly per design D4: `c.type in _JUDGE_READMIT_TYPES and (c.type == _TWIN_EXEMPT_TYPE or (meeting_shaped and _has_participant_anchor(c)))`; `meeting_shaped = _MEETING_SHAPED_TITLE_RE.search(source_title)` computed once and reused
- [x] 1.14 Confirmed GREEN — all 8 new tests pass
- [x] 1.15 Mutation-verified every new test's exact target line (see table below); `__pycache__` purged before each run; every mutation reverted with the exact inverse edit (never `git checkout --`)
- [x] 1.16 Full `tests/unit/extraction/test_concept.py` run unpiped: 247 passed, 0 failed, 0 regressions

### Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/extraction/concept.py` | Modified | Added `_JUDGE_READMIT_TYPES`, `_PARTICIPANT_ANCHOR_RE`, `_has_participant_anchor`; composed the D4 conjunct at the judge re-admission site; added `meeting_shaped` local reused from the existing `_MEETING_SHAPED_TITLE_RE` gate |
| `tests/unit/extraction/test_concept.py` | Modified | Added 8 new tests (1.1–1.8) covering re-admission, both D1 regression guards (twin-drop, framing-drop), Procedure non-regression, stub rejection, and meeting-shape scoping |
| `src/openkos/extraction/judge.py` | Unchanged | D2 — verified zero diff via `git diff` |

### TDD Cycle Evidence

| Task | Test | Layer | Safety Net | RED | GREEN | Mutation |
|------|------|-------|------------|-----|-------|----------|
| 1.8 | `test_judge_readmit_types_subset_of_classifiable_types` | Unit | 239/239 baseline | AttributeError (missing symbol) | Passed | Mutated set to include `"NotAType"` → failed correctly, reverted |
| 1.2 | `test_person_title_twin_of_source_still_dropped` | Unit | 239/239 | Passed before GREEN too (regression guard, already correct) | Passed | Mutated twin-drop exemption to `not in _JUDGE_READMIT_TYPES` → failed correctly, reverted |
| 1.3 | `test_meeting_titled_person_still_dropped_by_framing_removal` | Unit | 239/239 | Passed before GREEN too (regression guard) | Passed | Mutated framing-drop exemption to `in _JUDGE_READMIT_TYPES` → failed correctly, reverted |
| 1.4 | `test_procedure_behavior_unchanged_at_all_three_sites` | Unit | 239/239 | Passed before GREEN too (regression guard) | Passed | Mutated judge conjunct to require `meeting_shaped` for Procedure too → failed correctly, reverted |
| 1.1 | `test_judge_dropped_person_with_anchor_on_meeting_source_is_readmitted` | Unit | 239/239 | AssertionError (`Jordan Ellis` absent) | Passed | Mutated `_JUDGE_READMIT_TYPES` to exclude `"Person"` → failed correctly, reverted |
| 1.5 | `test_person_without_anchor_not_readmitted` | Unit | 239/239 | Passed before GREEN too (regression guard) | Passed | Mutated `_has_participant_anchor` to `return True` → failed correctly, reverted |
| 1.6 | `test_person_with_meeting_role_anchor_is_readmitted` | Unit | 239/239 | AssertionError (`Morgan Lee` absent) | Passed | Mutated `_PARTICIPANT_ANCHOR_RE` to drop the `chair` alternative → failed correctly, reverted |
| 1.7 | `test_person_not_readmitted_from_non_meeting_source` | Unit | 239/239 | Passed before GREEN too (regression guard) | Passed | Mutated conjunct to drop `meeting_shaped and` → failed correctly, reverted |

Note: tasks 1.2/1.3/1.4/1.5/1.7 are regression/non-regression guards that were already true of the codebase before the GREEN change (their production behavior doesn't change), so their assertions pass both before and after GREEN. Their RED-ness is proven instead by the mutation step, which confirms each test genuinely exercises the guarded behavior and fails when it's broken. Tasks 1.1/1.6/1.8 are new-behavior tests and show a true pre-GREEN failure.

### Test Summary
- Total tests written: 8
- Total tests passing: 8/8
- Mutations run: 8/8, all caught (100%)
- `__pycache__` purged before every mutation run
- Every mutation reverted with the exact inverse edit

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `pytest tests/unit/extraction/test_concept.py -v` (unpiped) → `247 passed in 0.51s` (239 baseline + 8 new, 0 regressions) |
| Runtime harness command/scenario and exact result | N/A — unit-level, deterministic post-filter logic, no live model call (per tasks.md's own Suggested Work Units row for Unit 1) |
| Rollback boundary | Delete `_JUDGE_READMIT_TYPES`, `_PARTICIPANT_ANCHOR_RE`, `_has_participant_anchor`, and revert the line-2234-region conjunct back to `or c.type == _TWIN_EXEMPT_TYPE`; `_TWIN_EXEMPT_TYPE` and its two deletion call sites are untouched throughout, so no revert step touches them |

### Deviations from Design
None — implementation matches design D1/D3/D4 exactly, including the literal conjunct shape specified in the design doc.

### Issues Found
None.

### Cross-cutting confirmation (informational, not part of Phase 1 scope)
- `git diff -- src/openkos/extraction/judge.py` → zero diff (D2 confirmed for this unit)
- `git diff --stat` → 265 insertions(+), 1 deletion(-) across 2 files — well under the 500-line unit cap and the 400-line review budget
- Full `pytest tests/unit -q` (unpiped, background) → `4607 passed, 1 skipped` — no regressions anywhere in the unit suite (this is Phase 4 task 4.1's check; run here only as an extra confirmation, not claimed as Phase 4 completion)

### Workload / PR Boundary
- Mode: chained/stacked PR slice (auto-chain, stacked-to-main)
- Current work unit: Unit 1 — Additive judge re-admission set + anchor gate for Person/Organization (PR1)
- Boundary: starts from `main`, ends with a complete, independently revertible, independently testable D1/D3/D4 implementation plus its full RED→GREEN→mutation test suite
- Estimated review budget impact: ~265 changed lines, well under the 400-line budget and the 500-line unit cap

### Status
16/16 tasks in this work unit complete. Ready for verify.

---

## Work Unit 2 (PR2) — Participant Coverage Probe (D5)

Branch: `feat/668-participant-coverage-probe` (from `feat/668-judge-readmit-participants`, stacked)
Scope: tasks 2.1–2.7, 2.10 ONLY. Tasks 2.8/2.9 (the live measured baseline run against ollama and its recording in `report.md`) are explicitly OUT OF SCOPE for this apply batch — the orchestrator runs the live measurement itself afterward. Phase 3/4 untouched.

### Completed Tasks

- [x] 2.1 `ExtractionReport` gained three new additive fields in `src/openkos/extraction/concept.py`: `participant_judge_selected_titles`, `participant_readmitted_titles`, `participant_anchorless_discarded_titles` (all default `()`), computed only on the successful non-empty `judge_status == "ok"` admission branch of `extract_concept_union`. Backed by unit tests (see TDD Cycle Evidence).
- [x] 2.2 Per-meeting Person/Organization recall scoring already existed structurally in `render()`'s AFFORDANCE/VERDICT sections (via `NE_TO_OKF`/`named_entity_floor`, unconditional, pre-dating this change) — confirmed still correct and unaffected.
- [x] 2.3 Precision-side reporting added: `render_participant_section()` reports "PRECISION: N admitted `{type}` object(s), 0 annotated mentions" whenever a type is emitted but its AMI mention-count floor for that source is zero — i.e., not explained by any ground-truth mention for that source (uses the same mention-count-floor evidence class as the existing affordance section, never a per-object name/text match, consistent with the harness's documented anti-over-scoring philosophy).
- [x] 2.4 `--participants` CLI flag added to `main()`; gates `run_source(..., participants=True)` and the two new render calls (`render_participant_section`, `render_participant_baseline`). Implies `--union-judge` (the guard reads judge/re-admission fields the single-cap path never sets). No per-type sensitivity value, override, or branch anywhere in `run_type_coverage.py` — confirmed by inspection (grep for "sensitivity" returns nothing in the file).
- [x] 2.5 RED: `_self_test()` extended with a two-meeting participant fixture (`participant_a`/`participant_b`, built from `ParticipantRunReport`) asserting the flooding guard fires for meeting A (re-admitted 3 > judge-selected 1) and does NOT fire for meeting B (re-admitted 0), plus precision and baseline-readback assertions. Confirmed RED: temporarily reverted the guard condition to `if True:` and reran `--self-test` → exit 1, `SELF-TEST FAILED: B's Person re-admission (0) does not exceed judge-selection (2) -- the guard must fire exactly once, for A only`.
- [x] 2.6 GREEN: implemented `render_participant_section()` (flooding guard + precision rows), `ParticipantRunReport` dataclass, `_participant_run_report()` join helper, `render_participant_baseline()`, and `read_participant_baseline()`. `--self-test` → exit 0, "self-test OK".
- [x] 2.7 Ran `PYTHONPATH=src python evals/decision_extraction/scripts/run_type_coverage.py --self-test` — exit 0, no live model call (no `OllamaClient` constructed on the self-test path).
- [ ] 2.8 NOT RUN — out of scope for this apply batch. Ready to run as a single command: `python evals/decision_extraction/scripts/run_type_coverage.py --participants --runs <n>` (against AMI fixtures under `evals/decision_extraction/sources/`).
- [ ] 2.9 NOT RECORDED — depends on 2.8's real output; out of scope for this apply batch.
- [x] 2.10 Readback mechanism implemented and verified: `render_participant_baseline()` writes a markdown table (`| Meeting | Type | Emitted | Judge-selected | Re-admitted | Unexplained |`) following `report.md`'s existing table convention; `read_participant_baseline()` parses it back into `{(meeting, type): {...}}`. `--self-test` asserts an exact round trip: `parsed_baseline[("A","Person")]["readmitted"] == 3` and `["emitted"] == 4`, and `parsed_baseline[("B","Organization")]["unexplained"] == 1`. Real-baseline readback against a `report.md` entry is pending 2.9.

### Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/extraction/concept.py` | Modified | Added `_PARTICIPANT_TYPES` constant and three additive `ExtractionReport` fields (`participant_judge_selected_titles`, `participant_readmitted_titles`, `participant_anchorless_discarded_titles`), wired into `extract_concept_union`'s successful judge-admission branch and the final `ExtractionOutcome` construction. No other logic touched; `extract_concept` (single-cap path) and `judge.py` unchanged. |
| `tests/unit/extraction/test_concept.py` | Modified | Added 3 new unit tests: `test_participant_readmitted_reported_separately_from_judge_selected`, `test_participant_selected_by_judge_reported_in_selected_not_readmitted`, `test_anchorless_participant_reported_in_discarded_titles` |
| `evals/decision_extraction/scripts/run_type_coverage.py` | Modified | Added `ParticipantRunReport` dataclass, `SourceResult.participant_runs` field, `_participant_run_report()`, `run_source(..., participants=...)` param, `render_participant_section()`, `render_participant_baseline()`, `read_participant_baseline()`, `--participants` CLI flag, and the flooding-guard/baseline-readback fixture extension to `_self_test()` |
| `evals/decision_extraction/report.md` | Unchanged | Baseline recording (2.9) is out of scope for this batch |
| `src/openkos/extraction/judge.py` | Unchanged | D2 confirmed — `git diff` empty |

### TDD Cycle Evidence

| Task | Test | Layer | Safety Net | RED | GREEN | Mutation |
|------|------|-------|------------|-----|-------|----------|
| 2.1 | `test_participant_readmitted_reported_separately_from_judge_selected` | Unit | 247/247 baseline | AssertionError (`() == ('Jordan Ellis',)`) — confirmed by temporarily disabling the computation and rerunning | Passed | Mutated `not in selected_titles` → `in selected_titles` on the `participant_readmitted_titles` comprehension → failed correctly, reverted |
| 2.1 | `test_participant_selected_by_judge_reported_in_selected_not_readmitted` | Unit | 247/247 | AssertionError (`() == ('Jordan Ellis',)`) — same disabled-computation RED check | Passed | Mutated `in selected_titles` → `not in selected_titles` on the `participant_judge_selected_titles` comprehension → failed correctly, reverted |
| 2.1 | `test_anchorless_participant_reported_in_discarded_titles` | Unit | 247/247 | AssertionError (`() == ('Alex Rivera',)`) — same disabled-computation RED check | Passed | Mutated `c not in kept` → `c in kept` on the `participant_anchorless_discarded_titles` comprehension → failed correctly, reverted |
| 2.5/2.6 | `_self_test()` flooding-guard fixture (`participant_a`/`participant_b`) | Eval self-test | N/A (new fixture) | Exit 1 with `if True:` mutation on the flooding-guard `if` condition, and separately confirmed RED before any guard code existed (task described the fixture-before-code order; guard condition mutated back to `if True:` reproduces the same failure shape) | Exit 0, "self-test OK" | Mutated `if readmitted[okf_type] and readmitted[okf_type] > judge_selected[okf_type]:` → `if True:` → exit 1 (`B's Person re-admission (0) does not exceed judge-selection (2)...`), reverted |
| 2.3 | `_self_test()` precision assertion | Eval self-test | N/A (new fixture) | — | Exit 0 | Mutated `if emitted and not mentions:` → `if emitted and mentions:` → exit 1, reverted |
| 2.10 | `_self_test()` baseline-readback assertions | Eval self-test | N/A (new fixture) | — | Exit 0 | Mutated `"unexplained": int(unexplained)` → `int(readmitted)` in `read_participant_baseline()` → exit 1, reverted |
| — | `_self_test()` anchor-less discard total | Eval self-test | N/A (new fixture) | — | Exit 0 | Mutated `anchorless_total += run.anchorless_discarded_total` → `+= 0` → exit 1, reverted |

### Test Summary
- Total unit tests written: 3
- Total unit tests passing: 3/3 (250/250 in `test_concept.py` including the 247 PR1 baseline)
- Self-test assertions added: 7 new expectations (flooding guard fires, flooding guard fires exactly once, precision fires, precision does not false-positive, anchor-less discard count, baseline readback ×2)
- Mutations run: 7/7, all caught (100%)
- `__pycache__` purged before every mutation run
- Every mutation reverted with the exact inverse edit
- Full unit suite (unpiped): `pytest tests/unit -q` → `4610 passed, 1 skipped` (4607 baseline from PR1 + 3 new)

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `pytest tests/unit/extraction/test_concept.py -v` (unpiped) → `250 passed in 0.48s`; `PYTHONPATH=src python evals/decision_extraction/scripts/run_type_coverage.py --self-test` (unpiped) → exit 0, "self-test OK" |
| Runtime harness command/scenario and exact result | `python evals/decision_extraction/scripts/run_type_coverage.py --participants --runs <n>` against AMI fixtures — NOT run in this batch (task 2.8 explicitly out of scope); the command is verified ready (flag wired, `--self-test` path exercises the same render/report code with zero model calls) |
| Rollback boundary | Revert the diff to `evals/decision_extraction/scripts/run_type_coverage.py` (removes `--participants`, `ParticipantRunReport`, `render_participant_section`, `render_participant_baseline`, `read_participant_baseline`, and the `_self_test()` extension) and to `src/openkos/extraction/concept.py` (removes `_PARTICIPANT_TYPES` and the three new `ExtractionReport` fields plus their computation in `extract_concept_union`); PR1's `_JUDGE_READMIT_TYPES`/`_PARTICIPANT_ANCHOR_RE`/`_has_participant_anchor` are untouched by this revert |

### Deviations from Design

1. **Precision-side matching is source/floor-level, not per-object text matching.** The spec's illustrative scenario ("admits three Person objects, one of which matches no ground-truth mention") reads as per-object name matching against transcript spans. Design D5 only specifies "new `ExtractionReport` fields" (readmitted/judge-selected/anchor-less counts) as the mechanism and does not mention word-span text resolution; the harness's own module docstring explicitly argues against scoring objects against mention counts as a target/match ("Scoring emitted objects AGAINST mention counts would be a scorer that rewards over-production"). I implemented precision as: an admitted object of a type is reported as "unexplained by any ground-truth mention for that source" when that source's AMI mention-count floor for that type is exactly zero — the same affordance-floor evidence class the existing recall section already uses, just read from the other direction. This is honest (never a false claim of per-object coreference) but is a conservative undercount relative to a literal per-object match (it will not flag an unexplained object in a source that has SOME mentions of that type but not enough to cover every admitted object). Flagging this explicitly rather than silently narrowing scope; the design doesn't specify AMI word-span (`words.xml`) resolution as an expected mechanism, and building it would have added a second corpus-parsing subsystem duplicating `named_entity_floor`'s established pattern with materially higher risk (multi-file cross-referencing, `words.xml` id-range resolution) for a manual, non-shipped eval tool.
2. **`participant_anchorless_discarded_titles` is combined across Person+Organization, not split per type**, in the `ParticipantRunReport`/reporting layer. The `ExtractionReport` field itself carries titles only (as decided for task 2.1, consistent with `discarded_titles`'s existing shape), and a dropped candidate's type is not recoverable from its title alone once it never reached `outcome.objects` — attributing it to a type would require guessing. Documented in `ParticipantRunReport`'s docstring.

### Issues Found
None — no test runner or infrastructure failures. `named_entity_floor`/AMI corpus parsing untouched.

### Cross-cutting confirmation (informational, not Phase 4 scope)
- `git diff -- src/openkos/extraction/judge.py` → zero diff (D2 confirmed for this unit too)
- `git diff --stat` (this work unit only) → 407 insertions(+)/4 deletions(-) across 3 files — under the 450-line attempt cap and the 400-line review budget
- `ruff check` on all 3 touched files → "All checks passed!"
- Full `pytest tests/unit -q` (unpiped) → `4610 passed, 1 skipped` — 0 regressions from PR1's 4607 baseline

### Workload / PR Boundary
- Mode: chained/stacked PR slice (auto-chain, stacked-to-main), branch `feat/668-participant-coverage-probe` stacked on `feat/668-judge-readmit-participants` (PR1, open as #670)
- Current work unit: Unit 2 — Participant coverage probe with stub-flooding guard (PR2), tasks 2.1–2.7 and 2.10 only
- Boundary: starts from PR1's tip, ends with a complete, independently revertible D5 probe implementation (fields + flooding guard + precision reporting + baseline readback mechanism), fully self-test-verified with zero live model calls; the live baseline measurement (2.8/2.9) is a deliberately separate follow-up step
- Estimated review budget impact: ~407 changed lines, under the 450-line attempt cap and close to but under the 400-line review budget guideline for this slice alone

### Status
9/10 tasks in this work unit complete (2.1–2.7, 2.10). Tasks 2.8/2.9 intentionally deferred to the orchestrator's live-measurement step. Ready for the orchestrator to run `python evals/decision_extraction/scripts/run_type_coverage.py --participants --runs <n>` against ollama and record the baseline in `report.md`.

### Remaining Tasks (out of scope for this work unit)
- [ ] 2.8 Live measured baseline run (orchestrator-owned, one probe process at a time)
- [ ] 2.9 Record baseline in `evals/decision_extraction/report.md`
- [ ] Phase 3 (PR3, tasks 3.1–3.7) — conditional on PR2 measurement (D6)
- [ ] Phase 4 (tasks 4.1–4.4) — cross-cutting verification
