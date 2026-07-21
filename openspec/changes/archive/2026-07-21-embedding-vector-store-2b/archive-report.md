# Archive Report: Embedding Vector Store — Slice 2b

**Archived**: 2026-07-21
**Status**: COMPLETE — All 39 tasks verified, zero CRITICAL issues, merged to main @ 4482e0f (PR #98)
**Store Mode**: hybrid

## SDD Artifacts (Engram Topic Keys)

| Artifact | Topic Key | Observation ID | Status |
|----------|-----------|----------------|--------|
| Proposal | `sdd/embedding-vector-store-2b/proposal` | 1402 | VERIFIED |
| Specification | `sdd/embedding-vector-store-2b/spec` | 1403 | VERIFIED |
| Design | `sdd/embedding-vector-store-2b/design` | 1404 | VERIFIED |
| Tasks | `sdd/embedding-vector-store-2b/tasks` | 1405 | VERIFIED ✓ (39/39 complete) |
| Verify Report | `sdd/embedding-vector-store-2b/verify-report` | 1407 | VERIFIED ✓ (1102 passed, mypy clean) |

## Scope Delivered

### Vector Store Capability (Modified)
- `state/vectorstore.py`: added `upsert(concept_id, embedding, content_hash)` and `query(embedding, k)` data flow
- `VectorStore` Protocol extended additively with `meta_hashes()` and `prune(concept_id)` cache accessors
- 4 deferred 2a follow-ups closed with tests:
  - (a) Single-level cleanup invariant on failed open + test coverage
  - (b) Stale test label renamed (line 476 in test_vectorstore.py)
  - (c) Idempotent double-close guarantee + test + docstring
  - (d) Pre-existing database survival on failed reopen + test

### Reindex Command Capability (New)
- `state/reindex.py`: bundle walker, content-hash cache gate, vec0 serialization, Embedder seam integration
- `openkos reindex` CLI verb: thin wiring with workspace + config + error ladder
- `--force` flag to bypass cache and re-embed all discovered documents
- Prune logic for deleted documents from vector_meta

## Merged Specifications

### Into `openspec/specs/vector-store/spec.md`
- Added 6 new requirements (upsert, query, Protocol extension, cleanup invariant, idempotent close, preexisting DB survival)
- 8 scenarios covering all data flow paths

### Created `openspec/specs/reindex-command/spec.md`
- Full spec for the new reindex command (7 requirements, 12 scenarios)
- Covers thin CLI wiring, bundle walk, content-hash caching, prune, --force, error handling, isolation guarantee

## Verification & Quality Gates

**Test Results**
- `uv run pytest -q`: 1102 passed, 0 failed (Engram #1407)
- `uv run mypy .`: Success on 93 source files, zero issues
- `uv run ruff check`: All checks passed
- `uv run ruff format --check`: All 93 files formatted correctly

**Task Completeness**
- 39/39 tasks marked complete in `openspec/changes/embedding-vector-store-2b/tasks.md`
- No unchecked implementation tasks in the final artifact
- All spec requirements covered by passing integration and unit tests

**Review Gate** (Bounded 4R Review)
- Lineage: `review-b6f880b4a0c1e6ea`
- Tier: HIGH (security/auth/permissions/data loss risk assessment)
- Status: APPROVED, 0 blockers
- Lens selection: 4R sweep (review-risk, review-resilience, review-readability, review-reliability)

## Deferred Follow-Ups (Engram #1407 — Non-Blocking WARNINGs for Slice 3)

These issues were discovered in the 2b bounded review but are deferred to Slice 3 (Hybrid Retrieval) as they do not block merge and can be addressed before retrieval wiring.

### (1) Reindex Prune — Unreadable Subdirectory Handling
**Issue**: reindex prune treats absence-from-walk as deletion. `okf._iter_docs` uses `rglob()` which silently drops unreadable subdirectories. A transient scan failure (permission error on a directory) can cause valid vectors to be incorrectly pruned.
**Impact**: Data loss on a rebuildable cache (low severity for vectors.db as an embeddings cache, becomes real once Slice 3 wires retrieval for production queries).
**Recommendation**: Distinguish walk failure from actual deletion before pruning. Track unreadable directories and either skip pruning on any failure or emit a warning.

### (2) Reindex Error Handling — Unguarded SQLite Errors
**Issue**: reindex CLI except ladder doesn't catch generic `sqlite3.Error`. Disk-full, db-locked, or db-corrupt conditions will emit a raw traceback to stderr instead of a friendly message.
**Impact**: Poor UX on disk/lock failures; potential information leak if traceback contains file paths.
**Recommendation**: Add `sqlite3.Error` clause in the except ladder. Consider enabling WAL mode and busy_timeout on the write path for better resilience.

### (3) Reindex Orchestrator — Unbatched Embedder Calls
**Issue**: reindex issues a single unchunked `embedder.embed([...])` for the entire changed/new batch. On a large backfill (e.g., 10k new documents), this creates a full-batch blast radius.
**Impact**: Memory pressure on large backfills; all-or-nothing failure mode on mid-batch network loss.
**Recommendation**: Chunk the embedding batch (e.g., 100 docs per call) and implement partial-progress checkpointing so a restart can resume from the last successful chunk.

### (4) Reindex — Dead Code Path
**Issue**: `state/reindex.py` contains an unreachable raw `bytes.decode(...) except UnicodeDecodeError` branch. `okf._iter_docs` already classifies decode errors as read_error earlier in the pipeline. The two unit tests (`test_reindex_skips_unreadable_doc_and_reports_it`) intending to cover this path exercise the earlier error path, not this dead branch.
**Impact**: Dead code slows debugging and increases maintenance burden.
**Recommendation**: Remove the dead branch or refactor the error classification so this path is reachable and the tests hit it.

## Files Changed for Archive

| File | Location | Status |
|------|----------|--------|
| proposal.md | `openspec/changes/archive/2026-07-21-embedding-vector-store-2b/proposal.md` | ✓ Copied |
| design.md | `openspec/changes/archive/2026-07-21-embedding-vector-store-2b/design.md` | ✓ Copied |
| tasks.md | `openspec/changes/archive/2026-07-21-embedding-vector-store-2b/tasks.md` | ✓ Copied |
| specs/vector-store/spec.md | `openspec/changes/archive/2026-07-21-embedding-vector-store-2b/specs/vector-store/spec.md` | ✓ Copied (delta) |
| specs/reindex-command/spec.md | `openspec/changes/archive/2026-07-21-embedding-vector-store-2b/specs/reindex-command/spec.md` | ✓ Copied |
| Main spec: vector-store | `openspec/specs/vector-store/spec.md` | ✓ MERGED (6 ADDED req, 8 scenarios) |
| Main spec: reindex-command | `openspec/specs/reindex-command/spec.md` | ✓ CREATED (7 requirements, 12 scenarios) |

## Closure Summary

**Change Name**: embedding-vector-store-2b
**Delivered To**: main @ 4482e0f (PR #98)
**Merged Specs**: vector-store (delta merged), reindex-command (new spec created)
**Status**: ARCHIVED — ready for Slice 3 (Hybrid Retrieval / answer.py / query wiring)

The SDD cycle for Slice 2b is complete. All implementation tasks verified, all spec requirements met, all quality gates green. The change has been archived with full traceability of proposal, spec, design, tasks, and verification artifacts.

---
**Archive Date**: 2026-07-21
**Store Mode**: hybrid (filesystem + Engram)
