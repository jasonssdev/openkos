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


_AUTOCOMMIT_PATH = _SRC_ROOT / "vcs" / "autocommit.py"
_AUTOCOMMIT_FORBIDDEN = (
    "openkos.state",
    "openkos.retrieval",
    "openkos.graph",
    "openkos.application",
    "openkos.cli",
    "openkos.mcp",
    "typer",
    "rich",
)
_AUTOCOMMIT_ALLOWED_OPENKOS = {
    "openkos.vcs",
    "openkos.vcs.git",
    "openkos.config",
    "openkos.model",
    "openkos.model.okf",
}


def _imported_names(source: str) -> set[str]:
    """Like `_collect_imported_modules`, but a `from pkg import name` also
    contributes `pkg.name`, so `from openkos import state` is seen as
    `openkos.state` and cannot slip past a prefix check."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _is_under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def test_autocommit_imports_only_the_allowed_set() -> None:
    """The print-free commit core may reach the standard library, `vcs.git`,
    `openkos.config` and `openkos.model.okf` and nothing that prints, prompts,
    or sits in the derived/application/adapter layers."""
    modules = _imported_names(_AUTOCOMMIT_PATH.read_text())

    assert modules, "autocommit.py imports nothing: the guard would be vacuous"
    forbidden = {
        module
        for module in modules
        if any(_is_under(module, prefix) for prefix in _AUTOCOMMIT_FORBIDDEN)
    }
    assert not forbidden, f"autocommit.py imports forbidden modules: {forbidden}"
    stray = {
        module
        for module in modules
        if _is_under(module, "openkos")
        and module != "openkos"
        and module not in _AUTOCOMMIT_ALLOWED_OPENKOS
    }
    assert not stray, f"autocommit.py imports outside the allowed set: {stray}"
    assert any(_is_under(module, "openkos.vcs") for module in modules)
