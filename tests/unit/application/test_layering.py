"""Layering guard for `openkos.application` (D5/the layering invariant,
ADR-0018): `docs/architecture.md:112` states this convention has NO
automated CI guard, so this test is the only thing that catches
`openkos.application` importing upward from `openkos.cli`, `typer`, or
`rich` -- an adapter importing another adapter, or an application module
rendering its own output, either of which would defeat the entire point of
the application layer (MVP 3's `api`/`mcp` adapters must be able to import
any `openkos.application.*` module without dragging in Typer, Rich, or
`openkos.cli`).

Generalized (issue #918, design "the layering guard is generalized, not
copied") from a single hardcoded `_QUERY_MODULE` constant to an iteration
over every module under `src/openkos/application/`, so a THIRD context
(e.g. `application/ingest.py`, or a future `application/lifecycle.py`) is
covered by construction the moment the file exists, rather than requiring
a per-module copy of this guard.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_APPLICATION_DIR = _REPO_ROOT / "src" / "openkos" / "application"


def _application_modules() -> list[Path]:
    """Every `.py` file directly under `application/`, sorted for a stable
    iteration order -- excludes `__init__.py`, which carries no imports of
    its own worth scanning and would otherwise show up as a spurious
    always-clean entry in a failure report."""
    return sorted(
        path for path in _APPLICATION_DIR.glob("*.py") if path.name != "__init__.py"
    )


def _imported_module_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_application_directory_is_scanned_completely() -> None:
    """Sanity check that the directory scan itself is not accidentally empty
    or stale. Pins that `ingest.py` -- this change's new application-layer
    module (issue #918, Slice 1) -- is discovered by the scan the moment it
    exists, so a future reader trusts the OTHER two tests in this file
    actually looked at it rather than silently scanning zero files."""
    modules = {path.name for path in _application_modules()}
    assert modules >= {"query.py", "ingest.py"}, (
        f"expected 'query.py' and 'ingest.py' among scanned application "
        f"modules; found: {modules}"
    )


def test_application_modules_never_import_cli_typer_or_rich() -> None:
    """AST-scan every `application/*.py` module's imports; none may
    reference `openkos.cli` (or a submodule of it), `typer`, or `rich`, in
    either `import` or `from ... import` form -- a runtime AST scan rather
    than an actual import, so the assertion holds even if the offending
    import would itself fail to resolve."""
    offenders: dict[str, list[str]] = {}
    for path in _application_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _imported_module_names(tree)
        bad = [
            name
            for name in imported
            if name == "openkos.cli"
            or name.startswith("openkos.cli.")
            or name == "typer"
            or name.startswith("typer.")
            or name == "rich"
            or name.startswith("rich.")
        ]
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "openkos.application modules must never import openkos.cli, typer, "
        f"or rich; found: {offenders}"
    )


def test_application_modules_bind_no_concrete_llm_backend() -> None:
    """Every application module takes its `LLMBackend` as a parameter (ADR-
    0018 D1) so every adapter supplies its own. Importing a CONCRETE backend
    anywhere under `application/` would bind that module to Ollama and
    defeat that -- the spec requirement "No concrete backend is bound
    inside the service" otherwise rests on static reading alone, which
    nothing re-checks on a later edit. Each module's `openkos.llm.*`
    imports, if any, must be exactly `openkos.llm.base` -- the Protocol
    seam -- never a concrete implementation module."""
    offenders: dict[str, list[str]] = {}
    for path in _application_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        llm_imports = [
            name
            for name in _imported_module_names(tree)
            if name.startswith("openkos.llm.")
        ]
        bad = [name for name in llm_imports if name != "openkos.llm.base"]
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "openkos.application modules may import only openkos.llm.base, "
        f"never a concrete backend; found: {offenders}"
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
