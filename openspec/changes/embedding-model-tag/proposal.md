# Proposal: Embedding-Model Tag in vectors.db

## Intent

`vectors.db` records no embedding-model identity. Switching the embedding model (edit `embedding_model` in `openkos.yaml` to another 1024-dim model, then `openkos reindex`) is silently absorbed as `content_hash` cache-hits: old-model vectors are reused and later matched against new-model query embeddings — same 1024 dim, incompatible vector space → semantically garbage dense-KNN ranking with **no error, no log, no signal**. `--force` is the only escape hatch, and only if the user knows to use it. This slice makes a model switch self-detecting and self-healing.

## Scope

### In Scope
- Add a generic `meta(key, value)` table to `vectors.db` in `open_vector_store` (mirror the derived-store `meta` pattern at `state/derived.py`).
- Store `('embedding_model', <tag>)` where `<tag>` is the current `cfg.embedding_model` string (Ollama `name:tag`).
- Gate the **vector** reindex pass on model-tag mismatch: at reindex start read the stored tag; if absent OR ≠ current tag, force a full re-embed of all vectors this run (via the existing per-concept DELETE+INSERT upsert path — not a vec0 DROP), then persist the new tag.
- Thread the tag into `state.reindex.reindex()` as an explicit string param; wire it from `cli/main.py` where `cfg.embedding_model` is already known.
- Back-compat: pre-slice `vectors.db` has no tag → treat as "unknown" → one forced re-embed, then self-heals.

### Out of Scope
- vec0 DROP+recreate / model-derived `EMBED_DIM` (blocked by the fixed 1024 contract — flagged as latent future concern).
- Any change to the bundle-manifest hash or FTS/graph reindex (model-independent by design — must stay decoupled).
- Query-time warnings (reindex self-heals; consistent with the D2 no-query-time-work contract).
- Embedder Protocol changes (keep it `embed`-only).

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `vector-store`: `vectors.db` gains a singleton `meta(key, value)` table recording the embedding-model tag.
- `reindex-command`: the vector pass forces a full re-embed when the stored model tag is absent or differs from the current model, then persists the new tag.

## Approach

Approach A from exploration `#1484`. New generic `meta(key,value)` table in `vectors.db`; a separate model-tag gate in the vector reindex path (independent of the `content_hash` gate and of the shared bundle-manifest hash); tag passed as an explicit `reindex()` param; back-compat via re-embed-once on unknown tag. Invalidation is re-embed-all (per-concept DELETE+INSERT), not a schema rebuild — valid because every storable model yields 1024-dim vectors under the fixed `EMBED_DIM` contract.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/state/vectorstore.py` | Modified | New `meta(key,value)` DDL in `open_vector_store` (~L275); read/write tag helpers |
| `src/openkos/state/reindex.py` | Modified | New tag param on `reindex()` (~L96); tag-mismatch gate forcing vector re-embed (~L128,151) |
| `src/openkos/cli/main.py` | Modified | Pass `cfg.embedding_model` into `reindex(...)` (~L2497–2506) |
| Tests | New | Strict-TDD coverage: mismatch forces re-embed; match stays incremental; absent tag self-heals |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| One-time forced full re-embed on first post-slice reindex of any existing `vectors.db` (unknown tag) | High | Expected, acceptable, self-healing after one run; document it |
| Latent vec0 fixed-dimension coupling (dimension change would need vec0 DROP) | Low | Out of scope; explicitly flagged — re-embed-all does not handle a real dim change |
| Accidentally coupling the tag to the bundle-manifest hash → spurious FTS/graph rebuilds | Low | Keep the tag gate strictly in the vector path; never touch `bundle_manifest_hash` |

## Rollback Plan

Revert the three source edits. The `meta` table is additive and idempotent; a leftover `meta` table in an existing `vectors.db` is harmless (unread by reverted code). No destructive migration to undo.

## Dependencies

- Slice 5 merged (post-Slice-5 file:line refs). No external dependencies.

## Success Criteria

- [ ] Switching `embedding_model` to another 1024-dim model then reindexing re-embeds all vectors (no silent cache-hit reuse).
- [ ] Same-model reindex stays incremental (content_hash gate unchanged).
- [ ] Pre-slice `vectors.db` self-heals in exactly one forced re-embed, then resumes incremental runs.
- [ ] FTS/graph reindex and the bundle-manifest hash are untouched by a model switch.
- [ ] `uv run pytest -q` and `uv run mypy .` pass repo-wide; forecast well under the 400-line budget.
