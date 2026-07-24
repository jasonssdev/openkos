"""Layering-boundary guard for `openkos.vcs` (git-lifecycle, Slice 1).

Mirrors `tests/unit/graph/test_base.py` and `tests/unit/resolution/
test_layering.py`'s AST-based canonical-import guard: the canonical layer
(`model`/`bundle`/`state`) MUST NOT import `openkos.vcs` (design.md's Git
Step Ordering and Layering requirement) -- git orchestration lives in the
`init` CLI verb, calling `vcs.git` write primitives, never the reverse."""

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
def test_canonical_layer_does_not_import_vcs(layer: str) -> None:
    layer_dir = _SRC_ROOT / layer
    for path in layer_dir.rglob("*.py"):
        modules = _collect_imported_modules(path.read_text())

        assert not any(
            module == "openkos.vcs" or module.startswith("openkos.vcs.")
            for module in modules
        ), f"{path} imports openkos.vcs"


def test_cli_main_imports_vcs_git() -> None:
    """Positive assertion (non-vacuous guard): `cli/main.py` really DOES
    import `openkos.vcs.git` -- proving the git-setup orchestration lives
    where design.md says it should (the `init` CLI verb), not merely that
    the canonical layer avoids it."""
    source = (_SRC_ROOT / "cli" / "main.py").read_text()
    modules = _collect_imported_modules(source)

    assert any(
        module == "openkos.vcs" or module.startswith("openkos.vcs.")
        for module in modules
    ), "expected cli/main.py to import openkos.vcs"
