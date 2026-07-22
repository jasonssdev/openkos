# Design: Embedding-Model Tag in vectors.db (MVP-2 follow-up #5)

## Technical Approach

Approach A from explore #1484. Add a generic `meta(key,value)` table to `vectors.db`
storing one `('embedding_model', <tag>)` row. Gate the VECTOR reindex pass on tag
equality — independent of both the per-concept `content_hash` gate and the shared
`bundle_manifest_hash` (FTS/graph) gate. On absent/differing tag, re-embed ALL vectors
this run via the existing per-concept DELETE+INSERT path (`upsert_many`), then persist the
new tag in the SAME Slice-5 single commit. Tag reaches `reindex()` as an explicit string
param; the Embedder Protocol stays embed-only.

## Architecture Decisions

| # | Decision | Choice | Rejected alt | Rationale |
|---|----------|--------|--------------|-----------|
| D1 | Where to store the tag | New generic `meta(key,value)` table in `vectors.db`, DDL mirroring `derived.py:47-52`; created idempotently in `open_vector_store` right after the `vector_meta` DDL (`vectorstore.py:276`) | Sentinel row in `vector_meta`; extra column; fold into manifest hash | A separate `meta` table keeps `meta_hashes()` (`SELECT ... FROM vector_meta`, `vectorstore.py:343-348`) untouched — a sentinel row would appear as a fake prunable concept_id and corrupt the content_hash cache/prune logic. Reuses a proven in-repo pattern. Correct singleton grain. |
| D2 | Tag threading | New keyword-only param `model_tag: str \| None = None` on `reindex()` (`reindex.py:96`); CLI passes `model_tag=cfg.embedding_model` at `main.py:2500-2506` | Add model-id accessor to `Embedder` Protocol | Keeps the embed-only seam (`llm/base.py:33-42`) pure. `None` default keeps every pre-Slice-5 caller/test inert (no gate, no forced re-embed) — pure back-compat; only the CLI opts in. |
| D3 | Mismatch gate mechanics | After `cached_hashes = db.meta_hashes()` (`reindex.py:128`) read `stored = db.read_model_tag()`; `model_changed = model_tag is not None and stored != model_tag`. The per-doc gate (`reindex.py:152`) becomes `if not force and not model_changed and cached_hashes.get(cid) == digest`. Mismatch routes every doc to `to_embed` → `upsert_many` re-embed-all | Separate DROP path; second walk | One boolean bypasses the content_hash skip, reusing the entire existing embed/upsert machinery. No new pass. |
| D4 | Tag write + commit | `tag_written = model_tag is not None and stored != model_tag`; when true call `db.write_model_tag(model_tag)` (no commit) before the run commit; broaden `if to_embed or to_prune` → `... or tag_written: db.commit()` (`reindex.py:187`) | Unconditional write every run | Only writing on change keeps a no-op reindex a true no-op. Broadened condition covers the empty-bundle-but-absent-tag edge (persist tag with zero docs). Same single commit as vectors (Slice-5 contract). |

## Data Flow

    cfg.embedding_model ──(main.py:2500)──▶ reindex(model_tag=…)
                                              │
       db.read_model_tag() ◀── meta table ───┤  stored != tag ?
                                              │        │yes
       content_hash gate  ◀── vector_meta ───┤   force re-embed-all
                                              ▼        ▼
                                   upsert_many + write_model_tag
                                              ▼
                                       db.commit()  (one)
    _reindex_fts / bundle_manifest_hash  ── UNCHANGED, model-independent

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `state/vectorstore.py` | Modify | Add `_CREATE_META_TABLE_SQL` (mirror derived), `_SELECT_META_SQL`/`_UPSERT_META_SQL`, `EMBEDDING_MODEL_KEY="embedding_model"`; create meta table in `open_vector_store` (~L277); add `read_model_tag()->str\|None` / `write_model_tag(tag)` (no commit) to `VectorStore` Protocol (~L155) and `VectorStoreDB` (~L349) |
| `state/reindex.py` | Modify | New `model_tag` param (~L96); read stored tag + `model_changed` gate (~L128,152); conditional tag write + broadened commit (~L173,187) |
| `cli/main.py` | Modify | Pass `model_tag=cfg.embedding_model` (~L2500-2506) |
| `tests/unit/state/…`, `tests/…reindex…` | Create | Strict-TDD RED tests |

## Interfaces / Contracts

```python
# VectorStore Protocol + VectorStoreDB (additive, mirrors upsert_many/commit)
def read_model_tag(self) -> str | None: ...      # None when row absent
def write_model_tag(self, tag: str) -> None: ...  # INSERT OR REPLACE, no commit
```

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit (vectorstore) | meta table exists on open; `read_model_tag` None when absent; write→read round-trips; `meta_hashes()` unaffected by meta rows | fake/tmp db |
| Unit (reindex) | absent/differing tag → embedded==all, cache_hits==0, tag persisted; matching tag → incremental (embedded==0); `model_tag=None` inert (back-compat); single commit; empty-bundle+absent-tag still persists tag; FTS gate untouched by model change | fake VectorStore + spy commit |
| Integration | switch model → reindex re-embeds all; second reindex incremental (self-heal in exactly one run) | real sqlite-vec |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Pure SQLite-schema + in-process gate change.

## Migration / Rollout

Idempotent additive migration: `open_vector_store` creates the `meta` table on every open;
a pre-slice db simply has no `embedding_model` row → `read_model_tag()` is `None` → one
forced re-embed-all, then the tag is written and subsequent runs are incremental. Self-healing,
one-time cost. Rollback = revert three edits; the leftover `meta` table is harmless (unread by
reverted code). No destructive migration.

**Latent out-of-scope risk (documented, NOT handled):** `EMBED_DIM=1024` is fixed at vec0
CREATE (`vectorstore.py:43`, `llm/base.py:29`) and enforced at embed time. Re-embed-all is
valid ONLY because every storable model yields 1024-dim vectors, so the vec0 CREATE dimension
always matches. A genuine dimension change would require vec0 DROP+recreate — explicitly out of
scope; this slice's re-embed-all must not be mistaken for handling a dimension change.

## Chained-PR Forecast

Single PR; ~3 small source edits + tests, well under the 400-line budget.
- Decision needed before apply: No
- Chained PRs recommended: No
- 400-line budget risk: Low

## Open Questions

None.
