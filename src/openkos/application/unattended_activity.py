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

import dataclasses
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from openkos import config
from openkos.application import budget
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


_RECENT = 10
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_RUNNING_GRACE_SECONDS = 300
"""How long past its deadline an unfinished job still counts as running. A job
that never finished (its process died) stays an open row for good; past the
deadline plus this grace it is history, not a task in progress."""
_WAITING_OUTCOMES = ("changed", "deferred")


@dataclass(frozen=True)
class EngineActivity:
    """What the engine is doing, has done lately and has waiting, plus today's
    model-call budget. A record that is absent reads as an empty activity; one
    that exists but cannot be read sets `jobs_unreadable` and nothing else."""

    current: jobs.JobRecord | None
    recent: tuple[jobs.JobRecord, ...]
    waiting_files: int
    uncommitted_paths: int
    calls_used_today: int
    calls_per_day: int
    budget_exhausted: bool
    jobs_unreadable: bool


def _is_running(
    job: jobs.JobRecord, unattended: config.UnattendedConfig, now: datetime
) -> bool:
    if job.ended_at is not None:
        return False
    try:
        began = datetime.strptime(job.started_at, _STAMP).replace(tzinfo=UTC)
    except ValueError:
        return False
    age = (now - began).total_seconds()
    return age <= unattended.job_deadline_seconds + _RUNNING_GRACE_SECONDS


def _is_waiting(obs: jobs.WatchObservation) -> bool:
    outcome = obs.outcome
    return outcome is None or outcome.startswith(_WAITING_OUTCOMES)


def read_engine_activity(
    layout: config.WorkspaceLayout,
    unattended: config.UnattendedConfig,
    now: datetime,
    *,
    recent_limit: int = _RECENT,
) -> EngineActivity:
    """The engine's activity. Read-only: no lock, no model, no write, and the
    job record is never created. Never raises for an absent or damaged record.

    `now` must be timezone-aware and local (it picks the budget's calendar
    day, as the runner does)."""
    empty = EngineActivity(
        current=None,
        recent=(),
        waiting_files=0,
        uncommitted_paths=0,
        calls_used_today=0,
        calls_per_day=unattended.max_calls_per_day,
        budget_exhausted=False,
        jobs_unreadable=False,
    )
    try:
        conn = jobs.open_jobs_read_only(layout.jobs_db_path)
    except jobs.JobsStoreUnreadableError:
        return dataclasses.replace(empty, jobs_unreadable=True)
    if conn is None:
        return empty
    try:
        newest = jobs.recent_jobs(conn, max(recent_limit, _SCANNED) + 1)
        used = jobs.chat_calls_since(conn, budget.local_day_start_utc(now))
        waiting = sum(1 for obs in jobs.observations(conn) if _is_waiting(obs))
        uncommitted = len(jobs.uncommitted_paths(conn))
    except sqlite3.Error:
        return dataclasses.replace(empty, jobs_unreadable=True)
    finally:
        conn.close()
    current = newest[0] if newest and _is_running(newest[0], unattended, now) else None
    finished = tuple(job for job in newest if job.ended_at is not None)
    budgeted = next((job for job in newest if job.kind in _BUDGETED_KINDS), None)
    return EngineActivity(
        current=current,
        recent=finished[:recent_limit],
        waiting_files=waiting,
        uncommitted_paths=uncommitted,
        calls_used_today=used,
        calls_per_day=unattended.max_calls_per_day,
        budget_exhausted=budgeted is not None
        and budgeted.outcome == "budget_exhausted",
        jobs_unreadable=False,
    )
