"""Source-derived guard: every direct `OllamaClient(...)`/
`OpenAICompatibleClient(...)` construction under `src/openkos/` outside
`llm/` must go through the resolver seam (`application/backends.py`'s
`chat_client`/`embed_client`/`diagnostics_client`) instead -- except inside
a `BackendFactories(...)` expression, which IS the one legitimate place a
concrete class reference is handed to the resolver (issue #1057 Phase 9,
design Decision 4; backend-selection spec "One Resolver Seam Constructs
Every Chat And Embed Client").

**Construction-guard ratchet** (tasks-phase decision 1, tasks.md): Phase 9
migrates exactly the three CHAT sites -- `cli/main.py::_chat_client`,
`cli/curate.py`'s stage loop, `mcp/server.py::_make_llm` -- none of which
construct a client directly any more (they all call
`application_backends.chat_client(...)`), so none needs an allowlist entry.
Ten sites (seven embed constructions plus three diagnostics/probe sites,
the "Construction sites routed through the resolver" table) still
construct directly; Phase 10 migrates them and shrinks `_PENDING_SITES` to
empty, at which point this guard's final test asserts the unconditional
form (no allowlist at all).

Walks the AST rather than the raw text on purpose: several docstrings in
`application/backends.py`/`cli/main.py` quote `OllamaClient(model=cfg.model)`
while describing the resolver seam, and a text scan would flag those as
offenders. Prose is not a construction site; only a `Call` node is.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "openkos"
_LLM_DIR = _SRC / "llm"

_CONCRETE_CLASS_NAMES = frozenset({"OllamaClient", "OpenAICompatibleClient"})

_PENDING_SITES: frozenset[tuple[str, int]] = frozenset(
    {
        ("cli/main.py", 339),  # init picker probe (_probe_installed_models)
        ("cli/main.py", 1770),  # init preflight
        ("cli/main.py", 4194),  # _refresh_derived_after_write
        ("cli/main.py", 4762),  # _ingest_batch's embed-host advisory
        ("cli/main.py", 5748),  # _ingest_single
        ("cli/main.py", 14831),  # query
        ("cli/main.py", 15548),  # reindex
        ("cli/main.py", 15930),  # doctor's _build_client
        ("cli/main.py", 16382),  # mcp_cmd
        ("mcp/server.py", 212),  # _make_embedder
    }
)
"""Exactly the ten sites Phase 10 migrates (tasks-phase decision 1): seven
embed-site constructions plus the three diagnostics/probe sites. A stale
entry -- one that no longer points at an ACTUAL direct construction -- is
itself a guard failure (`test_no_direct_client_construction_outside_llm_and_factories`'s
second assertion), so the list cannot rot silently as Phase 10 migrates
sites out from under it."""


def _src_modules() -> list[Path]:
    """Every `.py` file under `src/openkos/` EXCEPT `llm/` (the client
    modules themselves, where a concrete construction is the whole point)."""
    return sorted(path for path in _SRC.rglob("*.py") if _LLM_DIR not in path.parents)


def _is_backend_factories_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "BackendFactories"
    return isinstance(func, ast.Attribute) and func.attr == "BackendFactories"


def _direct_construction_sites(tree: ast.Module) -> list[tuple[ast.Call, bool]]:
    """Every `OllamaClient(...)`/`OpenAICompatibleClient(...)` CALL node in
    `tree`, paired with whether it sits inside a `BackendFactories(...)`
    expression (exempt) -- walked upward through a parent map built once per
    tree, since `ast` gives no parent pointers natively."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    sites: list[tuple[ast.Call, bool]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _CONCRETE_CLASS_NAMES
        ):
            continue
        exempt = False
        current: ast.AST | None = node
        while current is not None:
            current = parents.get(current)
            if _is_backend_factories_call(current):
                exempt = True
                break
        sites.append((node, exempt))
    return sites


def test_no_direct_client_construction_outside_llm_and_factories() -> None:
    """An AST walk of every `.py` under `src/openkos/` EXCEPT `llm/` rejects
    a bare `OllamaClient(`/`OpenAICompatibleClient(` call EXCEPT inside a
    `BackendFactories(...)` expression, carrying the `_PENDING_SITES`
    allowlist above (task 9.27). Also asserts every allowlisted entry still
    points at an ACTUAL direct construction -- a stale entry is itself a
    guard failure. **RED today (pre-Phase-9)**: the three chat sites this
    change migrates were also direct constructions before Phase 9 landed;
    **GREEN as shipped**: exactly the ten `_PENDING_SITES` entries remain,
    all real."""
    offenders: dict[str, list[int]] = {}
    matched_pending: set[tuple[str, int]] = set()

    for path in _src_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(_SRC).as_posix()
        for node, exempt in _direct_construction_sites(tree):
            if exempt:
                continue
            key = (rel, node.lineno)
            if key in _PENDING_SITES:
                matched_pending.add(key)
                continue
            offenders.setdefault(rel, []).append(node.lineno)

    assert offenders == {}, (
        "direct OllamaClient(...)/OpenAICompatibleClient(...) construction "
        f"found outside llm/, BackendFactories(...), and _PENDING_SITES: "
        f"{offenders} -- route it through application_backends.chat_client/"
        "embed_client/diagnostics_client instead"
    )

    stale = sorted(
        f"{path}:{line}" for (path, line) in _PENDING_SITES - matched_pending
    )
    assert stale == [], (
        f"_PENDING_SITES entries that no longer point at a real direct "
        f"construction (stale allowlist -- narrow it): {stale}"
    )


# Mutation-proof (task 9.29): temporarily added
# `OllamaClient(model="mutation-probe")` as an extra statement inside
# `application/backends.py::chat_client`'s Ollama branch (one of the three
# MIGRATED chat sites' real construction point, since `_chat_client`/
# curate's stage loop/`_make_llm` all delegate there) -- confirmed
# `test_no_direct_client_construction_outside_llm_and_factories` failed,
# reporting exactly `{'application/backends.py': [281]}` under `offenders`
# (not `_PENDING_SITES`, since that site is not allowlisted). Reverted with
# the exact inverse edit, purged `__pycache__`, reconfirmed GREEN.
