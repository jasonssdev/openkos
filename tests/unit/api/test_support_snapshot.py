"""`support.snapshot` compares a workspace's content, not git's packing.

A git commit may spawn a detached `git maintenance run --auto`, which packs
loose objects and refs at an arbitrary later moment. A byte snapshot that walks
`.git/objects` then sees a difference with nothing changed (the CI flake of
`test_an_altered_version_is_409_stale`). The snapshot must be blind to how git
stores history and still see what history says."""

import subprocess
from pathlib import Path

import pytest

from tests.unit.api import support


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "-c", "gc.auto=0", *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "bundle").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    for key, value in (("user.name", "t"), ("user.email", "t@openkos.invalid")):
        _git(root, "config", key, value)
    (root / "bundle" / "a.md").write_text("a\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


@pytest.mark.parametrize(
    "repack",
    [
        ("gc", "-q"),
        ("repack", "-a", "-d", "-q"),
        ("pack-refs", "--all", "--prune"),
        ("maintenance", "run", "--task=gc", "--quiet"),
    ],
)
def test_repacking_between_snapshots_changes_nothing(
    repo: Path, repack: tuple[str, ...]
) -> None:
    before = support.snapshot(repo)

    _git(repo, *repack)

    assert support.snapshot(repo) == before


def test_a_new_commit_is_still_seen(repo: Path) -> None:
    before = support.snapshot(repo)
    (repo / "bundle" / "b.md").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")

    assert support.snapshot(repo) != before


def test_a_moved_branch_is_still_seen(repo: Path) -> None:
    before = support.snapshot(repo)
    _git(repo, "branch", "other")
    assert support.snapshot(repo) != before

    packed = support.snapshot(repo)
    _git(repo, "pack-refs", "--all", "--prune")
    assert support.snapshot(repo) == packed
    _git(repo, "update-ref", "-d", "refs/heads/other")
    assert support.snapshot(repo) != packed


def test_a_moved_head_is_still_seen(repo: Path) -> None:
    before = support.snapshot(repo)
    _git(repo, "checkout", "-q", "-b", "elsewhere")

    assert support.snapshot(repo) != before


def test_an_edited_working_file_is_still_seen(repo: Path) -> None:
    before = support.snapshot(repo)
    (repo / "bundle" / "a.md").write_text("changed\n")

    assert support.snapshot(repo) != before
