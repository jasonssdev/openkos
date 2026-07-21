# Design: Graph-Augmented Retrieval (MVP-2 Slice 4)

## Technical Approach
Approach A from the proposal. A new pure module `retrieval/graph_retrieve.py` owns
Personalized PageRank (PPR) over a `GraphStore`, producing a `GraphHit` list.
`answer()` runs a two-stage flow: `fuse(fts, vec)` -> derive seeds -> `build_graph` +
`graph_rank` -> `fuse(fts, vec, graph)` -> truncate -> unchanged `_assemble_context`.
`fuse()` gains an additive optional 3rd `graph_hits` param (default `None` = byte-identical).
FTS stays the mandatory backbone; dense and graph degrade independently. Graph is built
in-process from `bundle_dir` (FTS `build_index` precedent, config-free). Layering is
enforced by the existing AST-guard tests, not import-linter.

## Architecture Decisions

### Decision: `graph_rank` operates on an UNDIRECTED view
**Choice**: `nx.pagerank(to_digraph(store).to_undirected(), personalization=..., alpha=0.85)`.
**Alternatives**: directed (out-edge only), reverse-augmented.
**Rationale**: `store.neighbors`/`edges` are out-edge-only (body links + `relations:` frontmatter,
source->target). The slice recovers *related/bridge* concepts; a concept that links INTO a seed
is as related as one the seed links to. Directed PPR starves in-linkers and complicates dangling-node
handling/determinism. Undirected treats adjacency symmetrically = maximal related-concept recall.
Hub-drift is bounded by seed-exclusion + the pool cap.

### Decision: UNIFORM personalization across seeds
**Choice**: `personalization = {seed: 1.0 for seed in seeds}` (nx normalizes internally).
**Alternatives**: rank-weighted personalization vector.
**Rationale**: Rank information is already preserved — seeds re-enter the *final* `fuse` as first-class
fts/vec list positions. Rank-weighting the personalization would double-count and bias toward hubs near
the top seed, defeating the goal of surfacing NEW concepts equidistant from the seed set. Uniform is
simpler and deterministic. Edges are unweighted today (typed-edge weighting is future/out-of-scope).

### Decision: EXCLUDE seeds from graph results
**Choice**: drop seed ids from the PPR pool before truncation.
**Alternatives**: keep seeds, rely on RRF de-emphasis.
**Rationale**: PPR always ranks seeds highest (personalization mass sits on them). Seeds are already in
the fts/vec lists; re-including them wastes the graph pool budget and triple-boosts them (fts+vec+graph),
crowding out the bridge concepts the slice exists to recover. Post-exclusion the graph list carries only
NEW neighbors. The pool cap `max(limit, 10)` is the second hub-drift safety valve.

### Decision: `GraphHit` lives in `retrieval/fusion.py`
**Choice**: define `GraphHit` in `fusion.py` (consumption site); `graph_retrieve.py` imports it.
**Alternatives**: `GraphHit` in `graph_retrieve.py` (producer, mirroring `FtsHit`/`VecHit`).
**Rationale**: Placing it with the producer would force `fusion.py` to transitively import `networkx`
via `graph_retrieve`, making the pure zero-I/O RRF leaf heavy. Defining it in `fusion.py` keeps `fusion`
dependency-free; `graph_retrieve` (the producer) imports it — a deliberate inversion of the FtsHit/VecHit
precedent, justified to protect the fusion leaf. No import cycle: `fusion` never imports `graph_retrieve`.

### Decision: build graph ONLY when seeds exist; degrade broadly
**Choice**: `_graph_search` skips `build_graph` when the initial fuse is empty; wraps build+PPR in
`try/except Exception -> ([], degraded=True)`.
**Rationale**: An empty initial fuse means both retrievers found nothing — the query is already a
zero-hit no-match, so a second full-bundle scan cannot help. Broad `except Exception` (not `BaseException`)
mirrors `sqlite_graph`'s degrade-not-crash posture: graph is purely additive and must never break FTS/dense
answering. `retrieval` importing `openkos.graph` is allowed (derived->derived, same as `resolution`); the
`cli/main.py` "No CLI Surface" guard stays green because the graph is built inside `answer()`, never the CLI.

## Data Flow

    question ─► fts.build_index ─► hits ─┐
              └► _dense_search ─► vec_hits┤
                                          ├─► fuse(fts,vec) ─► seeds = [:min(limit,5)]
                                          │                         │
                                          │        build_graph(bundle_dir) ─► GraphStore
                                          │                         │
                                          │        graph_rank(store, seeds, limit=max(limit,10))
                                          │                         │ (undirected PPR, seed-excluded)
                                          └────────► fuse(fts,vec,graph) ─► [:limit] ─► _assemble_context ─► llm.chat

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/retrieval/graph_retrieve.py` | Create | Pure `graph_rank(store, seeds, *, limit)` PPR retriever; imports `nx`, `to_digraph`, `GraphStore` |
| `src/openkos/retrieval/fusion.py` | Modify | Add `GraphHit` dataclass; widen `_accumulate` TypeVar to `(FtsHit, VecHit, GraphHit)`; add optional 3rd `graph_hits` param |
| `src/openkos/retrieval/answer.py` | Modify | `_graph_search` helper; two-stage fuse; `graph_hit_count`/`graph_degraded` fields |
| `src/openkos/cli/main.py` (`query`) | Modify | Extend stderr `retrieval:` line with graph count + degrade marker; STDOUT untouched; NO graph import |
| `openspec/changes/.../specs/{retrieval-fusion,query-answer,query-command}` | Modify | Additive scenarios incl. PPR-determinism |

## Interfaces / Contracts

```python
# retrieval/fusion.py
@dataclass(frozen=True)
class GraphHit:
    concept_id: str
    score: float          # PPR score; fuse() uses list POSITION, ignores magnitude

def fuse(fts_hits, vec_hits, graph_hits: list[GraphHit] | None = None) -> list[str]: ...
    # graph_hits=None -> byte-identical to the 2-list result

# retrieval/graph_retrieve.py
def graph_rank(store: GraphStore, seeds: Sequence[str], *, limit: int) -> list[GraphHit]:
    # to_digraph(store).to_undirected(); valid_seeds = seeds present in graph (dedup, sorted)
    # if no valid_seeds or 0 edges: return []
    # nx.pagerank(view, alpha=0.85, personalization={s:1.0 for s in valid_seeds})
    # drop valid_seeds; sort (-score, concept_id); return top `limit` as GraphHit(rank order)

# answer.py additive fields
graph_hit_count: int = 0
graph_degraded: bool = False   # True on build/PPR failure OR no seeds (mirrors dense_degraded)
```

## Degrade Matrix

| Condition | FTS | Dense | Graph | `graph_degraded` |
|-----------|-----|-------|-------|------------------|
| All healthy | run | run | run | `False` |
| No seeds (both retrievers empty) | run | any | skip build | `True` |
| `build_graph` / PPR raises | run | any | `[]` | `True` |
| Edgeless graph (build ok, 0 edges) | run | any | `[]` (ran) | `False` |
| All PPR neighbors were seeds (excluded) | run | any | `[]` (ran) | `False` |
| Dense degraded, graph healthy | run | `[]` | run | `False` |

FTS is mandatory; a `FtsUnavailable` still propagates. Graph never affects FTS/dense outcomes.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit `graph_retrieve` | Deterministic PPR order, seed-exclusion, undirected in-linker recall, uniform personalization, edgeless->[], missing-seed filtering, cap to `limit` | Inject a fake `GraphStore` fixture (existing `_FakeGraphStore` pattern); real `to_digraph`+`nx.pagerank`; assert exact `GraphHit` order twice for determinism |
| Unit `fusion` | `graph_hits=None` byte-identical (`fuse(f,v)==fuse(f,v,None)`); 3-list RRF math; `GraphHit` frozen | Table-driven, zero-I/O |
| Unit `answer` | Seeds = initial fuse `[:min(limit,5)]`; final 3-list fuse; `graph_hit_count`/`graph_degraded`; degrade on `build_graph` raise; no-seeds skips build (spy); config-free import guard still green | Monkeypatch `build_graph`; fake Embedder `embed(self, texts: Sequence[str]) -> list[list[float]]` and VectorStore `query(self, embedding: Sequence[float], k: int) -> list[VecHit]` — EXACT signatures (Engram #1363) |
| Unit `cli` | Stderr `retrieval:` line shows graph count + degrade marker; STDOUT unchanged; `cli/main.py` never imports graph (existing guard unmodified) | Capture streams; reuse existing guard test |

All hermetic: fixture graph or `tmp_path` bundle; no network.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Change is in-process graph ranking + fusion + additive stderr text.

## Migration / Rollout

No migration. `fuse()` 3rd arg defaults to `None` (byte-identical); `AnswerResult` fields are additive
with defaults; graph is built in-process with no persisted state. Removal reverts to Slice 3 exactly.

## Open Questions

- [ ] None blocking. Review-budget risk (fusion + graph_retrieve + answer + cli + 3 specs + tests) is
      Medium — flag to `sdd-tasks` for a possible test/impl slice split; all changes are additive.
