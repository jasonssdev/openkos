"""Layering guard for `openkos.mcp` (design Decisions 2 and 12, ADR-0027):

- `asyncio` is imported only under `src/openkos/mcp/` -- nowhere else in
  `src/openkos/`.
- No module under `src/openkos/mcp/` imports `openkos.cli`.
- No module under `src/openkos/application/` imports `openkos.mcp`.
- Every module under `src/openkos/mcp/` imports only stdlib or
  `openkos.*`.
- Among `mcp/`'s own modules, only `gate.py` references
  `sensitivity.blocks_disclosure` or `sensitivity.disclosable_concept_ids`
  -- a CALL-SITE scan, not only an import scan, since an aliased import
  could otherwise dodge a plain import check.
- No module under `src/openkos/mcp/` imports the lock-acquisition helper
  (`openkos.lock`) -- covers "A read tool never raises WorkspaceBusyError"
  and "Serving never waits on the workspace lock" by construction.
- The CLI imports `openkos.mcp` lazily: absent from `cli/main.py`'s
  module-level imports, present only inside the `mcp` verb's own body.

Mirrors `tests/unit/bundle/test_layering.py` and
`tests/unit/test_sensitivity.py::test_sensitivity_module_import_bound`'s
AST-based approach: a static scan, not an actual import, so the assertion
holds even if an offending import would itself fail to resolve.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "openkos"
_MCP_DIR = _SRC_ROOT / "mcp"
_APPLICATION_DIR = _SRC_ROOT / "application"
_CLI_MAIN = _SRC_ROOT / "cli" / "main.py"

_PREDICATE_NAMES = frozenset({"blocks_disclosure", "disclosable_concept_ids"})
_SENSITIVITY_NAMES_AT_MODULE_LEVEL = frozenset({"openkos.sensitivity"})


def _mcp_modules() -> list[Path]:
    return sorted(_MCP_DIR.glob("*.py"))


def _src_modules() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _application_modules() -> list[Path]:
    return sorted(
        path for path in _APPLICATION_DIR.glob("*.py") if path.name != "__init__.py"
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_module_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _references_predicate(tree: ast.Module) -> bool:
    """Whether `tree` references either disclosure-predicate name, as a
    plain name (`from openkos.sensitivity import blocks_disclosure` then a
    bare call) or an attribute access (`sensitivity.blocks_disclosure`)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _PREDICATE_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _PREDICATE_NAMES:
            return True
    return False


def test_layering_invariants() -> None:
    # -- asyncio confined to mcp/ ------------------------------------------
    asyncio_offenders = [
        path
        for path in _src_modules()
        if _MCP_DIR not in path.parents
        and any(
            name == "asyncio" or name.startswith("asyncio.")
            for name in _imported_module_names(_tree(path))
        )
    ]
    assert asyncio_offenders == [], (
        f"asyncio must be imported only under mcp/; found: {asyncio_offenders}"
    )

    # -- mcp/ never imports openkos.cli, and only stdlib or openkos.* -------
    cli_offenders: dict[str, list[str]] = {}
    non_openkos_offenders: dict[str, list[str]] = {}
    lock_offenders: dict[str, list[str]] = {}
    predicate_offenders: list[str] = []
    for path in _mcp_modules():
        tree = _tree(path)
        imported = _imported_module_names(tree)

        cli_hits = [
            name
            for name in imported
            if name == "openkos.cli" or name.startswith("openkos.cli.")
        ]
        if cli_hits:
            cli_offenders[path.name] = cli_hits

        non_openkos = [
            name
            for name in imported
            if not (name == "openkos" or name.startswith("openkos."))
            and name.split(".")[0] not in __import__("sys").stdlib_module_names
        ]
        if non_openkos:
            non_openkos_offenders[path.name] = non_openkos

        lock_hits = [
            name
            for name in imported
            if name == "openkos.lock" or name.startswith("openkos.lock.")
        ]
        if lock_hits:
            lock_offenders[path.name] = lock_hits

        if path.name != "gate.py" and _references_predicate(tree):
            predicate_offenders.append(path.name)

    assert cli_offenders == {}, f"mcp/ must never import openkos.cli: {cli_offenders}"
    assert non_openkos_offenders == {}, (
        f"mcp/ must import only stdlib or openkos.*: {non_openkos_offenders}"
    )
    assert lock_offenders == {}, (
        f"mcp/ must never import the lock-acquisition helper: {lock_offenders}"
    )
    assert predicate_offenders == [], (
        "only gate.py may reference the disclosure predicate or its "
        f"allowed-set sibling; found in: {predicate_offenders}"
    )

    # -- application/ never imports openkos.mcp -----------------------------
    application_offenders = {
        path.name: hits
        for path in _application_modules()
        if (
            hits := [
                name
                for name in _imported_module_names(_tree(path))
                if name == "openkos.mcp" or name.startswith("openkos.mcp.")
            ]
        )
    }
    assert application_offenders == {}, (
        f"application/ must never import openkos.mcp: {application_offenders}"
    )

    # -- the CLI imports mcp lazily, only inside the verb's own body --------
    cli_tree = _tree(_CLI_MAIN)
    module_level_names = {
        alias.name
        for node in cli_tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in cli_tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name == "openkos.mcp" or name.startswith("openkos.mcp.")
        for name in module_level_names
    ), "cli/main.py must import openkos.mcp lazily, not at module level"

    body_only_mcp_imports = [
        alias.name
        for node in ast.walk(cli_tree)
        if isinstance(node, ast.Import) and node not in cli_tree.body
        for alias in node.names
        if alias.name == "openkos.mcp" or alias.name.startswith("openkos.mcp.")
    ] + [
        node.module
        for node in ast.walk(cli_tree)
        if isinstance(node, ast.ImportFrom)
        and node not in cli_tree.body
        and node.module
        and (node.module == "openkos.mcp" or node.module.startswith("openkos.mcp."))
    ]
    assert body_only_mcp_imports, (
        "the mcp verb must import openkos.mcp somewhere inside a function "
        "body, otherwise the lazy-import contract has nothing behind it"
    )


def test_mcp_server_may_import_both_concrete_client_classes() -> None:
    """`mcp/server.py` may import BOTH `OllamaClient` and
    `OpenAICompatibleClient` (and their exception types), while the ban on
    `openkos.cli` imports still holds (issue #1057 task 9.22b, mcp spec
    "mcp/server.py may import both concrete client classes").

    Nothing in the layering guard above bars `openkos.llm.*` imports for
    `mcp/` -- only `openkos.cli` is banned -- so this is a positive
    assertion that the resolver-seam wiring (`_backend_factories()`)
    actually landed, run ALONGSIDE `test_layering_invariants`'s existing
    `cli_offenders`/`non_openkos_offenders` checks rather than replacing
    them."""
    server_tree = _tree(_MCP_DIR / "server.py")
    imported = set(_imported_module_names(server_tree))

    assert "openkos.llm.ollama" in imported
    assert "openkos.llm.openai_compatible" in imported

    cli_hits = [
        name
        for name in imported
        if name == "openkos.cli" or name.startswith("openkos.cli.")
    ]
    assert cli_hits == []
