"""Setup readiness through the facade: the default client is built by the API's
own composition root, and nothing in the workspace changes."""

from pathlib import Path
from typing import Any, ClassVar

import pytest

from openkos import api, config
from openkos.api import _wiring
from openkos.llm.base import BackendHostLocality, BackendUnavailable, InstalledModel
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace


class FakeOllama:
    """Stands in for `OllamaClient` in the API's composition root."""

    built: ClassVar[list[dict[str, Any]]] = []
    tags: ClassVar[list[str]] = []
    unreachable: ClassVar[bool] = False

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


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = make_workspace(tmp_path, monkeypatch)
    FakeOllama.built = []
    FakeOllama.tags = [config.DEFAULT_MODEL, config.DEFAULT_EMBEDDING_MODEL]
    FakeOllama.unreachable = False
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
