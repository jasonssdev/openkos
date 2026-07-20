"""Convert a `GraphStore` projection into a `networkx.DiGraph`.

The ONLY module in `openkos.graph` that imports `networkx` -- `graph.base`
stays a stdlib-only leaf and `graph.sqlite_graph` stays free of it too;
`networkx` is scoped entirely to this conversion. This is also where path
finding over the projection lives: `GraphStore`'s Protocol (`graph.base`)
intentionally excludes any path method, so a caller wanting a path (or any
other graph algorithm) converts via `to_digraph` first and then uses
`networkx`'s own algorithms over the returned `nx.DiGraph`.

Layering boundary: the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import `openkos.graph`.
"""

import networkx as nx

from openkos.graph.base import GraphStore


def to_digraph(store: GraphStore) -> "nx.DiGraph[str]":
    """Deterministically convert `store`'s projection into an `nx.DiGraph`.

    Every node from `store.nodes()` is added FIRST, so a node with no edges
    (isolated) still survives in the returned graph; every edge from
    `store.edges()` is then added, carrying its `relation_type` as an edge
    attribute (always `None` this slice -- reserved, unpopulated, see
    `graph.base.Edge`). `store.nodes()`/`store.edges()` are themselves
    already sorted and deterministic (any conforming `GraphStore`, e.g.
    `graph.sqlite_graph.SqliteGraphStore`, guarantees this), so repeated
    calls over the same store produce graphs with identical node/edge
    insertion order. An empty projection (`store.nodes() == []`) converts
    cleanly to an empty `DiGraph` -- zero nodes, zero edges -- without
    raising.
    """
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_nodes_from(store.nodes())
    for edge in store.edges():
        graph.add_edge(edge.source_id, edge.target_id, relation_type=edge.relation_type)
    return graph
