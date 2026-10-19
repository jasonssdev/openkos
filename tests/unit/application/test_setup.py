"""The setup readiness read (`application/setup.py`).

Every client is a fake; nothing here touches the network or a model.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openkos import config
from openkos.application import doctor, setup
from openkos.llm.base import (
    BackendError,
    BackendHostLocality,
    BackendUnavailable,
    InstalledModel,
)

_LOCAL = BackendHostLocality(is_local=True, display_host="localhost:11434")
_GIB = 1024**3


@dataclass
class _Diagnostics:
    """A `BackendDiagnostics` double: no version probe."""

    tags: list[str] = field(default_factory=list)
    error: Exception | None = None
    locality: BackendHostLocality = _LOCAL
    listed: int = 0

    def list_models(self) -> list[InstalledModel]:
        self.listed += 1
        if self.error is not None:
            raise self.error
        return [InstalledModel(tag=t, family=None) for t in self.tags]


@dataclass
class _Runtime(_Diagnostics):
    """A `ModelRuntime` double: diagnostics plus a version."""

    runtime_version: str = "0.32.9"
    version_error: Exception | None = None

    def version(self) -> str:
        if self.version_error is not None:
            raise self.version_error
        return self.runtime_version

    def pull(self, model: str, **kwargs: object) -> bool:
        raise AssertionError("readiness never pulls")


def _workspace(tmp_path: Path, extra: str = "") -> Path:
    config.write_config(tmp_path)
    if extra:
        path = config.WorkspaceLayout(tmp_path).config_path
        path.write_text(path.read_text("utf-8") + extra, encoding="utf-8")
    return tmp_path


def _ports(client: _Diagnostics, memory: int | None = 64 * _GIB) -> setup.SetupPorts:
    seen: list[float] = []

    def build(cfg: config.Config, timeout: float) -> _Diagnostics:
        seen.append(timeout)
        return client

    return setup.SetupPorts(client=build, total_memory=lambda: memory)


_ALL = [
    config.DEFAULT_MODEL,
    config.DEFAULT_EMBEDDING_MODEL,
    config.DEFAULT_JUDGE_MODEL,
]


def test_a_ready_machine(tmp_path: Path) -> None:
    client = _Runtime(tags=list(_ALL))

    got = setup.setup_readiness(_workspace(tmp_path), ports=_ports(client))

    assert got.runtime == setup.RuntimeReadiness(
        backend="ollama",
        host="localhost:11434",
        is_local=True,
        reachable=True,
        version="0.32.9",
        download_url=setup.OLLAMA_DOWNLOAD_URL,
    )
    assert got.ready is True
    assert [m.installed for m in got.models] == [True, True, True]


def test_the_models_are_listed_in_order_with_their_roles(tmp_path: Path) -> None:
    got = setup.setup_readiness(
        _workspace(tmp_path), ports=_ports(_Runtime(tags=list(_ALL)))
    )

    assert [(m.tag, m.roles, m.required, m.pullable) for m in got.models] == [
        (config.DEFAULT_MODEL, ("chat",), True, True),
        (config.DEFAULT_EMBEDDING_MODEL, ("embedding",), True, True),
        (
            config.DEFAULT_JUDGE_MODEL,
            ("adjudication", "contradiction", "sufficiency"),
            False,
            True,
        ),
    ]


def test_no_runtime_is_reported_not_raised(tmp_path: Path) -> None:
    client = _Runtime(version_error=BackendUnavailable("refused"))

    got = setup.setup_readiness(_workspace(tmp_path), ports=_ports(client))

    assert got.runtime.reachable is False
    assert got.runtime.version is None
    assert got.runtime.download_url == setup.OLLAMA_DOWNLOAD_URL
    assert [m.installed for m in got.models] == [None, None, None]
    assert got.ready is False
    assert client.listed == 0


def test_a_runtime_that_answers_but_cannot_list_is_reachable_with_unknown_models(
    tmp_path: Path,
) -> None:
    client = _Runtime(error=BackendError("bad listing"))

    got = setup.setup_readiness(_workspace(tmp_path), ports=_ports(client))

    assert got.runtime.reachable is True
    assert [m.installed for m in got.models] == [None, None, None]
    assert got.ready is False


def test_a_missing_embedding_model_blocks_readiness(tmp_path: Path) -> None:
    client = _Runtime(tags=[config.DEFAULT_MODEL, config.DEFAULT_JUDGE_MODEL])

    got = setup.setup_readiness(_workspace(tmp_path), ports=_ports(client))

    embedding = got.models[1]
    assert (embedding.installed, embedding.required) == (False, True)
    assert got.ready is False


def test_a_missing_judge_model_does_not_block_readiness(tmp_path: Path) -> None:
    client = _Runtime(tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL])

    got = setup.setup_readiness(_workspace(tmp_path), ports=_ports(client))

    judge = got.models[2]
    assert (judge.installed, judge.required) == (False, False)
    assert got.ready is True


def test_a_latest_qualified_tag_counts_as_installed(tmp_path: Path) -> None:
    client = _Runtime(
        tags=[config.DEFAULT_MODEL, f"{config.DEFAULT_EMBEDDING_MODEL}:latest"]
    )

    got = setup.setup_readiness(_workspace(tmp_path), ports=_ports(client))

    assert got.models[1].installed is True


def test_a_task_model_equal_to_the_chat_model_is_not_listed_twice(
    tmp_path: Path,
) -> None:
    extra = (
        "models:\n  adjudication: null\n  contradiction: null\n  sufficiency: null\n"
    )
    client = _Runtime(tags=list(_ALL))

    got = setup.setup_readiness(_workspace(tmp_path, extra), ports=_ports(client))

    assert [m.tag for m in got.models] == [
        config.DEFAULT_MODEL,
        config.DEFAULT_EMBEDDING_MODEL,
    ]


def test_the_doctor_enumeration_is_the_source_of_task_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        doctor, "configured_task_models", lambda cfg: {"edge_typing": "x:1"}
    )

    got = setup.setup_readiness(
        _workspace(tmp_path), ports=_ports(_Runtime(tags=["x:1"]))
    )

    assert got.models[-1].tag == "x:1"
    assert got.models[-1].roles == ("edge_typing",)


def test_an_openai_compatible_backend_has_no_version_and_cannot_pull(
    tmp_path: Path,
) -> None:
    extra = "backend: openai-compatible\nbase_url: http://127.0.0.1:8080/v1\n"
    client = _Diagnostics(tags=[config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL])

    got = setup.setup_readiness(_workspace(tmp_path, extra), ports=_ports(client))

    assert got.runtime.backend == "openai-compatible"
    assert got.runtime.reachable is True
    assert got.runtime.version is None
    assert got.runtime.download_url is None
    assert all(not m.pullable for m in got.models)
    assert got.ready is True


def test_an_unreachable_openai_compatible_server(tmp_path: Path) -> None:
    extra = "backend: openai-compatible\nbase_url: http://127.0.0.1:8080/v1\n"
    client = _Diagnostics(error=BackendUnavailable("refused"))

    got = setup.setup_readiness(_workspace(tmp_path, extra), ports=_ports(client))

    assert got.runtime.reachable is False
    assert got.ready is False


@pytest.mark.parametrize(
    ("memory", "meets"),
    [(16 * _GIB, False), (32 * _GIB, True), (32 * _GIB - 1, False), (None, None)],
)
def test_memory_is_reported_against_the_floor(
    tmp_path: Path, memory: int | None, meets: bool | None
) -> None:
    got = setup.setup_readiness(
        _workspace(tmp_path), ports=_ports(_Runtime(tags=list(_ALL)), memory)
    )

    assert got.memory == setup.MemoryReadiness(
        total_bytes=memory, floor_bytes=32 * _GIB, meets_floor=meets
    )
    assert got.ready is True


def test_the_probe_uses_the_preflight_timeout(tmp_path: Path) -> None:
    timeouts: list[float] = []

    def build(cfg: config.Config, timeout: float) -> _Runtime:
        timeouts.append(timeout)
        return _Runtime(tags=list(_ALL))

    setup.setup_readiness(
        _workspace(tmp_path),
        ports=setup.SetupPorts(client=build, total_memory=lambda: None),
    )

    assert timeouts == [setup.PROBE_TIMEOUT_SECONDS]


def test_an_unreadable_configuration_is_a_typed_refusal(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    config.WorkspaceLayout(root).config_path.write_text("model: [\n", encoding="utf-8")

    with pytest.raises(setup.SetupConfigUnreadable) as caught:
        setup.setup_readiness(root, ports=_ports(_Runtime()))
    assert caught.value.reason


def test_physical_memory_reads_sysconf(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"SC_PAGE_SIZE": 16384, "SC_PHYS_PAGES": 2097152}
    monkeypatch.setattr(os, "sysconf", lambda name: values[name])

    assert setup.physical_memory_bytes() == 32 * _GIB


@pytest.mark.parametrize("error", [ValueError("no such name"), OSError("nope")])
def test_physical_memory_is_none_when_unreadable(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail(name: str) -> int:
        raise error

    monkeypatch.setattr(os, "sysconf", fail)

    assert setup.physical_memory_bytes() is None


def test_physical_memory_is_none_without_sysconf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "sysconf")

    assert setup.physical_memory_bytes() is None


def test_a_non_positive_sysconf_answer_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "sysconf", lambda name: -1)

    assert setup.physical_memory_bytes() is None
