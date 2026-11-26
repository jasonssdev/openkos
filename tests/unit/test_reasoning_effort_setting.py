"""The opt-in `reasoning_effort` setting (#1800): an `openkos.yaml` key read
and validated by `config`, handed to the `openai-compatible` chat client only,
and refused under the default `ollama` backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import backends

_OPENAI = (
    "backend: openai-compatible\nbase_url: http://127.0.0.1:8000\nmodel: some-model\n"
)


def _write(root: Path, body: str) -> None:
    (root / "openkos.yaml").write_text(body, encoding="utf-8")


def test_the_default_is_unset() -> None:
    assert config.DEFAULT_REASONING_EFFORT is None


def test_unset_and_null_resolve_to_none(tmp_path: Path) -> None:
    _write(tmp_path, _OPENAI)
    assert config.read_config(tmp_path).reasoning_effort is None
    _write(tmp_path, _OPENAI + "reasoning_effort:\n")
    assert config.read_config(tmp_path).reasoning_effort is None


def test_a_lowercase_word_is_accepted_under_openai_compatible(tmp_path: Path) -> None:
    _write(tmp_path, _OPENAI + "reasoning_effort: low\n")
    assert config.read_config(tmp_path).reasoning_effort == "low"


@pytest.mark.parametrize(
    "body", ["1", "true", "[low]", '""', "Low", "LOW", '"low "', '"very low"', "low1"]
)
def test_an_invalid_value_is_refused_naming_the_key(tmp_path: Path, body: str) -> None:
    _write(tmp_path, _OPENAI + f"reasoning_effort: {body}\n")
    with pytest.raises(ValueError, match="'reasoning_effort'"):
        config.read_config(tmp_path)


def test_setting_it_under_ollama_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "model: gemma3\nreasoning_effort: low\n")
    with pytest.raises(ValueError, match="requires 'backend: openai-compatible'"):
        config.read_config(tmp_path)


def test_an_explicit_ollama_backend_refuses_it_too(tmp_path: Path) -> None:
    _write(tmp_path, "backend: ollama\nreasoning_effort: low\n")
    with pytest.raises(ValueError, match="reasoning_effort"):
        config.read_config(tmp_path)


def test_unset_under_ollama_changes_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "model: gemma3\n")
    assert config.read_config(tmp_path).reasoning_effort is None


class _Recorder:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _factories() -> backends.BackendFactories:
    return backends.BackendFactories(ollama=_Recorder, openai_compatible=_Recorder)


def test_the_openai_compatible_client_receives_the_effort(tmp_path: Path) -> None:
    _write(tmp_path, _OPENAI + "reasoning_effort: minimal\n")
    cfg = config.read_config(tmp_path)
    client = backends.chat_client(cfg, factories=_factories(), environ={})
    assert isinstance(client, _Recorder)
    assert client.kwargs["reasoning_effort"] == "minimal"


def test_the_openai_compatible_client_receives_none_when_unset(tmp_path: Path) -> None:
    _write(tmp_path, _OPENAI)
    cfg = config.read_config(tmp_path)
    client = backends.chat_client(cfg, factories=_factories(), environ={})
    assert isinstance(client, _Recorder)
    assert client.kwargs["reasoning_effort"] is None


def test_the_ollama_client_never_receives_the_key(tmp_path: Path) -> None:
    _write(tmp_path, "model: gemma3\n")
    cfg = config.read_config(tmp_path)
    client = backends.chat_client(cfg, factories=_factories(), environ={})
    assert isinstance(client, _Recorder)
    assert "reasoning_effort" not in client.kwargs
