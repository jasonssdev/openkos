"""Delegation guard for `cli/main.py`'s chat-client construction
(mcp-read-surface slice 8, design Decision 7): `_chat_client` and
`_resolve_local_exemption` are relocated definitions -- their real bodies
now live in `application/backends.py` -- kept under their existing names
as one-line delegators, so every existing test seam that patches them BY
NAME keeps working. Most load-bearingly, `tests/unit/conftest.py`'s
autouse network guard patches `openkos.cli.main.OllamaClient`, never
`application.backends`; a delegator that stopped reading that name at call
time would make roughly 200 existing patches silently inert.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from openkos import config
from openkos.application import backends as application_backends
from openkos.application.backends import BackendFactories
from openkos.cli import main as main_mod

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src" / "openkos"


def _delegator_function(name: str) -> ast.FunctionDef:
    tree = ast.parse((_SRC / "cli" / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in cli/main.py")


def _non_docstring_body(node: ast.FunctionDef) -> list[ast.stmt]:
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _count_definitions(name: str) -> int:
    """How many `def <name>(...)` exist under `src/`, across every module --
    a second copy of a relocated body left behind by an incomplete move
    would pass every OTHER test while quietly diverging from the one
    callers actually reach."""
    count = 0
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
    return count


def test_delegators_are_single_line_and_singly_defined() -> None:
    """`cli.main._resolve_local_exemption` is a single `return` statement
    calling `application_backends.resolve_local_exemption`; a source-wide
    AST/grep confirms its real body has exactly one definition under
    `src/`. Covers "The CLI keeps one-line delegators under their existing
    names" and "The CLI's observable behavior, and its test seam, are
    unaffected" (structural half). Kills leaving a second copy of the real
    body after the move.

    `_chat_client`/`_embed_client` are NOT covered here since issue #1057
    Phase 13b (task 13.27): both now call `_maybe_warn_insecure_key(cfg)`
    before delegating, so they are no longer single-statement -- their own
    shape is pinned by `test_chat_and_embed_client_delegate_and_warn`
    below instead. The mcp spec's "one-line delegator" MUST names
    `_chat_client` and `_resolve_local_exemption` only; `_embed_client` was
    never in that MUST clause."""
    node = _delegator_function("_resolve_local_exemption")
    body = _non_docstring_body(node)
    assert len(body) == 1, (
        f"_resolve_local_exemption must be a single statement: {body}"
    )
    (stmt,) = body
    assert isinstance(stmt, ast.Return), (
        "_resolve_local_exemption must be a bare return"
    )
    call = stmt.value
    assert isinstance(call, ast.Call), "_resolve_local_exemption must return a call"
    func = call.func
    assert isinstance(func, ast.Attribute), (
        "_resolve_local_exemption must call an attribute"
    )
    assert func.attr == "resolve_local_exemption", (
        f"_resolve_local_exemption must call "
        f"application_backends.resolve_local_exemption, found {ast.dump(func)}"
    )
    assert _count_definitions("resolve_local_exemption") == 1, (
        "resolve_local_exemption must be defined exactly once under src/"
    )


def test_chat_and_embed_client_delegate_and_warn() -> None:
    """`_chat_client`/`_embed_client` (issue #1057 Phase 13b, task 13.27)
    are each exactly TWO statements: a bare `_maybe_warn_insecure_key(cfg)`
    call, then a bare `return application_backends.<real_name>(...)` --
    never reimplementing dispatch logic of their own. Each real body
    (`chat_client`, `embed_client`) still has exactly one definition under
    `src/`. RED today: `_maybe_warn_insecure_key` doesn't exist and neither
    delegator calls it."""
    for delegator, real_name in (
        ("_chat_client", "chat_client"),
        ("_embed_client", "embed_client"),
    ):
        node = _delegator_function(delegator)
        body = _non_docstring_body(node)
        assert len(body) == 2, f"{delegator} must be exactly two statements: {body}"
        warn_stmt, return_stmt = body
        assert isinstance(warn_stmt, ast.Expr), (
            f"{delegator}'s first statement must be a bare call expression"
        )
        assert isinstance(warn_stmt.value, ast.Call), (
            f"{delegator}'s first statement must be a call"
        )
        warn_func = warn_stmt.value.func
        assert isinstance(warn_func, ast.Name), (
            f"{delegator} must call a bare name first, found {ast.dump(warn_func)}"
        )
        assert warn_func.id == "_maybe_warn_insecure_key", (
            f"{delegator} must call _maybe_warn_insecure_key first, found {warn_func.id}"
        )
        assert isinstance(return_stmt, ast.Return), (
            f"{delegator} must end in a bare return"
        )
        call = return_stmt.value
        assert isinstance(call, ast.Call), f"{delegator} must return a call"
        func = call.func
        assert isinstance(func, ast.Attribute), f"{delegator} must call an attribute"
        assert func.attr == real_name, (
            f"{delegator} must call application_backends.{real_name}, found "
            f"{ast.dump(func)}"
        )
        assert _count_definitions(real_name) == 1, (
            f"{real_name} must be defined exactly once under src/"
        )


def test_embed_client_delegator_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cli.main._embed_client(cfg)` (issue #1057 Phase 10, task 10.1) is a
    new one-line delegator mirroring `_chat_client`: it calls
    `application_backends.embed_client(cfg, factories=_backend_factories())`
    -- confirmed here by spying on `application_backends.embed_client` and
    asserting both the `cfg` and the `factories` it receives. RED today:
    `_embed_client` doesn't exist."""
    config.write_config(tmp_path)
    cfg = config.read_config(tmp_path)

    calls: list[tuple[config.Config, BackendFactories]] = []
    original_embed_client = application_backends.embed_client

    def _spy_embed_client(
        spied_cfg: config.Config, *, factories: BackendFactories
    ) -> object:
        calls.append((spied_cfg, factories))
        return original_embed_client(spied_cfg, factories=factories)

    monkeypatch.setattr(application_backends, "embed_client", _spy_embed_client)

    client = main_mod._embed_client(cfg)

    assert len(calls) == 1
    called_cfg, called_factories = calls[0]
    assert called_cfg is cfg
    assert called_factories.ollama is main_mod.__dict__["OllamaClient"]
    assert (
        called_factories.openai_compatible
        is (main_mod.__dict__["OpenAICompatibleClient"])
    )
    assert isinstance(client, called_factories.ollama)


def test_ollama_client_monkeypatch_still_intercepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuses (does not rewrite) the existing autouse network-guard fixture
    that patches `openkos.cli.main.OllamaClient`, and confirms a call path
    that constructs a chat client through the new
    `application/backends.py` delegator still gets the patched class.
    Proven with a mutation: patch `application.backends.chat_client` to
    bypass the injected factories and construct `OllamaClient` imported
    directly from `openkos.llm.ollama` instead; confirm this mutation makes
    the existing `tests/unit/conftest.py` network guard's patch silently
    inert (the returned client is no longer the patched class); then
    restore the real `chat_client` and confirm the patch intercepts again.
    Covers "The CLI's observable behavior, and its test seam, are
    unaffected" (the ~200-monkeypatch must-have), extended to the
    `factories=` parameter shape (issue #1057 task 9.20)."""
    config.write_config(tmp_path)
    cfg = config.read_config(tmp_path)

    # The autouse `_offline_ollama_by_default` fixture already patched
    # `openkos.cli.main.OllamaClient` to `OfflineOllama` for this test.
    # Read via `__dict__` (not `main_mod.OllamaClient`, and typed
    # `type[object]` rather than the module's own declared return type) so
    # this reads whatever class is bound RIGHT NOW, including the mutation
    # below, rather than a name mypy would otherwise treat as statically
    # fixed and unmodifiable.
    patched_class: type[object] = main_mod.__dict__["OllamaClient"]
    client = main_mod._chat_client(cfg)
    assert isinstance(client, patched_class)

    # The mutation: bypass the injected factories entirely, importing
    # OllamaClient directly the way a REPOINTED call site (not a
    # delegator) would -- this is exactly what design Decision 4 forbids.
    from openkos.llm import ollama as ollama_module

    original_chat_client = application_backends.chat_client

    def _bypassing_chat_client(
        bypassed_cfg: config.Config,
        *,
        factories: BackendFactories,
        task: str | None = None,
    ) -> object:
        del factories  # deliberately ignored -- the defect under test
        return ollama_module.OllamaClient(
            model=config.resolve_task_model(bypassed_cfg, task),
            timeout=bypassed_cfg.chat_timeout,
            max_generation_tokens=bypassed_cfg.max_generation_tokens,
            context_window=bypassed_cfg.context_window,
            temperature=bypassed_cfg.temperature,
            seed=bypassed_cfg.seed,
        )

    monkeypatch.setattr(application_backends, "chat_client", _bypassing_chat_client)
    mutated_client = main_mod._chat_client(cfg)
    assert not isinstance(mutated_client, patched_class), (
        "the network guard's patch went silently inert once the injected "
        "factory was bypassed -- this is exactly the regression this test "
        "exists to catch"
    )
    assert isinstance(mutated_client, ollama_module.OllamaClient)

    # Restore, and confirm the guard's patch intercepts again.
    monkeypatch.setattr(application_backends, "chat_client", original_chat_client)
    restored_client = main_mod._chat_client(cfg)
    assert isinstance(restored_client, patched_class)
