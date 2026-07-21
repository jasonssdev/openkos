# Archive Report: `embedding-vector-store` (MVP-2, Slice 2a)

**Date**: 2026-07-20  
**Change**: embedding-vector-store (Slice 2a) — Vector-Store Seam + On-Disk Scaffolding  
**Status**: ARCHIVED  
**Merged to main**: 82ab552 (squash of PR #97)

## Scope Summary

Slice 2a of the multi-slice embedding-vector-store change: sqlite-vec scaffolding + VectorStore seam ONLY (no vec0 data flow). All 28/28 implementation tasks completed, verified, and merged. Follow-ups deferred to Slice 2b (vec0 store + reindex + cache invalidation), Slice 3 (RRF fusion), Slice 4 (graph expansion), Slice 5 (two-output rule).

## Artifact Traceability

All artifacts retrieved and archived for audit trail:

| Artifact | Engram ID | Location | Status |
|----------|-----------|----------|--------|
| Proposal | #1380 | `archive/2026-07-20-embedding-vector-store/proposal.md` | Archived |
| Specification | #1381 | `archive/2026-07-20-embedding-vector-store/specs/{vector-store,doctor-command}/spec.md` | Archived & Merged to openspec/specs/ |
| Design | #1382 | `archive/2026-07-20-embedding-vector-store/design.md` | Archived |
| Tasks | #1383 | `archive/2026-07-20-embedding-vector-store/tasks.md` | Archived (28/28 complete) |
| Verification Report | #1386 | `archive/2026-07-20-embedding-vector-store/verify-report.md` | Archived (PASS WITH WARNINGS) |
| Apply Progress | #1384 | Referenced in verify-report | Verified accurate by independent verifier |

## Specs Merged to openspec/specs/

### vector-store (NEW capability)

**Status**: Created at `openspec/specs/vector-store/spec.md`  
**Source**: Delta spec (full spec for new capability)

**Requirements (7)**:
1. VectorStore Protocol Seam — fake-injectable, mirrors Embedder/LLMBackend
2. VecUnavailable Typed Error — RuntimeError subclass, mirrors FtsUnavailable
3. Guarded Extension Loader — enable_load_extension(True) → sqlite_vec.load → enable_load_extension(False)
4. Idempotent Vector Schema — CREATE VIRTUAL TABLE IF NOT EXISTS vec0 + companion table
5. On-Disk Location Via WorkspaceLayout — openkos_dir + vectors_db_path properties
6. Content Hash Helper — sha256 hexdigest for cache invalidation keys
7. No CLI Surface, No Init-Time Side Effect — module isolated, init behavior unchanged

### doctor-command (MODIFIED capability)

**Status**: Updated at `openspec/specs/doctor-command/spec.md`  
**Source**: Delta spec (MODIFIED requirements + ADDED requirement)

**Modifications**:
- "Doctor Runs And Prints All Applicable Checks": 6 checks → 7 checks (adds vector extension loadable)
- "Exit Code Reflects Critical Failures Only": Vector-extension-loadable added to informational set
- "Doctor Works Outside An Initialized Workspace": Vector check runs pre-init, independent of Ollama state

**Added**:
- "Vector-Extension-Loadable Check": Informational check (never flips exit code), runs independently, reports loadability + extension-capable remediation on failure

## Implementation & Verification Summary

### Gate Results (Independent Verification)

- `uv run pytest -q`: 1053 passed (repo-wide, matches apply-progress exactly)
- `uv run ruff check .`: All checks passed
- `uv run mypy .` (repo-wide strict): Success (sqlite_vec overrides in place)
- Coverage: 99.08% (state/vectorstore.py 100%, config.py 100%, cli/main.py 98%)
- All 28/28 implementation tasks marked complete with passing tests

### Task Completion Status

ALL IMPLEMENTATION TASKS COMPLETE (28/28 ✓):
- Phase 1: sqlite-vec dependency + mypy override ✓
- Phase 2: WorkspaceLayout paths (openkos_dir, vectors_db_path) ✓
- Phase 3: content_hash helper (sha256) ✓
- Phase 4: VecUnavailable + VectorStore Protocol + guarded loader + idempotent schema ✓
- Phase 5: probe_vec_loadable + doctor check #7 ✓
- Phase 6: docs + regression sweep ✓

### Verification Note: Test Coverage Gap (Non-Blocking)

Verify-report #1386 flagged one CRITICAL test coverage gap (non-blocking for archive):
- **Spec Requirement 4, Scenario 2** ("Companion table supports hash-keyed lookup"): The schema is correct, the `vector_meta` table exists, but there is no dedicated test that INSERTs and SELECTs from `vector_meta` by `content_hash`.
- **Risk Level**: LOW (plain SQL WHERE-clause on NOT NULL text column; implementation correct)
- **Verdict**: PASS WITH WARNINGS — test coverage gap deferred to Slice 2b

Verifier notes: All other scenarios (6/7 requirements) have passing covering tests. Assertion quality is strong (no call-through stubs, real connections/extension load, real CLI invocation). Schema and code paths are correct; only the explicit hash-lookup scenario test is missing. This is acceptable for archive given the low risk and implementation correctness.

### Review History

**Bounded 4R review** across three lineages:
- Two pristine lineages retired cleanly (scope locked, experimental)
- Final lineage (review-bc8da032af22f136) approved, 0 blockers
- Two rounds of failure-path hygiene fixes applied
- Review receipt: **ALLOW**

## Deferred Items (Non-Blocking, Moving to Slice 2b)

Per Engram #1397, the following non-blocking review residuals are deferred:

1. **(a) Multi-level parent cleanup in open_vector_store**: Currently uses `path.parent.mkdir(parents=True, exist_ok=True)`. Latent issue: should use `rmdir` only on the `.openkos` directory, not grandparent paths from `mkdir(parents=True)`. No current consumer; deferred to 2b when reindex walks the parent structure.

2. **(b) Leftover ephemeral comment at tests/unit/state/test_vectorstore.py:476**: "Review Correction 2" comment from apply-progress workflow; harmless but should be removed in 2b cleanup pass.

3. **(c) Harmless idempotent double-close of conn**: Undocumented split responsibility (context mgr + explicit close). Production code is correct (idempotent close), coverage gap only. Document or simplify in 2b.

4. **(d) db_preexisted=True protective branch untested**: Production code correct (creates no `.openkos/` if db already exists in integration test setup). Coverage gap only, deferred.

All deferred items are LOW risk and do not affect spec compliance or product behavior. Captured for Slice 2b cleanup + coverage expansion.

## Archive Contents

**Folder**: `openspec/changes/archive/2026-07-20-embedding-vector-store/`

```
archive/2026-07-20-embedding-vector-store/
├── proposal.md                                    (Engram #1380)
├── design.md                                      (Engram #1382)
├── exploration.md                                 (Engram #1376, mirrored read-only)
├── tasks.md                                       (Engram #1383, 28/28 complete)
├── specs/
│   ├── vector-store/
│   │   └── spec.md                                (Engram #1381, NEW → openspec/specs/vector-store/)
│   └── doctor-command/
│       └── spec.md                                (Engram #1381 delta, MERGED → openspec/specs/doctor-command/)
└── archive-report.md                              (this file)
```

Original change folder `openspec/changes/embedding-vector-store/` has been moved to archive (requires `git rm` of original by orchestrator).

## Merged Specs (Source of Truth Updated)

### openspec/specs/vector-store/spec.md
**Action**: CREATED  
**Purpose**: New capability for on-disk vector store scaffolding  
**Consumers**: Slice 2b (reindex), Slice 3 (answer/fusion), future slices  

### openspec/specs/doctor-command/spec.md
**Action**: UPDATED  
**Modifications**: Added vector-extension-loadable check (#7), updated all-checks scenarios from 6→7 checks, added pre-init run scenarios  
**Consumers**: CLI doctor command, CI health checks, user-facing diagnostics  

## SDD Cycle Status

**COMPLETE & ARCHIVED**

- ✓ Proposal phase (scope locked)
- ✓ Spec phase (delta specs defined)
- ✓ Design phase (architecture decided)
- ✓ Tasks phase (28 tasks enumerated)
- ✓ Apply phase (PR #97, merged at 82ab552)
- ✓ Verify phase (1053 tests, ruff/mypy clean, 99.08% coverage)
- ✓ Archive phase (specs merged, change folder archived, report filed)

**Next recommended**: None for this change. Slice 2a is complete and closed. Slice 2b (vec0 store + reindex) starts as an independent, autonomous change.

## Risk Assessment

**RISKS DURING ARCHIVE**: None — all gates passed independently.

**DEFERRED TECHNICAL DEBT**: See "Deferred Items" section above. All LOW risk, captured for 2b.

**PRODUCT RISKS**: None — no data flow wired, no consumers yet. Only scaffolding and infrastructure. Graceful degradation (VecUnavailable) handles system Python without extensions.

## Audit Trail

This archive report records:
- All artifact observation IDs (Engram #1380, #1381, #1382, #1383, #1384, #1386)
- Spec merge details (vector-store NEW, doctor-command MODIFIED+ADDED)
- Task completion (28/28)
- Verification results (1053 tests, 99.08% coverage, PASS WITH WARNINGS)
- Review gate (ALLOW, bounded 4R, 0 blockers)
- Deferred residuals (4 items, all LOW risk, captured for 2b)
- Archive folder location (2026-07-20-embedding-vector-store/)

Date archived: 2026-07-20  
Archived by: sdd-archive executor  
Project: openkos  
Change: embedding-vector-store (MVP-2, Slice 2a)
