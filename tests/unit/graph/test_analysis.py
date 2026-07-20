"""Unit tests for `openkos.graph.analysis`: the `networkx.DiGraph` conversion.

`to_digraph` is the ONLY function in `openkos.graph` that touches
`networkx` -- `graph.base`/`graph.sqlite_graph` stay free of it. Exercises:
node/edge set fidelity against a `GraphStore`, isolated-node survival, empty
projection, determinism, `relation_type` attribute propagation, and a
round-trip integration over the `good-life-demo` bundle via `build_graph`.

Also asserts the CLI half of the "No CLI Surface" layering boundary:
`cli/main.py` neither imports `openkos.graph` nor registers a `graph`
command. The canonical-layer half of this same boundary (`model`/`bundle`/
`state` never import `openkos.graph`) already lives in
`test_base.py::test_canonical_layer_does_not_import_graph` and is not
duplicated here.
"""

import ast
from pathlib import Path

import networkx as nx

from openkos.graph import sqlite_graph
from openkos.graph.analysis import to_digraph
from openkos.graph.base import Edge, GraphStore

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _FakeGraphStore:
    """A minimal `GraphStore` fixture: two edges plus one isolated node."""

    def nodes(self) -> list[str]:
        return ["concepts/a", "concepts/b", "concepts/isolated"]

    def edges(self) -> list[Edge]:
        return [
            Edge(source_id="concepts/a", target_id="concepts/b"),
            Edge(source_id="concepts/b", target_id="concepts/a"),
        ]

    def neighbors(self, concept_id: str) -> list[str]:
        raise NotImplementedError("not exercised by to_digraph")


class _EmptyGraphStore:
    """A `GraphStore` fixture with zero nodes and zero edges."""

    def nodes(self) -> list[str]:
        return []

    def edges(self) -> list[Edge]:
        return []

    def neighbors(self, concept_id: str) -> list[str]:
        return []


def test_to_digraph_returns_a_networkx_digraph() -> None:
    store: GraphStore = _FakeGraphStore()

    graph = to_digraph(store)

    assert isinstance(graph, nx.DiGraph)


def test_to_digraph_node_set_matches_store_nodes() -> None:
    store: GraphStore = _FakeGraphStore()

    graph = to_digraph(store)

    assert set(graph.nodes) == set(store.nodes())


def test_to_digraph_edge_set_matches_store_edges_as_pairs() -> None:
    store: GraphStore = _FakeGraphStore()

    graph = to_digraph(store)

    assert set(graph.edges) == {(e.source_id, e.target_id) for e in store.edges()}


def test_to_digraph_preserves_isolated_nodes_with_no_edges() -> None:
    store: GraphStore = _FakeGraphStore()

    graph = to_digraph(store)

    assert "concepts/isolated" in graph.nodes
    assert graph.out_degree("concepts/isolated") == 0
    assert graph.in_degree("concepts/isolated") == 0


def test_to_digraph_sets_relation_type_edge_attribute_from_edge() -> None:
    store: GraphStore = _FakeGraphStore()

    graph = to_digraph(store)

    assert graph.edges["concepts/a", "concepts/b"]["relation_type"] is None
    assert graph.edges["concepts/b", "concepts/a"]["relation_type"] is None


def test_to_digraph_on_empty_projection_returns_an_empty_digraph_without_raising() -> (
    None
):
    graph = to_digraph(_EmptyGraphStore())

    assert isinstance(graph, nx.DiGraph)
    assert list(graph.nodes) == []
    assert list(graph.edges) == []


def test_to_digraph_is_deterministic_across_repeated_calls() -> None:
    store: GraphStore = _FakeGraphStore()

    first = to_digraph(store)
    second = to_digraph(store)

    assert list(first.nodes) == list(second.nodes)
    assert list(first.edges) == list(second.edges)


# --- Integration: good-life-demo bundle round-trip --------------------------


def test_build_graph_to_digraph_round_trip_over_good_life_demo_bundle() -> None:
    """`build_graph` -> `to_digraph` over the demo bundle resolves the same
    node/edge counts the store itself reports, and a known edge (also
    asserted directly against the store in `test_sqlite_graph.py`) survives
    the conversion unchanged."""
    bundle_dir = _REPO_ROOT / "examples" / "good-life-demo" / "bundle"

    with sqlite_graph.build_graph(bundle_dir) as store:
        store_nodes = store.nodes()
        store_edges = store.edges()
        graph = to_digraph(store)

    assert graph.number_of_nodes() == len(store_nodes)
    assert graph.number_of_edges() == len(store_edges)
    assert ("concepts/stoicism", "concepts/epicureanism") in graph.edges


# --- Layering guard: No CLI Surface (cli/main.py half) ----------------------


def _collect_imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_cli_main_never_imports_graph_and_registers_no_graph_command() -> None:
    """Mirrors `test_ingest_and_forget_do_not_reference_state_fts`'s AST-guard
    pattern (spec: No CLI Surface): `cli/main.py` must never import
    `openkos.graph`, and none of its `@app.command()`-decorated functions may
    be named `graph`."""
    cli_main = _REPO_ROOT / "src" / "openkos" / "cli" / "main.py"
    source = cli_main.read_text(encoding="utf-8")

    modules = _collect_imported_modules(source)
    assert not any(
        module == "openkos.graph" or module.startswith("openkos.graph.")
        for module in modules
    )

    tree = ast.parse(source)
    command_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "command"
            for dec in node.decorator_list
        )
    }
    assert "graph" not in command_names
