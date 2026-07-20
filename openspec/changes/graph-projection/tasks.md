# Tasks: Graph Projection (MVP-2 slice 1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~600-700 (3 new source modules + pyproject + 3-4 new test files) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 base.py+deps -> PR2 build/extraction -> PR3 query surface -> PR4 analysis.py+layering guard |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (recommended: feature-branch-chain) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `GraphStore` Protocol + `Edge` + `networkx`/`types-networkx` deps | PR1 (base=tracker) | `uv run pytest tests/unit/graph/test_base.py` | N/A (structural-typing leaf, no runtime scenario) | delete `graph/base.py`, `graph/__init__.py`, revert pyproject lines |
| 2 | `build_graph`/`SqliteGraphStore` lifecycle + edge extraction + TOCTOU/no-disk guards | PR2 (base=PR1) | `uv run pytest tests/unit/graph/test_sqlite_graph.py` | `build_graph(examples/good-life-demo/bundle)` in a REPL/test | delete `sqlite_graph.py` + its tests, PR1 stays intact |
| 3 | `nodes()`/`edges()`/`neighbors()` query surface ordering | PR3 (base=PR2) | `uv run pytest tests/unit/graph/test_sqlite_graph.py -k query` | same demo-bundle build, inspect `.nodes()/.edges()` | revert query-method additions only |
| 4 | `analysis.to_digraph` + layering-boundary guard test | PR4 (base=PR3) | `uv run pytest tests/unit/graph/test_analysis.py` | `to_digraph(build_graph(demo_bundle))` assert concept nodes | delete `analysis.py` + its tests; layering test isolated |

## Phase 1: Foundation — Protocol + Dependencies

- [x] 1.1 RED `tests/unit/graph/test_base.py`: `Edge` is a frozen dataclass (source_id/target_id/relation_type); a plain class implementing `nodes/edges/neighbors` satisfies `GraphStore` structurally (mypy).
- [x] 1.2 GREEN `src/openkos/graph/__init__.py` (package marker) + `src/openkos/graph/base.py`: `Edge` dataclass, `GraphStore` Protocol (`nodes()`/`edges()`/`neighbors()` only, no path method), stdlib-only leaf docstring.
- [x] 1.3 `pyproject.toml`: add `networkx>=3.4` to `[project].dependencies`, `types-networkx` to `dev` group; run `uv lock`.

## Phase 2: Build Lifecycle + Edge Extraction (`graph/sqlite_graph.py`)

- [x] 2.1 RED: one node per non-reserved doc; node id = bundle-relative path minus `.md`.
- [x] 2.2 RED: `[t](/concepts/x.md)` link to an existing node -> directed edge with `relation_type IS NULL`.
- [x] 2.3 RED: external URL / no leading `/` / non-`.md` / dangling-target links produce NO edge; build does not raise.
- [x] 2.4 RED: duplicate `(source,target)` edges dedup before insert.
- [x] 2.5 RED: TOCTOU — doc vanishes/corrupts between `_iter_docs` and body re-read -> skipped + noted (mirrors `fts.py`).
- [x] 2.6 RED: no bundle bytes/mtime change; rebuild over unchanged bundle yields an equivalent node/edge set; conn closes on any build exception.
- [x] 2.7 GREEN: implement `_LINK_RE`, DDL constants, `build_graph(bundle_dir)`, `SqliteGraphStore` context manager satisfying 2.1-2.6.
- [x] 2.8 REFACTOR: align skip-note shape and exception handling with `fts.py`.

## Phase 3: GraphStore Query Surface

- [x] 3.1 RED: `nodes()`/`edges()`/`neighbors(concept_id)` return sorted, deterministic results, including on an empty projection.
- [x] 3.2 GREEN: implement `nodes/edges/neighbors` on `SqliteGraphStore`.

## Phase 4: NetworkX Conversion + Layering Guard (`graph/analysis.py`)

- [x] 4.1 RED `tests/unit/graph/test_analysis.py`: `to_digraph` preserves node ids + directed edges; empty projection converts cleanly (nodes, zero edges, isolated nodes survive, no raise).
- [x] 4.2 GREEN: implement `to_digraph(store) -> nx.DiGraph`.
- [x] 4.3 RED: ast-based guard (mirrors `test_ingest_and_forget_do_not_reference_state_fts`) — `model`/`bundle`/`state` source never imports `openkos.graph`; no `graph` CLI command in `cli/main.py`.
- [x] 4.4 GREEN: add a layering-boundary docstring note to each `graph/*.py` module.
- [x] 4.5 Integration proof: `build_graph` + `to_digraph` over `examples/good-life-demo/bundle` resolve expected concept nodes/edges (mirrors `fts.py`'s Phase 8 fixture test).
