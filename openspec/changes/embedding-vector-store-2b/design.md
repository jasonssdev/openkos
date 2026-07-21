# Design: Embedding Vector Store — Slice 2b (First Vec0 Consumer + Reindex)

## Technical Approach

Make the 2a seam real without disturbing its byte-stable open path. `VectorStoreDB`
gains data-flow methods (`upsert`, `query`) plus the two minimal cache accessors
(`meta_hashes`, `prune`) that the reindex orchestration requires; the `VectorStore`
Protocol is extended additively. A new `state/reindex.py` walks the bundle via
`okf._iter_docs` (mirroring `fts.build_index`), embeds through the injected `Embedder`,
and gates on the `vector_meta` content-hash cache. A thin `reindex` CLI verb wires
`require_workspace → read_config → open_vector_store → reindex()`, reusing `query`'s
error ladder. `retrieval/answer.py` and `query` stay untouched (Slice 3).

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| 1 | Embedding text | Raw decoded file text (whole `.md`, frontmatter included) | Per-field composition like FTS; body-only | Keeps hash↔embedding coherent (hash is over the same raw bytes); YAGNI on composition; revisit in Slice 3 |
| 2 | Redundant `content_hash` | KEEP both `vectors.content_hash` + `vector_meta.content_hash` | Drop the vec0 column | `vector_meta` is the cache authority (scannable, PK on `concept_id`); vec0 table is MATCH-only. Dropping the column forces a 2a vec0-table recreate (migration). Provenance kept for free |
| 3 | Store surface for prune/cache | Add `upsert`, `query`, `meta_hashes()`, `prune(concept_id)` methods on `VectorStoreDB` | reindex reaches into `db._conn` | Encapsulation: reindex needs bulk meta read + delete; two thin accessors beat leaking the private connection. Minimum beyond `upsert`/`query` |
| 4 | `.openkos/` gitignore | Add `.openkos/` to the **repository root** `.gitignore` | Have `init` write a workspace `.gitignore` | `vectors.db` is a rebuildable cache (rollback plan). `init` stays untouched (2a: init writes nothing under `.openkos/`). Dev/test workspaces created in-repo must not commit the cache |
| 5 | vec0 semantics proof | Runtime-assertion **spike test** gated on `probe_vec_loadable()` | Trust the assumption; mock the extension | Top risk. Mirror 2a's runtime-load assertion: prove real 0.1.9 behavior before building on it |

## Data Flow

    reindex CLI ─→ require_workspace ─→ read_config ─→ open_vector_store(vectors_db_path)
                                                              │
                        okf._iter_docs(bundle) ──┐            ▼
                                                 ├─→ reindex(bundle, db, embedder, force)
        Embedder.embed([raw_text]) ◀────embed────┤            │
                                                 └─→ db.upsert / db.prune / db.meta_hashes
                                                              ▼
                                              vectors (vec0) + vector_meta

Per-doc gate: `content_hash(raw_bytes)` vs `meta_hashes()[concept_id]` → equal = cache-hit
(no `embed`); changed/absent = embed + `upsert`; `--force` bypasses. After the walk,
`concept_id`s in `vector_meta` not seen on disk are `prune`d (single-level workspace invariant).

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `state/vectorstore.py` | Modify | Add `upsert`/`query`/`meta_hashes`/`prune` methods; extend `VectorStore` Protocol; doc follow-ups (a) single-level cleanup invariant + (c) idempotent double-close. Open path byte-stable |
| `state/reindex.py` | New | Walk + hash-cache + prune orchestrator; `ReindexReport(embedded, cache_hits, pruned, skipped)`; guarded per-doc skip |
| `cli/main.py` | Modify | New `reindex` command (thin wiring, `query`-shaped Ollama/Vec error ladder) |
| `.gitignore` | Modify | Add `.openkos/` |
| `tests/unit/state/test_vectorstore.py` | Modify | upsert/query + vec0 spike; follow-ups (b) rename label, (c) double-close, (d) `db_preexisted=True` survival |
| `tests/unit/state/test_reindex.py` | New | cache-hit=0 embeds, prune, `--force`, skip-notes |
| `tests/unit/cli/test_reindex_cmd.py` | New | wiring + error ladder |

## Interfaces / Contracts

```python
class VectorStore(Protocol):  # additive extension of 2a's close()-only Protocol
    def upsert(self, concept_id: str, embedding: Sequence[float], content_hash: str) -> None: ...
    def query(self, embedding: Sequence[float], k: int) -> list["VecHit"]: ...
    def meta_hashes(self) -> dict[str, str]: ...
    def prune(self, concept_id: str) -> None: ...
    def close(self) -> None: ...

@dataclass(frozen=True)
class VecHit:      # mirrors FtsHit
    concept_id: str
    distance: float
```

`upsert`: `DELETE FROM vectors WHERE concept_id=?` → `INSERT` (`serialize_float32(embedding)`)
→ `INSERT OR REPLACE INTO vector_meta`, one `commit`. `query`:
`embedding MATCH ? AND k = ? ORDER BY distance`. Fakes MUST declare `Sequence[float]`
verbatim (#1363); the fake `Embedder` returns `list[list[float]]`, each row `EMBED_DIM` (1024) floats.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Spike (integration) | vec0 0.1.9 DELETE-by-`concept_id` + re-INSERT + metadata-filtered KNN | Real extension, `skipif(not probe_vec_loadable())`; assert one row survives re-upsert and KNN returns expected `concept_id`/ascending distance. Gates all upsert/query work |
| Unit — store | upsert idempotency, query ordering, `meta_hashes`, `prune` | Real `open_vector_store(tmp_path)`; deterministic hand-built vectors |
| Unit — reindex | cache-hit ⇒ **zero** `embed` calls; changed/new ⇒ embed; prune; `--force` | Fake `Embedder` (call-counting); fake/real `VectorStoreDB`; assert `ReindexReport` |
| Unit — CLI | wiring + `OllamaUnavailable`→`OllamaModelNotFound`→`OllamaError`/`VecUnavailable` ladder, exit 1 | Typer runner, injected fakes |
| Follow-ups | (b) rename stale label; (c) double-close no-raise; (d) pre-create real `vectors.db`, force failing reopen, assert file+bytes survive | Extend `test_vectorstore.py` |

Gate: `uv run pytest -q` + `uv run mypy .` repo-wide (tests included). No real Ollama in units.

## Threat Matrix

N/A — no new routing, shell, subprocess, VCS/PR automation, or executable-file
classification. Ollama access is existing HTTP egress via `OllamaClient` (unchanged).

## Migration / Rollout

No migration: 2a already ships both tables. `.openkos/vectors.db` is a rebuildable
cache — deletable. Rollback = revert PR (reindex deleted, vectorstore additions removed).

## Open Questions

- [ ] Decision #3 extends the store surface beyond literal `upsert`/`query`; confirm the two accessors (`meta_hashes`/`prune`) over connection-leaking to reindex.
- [ ] Spike must confirm DELETE-by-metadata works in 0.1.9; fallback is DELETE-by-`rowid` (query rowid first) if unsupported.
