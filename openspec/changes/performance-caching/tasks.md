# Tasks: Persist Derived FTS + Graph Indexes (Slice 5 — performance-caching)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~900-1080 total (PR1 ~330, PR2 ~280, PR3 ~430) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 → PR2 → PR3 (PR2/PR3 depend on PR1's shared infra) |
| Delivery strategy | auto-forecast (orchestrator resolves chain_strategy) |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | `state/derived.py` + `retrieval/pool.py` + FTS on-disk writer/reader; `reindex` writes `fts.db` | PR 1 | `uv run pytest -q tests/unit/state/test_derived.py tests/unit/retrieval/test_pool.py tests/unit/state/test_fts.py tests/unit/state/test_reindex.py` | `openkos reindex && openkos query "..."` on a temp bundle (FTS-only, no wiring yet) | delete `.openkos/fts.db`; revert `state/{derived,fts,reindex}.py`, `retrieval/pool.py` |
| 2 | `graph/sqlite_graph.py` on-disk writer/reader; `reindex` also writes `graph.db`; edit-invisible-until-reindex contract | PR 2 | `uv run pytest -q tests/unit/graph/test_sqlite_graph.py tests/unit/state/test_reindex.py` | `openkos reindex` on temp bundle; edit a doc without reindexing; inspect `.openkos/graph.db` nodes/edges unchanged | delete `.openkos/graph.db`; revert `graph/sqlite_graph.py`, reindex graph hunk |
| 3 | `answer()` DI rewire (absent-or-corrupt-only degrade), CLI wiring, exception-vs-degrade boundary, follow-ups #1-#4, docstrings | PR 3 | `uv run pytest -q tests/unit/retrieval/test_answer.py tests/unit/test_main.py tests/unit/state/test_reindex.py` | `openkos reindex && openkos query "..."` end-to-end, verify stderr hints + no writes on query | revert `retrieval/answer.py`, `cli/main.py`, `state/reindex.py` follow-up hunks |

Scenario total (re-verified against the corrected spec, Engram `sdd/performance-caching/spec` #1449 rev.2): **42** — derived-index-cache 8, reindex-command 11, query-answer 12, query-command 4, fts-state 3, graph-projection 4. All 42 are mapped below. (Supersedes this artifact's own prior run, which reported 41/48 before the spec's D2 corrective re-sync.)

## Phase 1 (PR 1): Shared infra + FTS persistence

- [x] 1.1 RED: `tests/unit/state/test_derived.py` — manifest hash order-stable across two doc-discovery orders (derived-index-cache: Walk order does not affect the manifest hash)
- [x] 1.2 GREEN: `state/derived.py` — `bundle_manifest_hash(bundle_dir)`: sha256 over sorted `(concept_id, content_hash)` pairs
- [x] 1.3 RED: test `open_derived_connection` sets WAL + busy_timeout and creates `meta(key,value)` table
- [x] 1.4 GREEN: `state/derived.py` — `open_derived_connection(path)` (PRAGMA journal_mode=WAL, busy_timeout, `meta` DDL, mirrors `open_vector_store`'s lazy-create/cleanup)
- [x] 1.5 RED: `tests/unit/retrieval/test_pool.py` — `pool_limit(n) == max(n, 10)`
- [x] 1.6 GREEN: `retrieval/pool.py` — `POOL_FLOOR = 10`, `pool_limit(limit)`
- [x] 1.7 RED: `tests/unit/state/test_fts.py` — on-disk FTS write produces same rows as `build_index` (fts-state: Reindex persists FTS index to disk)
- [x] 1.8 GREEN: `state/fts.py` — extract shared row-population helper from `build_index`; add `write_fts_index(path, bundle_dir)` targeting disk via `derived.py`'s opener
- [x] 1.9 RED: test `build_index(bundle_dir)` direct call still creates no `.openkos/` artifacts (fts-state: Index never touches disk, regression)
- [x] 1.10 GREEN: confirm in-memory path untouched by the refactor
- [x] 1.11 RED: `tests/unit/state/test_reindex.py` — unchanged bundle skips FTS rebuild; any add/edit/remove rebuilds whole FTS index; reindex-only manifest compute (derived-index-cache: Unchanged bundle reuses cache, Any document change invalidates cache, Single-document edit triggers full rebuild)
- [x] 1.12 GREEN: `state/reindex.py` — open `fts.db`, compute manifest via `derived.bundle_manifest_hash`, compare to `meta.manifest_hash`, skip/rebuild whole index, rewrite meta — manifest computation/comparison lives ONLY here, never at query time
- [x] 1.13 RED: test no `.openkos/fts.db` exists before first `reindex` (derived-index-cache: No derived index before first reindex)
- [x] 1.14 GREEN: confirm lazy-create-on-success mirrors `open_vector_store`
- [x] 1.15 RED: test read-only open of `fts.db` performs zero writes and never recomputes a manifest (fts-state: Persisted index read-only for non-reindex consumers)
- [x] 1.16 GREEN: `state/fts.py` — `open_fts_index_readonly(path) -> FtsIndex | None`, existence-gated, never creates, never hashes the bundle
- [x] 1.17 REFACTOR: dedupe `build_index`'s core doc-walk loop between in-memory and on-disk writer paths

**PR1 unplanned but required mechanical addition** (not itemized above, needed to make the runtime harness meaningful): `config.WorkspaceLayout.fts_db_path` property added (mirrors `vectors_db_path`) and `cli/main.py`'s `reindex()` command wired to pass `fts_db_path=layout.fts_db_path` into `state.reindex.reindex(...)` — otherwise `openkos reindex` would never actually write `fts.db` end-to-end. Covered by a new RED/GREEN pair in `tests/unit/test_config.py` and `tests/unit/cli/test_reindex_cmd.py::test_reindex_persists_fts_db_end_to_end`. `query`/CLI read-side wiring (`_open_fts_or_degrade`, `answer()` DI) remains untouched — Phase 3 (PR3) scope.

## Phase 2 (PR 2): Graph persistence (reuses Phase 1 infra)

- [x] 2.1 RED: `tests/unit/graph/test_sqlite_graph.py` — on-disk graph write matches `build_graph`'s nodes/edges (graph-projection: Reindex persists graph index to disk; Projection builds one node per concept document)
- [x] 2.2 GREEN: `graph/sqlite_graph.py` — `write_graph_store(path, bundle_dir, *, manifest_hash=None)` via `derived.py`'s opener, reusing `build_graph`'s extraction logic (`_populate_graph_tables`, extracted); atomic `BEGIN IMMEDIATE`/rollback rebuild carried over from PR1's Finding B correction
- [x] 2.3 RED: test `build_graph(bundle_dir)` direct call still touches no disk (graph-projection: Projection never touches disk, regression)
- [x] 2.4 GREEN: confirm in-memory path untouched
- [x] 2.5 RED: test read-only open of `graph.db` performs zero writes (graph-projection: Persisted index read-only for non-reindex consumers)
- [x] 2.6 GREEN: `graph/sqlite_graph.py` — `open_graph_store_readonly(path) -> SqliteGraphStore | None`
- [x] 2.7 RED: one `reindex` CLI invocation writes/confirms `vectors.db` + `fts.db` + `graph.db` together (reindex-command: Reindex writes all three derived stores in one run) — implemented as `tests/unit/cli/test_reindex_cmd.py::test_reindex_persists_graph_db_end_to_end`, NOT `tests/unit/state/test_reindex.py` (see deviation note below)
- [x] 2.8 GREEN: **DEVIATION** — `state/reindex.py`'s `reindex()` does NOT open/gate/write `graph.db` itself (would require importing `openkos.graph`, violating the explicit, tested canonical/derived layering boundary: `state` is canonical, `graph` is derived, canonical MUST NOT import derived — `docs/architecture.md`, enforced by `tests/unit/graph/test_base.py::test_canonical_layer_does_not_import_graph`). Instead: `graph/sqlite_graph.py` gained `reindex_graph(bundle_dir, path, *, force=False)` (graph depending on `state.derived` IS the allowed direction), and `cli/main.py`'s `reindex()` command (the entry layer, unconstrained by canonical/derived) calls it separately, alongside `state.reindex.reindex(...)`, within the same CLI invocation/try-except ladder — `openkos reindex` still writes all three stores in one run, without the layering violation the literal task wording would have introduced. `config.WorkspaceLayout.graph_db_path` added mirroring `fts_db_path`.
- [x] 2.9 RED: test an edited doc (FTS row + graph node/edge) stays invisible to a read-only open until the next `reindex` runs — no auto-refresh, no query-side recompute (derived-index-cache: Edited doc stays invisible to query until the next reindex) — `test_reindex_graph_edited_doc_stays_invisible_to_readonly_open_until_next_run` in `test_sqlite_graph.py`
- [x] 2.10 GREEN: confirm `open_fts_index_readonly`/`open_graph_store_readonly` only ever reflect the last `reindex`-written state; add no recompute path
- [x] 2.11 REFACTOR: extracted shared manifest-gate-and-rebuild helper — **DEVIATION**: lives as `state/derived.py::reindex_gate` (canonical layer, store-agnostic, takes a `DerivedStoreWriter` callback), NOT inside `state/reindex.py`, so BOTH `state/reindex.py`'s `_reindex_fts` (canonical) and `graph/sqlite_graph.py`'s `reindex_graph` (derived, depending on canonical is allowed) reuse the SAME implementation without `state/reindex.py` ever importing `openkos.graph`.

**Layering note (read before Phase 3/PR3)**: the literal Phase 2 task wording ("extend `reindex()` to also gate/write `graph.db`", "shared ... helper ... in `state/reindex.py`") assumed `state/reindex.py` could directly call into `openkos.graph`. That assumption conflicts with this repo's own explicit, spec-tested layering rule (`openspec/specs/graph-projection/spec.md`: "No CLI Surface, No Canonical-Layer Import" — canonical layer never depends on derived). Discovered and corrected during PR2 apply; `state/reindex.py` remains vectors+FTS only. Also corrected an existing over-strict test, `tests/unit/graph/test_analysis.py::test_cli_main_never_imports_graph_and_registers_no_graph_command` (renamed `test_cli_main_registers_no_graph_command`): it asserted "`cli/main.py` never imports `openkos.graph`" as an implementation-detail proxy, not something the spec's own scenario text requires (scoped to `model`/`bundle`/`state` only) — mirrors the exact precedent already recorded in `add-query-command`'s task 10.4 for the analogous `state.fts` situation. PR3 should be aware `cli/main.py` now legitimately imports `openkos.graph.sqlite_graph`.

## Phase 3 (PR 3): `answer()`/CLI rewire, absent-or-corrupt degrade, exception boundary, follow-ups #1-#4

- [ ] 3.1 RED: `tests/unit/retrieval/test_answer.py` — `answer()` accepts `fts_index`/`graph_index` and calls `fts_index.search`/reads `graph_index` instead of building (query-answer: Lexical Retrieval Drives Answer Assembly, Graph Retrieval Runs As Second-Stage)
- [ ] 3.2 GREEN: `retrieval/answer.py` — add `fts_index`, `graph_index` params; drop `fts.build_index`/`sqlite_graph.build_graph` calls; use `retrieval.pool.pool_limit`
- [ ] 3.3 RED: test `None` (absent) `fts_index`/`graph_index` degrades to empty, no raise, no manifest comparison anywhere (query-answer: Absent FTS handle degrades, Absent graph handle degrades cleanly)
- [ ] 3.4 GREEN: implement degrade-to-`[]` branches triggered solely by `is None` — `answer()` never computes or compares a manifest hash
- [ ] 3.5 RED: test an unopenable/corrupt on-disk store (e.g. a truncated `fts.db`/`graph.db` file) degrades to empty exactly like absent, via a caller-side open failure — not a manifest check (query-answer: corrected trigger, replaces the earlier wrong "stale manifest hash" scenario)
- [ ] 3.6 GREEN: the handle-open layer (CLI) catches open failures (corrupt/unopenable file) and passes `None` into `answer()`; `answer()` only ever sees "handle or `None`"
- [ ] 3.7 RED (design-boundary pin): typed exceptions raised OUTSIDE the persisted-store open path (a genuine `FtsUnavailable` from an actual availability failure elsewhere) still propagate unswallowed — the degrade path applies ONLY to the store-open call site, never elsewhere (query-answer: Typed Exceptions Propagate Unswallowed, exception-vs-degrade boundary)
- [ ] 3.8 GREEN: implement the boundary precisely at the store-open call site — only that layer's open-failure catch degrades to `None`; every other typed exception (e.g. `FtsUnavailable` outside the open path) propagates unchanged
- [ ] 3.9 RED: test a present, successfully-opened handle is queried normally and feeds the fused list (query-answer: happy path reframed as "handle successfully opened" — reindex's guarantee, not a query-side freshness check; Matching concepts produce cited answer; Dense-only match retrievable; Graph contributes concept absent from FTS/dense; Seeds come from initial fuse)
- [ ] 3.10 GREEN: wire normal-path fuse/seed logic unchanged, now via injected handles
- [ ] 3.11 RED: test PPR exception still degrades cleanly; edgeless graph yields `[]` without `graph_degraded` (query-answer: Graph build failure degrades cleanly, Edgeless bundle yields empty graph list)
- [ ] 3.12 GREEN: keep try/except around PPR call, now wrapping `graph_index` read/rank
- [ ] 3.13 RED: static-import test — `retrieval/answer.py` has no `openkos.config` import (query-answer: Module has no config dependency)
- [ ] 3.14 GREEN: confirm/adjust imports
- [ ] 3.15 RED (fold-in #1): strengthen empty-question test — spy `fts_index`/`embedder`/`vector_store`/`graph_index` record zero calls (query-answer: Whitespace-only question touches no injected handle)
- [ ] 3.16 GREEN: confirm short-circuit precedes all four handle calls
- [ ] 3.17 RED: `tests/unit/test_main.py` — `query` opens FTS/graph read-only, injects into `answer()`, degrades with reindex hint on absent OR unopenable/corrupt store only, asserting no manifest comparison occurs (query-command: FTS/Graph-Unavailable Runs Degrade And Hint At Reindex, corrected absent/corrupt-only trigger)
- [ ] 3.18 GREEN: `cli/main.py` — add `_open_fts_or_degrade`/`_open_graph_or_degrade` (mirror `_open_vector_store_or_degrade`); catch only open failures; wire into `query()`; add hint lines
- [ ] 3.19 RED: test `query` docstring no longer claims "no persisted state" (query-command: Docstring reflects persisted-index contract)
- [ ] 3.20 GREEN: update `cli/main.py` `query()` docstring
- [ ] 3.21 RED: test `query` performs zero writes to `vectors.db`/`fts.db`/`graph.db` (query-command: Happy-Path Answer Rendering; reindex-command: Query never writes to a derived store)
- [ ] 3.22 GREEN: confirm read-only opens enforce no-write
- [ ] 3.23 RED (fold-in #3): `tests/unit/state/test_reindex.py` — `ReindexReport.prune_skipped` distinguishes walk-error-suppressed vs nothing-to-prune; CLI summary reflects it (reindex-command: Deleted doc pruned, Walk error suppresses pruning, No walk errors preserves normal behavior, prune-skip observable in report and CLI)
- [ ] 3.24 GREEN: add `prune_skipped: bool` to `ReindexReport`, set from `okf._walk_errors`; surface in `cli/main.py` `reindex()` summary line
- [ ] 3.25 RED (fold-in #4): test one commit per store per run (vectors/fts/graph) and WAL+busy_timeout active on all three (reindex-command: single run performs one commit per store, WAL mode active on every derived connection)
- [ ] 3.26 GREEN: batch `reindex()`'s per-doc commits into one commit-per-store; ensure WAL/busy_timeout at every derived open, incl. `vectorstore.py`
- [ ] 3.27 REFACTOR (fold-in #2): confirm `pool_limit` is the single source for `max(limit,10)` across `answer.py`/`graph_retrieve.py`; remove residual duplication
- [ ] 3.28 RED: regression — reindex CLI happy-path/refuse-outside-workspace still pass (reindex-command: Successful run prints summary and exits 0, Run outside workspace refuses)
- [ ] 3.29 GREEN: confirm CLI thin-wiring unaffected by the new opens

## Phase 4: Full Gate

- [ ] 4.1 `uv run pytest -q` green across all three PRs' combined test suite
- [ ] 4.2 `uv run mypy .` clean, repo-wide
- [ ] 4.3 Update any doc references (e.g. `docs/architecture.md`) describing FTS/graph as rebuild-per-run/no-persisted-state
