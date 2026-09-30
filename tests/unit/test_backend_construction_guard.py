"""Source-derived guard: every direct `OllamaClient(...)`/
`OpenAICompatibleClient(...)` construction under `src/openkos/` outside
`llm/` must go through the resolver seam (`application/backends.py`'s
`chat_client`/`embed_client`/`diagnostics_client`) instead -- except inside
a `BackendFactories(...)` expression, which IS the one legitimate place a
concrete class reference is handed to the resolver (issue #1057 Phase 9-10,
design Decision 4; backend-selection spec "One Resolver Seam Constructs
Every Chat And Embed Client").

**Construction-guard ratchet, closed** (tasks-phase decision 1, tasks.md):
Phase 9 migrated the three CHAT sites -- `cli/main.py::_chat_client`,
`cli/curate.py`'s stage loop, `mcp/server.py::_make_llm` -- carrying an
explicit `_PENDING_SITES` allowlist naming the seven embed constructions
plus three diagnostics/probe sites Phase 9 left direct. Phase 10 migrated
all ten (`cli/main.py::_embed_client`/`application_backends.diagnostics_client`
at every remaining site, `mcp/server.py::_make_embedder`), so the allowlist
is now EMPTY and this guard asserts the UNCONDITIONAL form: zero direct
constructions anywhere outside `llm/`/`BackendFactories(...)`.

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
    `BackendFactories(...)` expression -- the FINAL, unconditional form
    (task 10.19): no allowlist parameter at all. **RED at Phase 9**: ten
    sites (seven embed constructions plus three diagnostics/probe sites)
    were still direct; **GREEN as shipped (Phase 10 complete)**: zero
    exceptions anywhere outside `llm/`/`BackendFactories(...)`. Closes
    backend-selection's "no test that selects either backend can reach the
    network" contract for real."""
    offenders: dict[str, list[int]] = {}

    for path in _src_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(_SRC).as_posix()
        for node, exempt in _direct_construction_sites(tree):
            if exempt:
                continue
            offenders.setdefault(rel, []).append(node.lineno)

    assert offenders == {}, (
        "direct OllamaClient(...)/OpenAICompatibleClient(...) construction "
        f"found outside llm/ and BackendFactories(...): {offenders} -- route "
        "it through application_backends.chat_client/embed_client/"
        "diagnostics_client instead"
    )


def test_mutation_proof_a_migrated_embed_site_reintroducing_direct_construction_fails() -> (
    None
):
    """Mutation-proof (task 10.20): a SYNTHETIC source fixture, shaped like
    one of the ten sites Phase 10 migrated off the `_PENDING_SITES`
    allowlist (`cli/main.py::_embed_client`), with a direct
    `OllamaClient(model=cfg.embedding_model)` construction reintroduced in
    place of the delegating `application_backends.embed_client(...)` call,
    is flagged as an offender by the now-unconditional guard (mirrors
    `test_neutral_catch_sites.py`'s synthetic-fixture mutation-proof
    precedent rather than live-editing the real, currently-migrated
    source). Proves the guard can still fail after the allowlist was
    removed, rather than having quietly gone vacuous."""
    mutated_source = (
        "from openkos import config\n"
        "from openkos.llm.ollama import OllamaClient\n"
        "\n"
        "\n"
        "def _embed_client(cfg: config.Config) -> object:\n"
        "    return OllamaClient(model=cfg.embedding_model)\n"
    )
    mutated_tree = ast.parse(mutated_source)

    offenders: dict[str, list[int]] = {}
    for node, exempt in _direct_construction_sites(mutated_tree):
        if exempt:
            continue
        offenders.setdefault("cli/main.py", []).append(node.lineno)

    assert offenders != {}, (
        "the guard failed to catch a reintroduced direct OllamaClient(...) "
        "construction at a migrated site -- it has gone vacuous"
    )


# Mutation-proof (task 9.29, Phase 9): temporarily added
# `OllamaClient(model="mutation-probe")` as an extra statement inside
# `application/backends.py::chat_client`'s Ollama branch (one of the three
# MIGRATED chat sites' real construction point, since `_chat_client`/
# curate's stage loop/`_make_llm` all delegate there) -- confirmed
# `test_no_direct_client_construction_outside_llm_and_factories` failed,
# reporting exactly `{'application/backends.py': [281]}` under `offenders`
# (not `_PENDING_SITES`, since that site is not allowlisted). Reverted with
# the exact inverse edit, purged `__pycache__`, reconfirmed GREEN.
#
# Mutation-proof (task 10.20, Phase 10): the PERSISTED test above uses a
# synthetic source fixture (task text explicitly permits "scratch copy or
# planted fixture") since the real `cli/main.py::_embed_client` must stay
# migrated at rest. Additionally confirmed against the REAL, live file
# during this apply run: temporarily replaced `_embed_client`'s
# `return application_backends.embed_client(cfg, factories=_backend_factories())`
# body with `return OllamaClient(model=cfg.embedding_model)` (one of the ten
# Phase-10-migrated sites); `test_no_direct_client_construction_outside_llm_and_factories`
# failed, reporting exactly `{'cli/main.py': [206]}` under `offenders`.
# Reverted with the exact inverse edit, purged `__pycache__`, reconfirmed
# GREEN.
