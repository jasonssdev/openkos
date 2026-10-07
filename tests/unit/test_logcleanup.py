"""Cleanup of per-workspace daemon logs whose workspace is gone (#1334 item 10).

Each workspace's daemon log is `<sha256 of its real path>.log` (plus rotated
`.log.N`), and the hash cannot be reversed. The daemon therefore records which
workspace owns a log in a `<sha256>.workspace` sidecar, and removes a group only
when its recorded workspace no longer exists. Everything else is left alone,
above all a log nothing says whom it belongs to.
"""

import os
from pathlib import Path

import pytest

from openkos import logsetup, userstate
from openkos.lock import workspace_digest


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state-logs"
    directory.mkdir()
    monkeypatch.setattr(userstate, "log_dir", lambda *a, **k: directory)
    return directory


def _workspace(path: Path) -> Path:
    (path / "bundle").mkdir(parents=True)
    (path / "bundle" / "index.md").write_text("x", encoding="utf-8")
    (path / "bundle" / "log.md").write_text("x", encoding="utf-8")
    return path


def _group(
    directory: Path, workspace: Path, *, record: bool = True, rotated: int = 1
) -> str:
    """The log files a daemon would have left for `workspace`."""
    digest = workspace_digest(workspace)
    (directory / f"{digest}.log").write_text("current\n", encoding="utf-8")
    for n in range(1, rotated + 1):
        (directory / f"{digest}.log.{n}").write_text("old\n", encoding="utf-8")
    if record:
        (directory / f"{digest}.workspace").write_text(
            os.path.realpath(workspace), encoding="utf-8"
        )
    return digest


def _names(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


def _remove(current: Path) -> list[str]:
    said: list[str] = []
    logsetup.remove_stale_logs(current, said.append)
    return said


def test_configuring_the_daemon_log_records_the_owning_workspace(
    tmp_path: Path, log_dir: Path
) -> None:
    root = _workspace(tmp_path / "ws")

    logsetup.configure_logging("daemon", root=root)
    logsetup.reset_logging()

    record = logsetup.workspace_record_path_for(root)
    assert record == log_dir / f"{workspace_digest(root)}.workspace"
    assert record.read_text(encoding="utf-8") == os.path.realpath(root)
    assert not [p for p in log_dir.iterdir() if p.name.endswith(".tmp")]


def test_a_group_whose_workspace_is_gone_is_removed_with_its_rotations(
    tmp_path: Path, log_dir: Path
) -> None:
    current = _workspace(tmp_path / "current")
    gone = tmp_path / "gone"
    digest = _group(log_dir, gone, rotated=2)

    said = _remove(current)

    assert _names(log_dir) == set()
    assert len(said) == 4  # .log, .log.1, .log.2 and the record: all reported
    assert all(digest[:12] in line or str(gone) in line for line in said)


def test_the_current_workspace_is_never_removed(tmp_path: Path, log_dir: Path) -> None:
    current = tmp_path / "current"  # not even initialised: only identity protects it
    current.mkdir()
    _group(log_dir, current)

    assert _remove(current) == []
    assert len(_names(log_dir)) == 3


def test_a_workspace_that_still_exists_keeps_its_log(
    tmp_path: Path, log_dir: Path
) -> None:
    current = _workspace(tmp_path / "current")
    other = _workspace(tmp_path / "other")
    _group(log_dir, other)

    assert _remove(current) == []
    assert len(_names(log_dir)) == 3


def test_a_directory_that_is_no_longer_a_workspace_counts_as_gone(
    tmp_path: Path, log_dir: Path
) -> None:
    current = _workspace(tmp_path / "current")
    emptied = tmp_path / "emptied"
    emptied.mkdir()
    _group(log_dir, emptied)

    _remove(current)

    assert _names(log_dir) == set()


def test_a_log_with_no_recorded_workspace_is_never_removed(
    tmp_path: Path, log_dir: Path
) -> None:
    current = _workspace(tmp_path / "current")
    _group(log_dir, tmp_path / "unknown", record=False)

    assert _remove(current) == []
    assert len(_names(log_dir)) == 2


def test_a_record_that_does_not_hash_to_its_name_is_not_trusted(
    tmp_path: Path, log_dir: Path
) -> None:
    current = _workspace(tmp_path / "current")
    digest = _group(log_dir, tmp_path / "gone", record=False)
    (log_dir / f"{digest}.workspace").write_text(
        str(tmp_path / "somewhere-else"), encoding="utf-8"
    )

    assert _remove(current) == []
    assert f"{digest}.log" in _names(log_dir)


@pytest.mark.parametrize("content", ["", "relative/path", "\x00"])
def test_a_malformed_record_is_not_trusted(
    tmp_path: Path, log_dir: Path, content: str
) -> None:
    current = _workspace(tmp_path / "current")
    digest = _group(log_dir, tmp_path / "gone", record=False)
    (log_dir / f"{digest}.workspace").write_text(content, encoding="utf-8")

    assert _remove(current) == []
    assert f"{digest}.log" in _names(log_dir)


def test_only_names_the_daemon_writes_are_ever_touched(
    tmp_path: Path, log_dir: Path
) -> None:
    current = _workspace(tmp_path / "current")
    digest = _group(log_dir, tmp_path / "gone")
    bystanders = {
        "notes.log",
        f"{digest}.log.bak",
        f"{digest}.txt",
        f"{digest}.log.x",
        f"{digest[:-1]}.log",
        f"{digest}-extra.log",
        "README",
    }
    for name in bystanders:
        (log_dir / name).write_text("mine\n", encoding="utf-8")

    _remove(current)

    assert _names(log_dir) == bystanders


def test_a_symlink_is_never_followed_and_its_group_is_left_whole(
    tmp_path: Path, log_dir: Path
) -> None:
    current = _workspace(tmp_path / "current")
    digest = _group(log_dir, tmp_path / "gone")
    outside = tmp_path / "precious.txt"
    outside.write_text("keep me\n", encoding="utf-8")
    (log_dir / f"{digest}.log.2").symlink_to(outside)

    _remove(current)

    assert outside.read_text(encoding="utf-8") == "keep me\n"
    assert f"{digest}.log" in _names(log_dir)  # the whole group is skipped


def test_scan_reports_without_deleting(tmp_path: Path, log_dir: Path) -> None:
    current = _workspace(tmp_path / "current")
    _group(log_dir, tmp_path / "gone", rotated=1)
    _group(log_dir, tmp_path / "legacy", record=False, rotated=2)
    kept = _group(log_dir, _workspace(tmp_path / "live"))

    scan = logsetup.scan_logs(current)

    assert len(scan.stale) == 1
    assert scan.stale[0].workspace == tmp_path / "gone"
    assert scan.legacy_files == 3
    assert scan.legacy_bytes == len("current\n") + 2 * len("old\n")
    assert f"{kept}.log" in _names(log_dir)


def test_a_missing_log_directory_is_nothing_to_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(userstate, "log_dir", lambda *a, **k: tmp_path / "nope")

    assert _remove(_workspace(tmp_path / "current")) == []
    scan = logsetup.scan_logs(tmp_path / "current")
    assert (scan.stale, scan.legacy_files) == ((), 0)
