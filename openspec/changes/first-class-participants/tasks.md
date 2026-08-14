# Tasks: First-class Person/Organization participants

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~300 (PR1) + ~250 (PR2) + ~350 (PR3, conditional) = ~900 total |
| 400-line budget risk | Medium (each PR individually under 400; total exceeds 400) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (D1/D3/D4 + tests) → PR 2 (D5 probe + baseline) → PR 3 (conditional, D6) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Additive judge re-admission set + anchor gate for Person/Organization | PR 1 | `pytest tests/unit/extraction/test_concept.py -k judge_readmit or participant_anchor or twin or framing -v` | N/A — unit-level, no live model call | Delete `_JUDGE_READMIT_TYPES`/`_PARTICIPANT_ANCHOR_RE`/`_has_participant_anchor` and revert the line-2234 conjunct; `_TWIN_EXEMPT_TYPE` untouched |
| 2 | Participant recall + flooding-guard probe with recorded baseline | PR 2 | `python evals/decision_extraction/scripts/run_type_coverage.py --self-test` | `python evals/decision_extraction/scripts/run_type_coverage.py --participants --runs <n>` against AMI fixtures | Revert `run_type_coverage.py` diff and `report.md` baseline entry; no production code touched |
| 3 (conditional) | Scoped phase-2 capture pass, gated on PR2 measurement | PR 3 | `pytest tests/unit/extraction/test_concept.py -k reask_participant -v` | `python evals/decision_extraction/scripts/run_type_coverage.py --participants --runs <n>` re-measurement | Remove the phase-2 trigger/call site; PR1/PR2 remain valid independent of PR3 |

## Phase 1: PR1 — Judge Re-Admission Set (D1/D3/D4)

- [x] 1.1 RED: In `tests/unit/extraction/test_concept.py`, add `test_judge_dropped_person_with_anchor_on_meeting_source_is_readmitted` — Person candidate, meeting-shaped `source_title`, has a role/affiliation anchor, dropped by judge; assert it appears in the final union set. (spec: extraction-union-judge, "Judge-dropped Person on a meeting-shaped source is re-admitted")
- [x] 1.2 RED: Add `test_person_title_twin_of_source_still_dropped` — Person whose title twins the source title; assert dropped by `_is_droppable_source_title_twin`, unaffected by `_JUDGE_READMIT_TYPES`. (spec: "A Person title-twin of the source is still dropped" — D1 regression guard)
- [x] 1.3 RED: Add `test_meeting_titled_person_still_dropped_by_framing_removal` — Person titled after the meeting itself; assert dropped by `_drop_framing_objects`. (spec: "A meeting-titled Person is still dropped by framing removal" — D1 regression guard)
- [x] 1.4 RED: Add `test_procedure_behavior_unchanged_at_all_three_sites` — Procedure candidate exercised through twin-drop, framing-drop, and judge re-admission; assert byte-identical outcome to current behavior. (spec: "Procedure behavior is unchanged at all three sites")
- [x] 1.5 RED: Add `test_person_without_anchor_not_readmitted` — Person with only a name, no role/affiliation/relation, dropped by judge; assert stays dropped. (spec: "Stub Rejection at Judge Re-Admission", "Name-only candidate is not re-admitted")
- [x] 1.6 RED: Add `test_person_with_meeting_role_anchor_is_readmitted` — Person with a role like "chair" alongside name; assert re-admitted. (spec: "Candidate with a meeting-role anchor is re-admitted")
- [x] 1.7 RED: Add `test_person_not_readmitted_from_non_meeting_source` — Person dropped by judge on a non-meeting-shaped (technical-article) source; assert NOT re-admitted. (spec: "Judge Re-Admission Scoped to Meeting-Shaped Sources", "Non-meeting source does not re-admit a participant")
- [x] 1.8 RED: Extend `test_twin_exempt_type_is_in_the_vocabulary` (or add `test_judge_readmit_types_subset_of_classifiable_types`) asserting `_JUDGE_READMIT_TYPES ⊆ CLASSIFIABLE_TYPES`. (design D1 regression guard)
- [x] 1.9 Run all 1.1–1.8 tests and confirm RED (failing for the right reason — missing symbols/behavior, not import errors).
- [x] 1.10 GREEN: In `src/openkos/extraction/concept.py`, add `_JUDGE_READMIT_TYPES: Final = frozenset({"Procedure", "Person", "Organization"})` near `_TWIN_EXEMPT_TYPE`; leave `_TWIN_EXEMPT_TYPE = "Procedure"` byte-identical and its two deletion call sites (`_is_droppable_source_title_twin`, `_drop_framing_objects`) untouched.
- [x] 1.11 GREEN: Add `_PARTICIPANT_ANCHOR_RE` in `concept.py` — English + Spanish role/affiliation/relation lexicon only (mirrors `_MEETING_SHAPED_TITLE_RE`'s #522 two-language limit); document the two-language limit in a comment at the constant.
- [x] 1.12 GREEN: Add `_has_participant_anchor(result) -> bool` in `concept.py` reading `description`+`body` against `_PARTICIPANT_ANCHOR_RE`.
- [x] 1.13 GREEN: At judge re-admission (line ~2234), compose the conjunct exactly per design D4: `c.type in _JUDGE_READMIT_TYPES and (c.type == _TWIN_EXEMPT_TYPE or (meeting_shaped and _has_participant_anchor(c)))`, reusing existing `meeting_shaped = _MEETING_SHAPED_TITLE_RE.search(source_title)` in scope.
- [x] 1.14 Run tests 1.1–1.8 again and confirm GREEN.
- [x] 1.15 Mutate each new test's exact target assertion line (purge `__pycache__` first) to confirm each test fails when its target behavior is broken; restore after confirming.
- [x] 1.16 Run full `pytest tests/unit/extraction/test_concept.py` unpiped to confirm no regressions elsewhere in the file.

## Phase 2: PR2 — Participant Coverage Probe (D5)

- [x] 2.1 In `evals/decision_extraction/scripts/run_type_coverage.py`, add `ExtractionReport` fields for per-run `Person`/`Organization` emitted counts, re-admitted-vs-judge-selected counts, and anchor-less-discard counts.
- [x] 2.2 Add per-meeting Person/Organization recall scoring against AMI `PERSON`/`ORGANIZATION` ground truth, following the existing explained-vs-unexplained-absence shape. (spec: participant-coverage-probe, "Recall is reported per type")
- [x] 2.3 Add precision-side reporting: count of admitted Person/Organization objects unexplained by any ground-truth mention for that source, alongside recall. (spec: "Unexplained participant objects are counted")
- [x] 2.4 Add `--participants` CLI flag gating the new scoring/reporting path; ensure no per-type sensitivity value or branch is introduced anywhere in the report or measured path. (spec: "No Per-Type Sensitivity Behavior in Probe Scope")
- [x] 2.5 RED: Extend `_self_test()` with a two-meeting fixture that asserts the stub-flooding guard fires (re-admitted count vs. judge-selected count diverges as expected) before the scoring code exists.
- [x] 2.6 GREEN: Implement the flooding-guard computation/report row so `_self_test()` passes.
- [x] 2.7 Run `python evals/decision_extraction/scripts/run_type_coverage.py --self-test` and confirm pass (no live model call).
- [ ] 2.8 Run `python evals/decision_extraction/scripts/run_type_coverage.py --participants --runs <n>` against AMI fixtures to produce the real measured baseline. (OUT OF SCOPE for this apply batch — orchestrator runs the live measurement afterward)
- [ ] 2.9 Record the baseline in `evals/decision_extraction/report.md` following the existing recording convention for other types. (spec: "Recorded Baseline for Comparison") (OUT OF SCOPE for this apply batch — depends on 2.8's real output)
- [x] 2.10 Read the recorded baseline back and confirm it is present and comparable by a subsequent run (spec scenario check) — readback mechanism (`render_participant_baseline`/`read_participant_baseline`) implemented and round-trip-verified via `--self-test`; real-baseline readback pending 2.9.

## Phase 3: PR3 — Conditional Phase-2 Scoped Pass (D6, BLOCKED ON PR2 MEASUREMENT)

**This phase is not actionable until PR2's baseline (task 2.8/2.9) is evaluated against the trigger. Do not start PR3 tasks before that evaluation.**

Trigger (from design D6 / spec "Probe Result Gates Phase-2 Scoped Pass"): open ONLY if the PR2 baseline shows zero or near-zero `Person`/`Organization` generation across ≥2 AMI meetings and ≥3 runs. If PR2 shows non-zero recall improvement from phase 1a alone, these tasks MUST NOT be executed on this change's evidence — stop after PR2 and record that phase 2 was not justified.

- [x] 3.1 Trigger confirmed MET: `evals/decision_extraction/report.md`'s 2026-08-13 baseline section (qwen3:8b, 3 runs × 4 AMI sources, PR1's re-admission live) shows ZERO Person/Organization generation everywhere — re-admitted 0, anchor-less discards 0 — satisfying the spec's "zero generation on ≥2 meetings across ≥3 runs" gate.
- [x] 3.2 RED: Added `test_participant_capture_pass_joins_candidates_before_judge_on_meeting_source` in `tests/unit/extraction/test_concept.py` — mirrors `_reask_for_further_subjects`/`_add_reask_subjects` (#584) shape, gated on `_MEETING_SHAPED_TITLE_RE`, asserts a scoped second call joins a Person candidate before the judge on a meeting-shaped source and the judge's own reply must select it to be kept.
- [x] 3.3 RED: Added `test_participant_capture_pass_does_not_fire_on_non_meeting_source` — asserts the scoped pass spends zero extra calls on a non-meeting-shaped (technical-article) source.
- [x] 3.4 RED: Added `test_participant_capture_pass_leaves_system_prompt_byte_identical` — hash-pins `_SYSTEM_PROMPT` (mirrors `CONTROL_PROMPT_SHA`'s precedent) and asserts the new prompt constant is a distinct value.
- [x] 3.5 GREEN: Implemented `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` (new, separate constant), `_build_participant_capture_messages`, `_capture_further_participants`, and `_add_participant_capture` in `concept.py`; wired into `extract_concept_union` right after `_add_reask_subjects`, joining `merged` BEFORE `judge_input`/the judge call, on both the unchunked and chunked union paths (single call site after the branches converge). `ExtractionReport` gained additive `participant_capture_runs`/`participant_capture_added_titles` fields (both defaulted). `judge.py` untouched (confirmed via `git diff`).
- [x] 3.6 Mutation-verified all 3 new tests' exact target lines (`__pycache__` purged before each run; every mutation reverted via the exact inverse edit) — see TDD Cycle Evidence in apply-progress.md.
- [x] 3.7 DONE by the orchestrator (2026-08-14, recorded in `evals/decision_extraction/report.md`): the `--participants --runs 3` re-run reproduced the zero baseline exactly — a NULL EXPERIMENT, because the harness's `path.stem` source titles never match `_MEETING_SHAPED_TITLE_RE`, so the pass never fired. An isolated gate-fired probe (truthful meeting title, 2 runs) validated the mechanism: 4/4 anchored Person objects (AMI's A/B/C/D speakers), deterministic, zero stub flooding. The detection gap (title-only gate blind to code-titled transcripts) is filed as a follow-up issue with both measurements as evidence.

## Phase 4: Cross-Cutting Verification

- [x] 4.1 Full unit suite unpiped on the PR3 branch (all three slices present): `4614 passed, 1 skipped in 176.31s` — zero regressions.
- [x] 4.2 Confirm D7 finding stands: no changes needed to `_scrub_entry_snapshots` or `_reconcile_merged_survivor` — both remain type-blind; no task required, verification only.
- [x] 4.3 Confirm no `judge.py` diff exists anywhere in the change (D2 — judge.py unchanged).
- [x] 4.4 Confirm no per-type sensitivity code landed anywhere in `concept.py` or `run_type_coverage.py` (proposal out-of-scope / #669 boundary).
