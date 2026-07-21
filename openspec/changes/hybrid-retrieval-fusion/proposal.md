# Proposal: Hybrid Retrieval Fusion (MVP-2 Slice 3)

## Intent

Slices 2a/2b shipped a dense vector store and `reindex` writer, but nothing
reads them: `query` is still FTS-only, so semantic matches that share no
lexical tokens are invisible. This slice makes 2a/2b pay off — wire the dense
store into the answer path and fuse it with the existing lexical results.
Success: `query` retrieves from FTS **and** dense vectors, fuses both into one
ranked list, and degrades cleanly to FTS-only when no vectors exist.

## Scope

### In Scope
- `answer()` injects an `Embedder` + open `VectorStore`; runs FTS + dense, fuses, feeds unchanged `_assemble_context`.
- Pure RRF helper: rank-only fusion, `k_rrf=60`, equal weights, tie-break `concept_id` asc, per-retriever pool `max(limit,10)` → fuse → truncate to `limit`.
- CLI builds `OllamaClient(cfg.embedding_model)` + `open_vector_store(vectors_db_path)`, injects both; degrades to FTS-only + stderr `reindex` hint when dense unavailable (absent/empty db, `VecUnavailable`, read-path `sqlite3.Error`).
- `AnswerResult` grows additively (`dense_hit_count`, `fused_count`); stderr `retrieval:` line extends with dense + fused counts; `zero_hits` = zero across BOTH lists.
- reindex prune guard: skip prune pass when `okf` walk errors occurred (absence-from-walk no longer = deletion); remove dead decode branch (opportunistic).

### Out of Scope
- Weighted/normalized score fusion; distance→similarity conversion (RRF is rank-only by design).
- Graph participation in ranking (link projection, needs its own seed-and-expand slice; RRF accepts a 3rd list later).
- Embedding-model-tag provenance in `vector_meta` (silent semantic-mismatch risk noted, deferred).
- Write-path WAL/busy_timeout hardening; reindex embed batching/chunking; any new CLI verb.

## Capabilities

### New Capabilities
- `retrieval-fusion`: pure reciprocal-rank-fusion helper over `list[FtsHit]` + `list[VecHit]` → ordered `concept_id`s. Rank-only, `k=60`, equal weights, tie-break `concept_id` asc, pool→fuse→truncate. Zero I/O, table-testable.

### Modified Capabilities
- `query-answer`: `answer()` gains `Embedder` + `VectorStore` injection, runs dense retrieval, RRF-fuses with FTS, feeds fused list to assembly; `AnswerResult` grows `dense_hit_count`/`fused_count`; `zero_hits` = empty across both lists; degrades to FTS-only on dense-unavailable/read-path sqlite/vec errors; stays config-free (`FtsUnavailable` still propagates); drops the "vector/semantic retrieval" non-goal.
- `query-command`: CLI builds `Embedder` (`cfg.embedding_model`) + opens vector store, injects both; extends stderr `retrieval:` line with dense + fused counts; emits stderr `reindex` hint when dense degraded; drops the "semantic/vector retrieval" non-goal.
- `reindex-command`: `Prune Removed Documents` gains a walk-error guard — skip the prune pass when `okf` walk errors occurred; opportunistic dead decode-branch removal.

## Approach

**Approach A + pure RRF helper** (recommended by exploration). Fuse inside
`answer()` at the seam between `index.search()` and `_assemble_context()`
(which is identity-only, needs just `.concept_id`, so a fused ordered list
feeds it unchanged). Inject dense seams from the CLI, mirroring the existing
`llm` injection, keeping `answer.py` config-free. Extract the RRF math as a
standalone PURE helper (`retrieval/fusion.py`) — captures B's best property
(exhaustively table-tested fusion) without B's full re-architecture. FTS
stays the mandatory backbone; only the dense side degrades.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `retrieval/fusion.py` | New | Pure RRF helper. |
| `retrieval/answer.py` | Modified | Inject Embedder + VectorStore; dense retrieval; fuse; additive `AnswerResult`. |
| `cli/main.py` (`query`) | Modified | Build/inject dense seams; extend stderr line; degrade + hint. |
| `state/reindex.py` | Modified | Walk-error prune guard; dead-branch removal. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Review-budget churn (answer.py + main.py + reindex.py + 2 modified + 1 new spec + tests > 400 lines) | Med | If forecast is high, split the reindex prune fix into its own tiny PR; keep fusion PR focused. |
| Silent embedding-model mismatch (query model ≠ stored-vector model) | Med | `EMBED_DIM=1024` blocks dim mismatch structurally; note semantic mismatch, defer `vector_meta` model tag. |
| Cold store (user never ran `reindex`) yields empty dense | High | Graceful degrade to FTS-only + discoverable stderr `reindex` hint, exit 0. |
| Pre-existing prune bug becomes user-visible recall-loss now that retrieval reads the store | Med | Walk-error guard skips prune when `okf` reports scandir errors. |
| RRF `k=60` presence-dominated on tiny bundles | Low | Acceptable for MVP-2; documented tradeoff. |

## Rollback Plan

Revert the slice PR(s). `answer()` dense injection is optional/additive and
FTS remains the untouched backbone, so reverting restores FTS-only `query`
with no schema or data migration. `vectors.db` is read-only here — nothing to
undo on disk.

## Dependencies

- Slice 2a `VectorStoreDB.query` + `open_vector_store` (shipped).
- Slice 2b `OllamaClient.embed` / `Embedder` protocol, `state/reindex.py` (shipped).

## Success Criteria

- [ ] `query` fuses FTS + dense hits via RRF; citations render in fused-rank order.
- [ ] Absent/empty/corrupt/locked `vectors.db` degrades to FTS-only + stderr `reindex` hint, exit 0.
- [ ] `FtsUnavailable` still propagates; STDOUT stays answer + Citations only.
- [ ] `AnswerResult` carries `dense_hit_count`/`fused_count`; stderr line reports both; `zero_hits` = empty across both lists.
- [ ] Reindex does NOT prune a vector whose concept sits under an unreadable subtree (walk-error guard).
- [ ] `test_answer_module_does_not_import_config` still passes.
