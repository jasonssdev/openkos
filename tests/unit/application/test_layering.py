"""Layering guard for `openkos.application` (D5/the layering invariant,
ADR-0018): `docs/architecture.md:112` states this convention has NO
automated CI guard, so this test is the only thing that catches
`openkos.application` importing upward from `openkos.cli` -- an adapter
importing another adapter, which would defeat the entire point of the
application layer (MVP 3's `api`/`mcp` adapters must be able to import
`openkos.application.query` without dragging in Typer or `openkos.cli`).
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUERY_MODULE = _REPO_ROOT / "src" / "openkos" / "application" / "query.py"


def _imported_module_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_query_module_never_imports_cli() -> None:
    """AST-scan `application/query.py`'s imports; none may reference
    `openkos.cli` or a submodule of it, in either `import` or `from ... import`
    form -- a runtime AST scan rather than an actual import, so the assertion
    holds even if the offending import would itself fail to resolve."""
    tree = ast.parse(_QUERY_MODULE.read_text(encoding="utf-8"))
    imported = _imported_module_names(tree)
    offenders = [
        name
        for name in imported
        if name == "openkos.cli" or name.startswith("openkos.cli.")
    ]
    assert not offenders, (
        f"openkos.application.query must never import openkos.cli; found: {offenders}"
    )
