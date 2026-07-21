# Proposal: Embedding Vector Store — Slice 2b (First Vec0 Consumer + Reindex)

## Intent

Slice 2a landed a guarded `vectors.db` scaffold but stores and queries nothing —
`vector_meta` is empty and `content_hash()` has no caller. Slice 2b makes the seam
real: write embeddings, read k-NN, and populate the store from existing bundle docs.
Without a `content_hash` cache, a naive backfill re-embeds every `.md` on every run,
producing an Ollama call-storm; with no upsert/query path there is still no dense
retrieval consumer. This slice closes both gaps and pays down four deferred 2a review
follow-ups (Engram #1397).

## Scope

### In Scope
- **vec0 data flow** in `state/vectorstore.py`: `upsert` (serialize_float32 +
  DELETE-by-`concept_id` then INSERT) and k-NN `query`
  (`embedding MATCH ? AND k = ? ORDER BY distance`).
- **`reindex` CLI verb** (thin wiring) + `state/reindex.py` orchestrator: walk bundle
  `.md` via `okf._iter_docs`, embed through the Embedder seam (OllamaClient, EMBED_DIM=1024),
  upsert into the store. `concept_id` = bundle-relative path minus `.md` (FtsHit/Citation identity).
- **content_hash invalidation** via `vector_meta`: matching hash = cache-hit (no Ollama),
  changed/absent = re-embed, on-disk-missing = prune; `--force` bypasses.
- **4 deferred 2a follow-ups**: (a) document single-level parent-cleanup invariant + test root survives failed open; (b) rename stale test label at `test_vectorstore.py:476`; (c) document idempotent double-close of conn; (d) test the `db_preexisted=True` protective branch (file + bytes survive failed reopen).

### Out of Scope
- RRF / hybrid fusion, `retrieval/answer.py`, the `query` command (Slice 3).
- Graph traversal; chunk-level embedding; embedding-text composition beyond raw doc text unless trivially required; any new persistence beyond `vectors.db`.
- `reindex` as a `doctor` subcommand (doctor is read-only, writes nothing).

## Capabilities

### New Capabilities
- `reindex-command`: the `reindex` CLI verb, bundle-walk backfill orchestration (`state/reindex.py`), and the content_hash cache/prune incremental gate.

### Modified Capabilities
- `vector-store`: add vec0 `upsert` + k-NN `query` data flow (previously explicit non-goals) and the four hygiene/coverage follow-ups; 2a open-path signatures stay byte-stable (additive Protocol extension only).

## Approach

Keep `state/vectorstore.py` as the focused persistence seam — add only `upsert`,
`query`, and the 3 hygiene fixes; extend the `VectorStore` Protocol additively.
Put walk + content_hash cache + prune in a **new `state/reindex.py`** injecting an
Embedder and a `VectorStoreDB`, with a guarded per-doc skip mirroring `fts.build_index`.
The CLI `reindex` command is thin wiring: `require_workspace` → read config →
`open_vector_store(vectors_db_path)` → orchestrator → print embedded/cache-hit/pruned
summary. Error ladder mirrors `query` (Ollama + Vec typed errors → stderr + exit 1).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/state/vectorstore.py` | Modified | Add `upsert`/`query`; extend Protocol; follow-ups (a)/(c) |
| `src/openkos/state/reindex.py` | New | Walk + hash-cache + prune orchestrator |
| `src/openkos/cli/main.py` | Modified | New `reindex` command + error handling |
| `tests/unit/state/test_vectorstore.py` | Modified | upsert/query tests; follow-ups (b)/(c)/(d) |
| `retrieval/answer.py`, `query` cmd | Untouched | Slice 3 (RRF) |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| vec0 0.1.9 DELETE-by-metadata + re-INSERT + metadata-filtered KNN semantics differ from assumption | Med | Early implementation spike / runtime integration assertion before building on them (2a precedent) |
| Cache gate ineffective → Ollama call-storm persists | Med | Cover cache-hit = zero embed calls with a fake Embedder assertion |
| mypy repo-wide incl. tests rejects Protocol fakes (Seq-vs-list, #1363) | Med | Fakes match extended signatures exactly; Embedder returns `list[list[float]]` × EMBED_DIM |
| Redundant `content_hash` in vec0 + `vector_meta` | Low | `vector_meta` authoritative; keep vec0 column (avoids 2a migration) |

## Rollback Plan

Revert the PR: `state/reindex.py` is new (delete), `vectorstore.py` changes are additive
(remove `upsert`/`query`, restore 2a Protocol), `reindex` CLI command removed. No schema
migration (2a schema already ships both tables), so no data-format rollback needed;
`.openkos/vectors.db` is a rebuildable cache and can be deleted.

## Dependencies

- Slice 2a scaffolding (`open_vector_store`, schema, `VecUnavailable`) — landed.
- Ollama reachable with `qwen3-embedding:0.6b` at reindex time. No new packages
  (`serialize_float32` is stdlib struct; numpy not needed).

## Success Criteria

- [ ] `upsert` + k-NN `query` verified against sqlite-vec 0.1.9 (spike/integration).
- [ ] `reindex` embeds new/changed docs, cache-hits unchanged (zero Ollama calls), prunes deleted.
- [ ] `--force` re-embeds all.
- [ ] Four 2a follow-ups closed with tests where specified.
- [ ] 2a open-path signatures byte-stable; mypy + existing suite green.
- [ ] `.openkos/` gitignore convention decided and applied.
