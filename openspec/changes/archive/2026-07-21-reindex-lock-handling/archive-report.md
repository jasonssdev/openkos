# Archive Report: Reindex Lock-Contention Handling

**Date**: 2026-07-21  
**Change**: reindex-lock-handling (MVP-2 follow-up)  
**Status**: Archived and Closed  
**Repository**: openkos  
**Branch**: main (d5615cc merged)  

## SDD Artifact Traceability

All SDD phase artifacts have been successfully archived. Observation IDs recorded for full traceability:

| Artifact | Observation ID | Type | Status |
|----------|---|------|--------|
| Proposal | #1498 | architecture | Complete |
| Spec Deltas | #1500 | architecture | Complete |
| Design | #1502 | architecture | Complete |
| Tasks | #1504 | architecture | Complete (22/22 tasks marked complete) |
| Verify Report | #1508 | architecture | Complete (PASS: 1264 tests, mypy/ruff clean) |
| Archive Report | *pending* | architecture | *this artifact* |

## Work Summary

**Scope**: `openkos reindex` now maps SQLite lock-contention errors (SQLITE_BUSY/SQLITE_LOCKED) from all three on-disk stores (vectors.db, fts.db, graph.db) to clean exit-1 with a uniform retry message, instead of raw tracebacks. Lock discrimination uses errorcode-based predicate, not message substring, ensuring version-safety on Python 3.13+.

**Delivered**: PR #105 (d5615cc merged to main on 2026-07-21)
- Single-PR slice, strict TDD
- 1264 tests passed, mypy/ruff clean
- 4 production files modified, test suite extended
- Native 4R review: PASS (0 blockers; 1 non-blocking SUGGESTION about deduping lock-exit sequence, left as optional follow-up)

**Non-Goals Preserved**:
- No retry/backoff logic added (busy_timeout=5000ms is the SQLite-level gate)
- No busy_timeout configuration change
- No modification to query/reader degrade paths
- No cross-process mutex or explicit BEGIN IMMEDIATE for vectors.db

## Spec Merge Summary

### reindex-command (MODIFIED)

**Requirement Updated**: "Error Ladder Mirrors `query`"

**Change**: Extended the existing requirement to include lock-contention handling:
- Added lock-contention `sqlite3.OperationalError` catch at ANY write surface (store open, upsert_many/commit, BEGIN IMMEDIATE)
- Discriminated by `exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)`, not by message substring
- All three stores (vectors, FTS, graph) emit SAME uniform lock-contention message and exit 1
- Non-lock `OperationalError` is re-raised unchanged (keeping existing generic handling intact)
- Added 6 new scenarios covering locked vectors.db, locked fts.db, locked graph.db, non-lock error re-raise, and query command degrade-behavior regression guarantee

**Status**: Merged into `openspec/specs/reindex-command/spec.md`. All prior requirements preserved; lock requirement expanded with new scenarios.

### fts-state (ADDED)

**Requirement Added**: "`CREATE VIRTUAL TABLE` Failure Is Discriminated By Errorcode"

**New Requirement**: Specifies that `_populate_docs_table` MUST discriminate CREATE VIRTUAL TABLE failures by errorcode:
- Genuine fts5-module-absence errors (errorcode NOT BUSY/LOCKED) raise `FtsUnavailable` (unchanged behavior)
- Lock-contention errors (SQLITE_BUSY/SQLITE_LOCKED) propagate as raw `sqlite3.OperationalError` so the caller's lock handler can catch them
- Prevents fts5-locked errors from being mislabeled as "fts5 unavailable"
- Added 2 scenarios: fts5-unavailable regression (unchanged), lock-at-CREATE propagates raw (new)

**Status**: Appended to `openspec/specs/fts-state/spec.md`. All prior requirements preserved; new requirement documenting previously-unspecified CREATE behavior added.

## Archive Contents

Verified present in `openspec/changes/archive/2026-07-21-reindex-lock-handling/`:

- [x] `proposal.md` — Intent, scope, approach, risks, rollback plan
- [x] `design.md` — Architecture decisions, data flow, file changes, testing strategy
- [x] `tasks.md` — 22 implementation tasks, all marked `[x]` complete (5 phases: predicate, fts.py, ladder-1, ladder-2, integration/regression)
- [x] `specs/reindex-command/spec.md` — Delta for MODIFIED requirement
- [x] `specs/fts-state/spec.md` — Delta for ADDED requirement

All implementation tasks verified complete via `git diff 34c083b..HEAD`:
- state/derived.py: +33 lines (is_lock_contention predicate)
- state/fts.py: +9 lines (errorcode-discriminated CREATE catch)
- cli/main.py: +53 lines (ladder-1 lock catch + message, ladder-2 message branch)
- tests: +42, +59, +42, +165 lines (conftest helper, test_derived, test_fts, test_reindex_cmd)

## Source of Truth Updated

| File | Updated | Details |
|------|---------|---------|
| `openspec/specs/reindex-command/spec.md` | Yes | "Error Ladder Mirrors `query`" requirement expanded with lock-contention handling + 6 scenarios |
| `openspec/specs/fts-state/spec.md` | Yes | "`CREATE VIRTUAL TABLE` Failure Is Discriminated By Errorcode" requirement appended |

## Task Completion Verification

All 22 tasks in the persisted tasks artifact marked `[x]`:
- 5 tasks in Phase 1 (lock predicate + test helper): COMPLETE
- 4 tasks in Phase 2 (fts.py CREATE catch): COMPLETE
- 6 tasks in Phase 3 (ladder-1 vectors/fts): COMPLETE
- 4 tasks in Phase 4 (ladder-2 graph): COMPLETE
- 3 tasks in Phase 5 (integration/regression): COMPLETE

Verification report confirms every `[x]` traces to real code + passing test. No stale checkboxes.

## Quality Gates Passed

From verify-report (Observation #1508):

- **Test Suite**: `uv run pytest -q` → 1264 passed (exact match)
- **Type Check**: `uv run mypy .` → No issues found in 102 source files
- **Code Formatting**: `uv run ruff check .` → All checks passed; `uv run ruff format --check .` → 102 files already formatted
- **Lock-Specific Tests**: 12 lock-related tests isolated and passed independently
- **Spec/Design Conformance**: All 5 critical claims scrutinized (is_lock_contention safety, fts.py CREATE discrimination, ladder-1/ladder-2 message uniformity, non-goals preservation) — VERIFIED
- **Review**: Native 4R bounded review — PASS (0 CRITICAL, 0 WARNING, 0 SUGGESTION; 1 non-blocking SUGGESTION about optional deduping left for follow-up)

## Risks and Mitigation

**Risk Assessment**: Low

- **Message consistency**: Mitigated by single `_LOCK_CONTENTION_MSG` constant at module level, referenced by both ladders via grep-verification
- **Errorcode reliability**: Mitigated by RED test verifying sqlite_errorcode assignability on Python 3.13; real-lock fallback for older versions
- **Lock mislabeling**: Mitigated by explicit tests for both fts5-unavailable (stays FtsUnavailable) and lock-at-CREATE (propagates raw)
- **Reader-path regression**: Mitigated by verified non-modification of _open_*_or_degrade paths; existing tests pass unmodified

## State at Archive

| Item | State |
|------|-------|
| Main branch | main @ d5615cc (PR #105 merged) |
| CI Gate | Green (1264 tests, mypy/ruff clean) |
| Review Gate | Approved (4R: PASS, 0 blockers) |
| Spec Merge | Complete |
| Archive Folder | Created at `openspec/changes/archive/2026-07-21-reindex-lock-handling/` |
| Original Folder | Should be removed by orchestrator (openspec/changes/reindex-lock-handling/) |
| SDD Cycle | Complete and Closed |

## Notes

This is the **LAST accumulated MVP-2 follow-up**, completing the reindex error-handling harmonization across all three on-disk stores. The change is production-ready, fully specified, tested, and verified.

The predicate-based lock discrimination pattern is now available in `state/derived.py` for reuse by future lock-contention handling elsewhere in the codebase.

## Archive Signature

- Archived by: `sdd-archive` sub-agent
- Date: 2026-07-21
- Project: openkos
- Mode: hybrid (specs merged in main; folder archived in openspec/changes/archive/)
- Artifact Store: Engram + OpenSpec filesystem
