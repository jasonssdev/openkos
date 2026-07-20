"""Unit tests for `openkos.graph.base`: the `GraphStore` Protocol and `Edge`.

Mirrors `tests/unit/llm/test_ollama.py`'s structural-typing style: a plain
class satisfies `GraphStore` without inheriting it, exercised both at
runtime (`isinstance` via `@runtime_checkable`) and structurally (mypy).
Also asserts the layering boundary: the canonical layer (`model`/`bundle`/
`state`) never imports `openkos.graph`, and `graph.base` never imports
`networkx` (it is a stdlib-only leaf; the nx conversion lives in a later
`analysis.py` slice).
"""

import ast
import dataclasses
from pathlib import Path

import pytest

from openkos.graph.base import Edge, GraphStore

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "openkos"


def test_edge_carries_source_target_and_relation_type() -> None:
    edge = Edge(source_id="concepts/a", target_id="concepts/b")

    assert edge.source_id == "concepts/a"
    assert edge.target_id == "concepts/b"
    assert edge.relation_type is None


def test_edge_relation_type_can_be_set_explicitly() -> None:
    edge = Edge(source_id="a", target_id="b", relation_type="cites")

    assert edge.relation_type == "cites"


def test_edge_is_frozen() -> None:
    edge = Edge(source_id="a", target_id="b")

    with pytest.raises(dataclasses.FrozenInstanceError):
        edge.source_id = "changed"  # type: ignore[misc]


def test_graphstore_protocol_has_no_path_method() -> None:
    # Path finding is nx's job (`analysis.py`), never part of this Protocol.
    assert not hasattr(GraphStore, "path")


class _FakeGraphStore:
    """Implements `GraphStore`'s surface without inheriting it."""

    def nodes(self) -> list[str]:
        return ["a", "b"]

    def edges(self) -> list[Edge]:
        return [Edge(source_id="a", target_id="b")]

    def neighbors(self, concept_id: str) -> list[str]:
        return ["b"] if concept_id == "a" else []


def test_a_plain_class_satisfies_graphstore_structurally() -> None:
    store: GraphStore = _FakeGraphStore()

    assert isinstance(store, GraphStore)
    assert store.nodes() == ["a", "b"]
    assert store.edges() == [Edge(source_id="a", target_id="b")]
    assert store.neighbors("a") == ["b"]
    assert store.neighbors("missing") == []


def _collect_imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_graph_base_is_a_stdlib_only_leaf() -> None:
    source = (_SRC_ROOT / "graph" / "base.py").read_text()
    modules = _collect_imported_modules(source)

    assert not any(
        module == "networkx" or module.startswith("networkx.") for module in modules
    )
    assert not any(module.startswith("openkos.") for module in modules)


@pytest.mark.parametrize("layer", ["model", "bundle", "state"])
def test_canonical_layer_does_not_import_graph(layer: str) -> None:
    layer_dir = _SRC_ROOT / layer
    for path in layer_dir.rglob("*.py"):
        modules = _collect_imported_modules(path.read_text())

        assert not any(
            module == "openkos.graph" or module.startswith("openkos.graph.")
            for module in modules
        ), f"{path} imports openkos.graph"
