"""The model settings through the facade: a model-free read and a two-phase
change of `openkos.yaml`, committed alone, with no client built."""

from pathlib import Path

import pytest

from openkos import api, config
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.application.curation_support import make_workspace

_REMOTE = api.ModelSettingsChange(
    backend="openai-compatible",
    base_url="https://api.example.com/v1",
    embedding_base_url=None,
    model="gpt-4o-mini",
    embedding_model="text-embedding-3-small",
)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    root = make_workspace(tmp_path, monkeypatch)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "settle the workspace", "--allow-empty")
    support.forbid_model_clients(monkeypatch, "the model settings")
    return root


def _preview(root: Path) -> api.ModelSettingsPreview:
    preview = api.preview_model_settings(api.Workspace.open(root), _REMOTE)
    assert isinstance(preview, api.ModelSettingsPreview), preview
    return preview


def test_the_read_is_local_and_writes_nothing(root: Path) -> None:
    before = support.snapshot(root)

    settings = api.model_settings(api.Workspace.open(root))

    assert settings.backend == "ollama"
    assert (settings.is_local, settings.remote_hosts) == (True, ())
    assert settings.model == config.read_config(root).model
    assert support.snapshot(root) == before


def test_an_unreadable_configuration_is_workspace_unreadable(root: Path) -> None:
    workspace = api.Workspace.open(root)
    (root / "openkos.yaml").write_text("model: [\n", encoding="utf-8")

    with pytest.raises(api.WorkspaceUnreadable):
        api.model_settings(workspace)
    with pytest.raises(api.WorkspaceUnreadable):
        api.preview_model_settings(workspace, _REMOTE)


def test_a_preview_writes_nothing(root: Path) -> None:
    before = support.snapshot(root)

    preview = _preview(root)

    assert preview.proposed.remote_hosts == ("api.example.com",)
    assert support.snapshot(root) == before


def test_the_apply_commits_only_openkos_yaml(root: Path) -> None:
    template = (root / "openkos.yaml").read_text(encoding="utf-8")
    warnings: list[str] = []

    changed = api.apply_model_settings(
        api.Workspace.open(root), _preview(root).version, on_warning=warnings.append
    )

    assert isinstance(changed, api.ModelSettingsChanged)
    assert changed.commit_sha is not None
    assert warnings == []
    assert _head_files(root) == ["openkos.yaml"]
    text = (root / "openkos.yaml").read_text(encoding="utf-8")
    assert text.splitlines()[2:] == [
        *template.splitlines()[2:],
        "backend: openai-compatible",
        "base_url: https://api.example.com/v1",
    ]
    assert api.model_settings(api.Workspace.open(root)) == changed.settings


def test_an_uncommitted_edit_elsewhere_stays_out_of_the_commit(root: Path) -> None:
    yaml = root / "openkos.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8").replace("review: true", "review: false"),
        encoding="utf-8",
    )

    changed = api.apply_model_settings(api.Workspace.open(root), _preview(root).version)

    assert isinstance(changed, api.ModelSettingsChanged)
    assert "review: false" in yaml.read_text(encoding="utf-8")
    assert "review: true" in _git(root, "show", "HEAD:openkos.yaml")
    assert "backend: openai-compatible" in _git(root, "show", "HEAD:openkos.yaml")


def test_an_uncommitted_edit_to_a_model_line_is_refused(root: Path) -> None:
    yaml = root / "openkos.yaml"
    text = yaml.read_text(encoding="utf-8")
    edited = text.replace("embedding_model: bge-m3", "embedding_model: other")
    yaml.write_text(edited, encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD")

    with pytest.raises(api.Refused, match="uncommitted"):
        api.apply_model_settings(api.Workspace.open(root), _preview(root).version)

    assert yaml.read_text(encoding="utf-8") == edited
    assert _git(root, "rev-parse", "HEAD") == head


def test_a_stale_version_writes_nothing(root: Path) -> None:
    preview = _preview(root)
    yaml = root / "openkos.yaml"
    yaml.write_text(yaml.read_text(encoding="utf-8") + "# note\n", encoding="utf-8")

    outcome = api.apply_model_settings(api.Workspace.open(root), preview.version)

    assert outcome == api.ModelSettingsStale()


def test_a_refusal_is_returned(root: Path) -> None:
    change = api.ModelSettingsChange("openai-compatible", None, None, "m", "e")

    assert api.preview_model_settings(api.Workspace.open(root), change) == (
        api.ModelSettingsRefused("base_url_required")
    )


def test_an_out_of_range_wait_raises_before_anything(root: Path) -> None:
    with pytest.raises(ValueError, match="wait_seconds"):
        api.apply_model_settings(
            api.Workspace.open(root), _preview(root).version, wait_seconds=-1
        )
