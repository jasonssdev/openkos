"""Setup readiness through the facade: the default client is built by the API's
own composition root, and nothing in the workspace changes."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from openkos import api, config
from openkos.api import _wiring
from openkos.llm.base import (
    BackendHostLocality,
    BackendUnavailable,
    InstalledModel,
    PullEvent,
)
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace


class FakeOllama:
    """Stands in for `OllamaClient` in the API's composition root."""

    built: ClassVar[list[dict[str, Any]]] = []
    tags: ClassVar[list[str]] = []
    unreachable: ClassVar[bool] = False
    pulled: ClassVar[list[str]] = []

    def __init__(self, **kwargs: Any) -> None:
        FakeOllama.built.append(kwargs)

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=True, display_host="localhost:11434")

    def version(self) -> str:
        if FakeOllama.unreachable:
            raise BackendUnavailable("refused")
        return "0.32.9"

    def list_models(self) -> list[InstalledModel]:
        return [InstalledModel(tag=t, family=None) for t in FakeOllama.tags]

    def pull(
        self,
        model: str,
        *,
        on_event: Callable[[PullEvent], None],
        should_stop: Callable[[], bool],
    ) -> bool:
        FakeOllama.pulled.append(model)
        on_event(PullEvent("pulling a", "a", 5, 5))
        on_event(PullEvent("success"))
        return True


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    FakeOllama.built = []
    FakeOllama.tags = [config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
    FakeOllama.unreachable = False
    FakeOllama.pulled = []
    monkeypatch.setattr(_wiring, "OllamaClient", FakeOllama)
    return ws


def test_readiness_uses_the_default_client_and_changes_nothing(root: Path) -> None:
    before = support.snapshot(root)

    got = api.setup_readiness(api.Workspace.open(root))

    assert isinstance(got, api.SetupReadiness)
    assert got.runtime.version == "0.32.9"
    assert got.ready is True
    assert FakeOllama.built == [{"model": config.DEFAULT_MODEL, "timeout": 5.0}]
    assert support.snapshot(root) == before


def test_an_unreachable_runtime_is_a_value(root: Path) -> None:
    FakeOllama.unreachable = True

    got = api.setup_readiness(api.Workspace.open(root))

    assert got.runtime.reachable is False
    assert got.ready is False


def test_a_configuration_unreadable_after_open_is_workspace_unreadable(
    root: Path,
) -> None:
    workspace = api.Workspace.open(root)
    config.WorkspaceLayout(root).config_path.write_text("model: [\n", "utf-8")

    with pytest.raises(api.WorkspaceUnreadable) as caught:
        api.setup_readiness(workspace)
    assert caught.value.path == workspace.root


def test_the_setup_names_are_public() -> None:
    for name in (
        "setup_readiness",
        "SetupReadiness",
        "RuntimeReadiness",
        "ModelReadiness",
        "MemoryReadiness",
    ):
        assert name in api.__all__


class _Seen:
    def __init__(self) -> None:
        self.events: list[api.PullProgress] = []

    def progress(self, event: api.PullProgress) -> None:
        self.events.append(event)


def test_a_pull_goes_through_the_default_client_and_changes_nothing(
    root: Path,
) -> None:
    FakeOllama.tags = [config.DEFAULT_MODEL]
    before = support.snapshot(root)
    seen = _Seen()

    got = api.pull_model(
        api.Workspace.open(root), config.DEFAULT_EMBEDDING_MODEL, observer=seen
    )

    assert got == api.PullOutcome(config.DEFAULT_EMBEDDING_MODEL, "completed", 5, 5)
    assert FakeOllama.pulled == [config.DEFAULT_EMBEDDING_MODEL]
    assert FakeOllama.built[-1]["timeout"] == 600.0
    assert [e.phase for e in seen.events] == ["downloading", "finishing"]
    assert support.snapshot(root) == before


def test_an_unconfigured_pull_is_refused_as_a_value(root: Path) -> None:
    got = api.pull_model(api.Workspace.open(root), "llama3:70b")

    assert got == api.PullRefused("llama3:70b", "not_configured")
    assert FakeOllama.built == []


def test_a_stopped_token_pulls_nothing(root: Path) -> None:
    FakeOllama.tags = []
    stop = api.StopToken()
    stop.set()

    got = api.pull_model(api.Workspace.open(root), config.DEFAULT_MODEL, stop=stop)

    assert isinstance(got, api.PullOutcome)
    assert got.status == "stopped"
    assert FakeOllama.pulled == []


def test_a_pull_with_an_unreadable_configuration_is_workspace_unreadable(
    root: Path,
) -> None:
    workspace = api.Workspace.open(root)
    config.WorkspaceLayout(root).config_path.write_text("model: [\n", "utf-8")

    with pytest.raises(api.WorkspaceUnreadable):
        api.pull_model(workspace, config.DEFAULT_MODEL)
