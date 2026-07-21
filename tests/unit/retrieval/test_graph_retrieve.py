"""Unit tests for `retrieval/graph_retrieve.py`: the pure Personalized
PageRank (PPR) graph retriever.

`graph_rank` is zero-I/O over an already-built `GraphStore`: every scenario
here exercises a small, hermetic fixture graph with a real `to_digraph` +
`nx.pagerank` call -- never a real bundle, never the network.
"""

from openkos.graph.base import Edge, GraphStore
from openkos.retrieval import graph_retrieve
from openkos.retrieval.fusion import GraphHit


class _FakeGraphStore:
    """A `GraphStore` fixture: two seeds bridged by an intermediate concept,
    plus an isolated node reachable by nothing."""

    def nodes(self) -> list[str]:
        return [
            "concepts/bridge",
            "concepts/isolated",
            "concepts/seed_a",
            "concepts/seed_b",
        ]

    def edges(self) -> list[Edge]:
        return [
            Edge(source_id="concepts/seed_a", target_id="concepts/bridge"),
            Edge(source_id="concepts/bridge", target_id="concepts/seed_b"),
        ]

    def neighbors(self, concept_id: str) -> list[str]:
        raise NotImplementedError("not exercised by graph_rank")


class _InLinkerGraphStore:
    """A `GraphStore` fixture where the only edge points INTO the seed (no
    out-edge FROM the seed), proving `graph_rank` uses an undirected view."""

    def nodes(self) -> list[str]:
        return ["concepts/in_linker", "concepts/seed"]

    def edges(self) -> list[Edge]:
        return [Edge(source_id="concepts/in_linker", target_id="concepts/seed")]

    def neighbors(self, concept_id: str) -> list[str]:
        raise NotImplementedError("not exercised by graph_rank")


class _EmptyGraphStore:
    """A `GraphStore` fixture with zero nodes and zero edges."""

    def nodes(self) -> list[str]:
        return []

    def edges(self) -> list[Edge]:
        return []

    def neighbors(self, concept_id: str) -> list[str]:
        return []


class _EdgelessGraphStore:
    """A `GraphStore` fixture with nodes but zero edges."""

    def nodes(self) -> list[str]:
        return ["concepts/seed", "concepts/other"]

    def edges(self) -> list[Edge]:
        return []

    def neighbors(self, concept_id: str) -> list[str]:
        return []


class _LargePoolGraphStore:
    """A `GraphStore` fixture with a single seed connected to 12 distinct
    non-seed neighbors -- more than the `max(limit, 10)` pool cap when
    `limit` is small."""

    def nodes(self) -> list[str]:
        return ["concepts/seed"] + [f"concepts/n{i:02d}" for i in range(12)]

    def edges(self) -> list[Edge]:
        return [
            Edge(source_id="concepts/seed", target_id=f"concepts/n{i:02d}")
            for i in range(12)
        ]

    def neighbors(self, concept_id: str) -> list[str]:
        raise NotImplementedError("not exercised by graph_rank")


def test_multi_hop_bridge_concept_surfaces_in_ranked_output() -> None:
    """A concept reachable only via an intermediate node (not a direct seed
    neighbor) surfaces in `graph_rank`'s output."""
    store: GraphStore = _FakeGraphStore()

    result = graph_retrieve.graph_rank(
        store, ["concepts/seed_a", "concepts/seed_b"], limit=10
    )

    assert "concepts/bridge" in [hit.concept_id for hit in result]


def test_seeds_are_excluded_from_the_result() -> None:
    """No seed id ever appears in `graph_rank`'s returned list, even when a
    seed is reachable from another seed."""
    store: GraphStore = _FakeGraphStore()

    result = graph_retrieve.graph_rank(
        store, ["concepts/seed_a", "concepts/seed_b"], limit=10
    )

    result_ids = {hit.concept_id for hit in result}
    assert "concepts/seed_a" not in result_ids
    assert "concepts/seed_b" not in result_ids


def test_determinism_across_repeated_calls() -> None:
    """Two consecutive calls with identical `store`/`seeds`/`limit` return
    byte-identical ordered output."""
    store: GraphStore = _FakeGraphStore()

    first = graph_retrieve.graph_rank(
        store, ["concepts/seed_a", "concepts/seed_b"], limit=10
    )
    second = graph_retrieve.graph_rank(
        store, ["concepts/seed_a", "concepts/seed_b"], limit=10
    )

    assert first == second


def test_undirected_recall_surfaces_a_pure_in_linker() -> None:
    """A node with only an IN-edge from a seed (no out-edge to it) still
    surfaces, proving the undirected view, not the raw directed
    `to_digraph` output."""
    store: GraphStore = _InLinkerGraphStore()

    result = graph_retrieve.graph_rank(store, ["concepts/seed"], limit=10)

    assert "concepts/in_linker" in [hit.concept_id for hit in result]


def test_empty_graph_returns_empty_list() -> None:
    """`store.nodes() == []` -> `[]`."""
    store: GraphStore = _EmptyGraphStore()

    result = graph_retrieve.graph_rank(store, ["concepts/seed"], limit=10)

    assert result == []


def test_edgeless_graph_returns_empty_list() -> None:
    """A graph with nodes but zero edges -> `[]`."""
    store: GraphStore = _EdgelessGraphStore()

    result = graph_retrieve.graph_rank(store, ["concepts/seed"], limit=10)

    assert result == []


def test_seeds_not_present_in_graph_return_empty_list() -> None:
    """Seeds absent from the graph's nodes are filtered before
    `nx.pagerank`, so the result is `[]` rather than raising."""
    store: GraphStore = _FakeGraphStore()

    result = graph_retrieve.graph_rank(store, ["concepts/nonexistent"], limit=10)

    assert result == []


def test_pool_cap_never_exceeds_max_limit_ten() -> None:
    """Result length never exceeds `max(limit, 10)` even when the graph has
    more reachable non-seed nodes."""
    store: GraphStore = _LargePoolGraphStore()

    result = graph_retrieve.graph_rank(store, ["concepts/seed"], limit=5)

    assert len(result) <= max(5, 10)
    assert len(result) == 10


def test_returned_hits_are_graph_hit_instances() -> None:
    """Each returned element is a `GraphHit` carrying `concept_id` and
    `score`."""
    store: GraphStore = _FakeGraphStore()

    result = graph_retrieve.graph_rank(
        store, ["concepts/seed_a", "concepts/seed_b"], limit=10
    )

    assert all(isinstance(hit, GraphHit) for hit in result)


def test_ties_break_by_concept_id_ascending() -> None:
    """Structurally symmetric non-seed neighbors (same distance from the
    single seed) carry equal PPR mass, so ties break by `concept_id`
    ascending -- the pool cap keeps only the first 10 of the 12 candidates,
    `n00` through `n09`."""
    store: GraphStore = _LargePoolGraphStore()

    result = graph_retrieve.graph_rank(store, ["concepts/seed"], limit=5)
    ordered_ids = [hit.concept_id for hit in result]

    assert ordered_ids == [f"concepts/n{i:02d}" for i in range(10)]
