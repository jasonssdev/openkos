# Archive Report: suggest-relations-provenance (Closes #135)

**Date Archived**: 2026-07-25
**Status**: ARCHIVED
**Change**: suggest-relations-provenance
**GitHub Issue**: #135 (Closed)
**PR**: #179 (Merged)

## Summary

The `suggest-relations-provenance` change has been fully implemented, verified (PASS, 3/3 requirements, 14/14 scenarios), reviewed via full 4R (Risk/Reliability clean; Resilience 1 finding fixed with regression test), and archived. The change introduces provenance-mirror edge synthesis at graph-projection read time to automatically type edges that mirror frontmatter `provenance:` entries as `derived_from`, eliminating redundant LLM calls for deterministic fact derivations.

## Change Scope

**Title**: Type Provenance-Mirror Edges As `derived_from` At Projection Time

**Type**: Enhancement (graph analysis optimization)

**Capabilities Modified**:
- `graph-projection`: synthesize `relation_type="derived_from"` for untyped body-link edges whose target is a member of the source document's `provenance:` frontmatter list
- `llm-edge-production`: exclude provenance-mirror edges from suggestion candidates (now typed, not NULL)
- `contradiction-detection`: exclude `derived_from`-typed edges from contradiction candidate generation

## Delta Specs Merged

Three delta specs have been merged into the main specification tree:

| Domain | Requirement | Key Change |
|--------|-------------|-----------|
| `graph-projection` | Edge `relation_type` Populated From Frontmatter `relations:` And Provenance-Mirror Synthesis | Added synthesis of `derived_from` for provenance-mirror edges at projection read time |
| `llm-edge-production` | Read-Only Suggestion Of Relation Types For Untyped Links | Clarified exclusion of provenance-mirror edges (now typed) from suggestion candidates |
| `contradiction-detection` | Candidate Generation From Typed Graph Edges, Deduped | Added explicit exclusion of `derived_from`-typed edges from contradiction candidate pairs |

**Main Spec Files Updated**:
- `/Users/jasonssdev/Dev/Projects/openkos/openspec/specs/graph-projection/spec.md`
- `/Users/jasonssdev/Dev/Projects/openkos/openspec/specs/llm-edge-production/spec.md`
- `/Users/jasonssdev/Dev/Projects/openkos/openspec/specs/contradiction-detection/spec.md`

## Verification Results

**Verdict**: PASS

| Metric | Value |
|--------|-------|
| Requirements Implemented | 3/3 (100%) |
| Scenarios Passing | 14/14 (100%) |
| Test Suite | 2156 passed, 0 failed, exit 0 |
| Quality Gate | All checks passed (ruff, mypy) |
| Tasks Complete | 26/26 (100%) |
| Changed Lines | 524 insertions + 17 deletions (well under 400-line budget) |

**Key Compliance**:
- Graph projection correctly synthesizes `relation_type="derived_from"` for provenance-mirror edges via exact-id-match set membership on `provenance:` frontmatter
- Suggest-relations naturally excludes provenance-mirror edges (automatic, no code change to `edge_typing.py`)
- Contradiction detection properly guards `derived_from` edges from candidate generation (type-based, applies regardless of edge origin)
- Projection replaces (never duplicates) the edge row; edge count unchanged
- Malformed provenance frontmatter degrades gracefully without crashing

## Review Findings

**4R Review Results**:
- **Risk Lens**: Clean — no security, permission, data loss, or architectural issues
- **Reliability Lens**: Clean — all tests pass independently, TDD compliance verified
- **Resilience Lens**: 1 WARNING finding discovered and fixed
  - Issue: `_pair_relation_types` mislabeled contradiction pairs containing both genuine and provenance-mirror edges
  - Fix: Prefer genuine (non-`derived_from`) typed edges for relation labeling
  - Regression Test: `test_find_contradictions_relation_label_prefers_genuine_type_over_derived_from` added and passing
- **Readability Lens**: 2 docstring fixes applied for accuracy
  - `sqlite_graph.py`: Updated function docstring to reflect provenance-mirror synthesis
  - `analysis.py`: Corrected `to_digraph` docstring re: `relation_type` population

**Post-Review Quality**: Full suite 2157 passed after all fixes (original 2156 + 1 new regression test)

## Implementation Details

**Changed Files** (from PR #179):
- `src/openkos/graph/sqlite_graph.py`: Synthesize `derived_from` for provenance-mirror edges
- `src/openkos/resolution/contradiction.py`: Guard against `derived_from` in candidate pairs
- `tests/unit/graph/test_sqlite_graph.py`: Provenance-mirror synthesis tests
- `tests/unit/resolution/test_edge_typing.py`: Candidate exclusion tests
- `tests/unit/resolution/test_contradiction.py`: Guard verification tests (including regression test)
- `tests/unit/cli/test_suggest_relations.py`: CLI integration tests
- `tests/unit/graph/test_analysis.py`: Non-regression test for edge count preservation

**Rollback Boundary**:
- Revert `sqlite_graph.py` and `contradiction.py` hunks
- Projection is a read-only derived cache rebuilt per run
- No migration required; next reindex restores prior `None` typing

## Traceability

**Engram Artifacts** (all complete and verified):
- Proposal (ID 1936): Intent, scope, approach, risks, success criteria
- Spec (ID 1937): THREE delta specs with all 14 scenarios
- Design (ID 1938): Technical approach, architecture decisions, downstream audit
- Tasks (ID 1939): 26 TDD phases (RED/GREEN), all complete
- Apply-Progress (ID 1940): Implementation with 3 post-4R-review fixes
- Verify-Report (ID 1941): PASS verdict with full spec compliance matrix
- Archive-Report (ID from this save): Final closure record

**GitHub References**:
- Issue: #135 (closed)
- PR: #179 (merged to main)
- Commit: Merged commit in main branch

## Archive Contents

All original artifacts preserved in `/Users/jasonssdev/Dev/Projects/openkos/openspec/changes/archive/2026-07-25-suggest-relations-provenance/`:

```
.
├── proposal.md
├── design.md
├── tasks.md
├── verify-report.md
├── explore.md
├── specs/
│   ├── graph-projection/spec.md
│   ├── llm-edge-production/spec.md
│   └── contradiction-detection/spec.md
└── archive-report.md (this file)
```

## SDD Cycle Completion

The `suggest-relations-provenance` change has completed the full SDD lifecycle:

1. ✅ **Propose** (ID 1936): Intent and scope defined, risks identified, approach chosen
2. ✅ **Spec** (ID 1937): Three delta specs written with 14 scenarios
3. ✅ **Design** (ID 1938): Technical decisions made, downstream audit completed
4. ✅ **Tasks** (ID 1939): 26 implementation tasks scheduled (TDD Red/Green/Regression/QA)
5. ✅ **Apply** (ID 1940): All tasks completed + 3 post-4R fixes, 2157 tests passing
6. ✅ **Verify** (ID 1941): 3/3 requirements, 14/14 scenarios, PASS verdict
7. ✅ **Archive** (today): Change closed, main specs synced, artifacts archived

The change is ready for release on the main branch.

## Accepted Consequences

As noted in the proposal: on today's ingest-only bundles, `suggest-relations` now returns **zero candidates** (every body-link edge is a provenance-mirror edge, now typed as `derived_from`). This is intentional and honest — there are no genuine concept↔concept untyped edges to suggest until a separate follow-up change (issue #131 track) supplies extraction-LLM or mining candidate edges. The verb becomes meaningful once that work lands.

## Next Steps

No further work required for this change. Deployment to production via normal merge pipeline.

If concept↔concept edge candidates are desired, file a new SDD change linked to #131 (stable concept IDs) to define and implement candidate generation from external sources.

---

**Archived by**: SDD Archive Phase (automated)
**Archive Date**: 2026-07-25
**Status**: Complete
