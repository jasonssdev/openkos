"""Direct unit tests for `openkos.application.backends` (mcp-read-surface
slice 8, design Decision 7): chat-client construction and local-exemption
resolution, relocated out of `cli/main.py` so a non-CLI adapter (the MCP
server) can build a backend without importing `openkos.cli`. The concrete
client class always arrives as an injected `factory` -- this module binds
no concrete `openkos.llm.*` backend of its own, matching every other
`application/` module's own layering guard
(`tests/unit/application/test_layering.py`).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from openkos import config
from openkos.application import backends


def _cfg(tmp_path: Path, **overrides: Any) -> config.Config:
    config.write_config(tmp_path)
    cfg = config.read_config(tmp_path)
    return dataclasses.replace(cfg, **overrides) if overrides else cfg


class _RecordingFactory:
    """A structural stand-in for a chat client class: records the exact
    kwargs it was constructed with -- never a real `OllamaClient`, so this
    test proves the composition, not the transport."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def test_chat_client_passes_six_kwargs(tmp_path: Path) -> None:
    """`chat_client(cfg, factory=recording_factory, task=...)` calls the
    factory with exactly `model` (via `resolve_task_model`), `timeout`,
    `max_generation_tokens`, `context_window`, `temperature`, `seed` -- no
    more, no fewer. `task="edge_typing"` with a matching `models:` override
    proves the model is resolved THROUGH `resolve_task_model`, not read
    from `cfg.model` directly. Covers "The definition takes the client
    class as an injected factory". RED today: `ModuleNotFoundError` --
    `backends.py` does not exist. Kills dropping `timeout=` (or any of the
    six)."""
    cfg = _cfg(tmp_path, models={"edge_typing": "task-model:latest"})

    client = backends.chat_client(cfg, factory=_RecordingFactory, task="edge_typing")

    assert isinstance(client, _RecordingFactory)
    assert client.kwargs == {
        "model": "task-model:latest",
        "timeout": cfg.chat_timeout,
        "max_generation_tokens": cfg.max_generation_tokens,
        "context_window": cfg.context_window,
        "temperature": cfg.temperature,
        "seed": cfg.seed,
    }
    assert set(client.kwargs) == {
        "model",
        "timeout",
        "max_generation_tokens",
        "context_window",
        "temperature",
        "seed",
    }


class _StubClient:
    """A structural stand-in for `HasLocality`: a client whose `locality`
    reports a fixed `is_local`."""

    def __init__(self, *, is_local: bool) -> None:
        self._is_local = is_local

    @property
    def locality(self) -> _StubClient:
        return self

    @property
    def is_local(self) -> bool:
        return self._is_local


def test_resolve_local_exemption_truth_table(tmp_path: Path) -> None:
    """The 4 combinations of `client.locality.is_local` and
    `cfg.confidential_local_exemption`, asserting `and` (not `or`)
    semantics. RED today: same reason as above. Kills using `or` instead of
    `and`."""
    base_cfg = _cfg(tmp_path)
    for is_local, policy, expected in (
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ):
        cfg = dataclasses.replace(base_cfg, confidential_local_exemption=policy)
        client = _StubClient(is_local=is_local)
        assert backends.resolve_local_exemption(client, cfg) is expected, (
            is_local,
            policy,
        )
