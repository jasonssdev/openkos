# Archive Report: Rewrite inbound provenance on merge (issue #230)

**Change**: `rewrite-provenance-on-merge`
**Issue**: #230
**Status**: ARCHIVED and COMPLETE
**Archive Date**: 2026-07-29

## Executive Summary

Merge now retargets third-party `provenance:` entries to the survivor (ungated by absorbed concept type), records all rewrites in a v3 reversibility ledger, and unmerge reverses them with correct three-way precedence (`provenance > relations > links`). Two stacked PRs delivered 968 changed lines (82% code+tests, 18% docs), both merged to main at `3f26c98` (PR1) and `fb968d7` (PR2). Verification PASS: 0 CRITICAL, 0 WARNING, 12/12 spec scenarios verified. ADR-0011 flipped to Accepted. Change is closed; no follow-up work needed before ship.

## Change Overview

### Problem Addressed

`merge` retargets inbound body links and typed `relations:` to the survivor id, but never `provenance:`. Third-party concepts derived from the absorbed object keep a pointer to a non-addressable id. This causes:
- `forget --scope source` under-expands (orphaned descendants)
- `set-sensitivity` RAISE propagation skips provenance descendants (confidentiality leak, issue #230)
- Silent reference integrity violation in a knowledge engine (AGENTS.md non-negotiable)

### Solution Delivered

1. **Third-party provenance retargeting** — `merge` now runs a third scanner over the same `other_files` snapshot, retargeting every entry that names the absorbed id to the survivor id (retarget-then-dedupe, first-occurrence-wins).
2. **Scanner is ungated** — `query --save` writes arbitrary cited concept ids, not just Sources. The scanner correctly captures all provenance, regardless of absorbed object `type`.
3. **Reversible ledger v3** — `MERGE_LEDGER_SCHEMA_V3` adds `provenance_rewrites` field holding whole-file snapshots for reversal; v1 and v2 entries still decode and unmerge exactly (backward compatible).
4. **Unmerge precedence** — A file touched by all three rewrite kinds (link, relation, provenance) is restored exclusively from its `provenance_rewrites` snapshot; the snapshot guarantees byte-identity and is asserted, not assumed (T4 test).
5. **Functional proof** — After merge retargets provenance, a later `set-sensitivity` RAISE on the survivor now reaches the descendant (defect #230 fixed).

### Deliverables

| Artifact | Path | Status |
|---|---|---|
| Proposal | `openspec/changes/archive/2026-07-29-rewrite-provenance-on-merge/proposal.md` | Archived |
| Exploration | `openspec/changes/archive/2026-07-29-rewrite-provenance-on-merge/explore.md` | Archived |
| Design | `openspec/changes/archive/2026-07-29-rewrite-provenance-on-merge/design.md` | Archived |
| Tasks | `openspec/changes/archive/2026-07-29-rewrite-provenance-on-merge/tasks.md` | Archived (46/46 complete) |
| Specification | `openspec/specs/entity-resolution-merge/spec.md` | **MERGED** into main spec |
| Apply Progress | `openspec/changes/archive/2026-07-29-rewrite-provenance-on-merge/apply-progress.md` | Archived |
| Verify Report | `openspec/changes/archive/2026-07-29-rewrite-provenance-on-merge/verify-report.md` | Archived |

### Merged Artifacts

The delta spec for `entity-resolution-merge` has been merged into the canonical spec at `/Users/jasonssdev/Dev/Projects/openkos/openspec/specs/entity-resolution-merge/spec.md`. Changes include:

- **ADDED**: "Reversible Inbound-Provenance Rewiring" — new requirement with 4 scenarios
- **ADDED**: "Retargeted Provenance Reaches Later Sensitivity Propagation" — new requirement with 1 scenario
- **MODIFIED**: "Reversibility Ledger (`merged_from`)" — now includes `provenance_rewrites`, v3 schema, with `(Previously: ...)` annotation noting the v2→v3 transition
- **MODIFIED**: "Unmerge Achieves Round-Trip Parity" — now includes provenance reversal and three-way precedence rule, with `(Previously: ...)` annotation

### ADR Status

**ADR-0011** ("Third-party provenance retargets on merge; v3 reversibility ledger") flipped to **Accepted** in three places:
1. YAML frontmatter `status:` field — `/Users/jasonssdev/Dev/Projects/openkos/docs/adr/0011-provenance-retarget-on-merge.md` line 5
2. Body `**Status:**` line — line 17
3. Index row in `/Users/jasonssdev/Dev/Projects/openkos/docs/adr/README.md` line 49

## Verification Verdict

**PASS** — Per `verify-report.md` (observation #2136):
- 0 CRITICAL issues
- 0 WARNING issues (1 non-blocking SUGGESTION: apply-progress persisted to Engram only, pre-existing asymmetry)
- 12/12 spec scenarios covered by passing runtime tests
- 2565/2565 pytest pass, 97.62% branch coverage (gate 90%)
- All hard-checks independently verified against source and on-disk evidence

### Hard-Check Evidence

1. **Scanner ungated by type** — `find_inbound_provenance_rewrites` has no type filter; CLI test `test_merge_absorbing_non_source_concept_still_retargets_third_party_provenance` proves non-Source absorbed concepts still retarget (would fail if a filter were added).
2. **Functional defect #230 proof** — `test_merge_retarget_then_later_set_sensitivity_raise_reaches_descendant` sets up derived with `provenance` naming absorbed id at `private` (rank 1), merges to retarget to survivor, then raises survivor to `confidential` (rank 2) and asserts descendant reaches `confidential` via `combine_sensitivity`. Pre-fix would fail because provenance would still name the deleted id.
3. **Retarget-then-dedupe ordering** — Parametrized matrix test asserts exact ordered list equality for all spec cases, including `.md`-variant deduplication.
4. **Zero extra bundle walks** — Counting wrapper proves `rglob(bundle_dir, "*.md")` called exactly once, all three scanners read same `other_files` dict.
5. **Unmerge precedence** — Production code partitions files into provenance/relation/link sets matching design; test asserts byte-identity via on-disk file reads (not mocked snapshots).
6. **Round-trip** — Merge → unmerge byte-identical for all-three/provenance-only/relations-only/links-only files.
7. **Ledger v1/v2/v3** — Decode tests confirm v1 and v2 entries default `provenance_rewrites=[]` and unmerge exactly; encode guard rejects V1 or V2 with non-empty `provenance_rewrites`.

## Key Decisions and Rationale

### Decision A: Retargeting is correct

**Rationale**: Merge already makes the absorbed id non-addressable. `merged_from` + `unmerge` is the audit trail, not an orphaned pointer. Links and relations are already retargeted for referential integrity; leaving provenance dangling is an inconsistency, not fidelity. The `(Previously: ...)` annotations in the spec document what changed.

### Decision B: Retarget-then-dedupe, first-occurrence-wins

**Rationale**: Mirrors `build_merged_document`'s existing union rule. A naive substring replace would duplicate entries when a file already cites both ids. First-occurrence-wins preserves order and matches `apply_relation_rewrites`'s established pattern.

### Decision C: Unmerge precedence — provenance > relations > links

**Rationale**: Each rewrite kind holds a whole-file snapshot. Applying multiple reversals would clobber or fail closed. Provenance snapshot (widest coverage) wins; narrower reversals are skipped. Safe because all three scanners read the same pre-merge `other_files`, so snapshots are byte-identical (asserted by T4).

### Decision D: Third-party sensitivity restore out of scope

**Rationale**: Merge never writes third-party sensitivity (propagation is `set-sensitivity`'s exclusive concern). Lowering is gated one-way (ADR-0008/0010). Consequence: after retargeting, a later raise on the survivor now reaches these files — intended behavior, fixing the defect.

### Decision E: Two stacked PRs

**Rationale**: PR1 (primitives + ledger v3 + ADR) is self-contained and revertible. PR2 (CLI wiring + unmerge + docs) depends on PR1 but is logically separate. Both under individual 400-line budgets; combined 968 lines exceeds 800 but accepted via `exception-ok` strategy.

## Spec Modifications

### Previously annotations

All requirement revisions are marked with `(Previously: ...)` per convention #239:

1. **Reversibility Ledger** — `(Previously: v2 schema, no `provenance_rewrites` field — a provenance retarget would have had no recorded snapshot to reverse from.)`
2. **Unmerge Achieves Round-Trip Parity** — `(Previously: reversed only link and relation rewrites, relation taking precedence over link on a shared file; no provenance rewrite to reverse.)`

No requirements were removed or renamed; all existing requirements in the canonical spec remain intact.

## Recorded Follow-ups

These were deliberately out of scope but noted for future work:

1. **No `lint` check for dangling provenance** — Detection exists but is not repair. Issue #231 backfill (which walks provenance descendants) is now unblocked by this change. A dedicated `lint check_dangling_targets` provenance axis remains a follow-up.
2. **Issue #232 (set-sensitivity warning scope)** — That verb's warning scans the whole bundle rather than the invoked Source's closure. Overlaps with this change but explicitly out of scope.
3. **No change to `find_provenance_descendants` closure semantics** — Existing detect-only behavior unchanged.

## Observation IDs (Traceability)

For full audit trail and linkage to prior phases:

| Artifact | Observation ID | Engram Topic |
|---|---|---|
| Proposal | 2128 | `sdd/rewrite-provenance-on-merge/proposal` |
| Specification (Delta) | 2130 | `sdd/rewrite-provenance-on-merge/spec` |
| Design | 2131 | `sdd/rewrite-provenance-on-merge/design` |
| Tasks | 2132 | `sdd/rewrite-provenance-on-merge/tasks` |
| Verify Report | 2136 | `sdd/rewrite-provenance-on-merge/verify-report` |

## Closure Statement

The `rewrite-provenance-on-merge` SDD change is **fully archived and closed**. All 46 tasks marked complete, specification merged into canonical form, ADR-0011 accepted, verification PASS with no blockers. No stale artifacts remain in the active change directory. Issue #230 is resolved; `merge` now correctly retargets and records all inbound provenance, enabling `forget` and `set-sensitivity` propagation to work correctly.

**Ready for next change**.
