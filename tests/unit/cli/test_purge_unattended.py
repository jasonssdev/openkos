"""`purge` removes the workspace's unattended records and daemon logs (#1139;
privacy-purge spec: "Purge Removes The Workspace's Unattended Records And
Logs").

`jobs.db` is deleted with the other stores and named among them; the daemon's
per-workspace log files (and their rotated siblings) are deleted too, and one
that cannot be deleted is REPORTED with its path rather than skipped."""

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from openkos import logsetup, userstate
from openkos.cli.main import app
from tests.unit.cli.test_purge import _refuse_unlink_of, _seed_derived_stores
from tests.unit.vcs.conftest import TmpGitRepo, tmp_git_repo

__all__ = ["tmp_git_repo"]

runner = CliRunner()

SENTINEL = "ZQX-LOG-SENTINEL-8813"


@pytest.fixture
def log_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path.parent / f"{tmp_path.name}-logs"
    directory.mkdir()
    monkeypatch.setattr(userstate, "log_dir", lambda *a, **k: directory)
    return directory


def _purge_self(source_id: str) -> Result:
    return runner.invoke(
        app, ["purge", source_id, "--confirm-phrase", f"purge {source_id}"]
    )


def _daemon_logs(root: Path) -> list[Path]:
    """The workspace's log plus two rotated siblings, each carrying the
    sentinel so a surviving file is a recoverable leak, not just litter."""
    live = logsetup.log_path_for(root)
    live.parent.mkdir(parents=True, exist_ok=True)
    paths = [live, live.with_name(live.name + ".1"), live.with_name(live.name + ".2")]
    for path in paths:
        path.write_text(f"job failed on {SENTINEL}\n", encoding="utf-8")
    return paths


def test_purge_deletes_jobs_db_and_names_it_among_the_stores(
    tmp_git_repo: TmpGitRepo, log_directory: Path
) -> None:
    _seed_derived_stores(tmp_git_repo.root)
    jobs = tmp_git_repo.root / ".openkos" / "jobs.db"
    jobs.write_bytes(b"SQLite format 3\x00" + SENTINEL.encode())
    jobs.with_name("jobs.db-wal").write_bytes(b"")
    jobs.with_name("jobs.db-shm").write_bytes(b"")
    assert jobs.exists()

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert not jobs.exists()
    assert not jobs.with_name("jobs.db-wal").exists()
    assert not jobs.with_name("jobs.db-shm").exists()
    assert "jobs.db" in result.output


def test_purge_notice_names_the_queue_and_watch_history_and_how_to_recompute(
    tmp_git_repo: TmpGitRepo, log_directory: Path
) -> None:
    """The queue lives in `findings.db` and the watch's observation history in
    `jobs.db`; dropping either loses work the operator can see (#1266), so the
    notice names both and says what restores them."""
    _seed_derived_stores(tmp_git_repo.root)
    jobs = tmp_git_repo.root / ".openkos" / "jobs.db"
    jobs.write_bytes(b"SQLite format 3\x00")

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    lines = {
        name: line
        for line in result.output.splitlines()
        for name in ("findings.db", "jobs.db")
        if line.lstrip().startswith(f"- {name}:")
    }
    assert "pending-work queue" in lines["findings.db"]
    assert "openkos daemon --once" in lines["findings.db"]
    assert "inbox watch" in lines["jobs.db"]
    assert "openkos daemon --once" in lines["jobs.db"]


def test_purge_leaves_no_daemon_log_for_the_workspace(
    tmp_git_repo: TmpGitRepo, log_directory: Path
) -> None:
    logs = _daemon_logs(tmp_git_repo.root)
    other = log_directory / (hashlib.sha256(b"another-workspace").hexdigest() + ".log")
    other.write_text("not ours\n", encoding="utf-8")
    assert all(path.exists() for path in logs)  # precondition

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert [path for path in logs if path.exists()] == []
    assert other.read_text(encoding="utf-8") == "not ours\n"
    assert SENTINEL not in "".join(
        p.read_text(encoding="utf-8") for p in log_directory.iterdir()
    )


def test_purge_with_no_daemon_log_is_clean(
    tmp_git_repo: TmpGitRepo, log_directory: Path
) -> None:
    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert "INCOMPLETE ERASURE" not in result.output


def test_an_undeletable_log_is_reported_with_its_path_and_the_remedy(
    tmp_git_repo: TmpGitRepo, log_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = _daemon_logs(tmp_git_repo.root)
    stuck = logs[1]
    _refuse_unlink_of(monkeypatch, stuck.name)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 1, result.output
    assert "INCOMPLETE ERASURE" in result.output
    assert str(stuck) in result.output
    assert "remove the file(s) above" in result.output
    assert stuck.exists()
    # Every other log still went: one failure does not abandon the rest.
    assert not logs[0].exists()
    assert not logs[2].exists()
    assert str(logs[2]) not in result.output


def test_purge_removes_the_workspace_record_that_sits_beside_the_log(
    tmp_git_repo: TmpGitRepo, log_directory: Path
) -> None:
    """The record names the workspace's path; a purged workspace must not leave
    that behind (#1334)."""
    _daemon_logs(tmp_git_repo.root)
    record = logsetup.workspace_record_path_for(tmp_git_repo.root)
    record.write_text(str(tmp_git_repo.root), encoding="utf-8")

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert not record.exists()
