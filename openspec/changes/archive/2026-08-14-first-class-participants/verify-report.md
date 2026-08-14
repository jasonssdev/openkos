# Verify Report: first-class-participants

**Status**: pass-with-warnings (0 CRITICAL, 2 WARNING, 1 SUGGESTION)
**Verified against**: main HEAD `2b99b1d` (all three slices merged: PR #670 `e8ec17c`, PR #671 `b6c6c2d`, PR #672 `2b99b1d`)
**Suite**: `4614 passed, 1 skipped in 169.03s (0:02:49)` (unpiped)

## Scenario → evidence mapping

| Spec | Requirement/Scenario | Evidence |
|---|---|---|
| extraction-union-judge | Judge-dropped Person re-admitted (meeting+anchor) | `test_judge_dropped_person_with_anchor_on_meeting_source_is_readmitted` PASS |
| extraction-union-judge | Person title-twin still dropped | `test_person_title_twin_of_source_still_dropped` PASS |
| extraction-union-judge | Meeting-titled Person still dropped by framing removal | `test_meeting_titled_person_still_dropped_by_framing_removal` PASS |
| extraction-union-judge | Procedure unchanged at all 3 sites | `test_procedure_behavior_unchanged_at_all_three_sites` PASS |
| extraction-union-judge | Name-only candidate not re-admitted | `test_person_without_anchor_not_readmitted` PASS |
| extraction-union-judge | Meeting-role anchor re-admitted | `test_person_with_meeting_role_anchor_is_readmitted` PASS |
| extraction-union-judge | Non-meeting source not re-admitted | `test_person_not_readmitted_from_non_meeting_source` PASS |
| participant-coverage-probe | Recall reported per type | `NE_TO_OKF`/`named_entity_floor` path; `--self-test` shows distinct Person/Organization recall |
| participant-coverage-probe | Unexplained objects counted | `render_participant_section` PRECISION rows (floor-level — see WARNING 1) |
| participant-coverage-probe | Baseline recorded/readable | `report.md` 2026-08-13 section + `--self-test` round-trip |
| participant-coverage-probe | Probe gates phase-2 | `report.md` 2026-08-13: zero generation, 2 meetings × 3 runs, recorded justification for PR3 |
| participant-coverage-probe | No per-type sensitivity | grep: zero hits in `concept.py`/`run_type_coverage.py` |

## Design coherence

D1 deletion-site byte-identity verified via function-body diff against pre-change commit `e70a4c0`; D2 `judge.py` zero diff over the whole change; D3/D4 conjunct at `concept.py:2537-2543` matches the design literally; D6 single post-branch join before the judge on both paths; D7 scrub/reconcile spot-checked type-blind; #669 boundary intact. No drift.

## Issues

- **WARNING 1**: precision-side reporting is source/floor-level, not per-object as the spec's illustrative scenario literally reads. Disclosed in apply-progress.md with rationale (the harness's own anti-over-scoring philosophy); self-test covers the implemented shape.
- **WARNING 2**: the participant machinery is measurably inert on the AMI corpus itself (`path.stem` titles never fire `_MEETING_SHAPED_TITLE_RE`). Disclosed, measured (null experiment + gate-fired probe in report.md 2026-08-14), and tracked as issue #673 (P2). Unit-level spec scenarios pass (they use meeting-shaped titles directly).
- **SUGGESTION**: apply-progress.md task 2.10 note said "pending 2.9" after 2.9 was recorded — cosmetic, for archive cleanup.

## Verdict

Ready for archive. Orchestrator-executed tasks 2.8/2.9/3.7 independently confirmed in report.md by date and content. mypy and ruff clean on all touched files.
