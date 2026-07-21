# Tasks: Graph-Augmented Retrieval (MVP-2 Slice 4)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~580-855 (prod ~145-215, tests ~420-620, docs ~15-25) |
| 400-line budget risk | Medium — sits at/near the 800-line ceiling in the high end of the estimate |
| Chained PRs recommended | No (default) — single PR with `size:exception` |
| Suggested split (fallback only) | If the actual diff overshoots 800: PR1 = graph_retrieve + fusion + answer (core retrieval, ~515-735 est.) -> PR2 = CLI stderr + docs (~65-100 est., needs PR1) |
| Delivery strategy | auto-forecast (non-canonical; treated as auto-chain: proceed with chosen strategy, no interactive gate) |
| Chain strategy | N/A unless the fallback split triggers (then: stacked-to-main) |

Decision needed before apply: No — proceed as a single PR.
Chained PRs recommended: No, by default.
400-line budget risk: Medium.

Rationale: unlike the hybrid-retrieval-fusion precedent (which split into 4 PRs
because it added a genuinely independent dense-retrieval subsystem plus an
unrelated reindex guard fix), this slice is a single cohesive vertical —
`graph_retrieve` -> `fusion` -> `answer` -> `cli` are strictly sequential, each
phase consuming the previous phase's output, with no independently revertible
sub-feature except the thin CLI/docs surface. The design document's own
"Medium" risk flag is honored above by keeping the split explicitly available
as a fallback rather than silently forcing a single PR that could blow the
budget. Re-forecast with real diff stats after Phase 3 (the largest phase,
`answer.py`) lands in RED, before committing to a single PR.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure `graph_rank` PPR retriever | PR 1 (or Phase 1 of single PR) | `uv run pytest -q tests/unit/retrieval/test_graph_retrieve.py` | N/A — zero-I/O pure function over a fixture `GraphStore`, exercised only via unit tests | Revert `retrieval/graph_retrieve.py` + its test file + the minimal `GraphHit` dataclass in `fusion.py`; no consumer yet |
| 2 | `GraphHit` widening + `fuse()` 3rd list | Phase 2 (needs Unit 1) | `uv run pytest -q tests/unit/retrieval/test_fusion.py` | N/A — zero-I/O pure function | Revert the `_accumulate` TypeVar widening + `graph_hits` param + added test cases; `fuse(fts,vec)` callers unaffected |
| 3 | `answer()` two-stage fuse + degrade + additive fields | Phase 3 (needs Units 1-2) | `uv run pytest -q tests/unit/retrieval/test_answer.py` | Hermetic: fake `GraphStore`/`build_graph` monkeypatch, fake `Embedder`/`VectorStore` — no network | Revert `_graph_search` helper + two-stage fuse wiring + additive `AnswerResult` fields + test additions; `answer()` reverts to Slice 3 exactly |
| 4 | `query` CLI stderr line + docs | Phase 4-5 (needs Unit 3) — **fallback split boundary if PR grows too large** | `uv run pytest -q tests/unit/test_main.py` | `openkos query "<question>"` manual smoke against a bundle with graph edges | Revert the stderr `retrieval:` line extension + degrade note + `docs/cli.md` edit + test additions |

## Phase 1: Pure `graph_rank` PPR Retriever

- [x] 1.1 RED: `tests/unit/retrieval/test_graph_retrieve.py` — hermetic fixture `GraphStore` (reuse the existing `_FakeGraphStore`-style fixture pattern from `tests/unit/graph/test_analysis.py`) building a small multi-hop graph with a bridge concept between two seeds; scenarios:
  - a multi-hop bridge concept (reachable only via an intermediate node, not a direct seed neighbor) surfaces in the ranked output
  - seed-exclusion: no seed id ever appears in `graph_rank`'s returned list, even when a seed is reachable from another seed
  - determinism: two consecutive calls with identical `store`/`seeds`/`limit` return byte-identical ordered output; ties broken by `concept_id` ascending
  - undirected recall: a node that only has an IN-edge from a seed (no out-edge to it) still surfaces — proves the undirected view, not the raw directed `to_digraph` output
  - empty graph (`store.nodes() == []`) or zero-edge graph -> `[]`
  - seeds not present in the graph's nodes -> `[]` (filtered before `nx.pagerank`, not passed through raising)
  - pool cap: result length never exceeds `max(limit, 10)` even when the graph has more reachable non-seed nodes
- [x] 1.2 GREEN:
  - `retrieval/fusion.py` — add the minimal frozen `GraphHit` dataclass (`concept_id: str`, `score: float`) per the design's producer/consumer placement decision; needed here only as `graph_rank`'s return type — `fuse()` itself is NOT touched yet (Phase 2)
  - `retrieval/graph_retrieve.py` (new) — `graph_rank(store: GraphStore, seeds: Sequence[str], *, limit: int) -> list[GraphHit]`: `to_digraph(store).to_undirected()`; `valid_seeds` = seeds present in the graph's nodes, deduped and sorted; if no `valid_seeds` or the view has zero edges, return `[]`; else `nx.pagerank(view, alpha=0.85, personalization={s: 1.0 for s in valid_seeds})`; drop `valid_seeds` from the result; sort by `(-score, concept_id)`; return the top `limit` as `GraphHit`
- [x] 1.3 REFACTOR: docstrings (module + function, mirroring `graph/analysis.py`'s determinism/layering commentary); `uv run mypy .` incl. `tests/`; ruff clean

## Phase 2: `GraphHit` Widening + `fuse()` Third List

- [x] 2.1 RED: `tests/unit/retrieval/test_fusion.py` additions —
  - a `concept_id` ranked 1st in FTS, dense, AND graph lists outranks one ranked 1st in FTS only (`3 × 1/61` vs `1/61`)
  - a graph-only `concept_id` (absent from `fts_hits`/`vec_hits`) surfaces in the fused output, contributing exactly `1/(K_RRF + rank_graph(cid))`
  - `graph_hits=None` (omitted or explicit) is byte-identical to the current two-list `fuse(fts_hits, vec_hits)` output — assert `fuse(f, v) == fuse(f, v, None)` against a fixed pair
  - no truncation: every distinct `concept_id` from a 10-entry `graph_hits` list appears in the fused output
  - determinism: identical three-list input triple called twice returns byte-identical output
  - ties across all three lists still break by `concept_id` ascending
- [x] 2.2 GREEN: `retrieval/fusion.py` — widen `_accumulate`'s TypeVar bound from `(FtsHit, VecHit)` to `(FtsHit, VecHit, GraphHit)`; add optional third parameter `graph_hits: list[GraphHit] | None = None` to `fuse()`; fold via `_accumulate(scores, graph_hits or [])`, same RRF formula, no re-sorting by `score`
- [x] 2.3 REFACTOR: update module docstring's "combines... fts and vec" framing to "up to three lists"; `uv run mypy .` incl. `tests/`; ruff clean

## Phase 3: `answer()` Second-Stage Seeded Graph Retrieval

- [x] 3.1 RED: `tests/unit/retrieval/test_answer.py` additions — hermetic fake `GraphStore` fixture + `monkeypatch` of `graph.sqlite_graph.build_graph` (or the name `answer.py` imports it under); reuse the existing fake `Embedder`/`VectorStore` pattern (exact signatures: `embed(self, texts: Sequence[str]) -> list[list[float]]`, `EMBED_DIM=1024`; `query(self, embedding: Sequence[float], k: int) -> list[VecHit]`); scenarios:
  - a graph-reachable concept absent from both FTS and dense hits appears in the final answer's citations via its `graph_hits` rank
  - seeds passed to `graph_rank` equal the top `min(limit, 5)` `concept_id`s of the INITIAL `fuse(hits, vec_hits)` — not a raw union of FTS-only/dense-only top hits (spy on `graph_rank`'s `seeds` arg to assert the exact set/order)
  - `graph_hit_count` equals the raw pool size returned by `graph_rank` before final-fusion truncation
  - `build_graph` (or `graph_rank`) raising any `Exception` -> `graph_degraded=True`, `graph_hit_count=0`, no exception propagates, FTS+dense answer still produced
  - edgeless graph (build succeeds, zero edges) -> `graph_hits=[]`, `graph_degraded=False` (the build itself succeeded)
  - no seeds (initial fuse is empty) -> `build_graph`/`graph_rank` never called (spy), `graph_degraded=True`, `graph_hit_count=0`
  - empty/whitespace question -> extend the existing empty-query guard test: `build_graph` and `graph_rank` are never called, alongside the existing `embedder.embed`/`vector_store.query` assertions
  - determinism: same bundle + question, `answer()` called twice -> identical `graph_hits` ordering and identical final fused, limit-truncated `concept_id` list
  - existing config-free import guard (no `openkos.config` import in `answer.py`) still passes unmodified
- [x] 3.2 GREEN: `retrieval/answer.py` —
  - add `_graph_search(bundle_dir: Path, seeds: list[str], *, limit: int) -> tuple[list[fusion.GraphHit], bool]`: wraps `build_graph(bundle_dir)` + `graph_rank(store, seeds, limit=limit)` in `try/except Exception` (broad, mirrors `sqlite_graph`'s degrade-not-crash posture) -> `([], True)` on any failure
  - in `answer()`: compute `initial_fused = fusion.fuse(hits, vec_hits)`; `seeds = initial_fused[: min(limit, 5)]`; if `seeds`, call `_graph_search(bundle_dir, seeds, limit=max(limit, 10))`, else `([], True)` directly (no-seeds case skips the build entirely, per the degrade matrix)
  - final `fused_ids = fusion.fuse(hits, vec_hits, graph_hits)[:limit]` replaces the old two-list fuse call
  - add `graph_hit_count: int = 0` and `graph_degraded: bool = False` fields to `AnswerResult` (additive only — no existing field removed/retyped); populate both on the success path AND the no-match path
- [x] 3.3 REFACTOR: update module + `AnswerResult` docstrings (degrade matrix note, two-stage flow); `uv run mypy .` incl. `tests/`; ruff clean

## Phase 4: `query` CLI Stderr Extension

- [x] 4.1 RED: `tests/unit/test_main.py` additions — extend the existing `retrieval:` stderr-line test to assert the graph hit count appears in the summary; a new case asserting `graph_degraded=True` produces an additional stderr note (mirroring the existing dense-degrade hint shape); assert STDOUT is completely unaffected by both (existing two-output-rule test extended, not replaced)
- [x] 4.2 GREEN: `cli/main.py` `query` — extend the `retrieval:` f-string to include `result.graph_hit_count`; add a stderr note when `result.graph_degraded` is `True` (parallel to the existing dense-degrade `hint:` line); no import of `openkos.graph` anywhere in this file (existing "No CLI Surface" guard test stays green, unmodified — the graph is built inside `answer()`, never the CLI)
- [x] 4.3 REFACTOR: `uv run mypy .` incl. `tests/`; ruff clean

## Phase 5: Docs

- [x] 5.1 `docs/cli.md` — update the `query` section's stderr-summary description to mention the graph hit count and the graph-degrade note; no stdout-shape change to document

## Gate (every checkpoint)

`uv run pytest -q` all green + `uv run mypy .` repo-wide incl. `tests/` + ruff check/format clean.

## Apply Notes (post-implementation)

- Task 4.1/Unit 4's "runtime harness" reference to `tests/unit/test_main.py` was
  stale: the actual `query` CLI stderr tests live in `tests/unit/cli/test_query.py`
  (the file that already carries the existing `retrieval:` line assertions). All
  Phase 4 RED/GREEN work targeted that file instead; no `tests/unit/test_main.py`
  exists in this repo.
- Re-forecast per the Rationale's own instruction: the REAL diff came in at
  997 insertions + 56 deletions (11 files, incl. `uv.lock`), authored-lines
  total ~1010 excluding the generated lockfile — over the 800-line ceiling
  flagged as "Medium" risk. Per the orchestrator's explicit pre-acceptance
  ("If the real diff ends up over 800 lines, that is a pre-accepted
  `size:exception`; do NOT split into chained PRs"), this is delivered as a
  single PR with `size:exception`, not the fallback split.
- `nx.pagerank` (networkx >=3.4) unconditionally requires `scipy` for its
  default backend (`_pagerank_scipy`) — `scipy` was not previously a project
  dependency and had to be added to `pyproject.toml` (`scipy>=1.14`) for
  `graph_rank` to run at all. This is a necessary, in-scope addition to
  satisfy the resolved design decision to use `nx.pagerank`.
