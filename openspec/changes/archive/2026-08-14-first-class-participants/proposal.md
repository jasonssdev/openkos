# Proposal: First-class Person/Organization participants

## Intent

Issue #668: participants must be deliberately captured objects with stable identity, own relations, and list/query presence. The type registry already admits `Person`/`Organization` (`model/types.py`), yet production emits **zero**: 12 AMI runs produced no Person/Organization candidate against annotated ground truth (17/4 mentions in `TS3005a`). The dominant defect is generation, not judge suppression; #643's judge drop is a smaller second defect. Success = measured non-zero participant recall on AMI without regressing the tuned general prompt.

## Scope

### In Scope

- Phase 1a: generalize `_TWIN_EXEMPT_TYPE` (single-constant equality, `concept.py::extract_concept_union`) to a frozenset covering `Person`/`Organization`, reusing the proven D5 re-admission path.
- Phase 1b: extend `evals/decision_extraction/scripts/run_type_coverage.py` to score Person/Organization recall and explained-vs-unexplained absence; establish the measured baseline gate.
- Phase 2 (gated): only if zero-generation persists after phase 1, add a scoped capture pass triggered on transcript/meeting-shaped sources (mirroring `_MEETING_SHAPED_TITLE_RE`), keeping general-prompt restraint intact elsewhere.

### Out of Scope

- **Sensitivity-default-by-type — split to issue #669** (owner ruling, 2026-08-13). New infrastructure with no existing seam; needs its own design pass.
- Retroactive backfill of participants over already-ingested sources (no existing verb re-runs extraction).
- Any change to `judge.py` — it stays type-blind by design (D2).
- Silent or automatic person-name merge.

## Capabilities

### New Capabilities

- `participant-coverage-probe`: measured Person/Organization recall gate over the AMI corpus, reported per type and per run.

### Modified Capabilities

- `extraction-union-judge`: type exemption from twin-drop/judge re-admission becomes a set including `Person`/`Organization`, not only `Procedure`.

## Approach

Exploration's Approach 3 (hybrid, sequenced, measure-first): ship the deterministic re-admission plus the probe, measure, then invest in prompt/pass work only if the measurement demands it. Measure-first is project rule (#613/#622).

Identity resolution, when reached, must be a dedicated conservative person-name predicate (false merges cost more than recall), surfaced through the `suggest-relations --apply` per-item consent walk (#560/#483). `resolution/similarity.py` token containment is unsafe for names; `normalize-names` (#474) is a false lead (Unicode NFC only).

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/extraction/concept.py` | Modified | Exemption frozenset; phase-2 scoped pass |
| `evals/decision_extraction/scripts/run_type_coverage.py` | Modified | Participant recall scoring |
| `src/openkos/extraction/judge.py` | Unchanged | Stays type-blind (D2) |
| `src/openkos/model/types.py` | Unchanged | Already correct |
| `src/openkos/resolution/similarity.py` | Deferred | Person-aware companion, not naive reuse |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Prompt edits regress unrelated extraction (#380/#561/#563) | High | Phase 2 gated behind measurement; isolated harness run before merge |
| Re-admission floods bundles with Person stubs | Med | Probe reports precision-side counts, not recall only |
| False person-name merges (`_MIN_TOPIC_TOKENS` history) | Med | Identity deferred; conservative predicate + per-item consent |
| Forget scrub (#602) / reconcile (#645/#667) untraced for participants | Med | Trace before design; no type branching found so far |
| Scope creep from #669 | Med | Explicitly excluded here |

## Rollback Plan

Phase 1a reverts to the single-constant `_TWIN_EXEMPT_TYPE` (one-line revert, no data migration; already-written participant objects remain valid registry types). Phase 1b probe is eval-only and non-production. Phase 2, if built, is trigger-gated and disabled by removing the trigger.

## Dependencies

- AMI fixtures and annotations already present in `evals/decision_extraction/`.
- Issue #669 for sensitivity-by-type (independent).

## Delivery Shape

Auto-chain, 2500-line review budget. Expect chained PRs: (1) exemption generalization + tests, (2) probe extension + measured baseline, (3) conditional phase-2 pass. Slice 3 does not open unless slice 2's measurement justifies it.

## Success Criteria

- [ ] `run_type_coverage.py` reports Person/Organization recall as a first-class metric with a recorded baseline.
- [ ] Judge/twin re-admission retains Person/Organization candidates, proved by a scenario mirroring the `Procedure` exemption test.
- [ ] Measured Person/Organization emission on AMI is non-zero, or phase 2 is opened with the measurement as its evidence.
- [ ] No regression in existing extraction harness metrics.
- [ ] No per-type sensitivity behavior lands in this change.
