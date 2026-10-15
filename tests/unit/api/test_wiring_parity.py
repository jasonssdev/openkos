"""The API's default chat client: covered by the network guard, and the CLI's twin.

The API builds its own `BackendFactories` from its own module globals, as the
CLI and the MCP server do. Three things follow and are pinned here: the autouse
network guard reaches a client built through the API path; the factories hold
class references read at call time; and for the same `openkos.yaml` the API
selects the same backend, model and constructor arguments as the CLI.
"""

from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.api import _wiring
from openkos.application import backends
from openkos.cli import main as cli_main
from tests.unit.application.curation_support import make_workspace
from tests.unit.conftest import OfflineOllama, OfflineOpenAICompatible

_KEY = "sk-test-from-the-environment"
_TASKS = (None, "adjudication", "edge_typing", "volatility_typing")


def _bound(name: str) -> Any:
    """The module-level name `_wiring` binds, read at call time as the factories
    read it."""
    return vars(_wiring)[name]


class _Recorder:
    """A client factory that records how it was called and builds nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def named(self, label: str) -> _Named:
        return _Named(self, label)


class _Named:
    def __init__(self, recorder: _Recorder, label: str) -> None:
        self._recorder = recorder
        self._label = label

    def __call__(self, **kwargs: object) -> object:
        self._recorder.calls.append((self._label, kwargs))
        return object()


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    path = root / "openkos.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + extra, encoding="utf-8")
    return root


_OLLAMA = "temperature: 0.2\nseed: 7\nmodels:\n  adjudication: judge-tag\n"
_OPENAI = (
    "backend: openai-compatible\nbase_url: https://llm.example.test/v1\n"
    "temperature: 0.2\nseed: 7\nmodels:\n  adjudication: judge-tag\n"
)
_OLLAMA_REMOTE = "base_url: http://ollama.example.test:11434\n" + _OLLAMA


@pytest.mark.parametrize("task", _TASKS)
@pytest.mark.parametrize(
    "yaml_extra", [_OLLAMA, _OLLAMA_REMOTE, _OPENAI], ids=["ollama", "host", "openai"]
)
def test_the_default_client_is_built_as_the_cli_builds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, yaml_extra: str, task: str | None
) -> None:
    root = _workspace(tmp_path, monkeypatch, yaml_extra)
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", _KEY)
    cfg = config.read_config(root)
    cli, api = _Recorder(), _Recorder()
    monkeypatch.setattr(cli_main, "OllamaClient", cli.named("ollama"))
    monkeypatch.setattr(cli_main, "OpenAICompatibleClient", cli.named("openai"))
    monkeypatch.setattr(_wiring, "OllamaClient", api.named("ollama"))
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", api.named("openai"))

    cli_main._chat_client(cfg, task=task)
    _wiring.default_chat_client(cfg, task)

    assert len(api.calls) == 1
    assert api.calls == cli.calls
    label, kwargs = api.calls[0]
    assert label == ("openai" if cfg.backend == "openai-compatible" else "ollama")
    assert kwargs["model"] == config.resolve_task_model(cfg, task)
    assert kwargs["seed"] == 7
    if label == "openai":
        assert kwargs["api_key"] == _KEY
    else:
        assert "api_key" not in kwargs


def test_the_key_comes_only_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, _OPENAI)
    cfg = config.read_config(root)
    api = _Recorder()
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", api.named("openai"))

    _wiring.default_chat_client(cfg, None)
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", _KEY)
    _wiring.default_chat_client(cfg, None)

    assert [call[1]["api_key"] for call in api.calls] == [None, _KEY]


def test_the_default_follows_the_configuration_it_is_handed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, _OLLAMA)
    api = _Recorder()
    monkeypatch.setattr(_wiring, "OllamaClient", api.named("ollama"))
    before = config.read_config(root)
    path = root / "openkos.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("judge-tag", "another-judge-tag"),
        encoding="utf-8",
    )
    after = config.read_config(root)

    _wiring.default_chat_client(before, "adjudication")
    _wiring.default_chat_client(after, "adjudication")

    assert [call[1]["model"] for call in api.calls] == [
        "judge-tag",
        "another-judge-tag",
    ]


def test_the_network_guard_intercepts_a_client_built_through_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ollama = config.read_config(_workspace(tmp_path / "a", monkeypatch, _OLLAMA))
    openai = config.read_config(_workspace(tmp_path / "b", monkeypatch, _OPENAI))

    assert isinstance(_wiring.default_chat_client(ollama, None), OfflineOllama)
    assert isinstance(
        _wiring.default_chat_client(openai, None), OfflineOpenAICompatible
    )


def test_the_factories_hold_class_references_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factories = _wiring._backend_factories()
    assert isinstance(factories, backends.BackendFactories)
    assert factories.ollama is _bound("OllamaClient")
    assert factories.openai_compatible is _bound("OpenAICompatibleClient")

    sentinel_a, sentinel_b = object(), object()
    monkeypatch.setattr(_wiring, "OllamaClient", sentinel_a)
    monkeypatch.setattr(_wiring, "OpenAICompatibleClient", sentinel_b)
    patched = _wiring._backend_factories()

    assert (patched.ollama, patched.openai_compatible) == (sentinel_a, sentinel_b)


@pytest.mark.parametrize("name", ["chat_client", "embed_client", "diagnostics_client"])
def test_the_no_client_guard_covers_every_kind_of_client(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from tests.unit.api import support

    support.forbid_model_clients(monkeypatch, "a read")

    with pytest.raises(pytest.fail.Exception, match="a read built a model client"):
        getattr(backends, name)()
    with pytest.raises(pytest.fail.Exception):
        _bound("OllamaClient")()
    with pytest.raises(pytest.fail.Exception):
        _bound("OpenAICompatibleClient")()
