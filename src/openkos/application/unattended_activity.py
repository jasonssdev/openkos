"""The unattended engine's latest outcome, for the desktop app's watch status
(ADR-0078).

The daemon never exits when its model-call budget is spent: it records the
outcome in `.openkos/jobs.db` and idles until the budget window rolls. So
"stopped on the budget" is not an exit code the shell can see; it is the newest
watch or maintenance job ending `budget_exhausted`. This module reads exactly
that, plus the newest job of any kind, through the read-only opener: no lock,
no model, no write, and an absent record is never created.

A commit retry does not speak for the budget (it makes no model call), and a
pass still running (no outcome yet) is not a stop.
"""

import sqlite3
from dataclasses import dataclass

from openkos import config
from openkos.state import jobs

_BUDGETED_KINDS = ("watch", "maintenance")
_SCANNED = 20


@dataclass(frozen=True)
class UnattendedActivity:
    """What the job record says now. `last_job` is the newest job of any kind
    (`None`: no record, or none readable); `budget_exhausted` is whether the
    newest watch or maintenance job ended because the budget ran out;
    `jobs_unreadable` is a record that exists but cannot be read, which is
    reported and never read as "all well"."""

    last_job: jobs.JobRecord | None
    budget_exhausted: bool
    jobs_unreadable: bool


def read_unattended_activity(layout: config.WorkspaceLayout) -> UnattendedActivity:
    """The latest unattended outcome. Never raises for a damaged record."""
    try:
        conn = jobs.open_jobs_read_only(layout.jobs_db_path)
    except jobs.JobsStoreUnreadableError:
        return UnattendedActivity(None, False, True)
    if conn is None:
        return UnattendedActivity(None, False, False)
    try:
        recent = jobs.recent_jobs(conn, _SCANNED)
    except sqlite3.Error:
        return UnattendedActivity(None, False, True)
    finally:
        conn.close()
    budgeted = next((job for job in recent if job.kind in _BUDGETED_KINDS), None)
    return UnattendedActivity(
        last_job=recent[0] if recent else None,
        budget_exhausted=budgeted is not None
        and budgeted.outcome == "budget_exhausted",
        jobs_unreadable=False,
    )
