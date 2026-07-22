# Tasks: Embedding-Model Tag in vectors.db (MVP-2 follow-up #5)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~180-260 (3 source files, ~2 test files, no migrations) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Meta table + tag gate + CLI wiring + Protocol-fallout fix | PR 1 | `uv run pytest -q tests/unit/state/test_vectorstore.py tests/unit/state/test_reindex.py tests/unit/cli/test_reindex_cmd.py tests/unit/retrieval/test_answer.py` | Real sqlite-vec integration test in Phase 5 (model switch → re-embed-all → self-heal) | Revert `vectorstore.py`/`reindex.py`/`main.py` edits; leftover `meta` table is inert/unread |

## Phase 1: Vector-Store Meta Table Foundation

- [x] 1.1 RED — `tests/unit/state/test_vectorstore.py`: meta table created idempotently on reopen; absent tag reads `None`; write persists across reopen; write replaces prior tag (one row) (spec: vector-store Generic Meta Table scenarios)
- [x] 1.2 GREEN — `state/vectorstore.py`: add `_CREATE_META_TABLE_SQL`/`_SELECT_META_SQL`/`_UPSERT_META_SQL`, `EMBEDDING_MODEL_KEY="embedding_model"` (mirror `derived.py:47-56`); create table in `open_vector_store` (~L276); add `read_model_tag`/`write_model_tag` to `VectorStore` Protocol (~L155) and `VectorStoreDB` (~L349)
- [x] 1.3 REFACTOR — docstrings; assert `meta_hashes()` (L343-348) unaffected by new table

## Phase 2: Reindex Model-Tag Gate

- [x] 2.1 RED — `tests/unit/state/test_reindex.py`: mismatch forces re-embed-all + tag persisted; absent tag (pre-slice db) forces one re-embed then self-heals next run; matching tag leaves content_hash gate unchanged; `model_tag=None` stays inert (back-compat); single commit covers tag write; empty-bundle+absent-tag still persists tag; mismatch does not trigger FTS/graph rebuild (spec: reindex-command scenarios)
- [x] 2.2 GREEN — `state/reindex.py`: add kw-only `model_tag: str | None = None` (~L96-102); after `cached_hashes = db.meta_hashes()` (~L128) read `stored = db.read_model_tag()`, compute `model_changed`; gate becomes `if not force and not model_changed and cached_hashes.get(cid) == digest` (~L152); conditional `db.write_model_tag(model_tag)`; broaden commit to `if to_embed or to_prune or tag_written` (~L187)
- [x] 2.3 REFACTOR — docstring: gate independent of `--force` and of `bundle_manifest_hash`

## Phase 3: Protocol-Growth Fallout (mypy-level RED)

- [x] 3.1 RED — `uv run mypy .`: expect `_FakeVectorStore` (`tests/unit/retrieval/test_answer.py:122-161`) fails structural `VectorStore` match — missing `read_model_tag`/`write_model_tag`
- [x] 3.2 GREEN — add both methods to `_FakeVectorStore`, raising `NotImplementedError` (mirrors existing unused-method pattern, e.g. L137/149)

## Phase 4: CLI Wiring

- [x] 4.1 RED — `tests/unit/cli/test_reindex_cmd.py`: assert `reindex_module.reindex` receives `model_tag=cfg.embedding_model`
- [x] 4.2 GREEN — `cli/main.py` (~L2500-2506): pass `model_tag=cfg.embedding_model`

## Phase 5: Non-Coupling & Integration Proof

- [x] 5.1 RED — manifest-hash test: `derived.bundle_manifest_hash` unchanged when only the stored tag differs (spec: vector-store Model Tag Independent scenario) — expected already GREEN, proves no accidental coupling
- [x] 5.2 Integration RED→GREEN — real sqlite-vec test: switch `model_tag` → full re-embed; second run with same tag is incremental (self-heal in exactly one run)

## Phase 6: Full Gate & Cleanup

- [x] 6.1 `uv run pytest -q` full suite green
- [x] 6.2 `uv run mypy .` repo-wide green
- [x] 6.3 Update `vectorstore.py`/`reindex.py` module docstrings to note the follow-up #5 tag gate addition
