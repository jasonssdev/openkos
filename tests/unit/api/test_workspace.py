"""`openkos.api.Workspace`: the handle that resolves the workspace once.

The CLI's rule applies: the path is the workspace root itself, never a nested
directory and never found by searching upward.
"""

import dataclasses
import os
from pathlib import Path

import pytest

from openkos import config
from openkos.api import (
    OpenKosError,
    Workspace,
    WorkspaceNotFound,
    WorkspaceUnreadable,
)
from tests.unit.application.curation_support import make_workspace, tree


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_workspace(tmp_path, monkeypatch)


def test_open_returns_a_handle_exposing_the_root(root: Path) -> None:
    ws = Workspace.open(root)

    assert ws.root == root


def test_open_accepts_a_string_path(root: Path) -> None:
    assert Workspace.open(str(root)).root == root


def test_a_directory_that_is_not_a_workspace_is_not_found_and_untouched(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(WorkspaceNotFound) as excinfo:
        Workspace.open(empty)

    assert isinstance(excinfo.value, OpenKosError)
    assert excinfo.value.path == empty
    assert excinfo.value.reason
    assert str(empty) in str(excinfo.value)
    assert list(empty.iterdir()) == []  # nothing created


def test_a_missing_path_is_not_found_and_is_not_created(tmp_path: Path) -> None:
    gone = tmp_path / "gone"

    with pytest.raises(WorkspaceNotFound) as excinfo:
        Workspace.open(gone)

    assert excinfo.value.path == gone
    assert not gone.exists()


def test_a_nested_directory_is_not_a_workspace_root(root: Path) -> None:
    before = tree(root)

    with pytest.raises(WorkspaceNotFound) as excinfo:
        Workspace.open(root / "bundle")  # no upward search

    assert excinfo.value.path == root / "bundle"
    assert tree(root) == before


def test_an_invalid_config_is_unreadable_not_not_found(root: Path) -> None:
    (root / "openkos.yaml").write_text("model: [unterminated\n", encoding="utf-8")

    with pytest.raises(WorkspaceUnreadable) as excinfo:
        Workspace.open(root)

    assert isinstance(excinfo.value, OpenKosError)
    assert not isinstance(excinfo.value, WorkspaceNotFound)
    assert excinfo.value.path == root
    assert excinfo.value.reason


def test_a_missing_config_file_is_unreadable(root: Path) -> None:
    (root / "openkos.yaml").unlink()

    with pytest.raises(WorkspaceUnreadable):
        Workspace.open(root)


def test_a_symlinked_segment_inside_the_workspace_is_unreadable(
    root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-raw"
    outside.mkdir()
    (root / "raw").rmdir()
    (root / "raw").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceUnreadable) as excinfo:
        Workspace.open(root)

    assert "symlink" in excinfo.value.reason


def test_the_path_is_taken_as_given_not_symlink_resolved(
    root: Path, tmp_path: Path
) -> None:
    link = tmp_path / "link-to-ws"
    link.symlink_to(root, target_is_directory=True)

    ws = Workspace.open(link)

    assert ws.root == link
    assert ws.root != root


def test_a_relative_path_is_made_absolute(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(root.parent)

    ws = Workspace.open(Path(root.name))

    assert ws.root == root
    assert ws.root.is_absolute()


def test_constructing_directly_is_equivalent_to_open(root: Path) -> None:
    assert Workspace(root=root) == Workspace.open(root)
    assert hash(Workspace(root=root)) == hash(Workspace.open(root))


def test_constructing_directly_validates_too(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFound):
        Workspace(root=tmp_path)


def test_the_handle_is_immutable(root: Path) -> None:
    ws = Workspace.open(root)

    with pytest.raises(dataclasses.FrozenInstanceError):
        ws.root = root / "elsewhere"  # type: ignore[misc]


def test_the_private_layout_is_not_part_of_equality_or_repr(root: Path) -> None:
    ws = Workspace.open(root)

    assert "_layout=" not in repr(ws)
    assert [f.name for f in dataclasses.fields(ws) if f.compare] == ["root"]


def test_the_handle_caches_no_configuration(root: Path) -> None:
    ws = Workspace.open(root)
    lines = (root / "openkos.yaml").read_text(encoding="utf-8").splitlines()
    edited = [
        "model: a-model-changed-after-open" if line.startswith("model:") else line
        for line in lines
    ]
    (root / "openkos.yaml").write_text("\n".join(edited) + "\n", encoding="utf-8")

    # An operation re-reads openkos.yaml through the handle's root and sees the
    # change; the handle itself holds only the root and its path layout.
    assert config.read_config(ws.root).model == "a-model-changed-after-open"
    assert not any(isinstance(v, config.Config) for v in vars(ws).values())
    assert os.fspath(ws._layout.root) == os.fspath(ws.root)
