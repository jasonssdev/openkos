# Archive Report: reindex-summary-embed-failed

**Archived on**: 2026-07-22
**Status**: COMPLETE
**Commit**: f0e6e3b (PR #107)

## Change Summary

**Title**: Surface embed-failed in the reindex stdout summary

**Intent**: The `openkos reindex` command prints a summary of embedded/cache-hit/pruned/skipped counts but was omitting `embed_failed`, though ReindexReport already carried it. This change appends `{report.embed_failed} embed-failed` to the stdout summary tally, completing the factual count alongside four existing counters.

**Scope**: Minimal, display-only change:
- One production edit: append `embed-failed` counter to stdout summary f-string in `src/openkos/cli/main.py:2590-2594`
- Spec widening: "CLI Verb Is Thin Wiring" requirement + 5 scenarios updated to enumerate `embed-failed`
- Test assertions: 3 new assertions across 3 test functions to verify the new counter appears in stdout

## Delivery

**PR**: #107 (merged to main)
**Commit SHA**: f0e6e3b
**Merged at**: 2026-07-22

## Verification Report

**Status**: PASS
**Evidence**:
- **Task completeness**: 14/14 tasks complete across 4 phases (RED/GREEN/Spec-lockstep/Verify-gate)
- **Test results**: 1285/1285 tests pass (re-run independently, exit code 0)
- **Code quality**: ruff check ✅, ruff format --check ✅, mypy ✅ (all clean, exit code 0)
- **Spec compliance**: All 5 delta-spec scenarios (success/failure/zero-count/nonzero-count/distinct-streams) covered by real, passing tests
- **Change scope**: Confirmed minimal — only 3 non-planning files touched (main.py: 1 line; test file: 3 lines; spec: 6+/4- lines); well under 400-line budget
- **No regressions**: Untouched by design — ReindexReport computation, stderr notice, success gate, all verified in place and unchanged

Engram observation ID: 1550

## Spec Merge Outcome

**Status**: Merged (completed during archive)

The canonical spec at `openspec/specs/reindex-command/spec.md` required the two additional scenarios from the delta to be added:
1. "Zero embed failures still show the counter" (always-shown convention matched to `0 skipped`)
2. "Nonzero embed failures surface in both the stdout tally and the stderr notice" (distinct streams: stdout count + stderr call-to-action)

These scenarios were added to the spec during archive to complete the merge. The requirement "CLI Verb Is Thin Wiring" (lines 60-90) now fully enumerates all 5 scenarios from the delta spec, matching the implementation exactly.

Previous note in requirement (lines 68-70) updated to reflect `embed_failed` surfacing: now states that prior to this change, `embed_failed` was surfaced solely via the stderr notice, not the primary stdout tally.

## Artifacts Archived

**Source folder**: No active source folder found at `openspec/changes/reindex-summary-embed-failed/` (already removed or never persisted in repo; all artifacts present in Engram)

**Archive folder**: `openspec/changes/archive/2026-07-22-reindex-summary-embed-failed/`
- `archive-report.md` ✅ (this file)

**Canonical spec updated**: `openspec/specs/reindex-command/spec.md` ✅ (two scenarios merged)

## Engram Artifacts

All SDD artifacts for this change persisted in Engram with the following observation IDs for full traceability:

| Artifact | Type | Engram ID |
|----------|------|-----------|
| Proposal | architecture | 1545 |
| Spec Delta | architecture | 1546 |
| Design | architecture | 1547 |
| Tasks | architecture | 1548 |
| Apply-Progress | architecture | 1549 |
| Verify-Report | architecture | 1550 |
| Archive-Report | architecture | (this save) |

## SDD Cycle Complete

**Phase 1 (Proposal)**: ✅ Intent and scope defined; minimal change identified
**Phase 2 (Spec)**: ✅ Delta spec with 5 scenarios covering zero/nonzero/success/failure paths
**Phase 3 (Design)**: ✅ Thin-wiring architecture confirmed; display-only f-string edit
**Phase 4 (Tasks)**: ✅ 14/14 tasks completed in 4 phases (RED/GREEN/Spec/Verify)
**Phase 5 (Apply)**: ✅ Code merged (f0e6e3b, PR #107), all tests pass
**Phase 6 (Verify)**: ✅ PASS — 1285 tests, ruff/mypy clean, review-reliability 0 findings, spec scenarios covered
**Phase 7 (Archive)**: ✅ Specs merged, archive folder created, cycle closed

The change is now complete and closed. All follow-up work is deferred until the next SDD cycle.
