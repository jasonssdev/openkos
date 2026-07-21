# Tasks: Hybrid Retrieval Fusion (MVP-2 Slice 3)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~800-950 (prod ~210-275, tests ~560-610, docs ~15) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 fusion helper -> PR2 answer() wiring -> PR3 query CLI (+docs); PR4 reindex guard independent |
| Delivery strategy | auto-forecast (non-canonical; treated as auto-chain: proceed with chosen strategy, no interactive gate) |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Note: session review budget was raised to 800 lines; combined total still sits
at/above it, so chaining applies regardless of the raised threshold.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure RRF `fuse` helper | PR 1 | `uv run pytest -q tests/unit/retrieval/test_fusion.py` | N/A — zero-I/O pure function, exercised only via unit tests | Revert `retrieval/fusion.py` + its test file; no consumer yet |
| 2 | `answer()` dense injection, fuse, degrade, additive fields | PR 2 (needs PR1) | `uv run pytest -q tests/unit/retrieval/test_answer.py` | Real-ext gated test only (`probe_vec_loadable()`); otherwise N/A | Revert `answer()` changes + additive `AnswerResult` fields + test file; PR3 not yet landed |
| 3 | `query` CLI wiring, stderr/hint, docs | PR 3 (needs PR2) | `uv run pytest -q tests/unit/cli/test_query.py` | `openkos query "<question>"` against a reindexed workspace (manual smoke) | Revert `cli/main.py` query section + `docs/cli.md` + test file; restores FTS-only query |
| 4 | reindex prune walk-error guard (independent) | PR 4 | `uv run pytest -q tests/unit/state/test_reindex.py` | `openkos reindex` over a bundle with a permission-denied subdir | Revert `_walk_errors` guard call + test additions; restores unconditional prune |

## Phase 1: Pure RRF Fusion Helper (PR 1)

- [x] 1.1 RED: `tests/unit/retrieval/test_fusion.py` — table cases: both-lists outrank one-list, k=60 exact worked example, tie-break by `concept_id` asc, full-pool no truncation, empty FTS list, empty dense list, both empty, duplicate-within-list dedup by best rank, determinism (same inputs twice)
- [x] 1.2 GREEN: `retrieval/fusion.py` — `K_RRF=60`, `fuse(fts_hits: list[FtsHit], vec_hits: list[VecHit]) -> list[str]`, 1-based rank as given, `Σ 1/(K_RRF+rank)`, no truncation, imports only `FtsHit`/`VecHit`
- [x] 1.3 REFACTOR: dedup helper, docstrings; mypy/ruff clean

## Phase 2: `answer()` Dense Injection + Fuse (PR 2)

- [x] 2.1 RED: `tests/unit/retrieval/test_answer.py` — fake `Embedder` (`embed(Sequence[str])->list[list[float]]`, `EMBED_DIM=1024`, exact signature) + fake `VectorStore` (all 5 Protocol methods); scenarios: both-retrievers cited answer, dense-only match is retrievable, dense/fused counts, config-free import unchanged
- [x] 2.2 GREEN: `retrieval/answer.py` — add `embedder: Embedder | None = None`, `vector_store: VectorStore | None = None` params; `pool = max(limit, 10)`; embed query + `vector_store.query(pool)`; `fusion.fuse(...)`; truncate to `limit`; `_assemble_context` accepts `list[str]`; add `dense_hit_count`, `fused_count`, `dense_degraded` (additive only, keep `no_match_cause` Literal incl. `"none"`)
- [x] 2.3 RED: zero FTS + zero dense -> no-match, `llm.chat` never called; empty/whitespace question skips `embedder.embed`/`vector_store.query` entirely; `FtsUnavailable` still propagates
- [x] 2.4 GREEN: reclassify zero-hits on fused emptiness (either retriever alone suffices); empty-query short-circuit stays before all retrieval
- [x] 2.5 RED: `vector_store.query` raises `VecUnavailable` / `sqlite3.Error` -> FTS-only fuse, `dense_degraded=True`, no exception; cold store (`vector_store=None`) -> same degrade shape
- [x] 2.6 GREEN: wrap dense sub-phase only in `except (VecUnavailable, sqlite3.Error)`
- [x] 2.7 REFACTOR: extract `_dense_search` helper; `mypy .` repo-wide incl. `tests/`

## Phase 3: `query` CLI Wiring (PR 3)

- [x] 3.1 RED: `tests/unit/cli/test_query.py` — asserts `Embedder`+`VectorStore` built/injected; extended `retrieval:` stderr line (fts+dense+fused+cited); cold-store hint; `VecUnavailable`/`sqlite3.Error` hint; `OllamaModelNotFound` message names the real failing model
- [x] 3.2 GREEN: `cli/main.py` `query` — build `OllamaClient(cfg.embedding_model)` as embedder; open `open_vector_store` via context manager, existence-gate + catch `VecUnavailable` -> `vector_store=None`; inject both into `answer(...)`; extend stderr line with dense+fused counts; print reindex hint iff `store_was_unavailable or result.dense_degraded`; fix `OllamaModelNotFound` handler to use `{exc}` instead of hardcoded `cfg.model`
- [x] 3.3 REFACTOR: dedupe store-open guard; verify `query` never creates `vectors.db`
- [x] 3.4 `docs/cli.md` — update `query` section: dense+fused stderr fields, reindex hint behavior

## Phase 4: Reindex Prune Walk-Error Guard (PR 4, independent)

- [x] 4.1 RED: `tests/unit/state/test_reindex.py` — unreadable subdir holding an indexed concept -> that concept's vector NOT pruned; control case with zero walk errors -> normal prune unaffected
- [x] 4.2 GREEN: `state/reindex.py` — before the prune loop, call `okf._walk_errors(bundle_dir)`; skip the entire prune pass when non-empty (embed + cache-hit passes unchanged)
- [x] 4.3 REFACTOR: mypy/ruff clean

## Gate (every checkpoint)

`uv run pytest -q` all green + `uv run mypy .` repo-wide incl. `tests/` + ruff check/format clean.
