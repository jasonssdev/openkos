# Proposal: Graph-Augmented Retrieval (MVP-2 Slice 4)

## Intent

Slice 3 fused FTS + dense into one RRF ranking. The graph signal exists (`build_graph`, `to_digraph`) but is unused at query time, so bridge/related concepts reachable only through graph proximity to the best hits never surface. This slice brings GRAPH in as a first-class third RRF list, seeded from the top fused hits, to recover concepts that pure lexical + dense miss.

## Scope

### In Scope
- Personalized PageRank (PPR) graph retriever seeded on top fused hits, as a first-class 3rd RRF list.
- Additive optional `graph_hits` arg on `fuse()`; new `GraphHit` dataclass (concept_id + score/rank).
- Two-stage flow in `answer()`: `fuse(fts, vec)` seeds -> `build_graph` + PPR -> final `fuse(fts, vec, graph)` -> truncate -> unchanged `_assemble_context`.
- Graph degrade-to-`[]` via try/except; additive `graph_hit_count` / `graph_degraded` fields + extended `query` stderr line.
- SCOPED spec-vs-dataclass drift check on ONLY the 3 edited specs.

### Out of Scope
- Typed/weighted edge PPR (edges are unweighted today — uniform PPR; weighting is future).
- Persisted graph store; graph as a re-ranker instead of first-class list (keep first-class; seed-exclusion / list-cap is the safety valve).
- Changes to graph construction / entity resolution; new CLI verbs.
- DEFERRED: reindex prune-skip observability, reindex WAL/busy_timeout, embed batch chunking, embedding-model-tag provenance.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `retrieval-fusion`: `fuse()` gains additive optional 3rd typed `graph_hits` list; equal-weight RRF and rank-only purity preserved; default None = byte-identical current behavior.
- `query-answer`: `answer()` builds graph in-process, derives seeds, runs PPR, fuses 3 lists; adds `graph_hit_count`/`graph_degraded` to `AnswerResult`; graph degrades to `[]`; FTS stays mandatory backbone.
- `query-command`: `query` stderr `retrieval:` line reports graph counts + degrade note.

## Approach

Approach A from exploration. Extract pure `graph_rank(store, seeds)` in a new `retrieval/graph_retrieve.py` owning `nx.pagerank(to_digraph(store), personalization=...)` on an undirected view. Build graph inside `answer()` (FTS precedent; config-free, rebuilt per run from `bundle_dir`, no cold-start). Defaults: seeds = top `min(limit,5)` of initial fuse; alpha=0.85; graph pool = `max(limit,10)`. concept_id identity fully shared — no translation.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `retrieval/fusion.py` | Modified | Widen `_accumulate` TypeVar bound; add optional 3rd `graph_hits` param + `GraphHit` |
| `retrieval/graph_retrieve.py` | New | Pure `graph_rank()` PPR retriever, fake-store unit-testable |
| `retrieval/answer.py` | Modified | Build graph, seed, PPR, 3-list fuse, try/except degrade, new fields |
| `cli/main.py` (`query`) | Modified | Extend stderr `retrieval:` line with graph counts |
| 3 specs | Modified | `retrieval-fusion`, `query-answer`, `query-command` (all additive) |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| PPR hub-drift injects query-irrelevant concepts near seeds | Med | Seed-exclusion / graph-list cap; validate in future eval |
| `nx.pagerank` nondeterminism | Low | Deterministic for fixed graph+personalization+params; pin as spec scenario |
| `retrieval` importing `openkos.graph` regresses import-linter | Low | Allowed (forbidden importers are canonical model/bundle/state); confirm no rule regresses |
| Review budget: fusion + answer + cli + retriever + 3 specs + tests may exceed 800 lines | Med | Additive-only; flag to sdd-tasks for possible slice split |

## Rollback Plan

Revert the change branch. `fuse()` 3rd arg defaults to None (byte-identical prior behavior); `AnswerResult` fields are additive with defaults; graph is built in-process with no persisted state or migration, so removal leaves FTS + dense retrieval exactly as Slice 3 shipped.

## Dependencies

- `networkx>=3.4` + `types-networkx` (already runtime deps).
- `graph/analysis.py:to_digraph` and `graph/sqlite_graph.py:build_graph` (already exist).

## Success Criteria

- [ ] Graph seeds from top fused hits and contributes concepts to final ranking as a first-class RRF list.
- [ ] Graph degrades to `[]` + `graph_degraded=True` on empty seeds / edgeless graph / build failure; FTS unaffected.
- [ ] `fuse()` with no `graph_hits` is byte-identical to current behavior.
- [ ] PPR determinism pinned by a spec scenario; scoped drift check clean on the 3 edited specs.
