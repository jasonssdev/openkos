"""Workspace status and the pending-work report.

Both reads take no lock, build no model client and write nothing. The engine's
own report types carry lint findings, a bundle survey, queue rows and job
records; none of that crosses the facade. Each is mapped here to counts,
strings and the API-owned values below, so the stored shapes can change without
an `API_VERSION` bump.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol

from openkos import config
from openkos.api._workspace import Workspace
from openkos.application import pending_queue_report
from openkos.application import status as application_status
from openkos.application import unattended_activity as application_unattended


@dataclass(frozen=True)
class ActivityEntry:
    """One `log.md` bullet: its section date and its text."""

    date: str
    text: str


@dataclass(frozen=True)
class HealthCounts:
    """How many findings each knowledge-health check reports. Counts only; the
    findings themselves are not part of the surface."""

    dangling: int
    unextracted: int
    unjudged: int
    unevidenced: int
    staging_dropped: int
    below_source_sensitivity: int
    dangling_provenance: int
    unbacked_provenance: int
    exact_title_groups: int
    open_contradictions: int
    stale_contradictions: int


@dataclass(frozen=True)
class JobSummary:
    """One unattended job outcome."""

    kind: str
    started_at: str
    ended_at: str | None
    outcome: str | None
    detail_code: str | None
    units_deferred: int
    needs_attention: bool


@dataclass(frozen=True)
class WorkspaceStatus:
    """What `openkos status` reads, as counts and strings."""

    root: Path
    sources: int
    concepts: int
    concepts_by_type: Mapping[str, int]
    """Concepts per OKF type (sources excluded), as a read-only mapping."""
    conformance_findings: tuple[str, ...]
    recent_activity: tuple[ActivityEntry, ...] | None
    """`None`: the log could not be read. `()`: it is readable and empty."""
    health: HealthCounts
    vectors_missing: bool
    stale_indexes: tuple[str, ...]
    edges: tuple[int, int] | None
    """`(total, typed)` concept edges, or `None` when not computed."""
    queue: Literal["present", "absent", "unreadable"]
    open_decisions_by_kind: Mapping[str, int]
    last_job: JobSummary | None
    jobs_unreadable: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class KindCounts:
    """Lifetime counters of one pending-work kind."""

    kind: str
    enqueued: int
    applied_as_proposed: int
    applied_modified: int
    declined: int
    stale: int
    open: int


@dataclass(frozen=True)
class PendingRow:
    """One open pending-work row and the command that resolves it."""

    decision_key: str
    kind: str
    targets: tuple[str, ...]
    subject: str
    resolving_command: str


@dataclass(frozen=True)
class PendingReport:
    """What `openkos pending` reads."""

    queue_computed: bool
    """`False`: the queue was never computed. That is not the same as
    `True` with no open rows."""
    open_rows: tuple[PendingRow, ...]
    stats: tuple[KindCounts, ...]
    attention: tuple[JobSummary, ...]
    jobs_unreadable: bool


@dataclass(frozen=True)
class UnattendedActivity:
    """The unattended engine's latest outcome. `budget_exhausted`: the newest
    watch or maintenance job ended because the model-call budget ran out (the
    daemon idles and records it; it does not exit). `last_job` is the newest
    job of any kind. `jobs_unreadable` is a record that exists but cannot be
    read, which is not the same as no history."""

    last_job: JobSummary | None
    budget_exhausted: bool
    jobs_unreadable: bool


@dataclass(frozen=True)
class EngineJob:
    """One unattended task as the Activity view shows it. `ended_at` and
    `outcome` are `None` while it runs. `needs_attention`: it ended in a way a
    person should look at (a failure, a timeout or a spent budget)."""

    kind: str
    started_at: str
    ended_at: str | None
    outcome: str | None
    chat_calls: int
    units_done: int
    units_deferred: int
    needs_attention: bool


@dataclass(frozen=True)
class DailyBudget:
    """Today's model-call budget for unattended work, in calls, on the local
    calendar day. `exhausted`: the newest watch or maintenance job ended
    because the budget ran out."""

    calls_used: int
    calls_limit: int
    exhausted: bool


@dataclass(frozen=True)
class EngineActivity:
    """What the engine is doing: the task running now (`None` when idle), the
    latest finished ones (newest first), how many files wait in the watched
    folder, how many written paths wait for a commit retry, and today's budget.
    `jobs_unreadable` is a record that exists but cannot be read, which is not
    the same as an engine with nothing to show."""

    current: EngineJob | None
    recent: tuple[EngineJob, ...]
    waiting_files: int
    uncommitted_paths: int
    budget: DailyBudget
    jobs_unreadable: bool


class _JobFields(Protocol):
    """The fields of an engine job record the summary reads. Structural, so the
    facade names no `state` type."""

    @property
    def kind(self) -> str: ...
    @property
    def started_at(self) -> str: ...
    @property
    def ended_at(self) -> str | None: ...
    @property
    def outcome(self) -> str | None: ...
    @property
    def detail_code(self) -> str | None: ...
    @property
    def units_deferred(self) -> int: ...


class _EngineJobFields(_JobFields, Protocol):
    @property
    def chat_calls(self) -> int: ...
    @property
    def units_done(self) -> int: ...


def _job_summary(job: _JobFields) -> JobSummary:
    return JobSummary(
        kind=job.kind,
        started_at=job.started_at,
        ended_at=job.ended_at,
        outcome=job.outcome,
        detail_code=job.detail_code,
        units_deferred=job.units_deferred,
        needs_attention=job.outcome in pending_queue_report.ATTENTION_OUTCOMES,
    )


def status(workspace: Workspace) -> WorkspaceStatus:
    """The workspace's status. Takes no lock, builds no client, writes nothing."""
    layout = workspace._layout
    overview = application_status.read_status_overview(layout)
    report = application_status.build_status_report(layout)
    survey = overview.survey
    open_by_kind: dict[str, int] = {}
    for item in report.unattended.open_items:
        open_by_kind[item.kind] = open_by_kind.get(item.kind, 0) + 1
    last_job = report.unattended.last_job
    return WorkspaceStatus(
        root=workspace.root,
        sources=survey.sources,
        concepts=survey.concepts,
        concepts_by_type=MappingProxyType(
            {name: n for name, n in survey.by_type.items() if name != "Source"}
        ),
        conformance_findings=tuple(survey.findings),
        recent_activity=(
            None
            if overview.recent_entries is None
            else tuple(ActivityEntry(e.date, e.text) for e in overview.recent_entries)
        ),
        health=HealthCounts(
            dangling=len(report.dangling),
            unextracted=len(report.unextracted),
            unjudged=len(report.unjudged),
            unevidenced=len(report.unevidenced),
            staging_dropped=len(report.staging_dropped),
            below_source_sensitivity=len(report.sensitivity_findings),
            dangling_provenance=len(report.dangling_provenance),
            unbacked_provenance=len(report.unbacked_provenance),
            exact_title_groups=report.exact_title_group_count,
            open_contradictions=report.open_contradictions,
            stale_contradictions=report.stale_contradictions,
        ),
        vectors_missing=report.vectors_missing,
        stale_indexes=report.stale_indexes,
        edges=report.edge_summary,
        queue=report.unattended.queue,
        open_decisions_by_kind=MappingProxyType(open_by_kind),
        last_job=None if last_job is None else _job_summary(last_job),
        jobs_unreadable=report.unattended.jobs_unreadable,
        warnings=report.warnings,
    )


def pending_report(workspace: Workspace) -> PendingReport:
    """The pending-work report. Raises `QueueUnavailableError` when the queue
    file exists but cannot be read. Takes no lock, builds no client, writes
    nothing."""
    raw = pending_queue_report.read_report(workspace._layout)
    rows = tuple(
        PendingRow(
            decision_key=item.decision_key,
            kind=item.kind,
            targets=item.targets,
            subject=pending_queue_report.row_subject(item),
            resolving_command=pending_queue_report.row_resolving_command(
                item, raw.inbox, raw.bundle_dir
            ),
        )
        for item in pending_queue_report.open_items(raw)
    )
    return PendingReport(
        queue_computed=raw.queue_present,
        open_rows=rows,
        stats=tuple(
            KindCounts(
                kind=s.kind,
                enqueued=s.enqueued,
                applied_as_proposed=s.as_proposed,
                applied_modified=s.modified,
                declined=s.declined,
                stale=s.stale,
                open=s.open,
            )
            for s in raw.stats
        ),
        attention=tuple(_job_summary(job) for job in raw.attention),
        jobs_unreadable=raw.jobs_unreadable,
    )


def unattended_activity(workspace: Workspace) -> UnattendedActivity:
    """The latest unattended job and whether the budget is spent. Takes no
    lock, builds no client, writes nothing, and never creates the job record."""
    activity = application_unattended.read_unattended_activity(workspace._layout)
    last = activity.last_job
    return UnattendedActivity(
        last_job=None if last is None else _job_summary(last),
        budget_exhausted=activity.budget_exhausted,
        jobs_unreadable=activity.jobs_unreadable,
    )


def _engine_job(job: _EngineJobFields) -> EngineJob:
    return EngineJob(
        kind=job.kind,
        started_at=job.started_at,
        ended_at=job.ended_at,
        outcome=job.outcome,
        chat_calls=job.chat_calls,
        units_done=job.units_done,
        units_deferred=job.units_deferred,
        needs_attention=job.outcome in pending_queue_report.ATTENTION_OUTCOMES,
    )


def engine_activity(workspace: Workspace) -> EngineActivity:
    """What the engine is doing now, did lately and has waiting, with today's
    model-call budget. Takes no lock, builds no client, writes nothing, never
    creates the job record, and never raises for an absent or damaged one."""
    unattended = config.read_config(workspace.root).unattended
    raw = application_unattended.read_engine_activity(
        workspace._layout, unattended, datetime.now().astimezone()
    )
    return EngineActivity(
        current=None if raw.current is None else _engine_job(raw.current),
        recent=tuple(_engine_job(job) for job in raw.recent),
        waiting_files=raw.waiting_files,
        uncommitted_paths=raw.uncommitted_paths,
        budget=DailyBudget(
            calls_used=raw.calls_used_today,
            calls_limit=raw.calls_per_day,
            exhausted=raw.budget_exhausted,
        ),
        jobs_unreadable=raw.jobs_unreadable,
    )
