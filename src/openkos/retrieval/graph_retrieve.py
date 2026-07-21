"""Pure Personalized PageRank (PPR) graph retriever: no I/O, no config.

`graph_rank` converts a `GraphStore` projection into an UNDIRECTED
`networkx` view (`graph.analysis.to_digraph(store).to_undirected()`) and
runs personalized PageRank seeded uniformly on the caller's `seeds`,
surfacing NEW concepts related to the seed set -- never the seeds
themselves. See `openspec/changes/graph-augmented-retrieval/specs/
retrieval-fusion/spec.md` and the design's "Architecture Decisions" for the
undirected-view and seed-exclusion rationale.

Layering: this module imports `networkx` (via `graph.analysis.to_digraph`)
and `openkos.graph.base.GraphStore` -- both derived-layer siblings, mirroring
`graph.analysis`'s own layering. `retrieval/fusion.py` never imports this
module, so `GraphHit`'s producer/consumer placement (defined in `fusion.py`,
imported here) introduces no cycle.
"""

from collections.abc import Sequence

import networkx as nx

from openkos.graph.analysis import to_digraph
from openkos.graph.base import GraphStore
from openkos.retrieval.fusion import GraphHit

_ALPHA = 0.85
"""PageRank damping factor (spec-pinned, `nx.pagerank`'s own default)."""


def graph_rank(
    store: GraphStore, seeds: Sequence[str], *, limit: int
) -> list[GraphHit]:
    """Rank concepts related to `seeds` via personalized PageRank over an
    UNDIRECTED view of `store`'s projection.

    `seeds` present in `store.nodes()` are deduped and sorted, then given
    uniform `personalization` mass (`{seed: 1.0 for seed in valid_seeds}`,
    `nx` normalizes internally) for `nx.pagerank(view, alpha=0.85,
    personalization=...)`. Seeds absent from the graph are filtered out
    before the call rather than raising; if no seed survives, or the
    undirected view has zero edges, returns `[]` without invoking
    `nx.pagerank` at all (an edgeless/dangling graph has no meaningful PPR
    distribution to compute).

    Seed ids are DROPPED from the result -- PageRank always ranks them
    highest since the personalization mass sits on them, and they are
    already first-class members of the fts/vec fusion lists (design:
    "EXCLUDE seeds from graph results"). The remaining candidates are
    ordered by `(-score, concept_id)` -- descending PPR score, ties broken
    by `concept_id` ascending -- and truncated to the top `max(limit, 10)`
    (the pool-cap hub-drift safety valve). Deterministic for a fixed
    `store`/`seeds`/`limit`: `store.nodes()`/`store.edges()` are themselves
    sorted and deterministic, and `nx.pagerank`'s power-iteration is a pure
    function of its inputs.
    """
    graph_nodes = set(store.nodes())
    valid_seeds = sorted({seed for seed in seeds if seed in graph_nodes})
    if not valid_seeds:
        return []

    view = to_digraph(store).to_undirected()
    if view.number_of_edges() == 0:
        return []

    scores: dict[str, float] = nx.pagerank(
        view, alpha=_ALPHA, personalization={seed: 1.0 for seed in valid_seeds}
    )

    pool_limit = max(limit, 10)
    candidates = [
        GraphHit(concept_id=concept_id, score=score)
        for concept_id, score in scores.items()
        if concept_id not in valid_seeds
    ]
    candidates.sort(key=lambda hit: (-hit.score, hit.concept_id))
    return candidates[:pool_limit]
