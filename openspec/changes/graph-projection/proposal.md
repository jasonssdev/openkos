# Proposal: Graph Projection (MVP-2 slice 1)

## Intent

OpenKOS ships zero object-to-object links today: `model/okf.py::build_concept()` emits only provenance backlinks in `## Related`. The MVP-2 deliverable is "a typed knowledge graph over the bundle (markdown links plus a SQLite node-edge projection; NetworkX for analysis)... a bundle stays readable by any OKF consumer, which simply sees untyped edges." This slice lays the substrate: a read-only, derived node-edge projection over the existing untyped bundle links, plus the `GraphStore` extension point. It unblocks later MVP-2 slices (hybrid retrieval, graph-based lint, relation typing) without committing to a relation vocabulary now.

## Scope

### In Scope
- New `src/openkos/graph/` package (first derived-layer package).
- In-memory SQLite node-edge projection mirroring `state/fts.py` (`:memory:`, rebuild-per-run, context-managed) over `okf._iter_docs`.
- Node identity = OKF concept id (bundle-relative path minus `.md`, same as `fts.py`).
- Edges from bundle-relative markdown links (`/concepts/foo.md`) in doc bodies; `relation_type` column present but **NULL** this slice.
- `graph/base.py::GraphStore` Protocol (mirrors `llm/base.py`: Protocol + supporting types, no concrete-swap logic).
- `graph/analysis.py`: thin SQLite-subgraph → `nx.DiGraph` conversion.
- Add `networkx` to `pyproject.toml` runtime deps.

### Out of Scope (explicit non-goals)
- Relation-type extraction / NLP; frontmatter-vs-prose typing decision (`knowledge-object-model.md:211-222`) — RESERVED, schema keeps nullable column.
- Persistence to `.openkos/openkos.db` and a `state/db.py` shared connection.
- CLI `graph` verb (`docs/cli.md`: "graph-based analysis is MVP 2", not yet).
- Cross-source entity resolution / reversible merge; hybrid vector retrieval (sqlite-vec / Sentence Transformers).
- import-linter / CI layering enforcement.

## Capabilities

### New Capabilities
- `graph-projection`: derived, read-only node-edge projection of the bundle's untyped links, with a `GraphStore` interface and NetworkX analysis conversion.

### Modified Capabilities
- None.

## Approach

Adopt exploration **Approach #3 (Hybrid)**: Approach-1 in-memory effort/risk, but a schema shaped so a later slice flips `:memory:` to a file path and populates `relation_type` with zero migration. **Edge extraction uses a scoped regex** over bundle-relative markdown links — consistent with `okf.py`'s existing regex approach, and avoids pulling `markdown-it-py` (tech-stack-approved but not yet a dependency) into a slice that only needs `[text](/path.md)` links. The projection is a derived cache reconstructible from canonical markdown; OKF bundle bytes are never written (`AGENTS.md` reconstructibility invariant). Keep layering discipline manually (docstring + review) since import-linter is not wired.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/graph/` | New | `base.py` (GraphStore Protocol), `sqlite_graph.py` (projection), `analysis.py` (NetworkX). |
| `src/openkos/model/okf.py` | Read-only | Projection reads via `_iter_docs`; never modified. |
| `pyproject.toml` | Modified | Add `networkx` runtime dependency. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Relation-typing ambiguity has no populate path later | Med | Reserve nullable `relation_type` now; design phase names the resolution as its own later-slice question. |
| Layering not CI-enforced | Med | Docstring declares derived-layer boundary; review enforces. |
| Rebuild-per-run perf at scale | Low | Acceptable pre-alpha; matches `fts.py` precedent; persisted path reserved. |
| Regex misses/over-matches link forms | Low | Scope to bundle-relative `/…​.md` links; unit fixtures cover edge forms. |

## Rollback Plan

Delete `src/openkos/graph/` and revert the `networkx` line in `pyproject.toml`. No canonical files, bundle bytes, or existing specs are touched, so removal is clean and leaves no derived state behind.

## Dependencies

- `networkx` (BSD, tech-stack-approved default).

## Success Criteria

- [ ] `graph/` package builds a `nodes`/`edges` projection in-memory from a bundle with zero writes to `bundle/`.
- [ ] Edges reflect bundle-relative markdown links; `relation_type` is present and NULL.
- [ ] `GraphStore` Protocol exposes neighbor/adjacency queries; path finding via `analysis.py`/NetworkX; `analysis.py` returns an `nx.DiGraph`.
- [ ] No CLI verb, no `.openkos/openkos.db` file, no imports from `graph` into `model`/`bundle`/`state`.
