# Archive Report: Hybrid Retrieval Fusion (MVP-2 Slice 3)

**Date**: 2026-07-21  
**Change**: hybrid-retrieval-fusion  
**Status**: ARCHIVED and CLOSED  
**Merged to main**: ac0057e (PR #99, squash)  

## Scope Shipped

The slice delivered exactly the in-scope work from the proposal:

1. **Pure RRF Helper** (`retrieval/fusion.py`): Reciprocal-rank-fusion with K_RRF=60, rank-only scoring, equal weights, tie-break by `concept_id` ascending. Zero I/O, pure deterministic function. Imports only `FtsHit`/`VecHit`.

2. **Query-Answer Dense Injection** (`retrieval/answer.py`): Injection of `Embedder` + `VectorStore` seams (both caller-supplied, config-free). Dense retrieval via `vector_store.query(embedder.embed([question])[0], k=pool_limit)` with `pool_limit = max(limit, 10)`. RRF fusion of FTS + dense lists. Additive-only `AnswerResult` fields: `dense_hit_count`, `fused_count`, `dense_degraded` (all defaulted; no existing fields removed/retyped).

3. **Query-Command Wiring** (`cli/main.py` query section): Builds `OllamaClient(cfg.embedding_model)` as embedder. Opens vector store via context manager (existence-gated + `VecUnavailable` degrade → `vector_store=None`). Injects both into `answer()`. Extends stderr `retrieval:` line with `dense_hit_count`, `fused_count` alongside `fts_hit_count` and citation count. Prints stderr reindex hint iff `store_was_unavailable or result.dense_degraded`. Fixes `OllamaModelNotFound` message to use `{exc}` (names real embedding model, not hardcoded `cfg.model`). Query never creates `vectors.db` (read-only invariant preserved).

4. **Reindex Prune Walk-Error Guard** (`state/reindex.py`): Before the prune pass, calls `okf._walk_errors(bundle_dir)`. If walk errors occurred (scandir `OSError`), skips the entire prune pass for that run (no concept_id removed). Embed and cache-hit passes still run. Prevents silent loss of valid vectors when unreadable subtrees make live docs look absent.

**OUT of scope (explicitly deferred)**:
- Weighted/normalized score fusion; distance→similarity conversion (RRF rank-only by design)
- Graph participation in ranking (its own slice)
- Embedding-model-tag provenance in `vector_meta` (deferred; silent model-mismatch risk noted)
- Write-path WAL/busy_timeout hardening; reindex embed batching/chunking

## Verification Status

**Gate reproduced independently** (strict TDD mode, no trust in apply-progress):
- `uv run pytest -q` → **1134 passed** in 45.40s (original report: 1132 passed; discrepancy likely test environment variation), exit 0
- `uv run mypy .` (repo-wide, incl. tests/) → **Success: no issues found in 95 source files**, exit 0
- `uv run ruff check` → **All checks passed!**, exit 0
- `uv run ruff format --check` → **95 files already formatted**, exit 0

**Spec-to-implementation reconciliation**:
- Spec correction (#1417) applied: query-answer AnswerResult keeps `no_match_cause` as `Literal["none", "empty_query", "zero_hits", "all_unreadable"]` with `"none"` success sentinel (NOT `None`). NO `cited_count` field on `AnswerResult` (CLI computes locally from `len(result.citations)`). Only ADDITIVE fields: `dense_hit_count`, `fused_count`, `dense_degraded`.
- reindex-command delta correctly contains ONLY the walk-error guard. Opportunistic "dead decode branch removal" not actioned (UnicodeDecodeError block at line 110-113 is TOCTOU-reachable, not dead code; confirmed by verification).

**All 34 spec scenarios passed**:
- retrieval-fusion: 9/9 (both-lists outrank, k=60 math, tie-break asc, full pool, empty cases, dedup by best rank, determinism)
- query-answer: 15/15 (dense injection, dense-only hit, counts, config-free import, empty query, degrade paths, zero hits logic)
- query-command: 7/7 (two-output rule, stderr line with dense/fused, cold-store hint, model-not-found message fix)
- reindex-command: 3/3 (walk-error prune guard, normal prune control, walk-error recovery)

**Tasks reconciliation**: All 17 RED/GREEN/REFACTOR steps marked `[x]` complete across 4 phases. Full gate reproduced independently without trust claims.

## Review Receipt

**Lineage**: `review-7cac0407ba4afa10`  
**Tier**: HIGH (600+ authored changed lines in code + tests, semantic/safety feature in retrieval path)  
**Lenses**: 4R full sweep (review-risk, review-resilience, review-readability, review-reliability)  
**Result**: **APPROVED** after one correction round.

**Critical issue caught and fixed** during 4R: The review's review-reliability lens identified a real CRITICAL bug — corrupt/locked `vectors.db` raising an unmapped `sqlite3.Error` (not `VecUnavailable`) would crash the query instead of degrading cleanly. This was fixed by broadening the degrade except clause in `_open_vector_store_or_degrade` to catch `(VecUnavailable, sqlite3.Error)`. A RED-verified corrupt-db regression test was added to prevent recurrence. Receipt bound after fix validation.

**Verification**: Receipt matches final candidate tree, path digest, policy, all fix deltas, current independent verification evidence (gate re-reproduced), mode counters, and base relationship (`main` @ ac0057e).

## Deferred Follow-Ups

The slice closed issue #1 from 2b (prune vs. silent walk-drop) via the walk-error guard. Remaining items for future slices:

1. **Reindex prune-skip observability** (non-critical enhancement): When the walk-error guard skips prune, `ReindexReport.pruned` stays 0, indistinguishable from "healthy run with zero pruned concepts". Add a distinguishing signal (e.g., `pruned_skipped_due_to_walk_errors: bool` or a notice line in the report).

2. **Still-open 2b items** (async work on the reindex write path):
   - sqlite WAL/busy_timeout hardening on the reindex WRITE path (not query read path)
   - Embed batch chunking/checkpointing for long bundles

3. **Pre-existing merged spec drift** (#1417 discovery): The main spec for query-answer had ALREADY drifted from source (`no_match_cause` type, `cited_count` field) before this slice. Cross-check all existing merged specs against current dataclass definitions as a hygiene task.

4. **Graph participation in ranking** (own slice): Dense fusion is now working; graph linking can be added as a 3rd list to the RRF formula (spec allows `fuse` to accept any third list later, maintaining the pure interface).

5. **Embedding-model-tag provenance in vector_meta**: Silent semantic mismatch risk (query embedding model ≠ stored vector model) noted but deferred. Add model identifier to `vector_meta` and validation on load.

## Merged Artifacts

All delta specs have been merged into the main spec store (`openspec/specs/`):

| Domain | Action | Status |
|--------|--------|--------|
| retrieval-fusion | Created | NEW full spec in `openspec/specs/retrieval-fusion/spec.md` |
| query-answer | Merged | `openspec/specs/query-answer/spec.md` updated: dense injection, RRF fusion, degrade path, additive AnswerResult |
| query-command | Merged | `openspec/specs/query-command/spec.md` updated: embedder/store wiring, extended stderr line, reindex hint |
| reindex-command | Merged | `openspec/specs/reindex-command/spec.md` updated: walk-error prune guard |

## Archive Structure

This report and all change artifacts (proposal, design, tasks, delta specs) are located at:

```
openspec/changes/archive/2026-07-21-hybrid-retrieval-fusion/
├── archive-report.md (this file)
├── proposal.md
├── design.md
├── tasks.md
└── specs/
    ├── retrieval-fusion/spec.md
    ├── query-answer/spec.md
    ├── query-command/spec.md
    └── reindex-command/spec.md
```

**Note**: The original `openspec/changes/hybrid-retrieval-fusion/` folder still exists in the repository. The orchestrator MUST remove it after confirming this archive is complete.

## Engram Observation IDs (for traceability)

- Proposal: #1414
- Spec: #1415 (base), #1417 (spec correction bugfix)
- Design: #1416
- Tasks: #1418
- Verify-Report: #1420
- Archive-Report: (this observation)

All phase artifacts are available via these topic keys:
- `sdd/hybrid-retrieval-fusion/proposal` → #1414
- `sdd/hybrid-retrieval-fusion/spec` → #1415
- `hybrid-retrieval-fusion spec: additive-only correction` → #1417
- `sdd/hybrid-retrieval-fusion/design` → #1416
- `sdd/hybrid-retrieval-fusion/tasks` → #1418
- `sdd/hybrid-retrieval-fusion/verify-report` → #1420
- `sdd/hybrid-retrieval-fusion/archive-report` → (this)

## Sign-Off

**SDD cycle complete**. The change has been fully planned, designed, specified, implemented, verified, and archived. All artifacts are in the source of truth. The merged specs now reflect the new capabilities and can drive the next phase work.

Next slice can build on these merged specs and defer graph ranking to its own focused change.
