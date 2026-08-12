"""Layering-boundary guard for `bundle/decisions.py` (task A2.5).

Mirrors `tests/unit/resolution/test_layering.py`'s AST-based canonical-import
guard, applied to the canonical layer's own `bundle` package: `bundle` MUST
NOT import `openkos.graph` (AGENTS.md:41 -- the canonical layer, `model` /
`bundle` / `state`, never depends on the derived layer)."""

import ast
from pathlib import Path

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


def test_bundle_decisions_does_not_import_graph() -> None:
    path = _SRC_ROOT / "bundle" / "decisions.py"
    modules = _collect_imported_modules(path.read_text())

    assert not any(
        module == "openkos.graph" or module.startswith("openkos.graph.")
        for module in modules
    ), f"{path} imports openkos.graph"


def test_bundle_package_does_not_import_graph() -> None:
    """Positive coverage over the WHOLE `bundle` package, not only
    `decisions.py`: the canonical layer never depends on the derived layer
    (AGENTS.md:41), and a future sibling module gaining this import should
    fail the same guard `resolution`'s own suite applies."""
    bundle_dir = _SRC_ROOT / "bundle"
    for path in bundle_dir.rglob("*.py"):
        modules = _collect_imported_modules(path.read_text())
        assert not any(
            module == "openkos.graph" or module.startswith("openkos.graph.")
            for module in modules
        ), f"{path} imports openkos.graph"
