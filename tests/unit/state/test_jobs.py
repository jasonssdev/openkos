"""Unit tests for `state/jobs.py`: the disposable `.openkos/jobs.db` job record
(MVP 4 job-runtime: "Every Job Records One Outcome")."""

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from openkos.state import jobs


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / ".openkos" / "jobs.db"


def _outside_engine_state(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".openkos" not in p.relative_to(root).parts
    }


def test_job_round_trip_carries_every_field(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    job_id = jobs.start_job(conn, "watch", "2026-09-30T10:00:00Z")
    assert jobs.last_job(conn) == jobs.JobRecord(
        job_id, "watch", "2026-09-30T10:00:00Z", None, None, None, 0, 0, 0
    )
    jobs.finish_job(
        conn,
        job_id,
        outcome="budget_exhausted",
        ended_at="2026-09-30T10:05:00Z",
        chat_calls=7,
        units_done=3,
        units_deferred=2,
        detail_code="max_sources_per_pass",
    )
    (rec,) = jobs.recent_jobs(conn)
    assert rec == jobs.JobRecord(
        job_id,
        "watch",
        "2026-09-30T10:00:00Z",
        "2026-09-30T10:05:00Z",
        "budget_exhausted",
        "max_sources_per_pass",
        7,
        3,
        2,
    )
    conn.close()


def test_outcome_and_kind_vocabularies_are_enforced(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        jobs.start_job(conn, "bogus", "t")
    job_id = jobs.start_job(conn, "maintenance", "t")
    with pytest.raises(sqlite3.IntegrityError):
        jobs.finish_job(
            conn,
            job_id,
            outcome="success",
            ended_at="t",
            chat_calls=0,
            units_done=0,
            units_deferred=0,
        )
    conn.close()


def test_recent_jobs_newest_first_and_last_job_by_kind(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    a = jobs.start_job(conn, "watch", "t1")
    b = jobs.start_job(conn, "maintenance", "t2")
    c = jobs.start_job(conn, "watch", "t3")
    assert [j.id for j in jobs.recent_jobs(conn)] == [c, b, a]
    assert [j.id for j in jobs.recent_jobs(conn, limit=2)] == [c, b]
    last_maintenance = jobs.last_job(conn, "maintenance")
    assert last_maintenance is not None
    assert last_maintenance.id == b
    assert jobs.last_job(conn, "commit-retry") is None
    conn.close()


def test_chat_calls_since_sums_only_the_window(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    assert jobs.chat_calls_since(conn, "2026-09-30T00:00:00Z") == 0
    for started, calls in (
        ("2026-09-29T23:00:00Z", 100),
        ("2026-09-30T01:00:00Z", 4),
        ("2026-09-30T02:00:00Z", 5),
    ):
        job_id = jobs.start_job(conn, "watch", started)
        jobs.finish_job(
            conn,
            job_id,
            outcome="completed",
            ended_at=started,
            chat_calls=calls,
            units_done=1,
            units_deferred=0,
        )
    assert jobs.chat_calls_since(conn, "2026-09-30T00:00:00Z") == 9
    conn.close()


def test_uncommitted_paths_dedupe_and_clear(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    j1 = jobs.start_job(conn, "watch", "t")
    j2 = jobs.start_job(conn, "watch", "t")
    jobs.record_uncommitted_paths(conn, j1, ["bundle/b.md", "bundle/a.md"])
    jobs.record_uncommitted_paths(conn, j2, ["bundle/a.md"])
    assert jobs.uncommitted_paths(conn) == ("bundle/a.md", "bundle/b.md")
    jobs.clear_uncommitted_paths(conn, ["bundle/a.md"])
    assert jobs.uncommitted_paths(conn) == ("bundle/b.md",)
    jobs.clear_uncommitted_paths(conn)
    assert jobs.uncommitted_paths(conn) == ()
    conn.close()


def test_observation_upsert_replaces_and_forget_drops(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    first = jobs.WatchObservation("inbox/a.md", 10, 111, "t1", None, None)
    jobs.upsert_observation(conn, first)
    second = jobs.WatchObservation("inbox/a.md", 12, 222, "t2", "abc", "imported")
    jobs.upsert_observation(conn, second)
    jobs.upsert_observation(
        conn, jobs.WatchObservation("inbox/b.md", 1, 1, "t", None, None)
    )
    assert jobs.observations(conn)[0] == second
    jobs.forget_observations(conn, ["inbox/a.md"])
    assert [o.path for o in jobs.observations(conn)] == ["inbox/b.md"]
    conn.close()


# --- disposable: absent / unreadable ---------------------------------------


def test_deleted_record_starts_fresh_without_error_or_workspace_writes(
    tmp_path: Path,
) -> None:
    (tmp_path / "bundle").mkdir()
    (tmp_path / "raw").mkdir()
    (tmp_path / "bundle" / "c.md").write_text("---\ntype: x\n---\nbody\n")
    (tmp_path / "raw" / "s.txt").write_text("source")
    path = tmp_path / ".openkos" / "jobs.db"

    conn = jobs.open_jobs(path)
    job_id = jobs.start_job(conn, "watch", "2026-09-30T01:00:00Z")
    jobs.finish_job(
        conn,
        job_id,
        outcome="completed",
        ended_at="2026-09-30T01:01:00Z",
        chat_calls=42,
        units_done=1,
        units_deferred=0,
    )
    jobs.record_uncommitted_paths(conn, job_id, ["bundle/c.md"])
    assert jobs.chat_calls_since(conn, "2026-09-30T00:00:00Z") == 42
    conn.close()

    for sidecar in path.parent.glob("jobs.db*"):
        sidecar.unlink()
    before = _outside_engine_state(tmp_path)

    conn = jobs.open_jobs(path)
    assert jobs.recent_jobs(conn) == ()
    assert jobs.chat_calls_since(conn, "2026-09-30T00:00:00Z") == 0
    assert jobs.uncommitted_paths(conn) == ()
    assert jobs.observations(conn) == ()
    conn.close()
    assert _outside_engine_state(tmp_path) == before
    assert set(before) == {"bundle/c.md", "raw/s.txt"}


def test_absent_read_only_is_none_and_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / ".openkos" / "jobs.db"
    assert jobs.open_jobs_read_only(path) is None
    assert not path.parent.exists()


def test_empty_file_without_schema_reads_as_no_record(db_path: Path) -> None:
    db_path.parent.mkdir()
    db_path.touch()
    assert jobs.open_jobs_read_only(db_path) is None


@pytest.mark.parametrize(
    "payload",
    [b"this is not a database" * 50, b"SQLite format 3\x00" + b"\xff" * 200],
    ids=["garbage", "bad-header"],
)
def test_unreadable_record_names_deletion_and_is_left_untouched(
    db_path: Path, payload: bytes
) -> None:
    db_path.parent.mkdir()
    db_path.write_bytes(payload)
    for opener in (jobs.open_jobs, jobs.open_jobs_read_only):
        with pytest.raises(jobs.JobsStoreUnreadableError) as excinfo:
            opener(db_path)
        message = str(excinfo.value)
        assert "delete" in message
        assert str(db_path) in message
        assert excinfo.value.path == db_path
    assert db_path.read_bytes() == payload


def test_read_only_connection_reads_and_refuses_writes(db_path: Path) -> None:
    rw = jobs.open_jobs(db_path)
    job_id = jobs.start_job(rw, "maintenance", "t")
    rw.close()
    ro = jobs.open_jobs_read_only(db_path)
    assert ro is not None
    assert [j.id for j in jobs.recent_jobs(ro)] == [job_id]
    with pytest.raises(sqlite3.OperationalError):
        jobs.start_job(ro, "watch", "t")
    ro.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_store_is_owner_only_with_private_directory(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    jobs.start_job(conn, "watch", "t")
    conn.close()
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700


def test_record_has_no_column_for_document_text(db_path: Path) -> None:
    conn = jobs.open_jobs(db_path)
    columns = {
        r[1]
        for t in ("jobs", "job_uncommitted_paths", "watch_observations")
        for r in conn.execute(f"PRAGMA table_info({t})")
    }
    conn.close()
    assert columns == {
        "id", "kind", "started_at", "ended_at", "outcome", "detail_code",
        "chat_calls", "units_done", "units_deferred",
        "job_id", "path",
        "size", "mtime_ns", "first_stable_at", "digest",
    }  # fmt: skip
