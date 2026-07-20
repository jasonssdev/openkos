"""Layering-boundary guard for `openkos.resolution`.

Mirrors `tests/unit/graph/test_base.py`'s AST-based canonical-import guard:
the canonical layer (`model`/`bundle`/`state`) MUST NOT import
`openkos.resolution` (design.md's Layering section). `resolution` itself
may import `openkos.model.okf` read-only (the reverse direction), which is
asserted separately below.

Slice 2 (entity-resolution adjudication) adds a POSITIVE assertion:
`resolution` MAY import `openkos.llm` (a sibling, not canonical) -- the
`adjudication` leaf injects an `LLMBackend` -- which keeps the AST guard
non-vacuous by proving the allowed import actually happens somewhere,
rather than merely asserting the forbidden ones don't.

MVP-2 slice 2b (`llm-edge-production`) adds a second POSITIVE assertion:
`resolution` MAY import `openkos.graph` too, now that `edge_typing.py`
owns the `graph` read internally (derived -> derived is allowed; only
canonical -> `graph` and `cli` -> `graph` stay forbidden, guarded
separately by `tests/unit/graph/test_base.py`'s canonical-layer guard and
`tests/unit/graph/test_analysis.py`'s "No CLI Surface" guard, both
UNMODIFIED by this slice). This supersedes the prior "this slice does not
import `openkos.graph`" assumption (see `resolution/__init__.py`'s
docstring).
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


def test_resolution_may_import_graph() -> None:
    """`resolution` MAY import `openkos.graph` (design.md, slice 2b,
    "llm-edge-production"): `edge_typing.py` owns the `graph` read
    internally (derived -> derived, allowed) so `cli/main.py` never has to
    import `openkos.graph` directly. A POSITIVE assertion -- not just "not
    forbidden" -- so this guard stays non-vacuous: it proves `openkos.graph`
    really is imported somewhere under `resolution`, and would fail loudly
    if that import were ever removed without updating this test."""
    resolution_dir = _SRC_ROOT / "resolution"
    imports_graph = any(
        module == "openkos.graph" or module.startswith("openkos.graph.")
        for path in resolution_dir.rglob("*.py")
        for module in _collect_imported_modules(path.read_text())
    )
    assert imports_graph, "expected `resolution` to import `openkos.graph` somewhere"


def test_resolution_may_import_llm() -> None:
    """`resolution` MAY import `openkos.llm` (design.md, slice 2): the
    `adjudication` leaf injects an `LLMBackend` rather than constructing an
    `OllamaClient` itself. A POSITIVE assertion -- not just "not
    forbidden" -- so this guard stays non-vacuous: it proves `openkos.llm`
    really is imported somewhere under `resolution`, and would fail loudly
    if that import were ever removed without updating this test."""
    resolution_dir = _SRC_ROOT / "resolution"
    imports_llm = any(
        module == "openkos.llm" or module.startswith("openkos.llm.")
        for path in resolution_dir.rglob("*.py")
        for module in _collect_imported_modules(path.read_text())
    )
    assert imports_llm, "expected `resolution` to import `openkos.llm` somewhere"


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
