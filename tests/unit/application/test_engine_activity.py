"""The engine's activity for the desktop Activity view: the running task, the
recent ones, what waits, and today's model-call budget. Read-only over
`jobs.db`, which is disposable, so a missing or damaged record degrades to an
honest empty or unavailable answer rather than an error."""

from datetime import UTC, datetime
from pathlib import Path

from openkos import config
from openkos.application import unattended_activity as ua
from openkos.state import jobs as jobs_store

NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
LIMITS = config.UnattendedConfig(max_calls_per_day=50, job_deadline_seconds=600)


def _layout(tmp_path: Path) -> config.WorkspaceLayout:
    (tmp_path / ".openkos").mkdir()
    return config.WorkspaceLayout(tmp_path)


def _job(
    layout: config.WorkspaceLayout,
    kind: str,
    outcome: str | None,
    *,
    at: str = "2026-10-27T10:00:00Z",
    calls: int = 0,
    done: int = 0,
    deferred: int = 0,
) -> None:
    conn = jobs_store.open_jobs(layout.jobs_db_path)
    try:
        job_id = jobs_store.start_job(conn, kind, at)
        if outcome is not None:
            jobs_store.finish_job(
                conn,
                job_id,
                outcome=outcome,
                ended_at=at,
                chat_calls=calls,
                units_done=done,
                units_deferred=deferred,
            )
    finally:
        conn.close()


def _read(layout: config.WorkspaceLayout, **kw: int) -> ua.EngineActivity:
    return ua.read_engine_activity(layout, LIMITS, NOW, **kw)


def test_no_record_is_an_empty_answer_and_creates_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    activity = _read(layout)

    assert activity == ua.EngineActivity(
        current=None,
        recent=(),
        waiting_files=0,
        uncommitted_paths=0,
        calls_used_today=0,
        calls_per_day=50,
        budget_exhausted=False,
        jobs_unreadable=False,
    )
    assert not layout.jobs_db_path.exists()


def test_an_unreadable_record_is_reported_not_read_as_empty(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.jobs_db_path.write_bytes(b"this is not a database" * 50)

    activity = _read(layout)

    assert activity.jobs_unreadable is True
    assert activity.current is None
    assert activity.recent == ()
    assert layout.jobs_db_path.read_bytes().startswith(b"this is not")


def test_a_recent_unfinished_job_is_the_current_task(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _job(layout, "watch", "completed", at="2026-10-27T09:00:00Z", done=2)
    _job(layout, "maintenance", None, at="2026-10-27T11:55:00Z")

    activity = _read(layout)

    assert activity.current is not None
    assert activity.current.kind == "maintenance"
    assert activity.current.ended_at is None
    assert [j.kind for j in activity.recent] == ["watch"]


def test_a_stale_unfinished_job_is_not_a_running_task(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    # Started hours ago with a ten minute deadline: the process died.
    _job(layout, "watch", None, at="2026-10-27T03:00:00Z")

    activity = _read(layout)

    assert activity.current is None
    assert activity.recent == ()


def test_an_unfinished_job_older_than_a_newer_one_is_not_current(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _job(layout, "watch", None, at="2026-10-27T11:55:00Z")
    _job(layout, "maintenance", "completed", at="2026-10-27T11:57:00Z")

    assert _read(layout).current is None


def test_recent_tasks_are_newest_first_and_bounded(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    for n in range(5):
        _job(layout, "watch", "completed", at=f"2026-10-27T0{n}:00:00Z", done=n)

    activity = _read(layout, recent_limit=3)

    assert [j.units_done for j in activity.recent] == [4, 3, 2]


def test_today_counts_only_jobs_from_the_local_day(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _job(layout, "watch", "completed", at="2026-10-26T10:00:00Z", calls=40)
    _job(layout, "watch", "completed", at="2026-10-27T10:00:00Z", calls=7)
    _job(layout, "maintenance", "completed", at="2026-10-27T11:00:00Z", calls=5)

    activity = _read(layout)

    assert activity.calls_used_today == 12
    assert activity.calls_per_day == 50


def test_a_spent_budget_is_flagged(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _job(layout, "watch", "budget_exhausted", calls=50)

    assert _read(layout).budget_exhausted is True


def test_waiting_files_and_uncommitted_paths_are_counted(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    conn = jobs_store.open_jobs(layout.jobs_db_path)
    try:
        for path, outcome in [
            ("a.md", None),
            ("b.md", "changed"),
            ("c.md", "changed:imported"),
            ("d.md", "deferred"),
            ("e.md", "imported"),
            ("f.md", "refused"),
            ("g.md", "gone:refused"),
        ]:
            jobs_store.upsert_observation(
                conn,
                jobs_store.WatchObservation(
                    path, 1, 1, "2026-10-27T10:00:00Z", None, outcome
                ),
            )
        job_id = jobs_store.start_job(conn, "watch", "2026-10-27T10:00:00Z")
        jobs_store.record_uncommitted_paths(conn, job_id, ["x", "y"])
    finally:
        conn.close()

    activity = _read(layout)

    assert activity.waiting_files == 4
    assert activity.uncommitted_paths == 2
