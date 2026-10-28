"""The inbox watch folder through the facade (ADR-0078): a read and a
two-phase change of `unattended.inbox`, committed alone, with no model."""

from pathlib import Path

import pytest

from openkos import api, config
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.application.curation_support import make_workspace


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    root = make_workspace(tmp_path, monkeypatch)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "settle the workspace", "--allow-empty")
    support.forbid_model_clients(monkeypatch, "the inbox settings")
    return root


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    inbox = tmp_path / "Inbox folder"
    inbox.mkdir()
    return inbox


def _preview(root: Path, folder: Path | None) -> api.InboxSettingsPreview:
    change = api.InboxSettingsChange(None if folder is None else str(folder))
    preview = api.preview_inbox_settings(api.Workspace.open(root), change)
    assert isinstance(preview, api.InboxSettingsPreview), preview
    return preview


def test_the_read_is_off_by_default_and_writes_nothing(root: Path) -> None:
    before = support.snapshot(root)

    settings = api.inbox_settings(api.Workspace.open(root))

    assert settings.inbox is None
    assert settings.quiet_seconds == config.UnattendedConfig().quiet_seconds
    assert settings.max_calls_per_day == config.UnattendedConfig().max_calls_per_day
    assert support.snapshot(root) == before


def test_an_unreadable_configuration_is_workspace_unreadable(
    root: Path, folder: Path
) -> None:
    workspace = api.Workspace.open(root)
    (root / "openkos.yaml").write_text("model: [\n", encoding="utf-8")

    with pytest.raises(api.WorkspaceUnreadable):
        api.inbox_settings(workspace)
    with pytest.raises(api.WorkspaceUnreadable):
        api.preview_inbox_settings(workspace, api.InboxSettingsChange(str(folder)))


def test_a_preview_names_the_folder_and_writes_nothing(
    root: Path, folder: Path
) -> None:
    before = support.snapshot(root)

    preview = _preview(root, folder)

    assert preview.current.inbox is None
    assert preview.proposed.inbox == str(folder.resolve())
    assert support.snapshot(root) == before


def test_the_apply_turns_the_watch_on_and_commits_only_openkos_yaml(
    root: Path, folder: Path
) -> None:
    warnings: list[str] = []

    changed = api.apply_inbox_settings(
        api.Workspace.open(root),
        _preview(root, folder).version,
        on_warning=warnings.append,
    )

    assert isinstance(changed, api.InboxSettingsChanged)
    assert changed.commit_sha is not None
    assert warnings == []
    assert _head_files(root) == ["openkos.yaml"]
    assert config.read_config(root).unattended.inbox == folder.resolve()
    assert api.inbox_settings(api.Workspace.open(root)) == changed.settings


def test_turning_it_off_removes_the_key(root: Path, folder: Path) -> None:
    workspace = api.Workspace.open(root)
    api.apply_inbox_settings(workspace, _preview(root, folder).version)

    off = api.apply_inbox_settings(workspace, _preview(root, None).version)

    assert isinstance(off, api.InboxSettingsChanged)
    assert off.settings.inbox is None
    text = (root / "openkos.yaml").read_text(encoding="utf-8")
    assert config.unattended_inbox_line(text) is None
    assert config.read_config(root).unattended.inbox is None


def test_an_uncommitted_edit_elsewhere_stays_out_of_the_commit(
    root: Path, folder: Path
) -> None:
    yaml = root / "openkos.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8").replace("review: true", "review: false"),
        encoding="utf-8",
    )

    changed = api.apply_inbox_settings(
        api.Workspace.open(root), _preview(root, folder).version
    )

    assert isinstance(changed, api.InboxSettingsChanged)
    assert "review: false" in yaml.read_text(encoding="utf-8")
    assert "review: true" in _git(root, "show", "HEAD:openkos.yaml")
    assert "inbox:" in _git(root, "show", "HEAD:openkos.yaml")


def test_an_uncommitted_edit_to_the_inbox_line_is_refused(
    root: Path, folder: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    workspace = api.Workspace.open(root)
    api.apply_inbox_settings(workspace, _preview(root, folder).version)
    yaml = root / "openkos.yaml"
    edited = yaml.read_text(encoding="utf-8").replace(
        json_path(folder), json_path(other)
    )
    assert edited != yaml.read_text(encoding="utf-8")
    yaml.write_text(edited, encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD")

    with pytest.raises(api.Refused, match="uncommitted"):
        api.apply_inbox_settings(workspace, _preview(root, None).version)

    assert yaml.read_text(encoding="utf-8") == edited
    assert _git(root, "rev-parse", "HEAD") == head


def json_path(path: Path) -> str:
    import json

    return json.dumps(str(path), ensure_ascii=False)


def test_a_stale_version_writes_nothing(root: Path, folder: Path) -> None:
    preview = _preview(root, folder)
    yaml = root / "openkos.yaml"
    yaml.write_text(yaml.read_text(encoding="utf-8") + "# note\n", encoding="utf-8")

    outcome = api.apply_inbox_settings(api.Workspace.open(root), preview.version)

    assert outcome == api.InboxSettingsStale()


def test_unchanged_is_refused(root: Path) -> None:
    outcome = api.preview_inbox_settings(
        api.Workspace.open(root), api.InboxSettingsChange(None)
    )

    assert outcome == api.InboxSettingsRefused("unchanged")


def test_each_refusal_config_makes_is_returned(root: Path, tmp_path: Path) -> None:
    workspace = api.Workspace.open(root)

    def refused(value: str) -> str:
        outcome = api.preview_inbox_settings(workspace, api.InboxSettingsChange(value))
        assert isinstance(outcome, api.InboxSettingsRefused)
        return outcome.reason

    assert refused("   ") == "invalid"
    assert refused(str(root / "raw")) == "inside_workspace"
    assert refused(str(root)) == "inside_workspace"
    assert refused(str(tmp_path / "missing")) == "not_a_directory"


def test_an_out_of_range_wait_raises_before_anything(root: Path, folder: Path) -> None:
    with pytest.raises(ValueError, match="wait_seconds"):
        api.apply_inbox_settings(
            api.Workspace.open(root), _preview(root, folder).version, wait_seconds=-1
        )
