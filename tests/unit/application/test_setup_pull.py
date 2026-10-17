"""The model pull (`application/setup.py`): refusals, progress, stop.

Every client is a fake; nothing here touches the network.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openkos import config
from openkos.application import setup
from openkos.application.runtime import StopToken
from openkos.llm.base import (
    BackendHostLocality,
    BackendUnavailable,
    InstalledModel,
    PullEvent,
)

_LOCAL = BackendHostLocality(is_local=True, display_host="localhost:11434")


@dataclass
class _Runtime:
    tags: list[str] = field(default_factory=list)
    events: list[PullEvent] = field(default_factory=list)
    list_error: Exception | None = None
    stop_after: int | None = None
    locality: BackendHostLocality = _LOCAL
    pulled: list[str] = field(default_factory=list)
    listed: int = 0

    def version(self) -> str:
        return "0.32.9"

    def list_models(self) -> list[InstalledModel]:
        self.listed += 1
        if self.list_error is not None:
            raise self.list_error
        return [InstalledModel(tag=t, family=None) for t in self.tags]

    def pull(
        self,
        model: str,
        *,
        on_event: Callable[[PullEvent], None],
        should_stop: Callable[[], bool],
    ) -> bool:
        self.pulled.append(model)
        for event in self.events:
            on_event(event)
            if event.status == "success":
                return True
            if should_stop():
                return False
        return True


@dataclass
class _Diagnostics:
    locality: BackendHostLocality = _LOCAL

    def list_models(self) -> list[InstalledModel]:
        return []


class _Recorder:
    def __init__(self) -> None:
        self.seen: list[setup.PullProgress] = []

    def progress(self, event: setup.PullProgress) -> None:
        self.seen.append(event)


def _workspace(tmp_path: Path, extra: str = "") -> Path:
    config.write_config(tmp_path)
    if extra:
        path = config.WorkspaceLayout(tmp_path).config_path
        path.write_text(path.read_text("utf-8") + extra, encoding="utf-8")
    return tmp_path


def _ports(client: object, timeouts: list[float] | None = None) -> setup.SetupPorts:
    def build(cfg: config.Config, timeout: float) -> object:
        if timeouts is not None:
            timeouts.append(timeout)
        return client

    return setup.SetupPorts(client=build, total_memory=lambda: None)  # type: ignore[arg-type]


def _layer(digest: str, completed: int, total: int) -> PullEvent:
    return PullEvent(
        status=f"pulling {digest}", digest=digest, completed=completed, total=total
    )


_STREAM = [
    PullEvent(status="pulling manifest"),
    _layer("a", 10, 100),
    _layer("a", 100, 100),
    _layer("b", 5, 50),
    _layer("b", 50, 50),
    PullEvent(status="verifying sha256 digest"),
    PullEvent(status="writing manifest"),
    PullEvent(status="success"),
]


def test_a_pull_reports_aggregated_progress_and_completes(tmp_path: Path) -> None:
    client = _Runtime(events=list(_STREAM))
    recorder = _Recorder()
    timeouts: list[float] = []

    got = setup.pull_model(
        _workspace(tmp_path),
        config.DEFAULT_EMBEDDING_MODEL,
        ports=_ports(client, timeouts),
        stop=StopToken(),
        observer=recorder,
    )

    assert got == setup.PullOutcome(
        model=config.DEFAULT_EMBEDDING_MODEL,
        status="completed",
        completed_bytes=150,
        total_bytes=150,
    )
    assert client.pulled == [config.DEFAULT_EMBEDDING_MODEL]
    assert timeouts == [setup.PULL_TIMEOUT_SECONDS]
    completed = [p.completed_bytes for p in recorder.seen]
    assert completed == sorted(completed)
    assert [(p.completed_bytes, p.total_bytes) for p in recorder.seen] == [
        (0, None),
        (10, 100),
        (100, 100),
        (105, 150),
        (150, 150),
        (150, 150),
        (150, 150),
        (150, 150),
    ]
    assert [p.phase for p in recorder.seen] == [
        "preparing",
        "downloading",
        "downloading",
        "downloading",
        "downloading",
        "verifying",
        "finishing",
        "finishing",
    ]
    assert {p.model for p in recorder.seen} == {config.DEFAULT_EMBEDDING_MODEL}


def test_a_layer_reported_with_less_completed_never_moves_backwards(
    tmp_path: Path,
) -> None:
    client = _Runtime(
        events=[_layer("a", 60, 100), _layer("a", 20, 100), PullEvent("success")]
    )
    recorder = _Recorder()

    setup.pull_model(
        _workspace(tmp_path),
        config.DEFAULT_MODEL,
        ports=_ports(client),
        stop=StopToken(),
        observer=recorder,
    )

    assert [p.completed_bytes for p in recorder.seen] == [60, 60, 60]


def test_a_stop_ends_the_pull_as_stopped(tmp_path: Path) -> None:
    stop = StopToken()
    client = _Runtime(events=list(_STREAM))

    class StopAfterFirst(_Recorder):
        def progress(self, event: setup.PullProgress) -> None:
            super().progress(event)
            stop.set()

    recorder = StopAfterFirst()

    got = setup.pull_model(
        _workspace(tmp_path),
        config.DEFAULT_MODEL,
        ports=_ports(client),
        stop=stop,
        observer=recorder,
    )

    assert isinstance(got, setup.PullOutcome)
    assert got.status == "stopped"
    assert len(recorder.seen) == 1


def test_a_stop_before_the_stream_makes_no_pull(tmp_path: Path) -> None:
    stop = StopToken()
    stop.set()
    client = _Runtime(events=list(_STREAM))

    got = setup.pull_model(
        _workspace(tmp_path), config.DEFAULT_MODEL, ports=_ports(client), stop=stop
    )

    assert isinstance(got, setup.PullOutcome)
    assert got.status == "stopped"
    assert client.pulled == []


def test_an_unconfigured_model_is_refused_before_any_request(tmp_path: Path) -> None:
    def build(cfg: config.Config, timeout: float) -> _Runtime:
        pytest.fail("a client was built for an unconfigured model")

    got = setup.pull_model(
        _workspace(tmp_path),
        "llama3:70b",
        ports=setup.SetupPorts(client=build),
        stop=StopToken(),
    )

    assert got == setup.PullRefused(model="llama3:70b", reason="not_configured")


def test_a_latest_spelling_of_a_configured_tag_is_still_unconfigured(
    tmp_path: Path,
) -> None:
    got = setup.pull_model(
        _workspace(tmp_path),
        f"{config.DEFAULT_MODEL}:latest",
        ports=_ports(_Runtime()),
        stop=StopToken(),
    )

    assert isinstance(got, setup.PullRefused)
    assert got.reason == "not_configured"


def test_a_configured_task_model_can_be_pulled(tmp_path: Path) -> None:
    client = _Runtime(events=[PullEvent("success")])

    got = setup.pull_model(
        _workspace(tmp_path),
        config.DEFAULT_JUDGE_MODEL,
        ports=_ports(client),
        stop=StopToken(),
    )

    assert isinstance(got, setup.PullOutcome)
    assert client.pulled == [config.DEFAULT_JUDGE_MODEL]


def test_an_openai_compatible_backend_cannot_pull(tmp_path: Path) -> None:
    extra = "backend: openai-compatible\nbase_url: http://127.0.0.1:8080/v1\n"

    def build(cfg: config.Config, timeout: float) -> _Runtime:
        pytest.fail("a client was built for a backend that cannot pull")

    got = setup.pull_model(
        _workspace(tmp_path, extra),
        config.DEFAULT_MODEL,
        ports=setup.SetupPorts(client=build),
        stop=StopToken(),
    )

    assert got == setup.PullRefused(
        model=config.DEFAULT_MODEL, reason="backend_cannot_pull"
    )


def test_a_client_without_the_pull_capability_cannot_pull(tmp_path: Path) -> None:
    got = setup.pull_model(
        _workspace(tmp_path),
        config.DEFAULT_MODEL,
        ports=_ports(_Diagnostics()),
        stop=StopToken(),
    )

    assert isinstance(got, setup.PullRefused)
    assert got.reason == "backend_cannot_pull"


def test_an_installed_model_is_not_pulled_again(tmp_path: Path) -> None:
    client = _Runtime(
        tags=[f"{config.DEFAULT_EMBEDDING_MODEL}:latest"], events=list(_STREAM)
    )

    got = setup.pull_model(
        _workspace(tmp_path),
        config.DEFAULT_EMBEDDING_MODEL,
        ports=_ports(client),
        stop=StopToken(),
    )

    assert got == setup.PullOutcome(
        model=config.DEFAULT_EMBEDDING_MODEL,
        status="already_installed",
        completed_bytes=0,
        total_bytes=None,
    )
    assert client.pulled == []


def test_an_unreachable_runtime_raises(tmp_path: Path) -> None:
    client = _Runtime(list_error=BackendUnavailable("refused"))

    with pytest.raises(BackendUnavailable):
        setup.pull_model(
            _workspace(tmp_path),
            config.DEFAULT_MODEL,
            ports=_ports(client),
            stop=StopToken(),
        )
    assert client.pulled == []


def test_an_unreadable_configuration_is_a_typed_refusal(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    config.WorkspaceLayout(root).config_path.write_text("model: [\n", encoding="utf-8")

    with pytest.raises(setup.SetupConfigUnreadable):
        setup.pull_model(root, "x", ports=_ports(_Runtime()), stop=StopToken())


@pytest.mark.parametrize(
    ("status", "phase"),
    [
        ("pulling manifest", "preparing"),
        ("pulling 0123abcd", "downloading"),
        ("verifying sha256 digest", "verifying"),
        ("writing manifest", "finishing"),
        ("removing any unused layers", "finishing"),
        ("success", "finishing"),
        ("something new", "finishing"),
    ],
)
def test_the_phase_is_normalised(status: str, phase: str) -> None:
    assert setup.pull_phase(status) == phase
