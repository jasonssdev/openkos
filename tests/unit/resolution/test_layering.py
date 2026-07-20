"""Layering-boundary guard for `openkos.resolution`.

Mirrors `tests/unit/graph/test_base.py`'s AST-based canonical-import guard:
the canonical layer (`model`/`bundle`/`state`) MUST NOT import
`openkos.resolution` (design.md's Layering section). `resolution` itself
may import `openkos.model.okf` read-only (the reverse direction), which is
asserted separately below, and this slice does not import `openkos.graph`.
"""

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "openkos"


def _collect_imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("layer", ["model", "bundle", "state"])
def test_canonical_layer_does_not_import_resolution(layer: str) -> None:
    layer_dir = _SRC_ROOT / layer
    for path in layer_dir.rglob("*.py"):
        modules = _collect_imported_modules(path.read_text())

        assert not any(
            module == "openkos.resolution" or module.startswith("openkos.resolution.")
            for module in modules
        ), f"{path} imports openkos.resolution"


def test_resolution_package_does_not_import_graph() -> None:
    """`resolution` does not depend on `graph` this slice (design.md: "does
    not import `graph` this slice")."""
    resolution_dir = _SRC_ROOT / "resolution"
    for path in resolution_dir.rglob("*.py"):
        modules = _collect_imported_modules(path.read_text())

        assert not any(
            module == "openkos.graph" or module.startswith("openkos.graph.")
            for module in modules
        ), f"{path} imports openkos.graph"


def test_resolution_only_imports_model_okf_from_canonical() -> None:
    """`resolution` may import `openkos.model.okf` read-only, but never
    `openkos.bundle` or `openkos.state` (design.md: "may import
    `openkos.model.okf` read-only, never the reverse")."""
    resolution_dir = _SRC_ROOT / "resolution"
    for path in resolution_dir.rglob("*.py"):
        modules = _collect_imported_modules(path.read_text())

        assert not any(
            module == "openkos.bundle" or module.startswith("openkos.bundle.")
            for module in modules
        ), f"{path} imports openkos.bundle"
        assert not any(
            module == "openkos.state" or module.startswith("openkos.state.")
            for module in modules
        ), f"{path} imports openkos.state"
