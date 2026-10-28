"""The unattended engine's latest outcome, read for the desktop app's watch
status (ADR-0078): read-only, and a spent budget is the newest watch or
maintenance job ending `budget_exhausted` -- the daemon never exits on it, it
idles and records the outcome in `jobs.db`."""

from pathlib import Path

import pytest

from openkos import config
from openkos.application import unattended_activity
from openkos.state import jobs as jobs_store


def _layout(tmp_path: Path) -> config.WorkspaceLayout:
    (tmp_path / ".openkos").mkdir()
    return config.WorkspaceLayout(tmp_path)


def _record(
    layout: config.WorkspaceLayout,
    kind: str,
    outcome: str | None,
    *,
    at: str = "2026-10-27T10:00:00Z",
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
                chat_calls=0,
                units_done=0,
                units_deferred=2,
            )
    finally:
        conn.close()


def test_no_record_is_no_history_and_creates_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    activity = unattended_activity.read_unattended_activity(layout)

    assert activity.last_job is None
    assert activity.budget_exhausted is False
    assert activity.jobs_unreadable is False
    assert not layout.jobs_db_path.exists()


@pytest.mark.parametrize("kind", ["watch", "maintenance"])
def test_a_job_that_ran_out_of_budget_is_the_stop(tmp_path: Path, kind: str) -> None:
    layout = _layout(tmp_path)
    _record(layout, kind, "budget_exhausted")

    activity = unattended_activity.read_unattended_activity(layout)

    assert activity.budget_exhausted is True
    assert activity.last_job is not None
    assert (activity.last_job.kind, activity.last_job.outcome) == (
        kind,
        "budget_exhausted",
    )


def test_a_later_clean_pass_ends_the_stop(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _record(layout, "watch", "budget_exhausted")
    _record(layout, "maintenance", "completed")

    assert (
        unattended_activity.read_unattended_activity(layout).budget_exhausted is False
    )


def test_a_commit_retry_does_not_end_the_stop(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _record(layout, "watch", "budget_exhausted")
    _record(layout, "commit-retry", "completed")

    activity = unattended_activity.read_unattended_activity(layout)

    assert activity.budget_exhausted is True
    assert activity.last_job is not None
    assert activity.last_job.kind == "commit-retry"


def test_a_pass_still_running_is_not_a_stop(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _record(layout, "maintenance", "budget_exhausted")
    _record(layout, "maintenance", None)

    assert (
        unattended_activity.read_unattended_activity(layout).budget_exhausted is False
    )


def test_an_unreadable_record_is_reported_not_guessed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.jobs_db_path.write_bytes(b"this is not a database" * 100)

    activity = unattended_activity.read_unattended_activity(layout)

    assert activity.jobs_unreadable is True
    assert activity.budget_exhausted is False
    assert activity.last_job is None
