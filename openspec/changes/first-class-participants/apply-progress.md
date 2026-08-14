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

### Remaining Tasks (out of scope for this work unit)
- [ ] Phase 2 (PR2, tasks 2.1–2.10) — participant coverage probe (D5)
- [ ] Phase 3 (PR3, tasks 3.1–3.7) — conditional on PR2 measurement (D6)
- [ ] Phase 4 (tasks 4.1–4.4) — cross-cutting verification

### Workload / PR Boundary
- Mode: chained/stacked PR slice (auto-chain, stacked-to-main)
- Current work unit: Unit 1 — Additive judge re-admission set + anchor gate for Person/Organization (PR1)
- Boundary: starts from `main`, ends with a complete, independently revertible, independently testable D1/D3/D4 implementation plus its full RED→GREEN→mutation test suite
- Estimated review budget impact: ~265 changed lines, well under the 400-line budget and the 500-line unit cap

### Status
16/16 tasks in this work unit complete. Ready for verify.
