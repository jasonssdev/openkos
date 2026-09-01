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


def test_query_module_binds_no_concrete_backend() -> None:
    """The service takes its `LLMBackend`/`Embedder` as parameters (ADR-0018
    D1) so every adapter supplies its own. Importing a CONCRETE backend here
    would bind the application layer to Ollama and defeat that -- the spec
    requirement "No concrete backend is bound inside the service" otherwise
    rests on static reading alone, which nothing re-checks on a later edit."""
    tree = ast.parse(_QUERY_MODULE.read_text(encoding="utf-8"))
    offenders = [
        name for name in _imported_module_names(tree) if name.startswith("openkos.llm.")
    ]
    assert offenders == ["openkos.llm.base"], (
        "openkos.application.query may import only the Protocol seams in "
        f"openkos.llm.base, never a concrete backend; found: {offenders}"
    )


def test_shared_write_helpers_are_never_forked() -> None:
    """`_reject_drifted_targets`, `_autocommit` and `_refresh_derived_after_write`
    are shared write infrastructure the query service calls THROUGH rather than
    owns (ADR-0018 D3). A second definition anywhere under `src/` would mean one
    write path silently diverged from the one every other command uses."""
    shared = {"_reject_drifted_targets", "_autocommit", "_refresh_derived_after_write"}
    counts = dict.fromkeys(shared, 0)
    for path in (_REPO_ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name in shared:
                counts[node.name] += 1
    assert counts == dict.fromkeys(shared, 1), (
        f"each shared write helper must keep exactly one definition; found: {counts}"
    )
