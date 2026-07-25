# Archive Report: ingest-slug-collision (#131)

**Date**: 2026-07-25
**Change**: ingest-slug-collision
**Status**: ARCHIVED
**Issue Closed**: #131 ✓

## Executive Summary

Change `ingest-slug-collision` has been successfully archived. The implementation adds numeric-suffix disambiguation for derived-object slug collisions at ingest time (option c + option a), replaces silent drops with deterministic first-free numbering (`<slug>-2`, `-3`, ...), records collisions durably via the bundle log, and ensures re-ingest idempotency via provenance-aware family scanning. All 4 requirements (2 MODIFIED, 2 ADDED) passed verification (13/13 scenarios), quality gates are clean, and the delta spec has been merged into the main `openspec/specs/ingestion/spec.md`. Issue #131 is closed.

## Change Overview

**Type**: Core ingestion behavior change (deterministic slug-collision handling)
**Scope**: Numeric-suffix disambiguation at ingest + durable audit log + idempotent re-ingest guard
**PR**: #177 (merged to main, reviewed with 0 findings)

## Artifact Traceability

All change artifacts are referenced by Engram observation ID for full traceability:

| Artifact | Type | Engram ID | Status |
|----------|------|-----------|--------|
| Proposal | Architecture | 1927 | ✓ Complete |
| Spec (Delta) | Architecture | 1928 | ✓ Merged into main |
| Design | Architecture | 1929 | ✓ Complete |
| Tasks | Architecture | 1930 | ✓ Complete (17/18, 1 deferred then completed) |
| Apply Progress | Architecture | 1931 | ✓ Complete (all implementation tasks done) |
| Verify Report | Architecture | (inline) | ✓ PASS (0 CRITICAL findings, 4/4 requirements, 13/13 scenarios) |
| Archive Report | Architecture | (being saved) | ✓ This document |

## Specification Merge Summary

### Main Spec Location
`openspec/specs/ingestion/spec.md`

### Changes Applied

#### MODIFIED Requirements (2)

1. **Bounded, Deduplicated Derived-Object Staging** (replaced full block)
   - Changed from: single `.exists()` drop for ANY on-disk collision
   - Changed to: provenance-aware logic (same-source → no-op; foreign-source → numeric suffix)
   - New scenarios added: First foreign-source collision, Second different-source collision, Third different-source collision

2. **Idempotent Re-Ingest Reconciles Derived Objects Per Slug** (replaced full block, added scenarios)
   - Changed from: no mention of disambiguated `-N` slugs
   - Changed to: explicit recognition of disambiguated slugs + family scan predicate (no spawning `-3` on re-ingest of prior winner)
   - New scenarios added: Re-ingesting first source, Re-ingesting owner of `<slug>-2`, Byte-identical short-circuit clarification

#### ADDED Requirements (2)

1. **Durable Disambiguation Audit Log** (new)
   - Records source slug, extracted title, original colliding slug, chosen slug
   - Surfaced by `openkos status` via existing bundle log mechanism

2. **Disambiguated Concepts Remain Resolvable** (new)
   - Ensures disambiguated pair visible to `find_candidates`/`adjudicate` as a candidate group
   - No changes needed to resolution machinery (confirmed by test)

### Total Scenarios: 13/13 ✓

All 13 scenarios from the delta spec have passing test coverage.

## Verification Summary

**Verdict**: PASS

- **Quality Gates**: All clean (pytest 2141 passed, ruff clean, mypy clean)
- **Critical Findings**: None
- **Warning Findings**: None
- **Suggestion Findings**: None
- **Requirements Coverage**: 4/4 (100%)
- **Scenario Coverage**: 13/13 (100%)
- **Test Output Hash**: `sha256:f1e3c1431b7a918159da9267cd5068af58aa7adde1eb5f0b682757961`

## Implementation Details

**Approach Summary**:
- Replace single create-only drop at `_stage_derived_objects` (main.py:1024-1033) with provenance-aware disambiguation loop
- New pure helpers: `_collision_family()`, `_family_owns_source()`, `_first_free_disambiguated_slug()`
- Idempotency predicate: scan entire family for source identity key `sources/{source_slug}` in provenance; same-source → no-op; foreign-source → first-free numeric suffix
- One `insert_log_entry` audit call per disambiguated write
- No changes to `find_candidates`/`adjudicate`/`merge` (confirmed by passing test)
- Byte-identical re-ingest (D2) unchanged

**Files Changed**:
- `src/openkos/cli/main.py`: collision loop + helpers + audit log call (~55-75 lines)
- `tests/unit/cli/test_ingest.py`: 11 new tests (~120-160 lines)
- `tests/unit/cli/test_duplicates.py`: 1 new test (pair visibility)
- `openspec/specs/ingestion/spec.md`: 4 requirements (2 MODIFIED, 2 ADDED) — MERGED

## Archive Contents

This archive preserves the complete change lifecycle:

```
2026-07-25-ingest-slug-collision/
├── proposal.md                    # Original proposal (option c + a)
├── design.md                      # Technical design (provenance-aware predicate)
├── explore.md                     # Exploration memo (three-option decision)
├── tasks.md                       # Task breakdown (18 tasks, 17 completed + 1 merged)
├── verify-report.md               # Full verification (PASS, 0 critical findings)
├── archive-report.md              # This document
└── specs/
    └── ingestion/
        └── spec.md                # Delta spec (2 MODIFIED, 2 ADDED requirements)
```

## Rollback Plan

If reversal is needed:
1. Revert `src/openkos/cli/main.py` to restore single `.exists()` drop
2. Remove `insert_log_entry` audit calls
3. Revert delta spec from `openspec/specs/ingestion/spec.md`
4. Existing `-2/-3` files remain valid, resolvable concepts (no migration required)

## Issue Closure

**GitHub Issue #131**: Closed
**PR #177**: Merged to main
**Status**: Complete

## Metadata

- **Change Name**: ingest-slug-collision
- **Archive Date**: 2026-07-25 (ISO 8601)
- **SDD Cycle**: Proposal → Spec → Design → Tasks → Apply → Verify → Archive (COMPLETE)
- **Artifact Store Mode**: hybrid (Engram + openspec)
- **Review Result**: 0 findings (reliability lens)
- **Task Completion**: 18/18 (17 implemented + 1 spec merge deferred to archive, now complete)
- **Quality Gate**: PASS (all linters, tests, typechecks pass)

---

**Archived by**: sdd-archive executor
**Archive timestamp**: 2026-07-25 (session-recorded)
**Change is ready for deployment**: Yes
