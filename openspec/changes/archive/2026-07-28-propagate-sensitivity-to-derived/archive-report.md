# Archive Report: propagate-sensitivity-to-derived (#219)

**Change**: propagate-sensitivity-to-derived (GitHub issue #219)
**Archived**: 2026-07-28
**Status**: Closed — both PRs merged and integrated into main at `a65faa2`

## Executive Summary

Issue #219 addressed a critical gap: the ingestion spec claimed derived objects inherit their Source's `sensitivity`, but no code backed this claim. The change implements real creation-time and set-time propagation, with raise-only semantics to prevent silent declassification. Two PRs were merged over the 400-line budget via stacked-to-main chaining; the native 4R review caught two real defects in PR 2, both fixed in the correction commit that shipped in PR #227. The change is production-ready: 2397 tests pass, coverage 97.52%, CI green on Python 3.12/3.13/3.14, and ADR-0009 is now Accepted.

## Deliverables Summary

| Artifact | Artifact ID | Location |
|---|---|---|
| Proposal | 2071 | sdd/propagate-sensitivity-to-derived/proposal (Engram) |
| Specification (delta) | 2073 | sdd/propagate-sensitivity-to-derived/spec (Engram) |
| Design | 2074 | sdd/propagate-sensitivity-to-derived/design (Engram) |
| Tasks | 2075 | sdd/propagate-sensitivity-to-derived/tasks (Engram) |
| Verification Report | 2077 | sdd/propagate-sensitivity-to-derived/verify-report (Engram) |
| Archive Report | (this document) | sdd/propagate-sensitivity-to-derived/archive-report (Engram, hybrid mode) |

Filesystem copies in hybrid mode: `openspec/changes/archive/2026-07-28-propagate-sensitivity-to-derived/`

## Change Implementation Summary

### Scope Delivered

**Phase 1 (PR #226, merged as `94552c0`)**: Real creation-time inheritance
- `_stage_derived_objects` parameter split into `workspace_floor` (config default, gates extract check per Req 4) and `stamp_sensitivity` (read-back Source value)
- Ingest now reads the built Source document's own resolved `sensitivity` back via `okf.load_frontmatter` before passing it to derived object staging
- Ingestion spec requirement updated to back the inheritance claim with a real read
- 2 new RED tests (`test_derived_object_inherits_source_document_value_not_config`, `test_extract_gate_still_reads_workspace_floor`) distinguish real inheritance from shared-constant coincidence
- 105 tests pass in focused file; full suite 2385 passed; 97.52% branch coverage

**Phase 2 (PR #227, merged as `a65faa2`)**: Set-time raise-only propagation
- `set_sensitivity_cmd` gains a Source-typed branch that resolves provenance descendants via `find_provenance_descendants` and raises each via `okf.combine_sensitivity`
- Preview and success message list every staged descendant raise
- Unresolvable provenance entries warn and are excluded; no abort of the Source's own write
- Phase B write order: descendants first, then target, then log.md, then one autocommit (fail-closed on partial failure)
- ADR-0009 created (status now Accepted at archive time per repo convention); ADR-0008 left unchanged (partial supersession only)
- Sensitivity-config spec delta applied
- 11 new/inverted tests covering all 8 requested behavioral invariants
- Full suite 2396 passed; coverage 97.52%; all spec scenarios (7/7) and behavioral invariants (8/8) proven

### Review Findings (Native 4R)

The native 4R review on PR #227 (verification-report preliminary version, before archive) discovered two real defects in the code as submitted. Both were fixed in a correction commit that shipped inside #227 (before main merge):

**Defect 1: `test_descendant_already_higher_is_not_lowered` was vacuous**
- **What**: The test targeted a Source and descendant already at the same (or higher) level, so the idempotence short-circuit returned early and never ran the descendant-propagation logic.
- **Why it mattered**: The test was claimed as proof that a descendant already above the Source's target level stays untouched, but it actually proved nothing.
- **Fix**: The correction commit (shipped in #227) changed the test to start the workspace at `public` so that `public -> private` is a genuine raise that actually reaches the descendant branch. The test now proves the invariant correctly.
- **Evidence**: Verify-report confirms both versions of the test; the fixed version passes and is load-bearing.

**Defect 2: Propagation was not gated on direction**
- **What**: The `--allow-downgrade` guard prevented lowering the named concept, but a lower-level run could still **raise** a descendant sitting below the new lower level. The guard was only `metadata.get("type") == "Source"`, with no direction check.
- **Why it mattered**: ADR-0009's design and the spec both require raise-only propagation. A downgrade should not cascade to descendants at all.
- **Fix**: The correction commit added a direction guard: `if direction == "raise":` before the descendant-propagation block. Now descendants are only touched on a raise; a downgrade run's write set stays to exactly the named concept, matching the design and ADR-0009's explicit rejection of "cascading downgrades."
- **Evidence**: `test_lowering_source_never_lowers_derived` now passes; the fix also makes the combined monotonic rule fall out for free (combine on a lower returns the descendant's existing value, so empty write set with zero direction special-casing).

**Issue resolution**: Both defects were identified during review, fixed in the correction commit, and all tests pass with the fixes in place. The verify-report's earlier claim about `test_descendant_already_higher_is_not_lowered` proving the invariant is superseded by the corrected test in the merged code.

### Specs Synced to Main (Hybrid Mode)

Delta specs were already applied to main by the merged PRs. Archive verification confirms:

| Spec | Applied location | Delta vs. main | Status |
|---|---|---|---|
| ingestion/spec.md | openspec/specs/ingestion/spec.md:414-443 | "Derived Object Provenance and Sensitivity Inheritance" requirement and 2 scenarios — verbatim match | ✅ Verified |
| sensitivity-config/spec.md | openspec/specs/sensitivity-config/spec.md:208-289 | "Scope Is Exactly One Named Concept" (modified) and "Raise-Only Propagation to Provenance Descendants" (added) — verbatim match | ✅ Verified |

No re-application needed; delta files preserved in archive for traceability.

### ADR-0009 Status Update

ADR-0009 ("Source sensitivity propagates to provenance descendants, raise-only") was created during implementation and is now **Accepted** at archive time per repository convention:
- Status flipped from Proposed to Accepted in `docs/adr/0009-source-sensitivity-propagation.md` frontmatter and document header
- README index row updated from Proposed to Accepted
- ADR-0008 left completely unedited; ADR-0009 narrows only ADR-0008's scope sentence, following the same partial-supersession precedent ADR-0008 established with ADR-0003

## Gate Results

**Task Completion Gate**: PASS
- All 27 implementation tasks (1.1-1.8, 2.1-2.19) checked `[x]` in tasks.md (verified in archive)
- No stale unchecked tasks for completed work

**Native Review Receipt Gate**: PASS
- PR #226 (Phase 1): Gentle-ai review 4R verdict PASS, 0 CRITICAL, 0 WARNING, 1 SUGGESTION (pre-existing)
- PR #227 (Phase 2): Gentle-ai review 4R verdict PASS WITH WARNINGS, 0 CRITICAL, 1 WARNING (dangling-provenance scan scope undocumented), 1 SUGGESTION (pre-existing test_relate.py flake)
- Defects found: 2 (both fixed in correction commit before merge)

**Final Test Gates** (as reported in verify-report; CI green on merge):
- `uv run pytest`: 2397 passed
- `uv run ruff check .`: All checks passed
- `uv run ruff format --check .`: 143 files already formatted
- `uv run mypy .`: Success, no issues
- Coverage: 97.52% branch coverage (gate: 90%)
- CI: Green on Python 3.12, 3.13, 3.14; build quality and GitGuardian clean on both PRs

## Archive Contents

```
openspec/changes/archive/2026-07-28-propagate-sensitivity-to-derived/
├── proposal.md                          (from Engram #2071)
├── design.md                            (from Engram #2074)
├── tasks.md                             (from Engram #2075, all 27 tasks checked)
├── verify-report.md                     (from Engram #2077, combined PR 1 + PR 2)
├── archive-report.md                    (this document, from Engram #archive-report)
└── specs/
    ├── ingestion/spec.md                (delta — applied to main)
    └── sensitivity-config/spec.md       (delta — applied to main)
```

No `apply-progress.md` persisted in archive; verify-report serves as the implementation and verification record.

## Known Follow-Ups and Deferred Work

The following items are intentionally out of scope for this change and documented for future work. They were deferred by design and reaffirmed during review:

1. **Dangling-provenance warning scan is bundle-wide rather than scoped to the invoked Source's own closure.** The full-bundle scan is by design (matching design.md's literal instruction), but no test explicitly pins this scope decision for a multi-Source bundle. A follow-up test is recommended to assert the bundle-wide scope intentionally, preventing a future refactor from narrowing it in a way that hides real dangling references outside the current root's descendants.

2. **Phase B write-failure message does not enumerate which files already landed.** The existing "failed while writing" handler is reused and extended to name paths, but the message does not enumerate the descendants already successfully written when the Source's own write fails. A future revision could improve the UX by listing them.

3. **The two preparation `except` blocks in `set_sensitivity_cmd` share a byte-identical stderr literal.** Both error messages use the same text (e.g., "Cannot read concept..."). Factoring into a shared constant is a low-risk refactor deferred for a separate change.

4. **The descendant-scan block is inline in `set_sensitivity_cmd`, which doubled the function size.** `_stage_derived_objects` is the file's own precedent for extracting complex staging logic. The descendant walk could be extracted into a helper for readability, deferred to a separate refactor change.

5. **At ingest, the stamp reads the freshly built Source document.** A re-ingest after a manual raise would stamp new derived objects with the config default rather than the on-disk Source's raised value. This means idempotent re-ingest does not fix stale derived objects from an earlier ingest; the source must be re-raised via `set-sensitivity` after being manually updated. Tracked as expected behavior; bulk backfill is deferred.

6. **Bulk backfill of existing bundles: deliberately out of scope by user decision.** No verb exists to reconcile sensitivity across all Sources in an existing bundle in one operation. Existing bundles are corrected at the next `set-sensitivity` on their Source; rollback is always available.

7. **Repairing merge-orphaned `provenance` (merging away a Source leaves derived objects' `provenance` dangling; neither `bundle/links.py` nor `bundle/relations.py` rewrites `provenance:`): deliberately out of scope.** The merge link-integrity defect is independent and tracked as a separate follow-up issue. Here we only fail closed: unresolvable provenance warns, never lowers.

8. **A pre-existing, unrelated flake in `tests/unit/cli/test_relate.py` observed under `--cov`.** One transient failure; passed on 2 other full-suite runs and in isolation. Not a regression from this change; recommended for separate investigation ticket on test isolation under coverage instrumentation.

## Final Authority

This archive report reflects the FINAL state of the change at close per the SDD Final-State Authority hierarchy:

- **Native review authority**: Gentle-ai 4R verdicts and receipts for both PRs (most authoritative)
- **Task completion**: All 27 tasks checked in persisted tasks.md
- **Explicit final-state facts from launch prompt**: Both PRs merged at `a65faa2` (main), defects found and fixed, test counts and CI gates reported
- **Intermediate snapshots**: verify-report, apply-progress serve as implementation/verification records, not terminal state

The verify-report's claims about task completion and test counts are confirmed at archive time; the defect fixes are recorded here with their exact corrections. The archive is the terminal SDD cycle record.

## Rollback Plan

Revert the change branch. Propagation only ever raised values, so no data is lost. Already-raised derived objects keep their higher (fail-closed) sensitivity and can be lowered deliberately via `set-sensitivity`'s gated downgrade path. No schema changes, no database migrations, no cross-system dependencies introduced.

## Related ADRs and Specs

- **ADR-0003** (Sensitivity high-water-mark ordering and fail-closed combine) — re-used unchanged; `combine_sensitivity` is the basis for descendant raises
- **ADR-0008** (Human sensitivity override, and where lowering needs a flag) — partially superseded in scope by ADR-0009; ADR-0008 left Accepted and unchanged
- **ADR-0009** (Source sensitivity propagates to provenance descendants, raise-only) — now Accepted (status flipped at archive per repo convention)
- `openspec/specs/ingestion/spec.md` — inheritance requirement now backed by code (Phase 1)
- `openspec/specs/sensitivity-config/spec.md` — scope requirement narrowed to include raise-only propagation (Phase 2)

## Metadata

- **Change name**: propagate-sensitivity-to-derived
- **Issue**: #219 (closed)
- **PRs**: #226 (merged as `94552c0`), #227 (merged as `a65faa2`)
- **Branch**: main (both PRs integrated)
- **Archived**: 2026-07-28
- **Engram artifacts**: All 6 persisted with topic_key `sdd/propagate-sensitivity-to-derived/{artifact}`
- **Hybrid mode**: Both Engram and filesystem (openspec/changes/archive/) copies complete
- **Review budget**: 800 lines (session), 400 lines default; delivered 743 lines across 2 chained PRs
- **Delivery strategy**: auto-chain, stacked-to-main (approved in tasks phase)

## Sign-Off

Archive complete. All artifacts persisted, all tasks marked complete, all review findings recorded with fixes confirmed. The change is production-ready and the SDD cycle is closed.
