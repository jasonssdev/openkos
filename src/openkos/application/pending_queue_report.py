"""The read-only report behind `openkos pending` (#1141).

Reads the pending-work queue (`.openkos/findings.db`) and the unattended job
record (`.openkos/jobs.db`) through their read-only openers: no lock, no model
call, no write, and neither file is ever created. An ABSENT queue is a state of
its own ("not computed"), never an empty queue: reporting the base as having
nothing pending when nothing has looked would be a false all-clear.

Only a row's kind, status, target ids and the resolving command are rendered.
A row's payload is never put in the report, because proposal text can carry a
person's words.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from openkos.application.budget import JOBS_DB_NAME
from openkos.config import WorkspaceLayout
from openkos.state import jobs
from openkos.state import pending_queue as pq
from openkos.state.readonly import open_read_only

ATTENTION_OUTCOMES = ("budget_exhausted", "timed_out", "commit_failed", "failed")
"""The unattended job outcomes that need a human's attention."""

ATTENTION_LIMIT = 5
_RECENT_JOBS_SCANNED = 50

ABSENT_LINES = (
    "The pending-work queue has not been computed yet.",
    "Run `openkos daemon --once` to compute it.",
)

_RESOLVING_COMMAND = {
    "identity": "openkos duplicates",
    "relation_type": "openkos curate",
    "volatility": "openkos curate",
    "contradiction": "openkos contradictions",
    "revision": "openkos revisions",
    "watch_refusal": "openkos ingest <the refused file under raw/>",
}


class QueueUnavailableError(Exception):
    """`findings.db` exists but cannot be read as a queue."""


@dataclass(frozen=True)
class PendingReport:
    queue_present: bool
    items: tuple[pq.PendingItem, ...]
    open_count: int
    stats: tuple[pq.KindStats, ...]
    attention: tuple[jobs.JobRecord, ...]
    jobs_unreadable: bool


def _read_queue(
    path: Path,
) -> tuple[bool, tuple[pq.PendingItem, ...], tuple[pq.KindStats, ...]]:
    if not path.exists():
        return False, (), ()
    try:
        conn = open_read_only(path)
    except sqlite3.Error as exc:
        raise QueueUnavailableError(str(exc)) from exc
    try:
        if not pq.queue_exists(conn):
            return False, (), ()
        return True, tuple(pq.all_items(conn)), tuple(pq.kind_stats(conn))
    except sqlite3.Error as exc:
        raise QueueUnavailableError(str(exc)) from exc
    finally:
        conn.close()


def _read_attention(path: Path) -> tuple[tuple[jobs.JobRecord, ...], bool]:
    try:
        conn = jobs.open_jobs_read_only(path)
    except jobs.JobsStoreUnreadableError:
        return (), True
    if conn is None:
        return (), False
    try:
        recent = jobs.recent_jobs(conn, _RECENT_JOBS_SCANNED)
    except sqlite3.Error:
        return (), True
    finally:
        conn.close()
    needing = tuple(j for j in recent if j.outcome in ATTENTION_OUTCOMES)
    return needing[:ATTENTION_LIMIT], False


def read_report(layout: WorkspaceLayout) -> PendingReport:
    """Gather the queue and the job outcomes needing attention. Raises
    `QueueUnavailableError` when the queue file exists but is unreadable; a
    damaged job record only sets `jobs_unreadable`."""
    present, items, stats = _read_queue(layout.findings_db_path)
    attention, jobs_unreadable = _read_attention(layout.openkos_dir / JOBS_DB_NAME)
    open_count = sum(1 for i in items if i.status in pq.OPEN_STATUSES)
    return PendingReport(
        queue_present=present,
        items=items,
        open_count=open_count,
        stats=stats,
        attention=attention,
        jobs_unreadable=jobs_unreadable,
    )


def _attention_lines(report: PendingReport) -> list[str]:
    lines: list[str] = []
    if report.attention:
        lines.append("Needs attention (unattended jobs):")
        for job in report.attention:
            detail = f" ({job.detail_code})" if job.detail_code else ""
            deferred = f", {job.units_deferred} deferred" if job.units_deferred else ""
            lines.append(
                f"  {job.kind} job {job.id}: {job.outcome}{detail}{deferred},"
                f" {job.ended_at or job.started_at}"
            )
    if report.jobs_unreadable:
        lines.append(
            "The job record could not be read (unreadable); unattended job"
            " outcomes are not shown."
        )
    return lines


def _listing_lines(report: PendingReport, *, include_all: bool) -> list[str]:
    visible = [i for i in report.items if include_all or i.status in pq.OPEN_STATUSES]
    if not visible and not include_all:
        return ["No open pending-work rows."]
    lines = [f"Pending work: {report.open_count} open row(s)."]
    for kind in pq.KINDS:
        rows = [i for i in visible if i.kind == kind]
        if not rows:
            continue
        lines.append("")
        lines.append(f"{kind} ({len(rows)})")
        for row in rows:
            is_open = row.status in pq.OPEN_STATUSES
            lines.append(
                f"  - {', '.join(row.targets)} [{'pending' if is_open else row.status}]"
            )
            if is_open:
                lines.append(f"    resolve: {_RESOLVING_COMMAND[kind]}")
    return lines


_STATS_HEADERS = (
    "kind",
    "enqueued",
    "as proposed",
    "modified",
    "declined",
    "stale",
    "open",
    "as proposed / resolved",
)


def _fraction(stat: pq.KindStats) -> str:
    resolved = stat.resolved_by_a_human
    if resolved == 0:
        return "n/a"
    percent = round(100 * stat.as_proposed / resolved)
    return f"{stat.as_proposed} of {resolved} ({percent}%)"


def _stats_lines(report: PendingReport) -> list[str]:
    rows = [
        (
            s.kind,
            str(s.enqueued),
            str(s.as_proposed),
            str(s.modified),
            str(s.declined),
            str(s.stale),
            str(s.open),
            _fraction(s),
        )
        for s in report.stats
    ]
    widths = [
        max([len(_STATS_HEADERS[c]), *(len(r[c]) for r in rows)])
        for c in range(len(_STATS_HEADERS) - 1)
    ]

    def fmt(cells: tuple[str, ...]) -> str:
        padded = [c.ljust(w) for c, w in zip(cells, widths, strict=False)]
        return "  ".join([*padded, cells[-1]])

    return [
        "Pending-work statistics. Counts cover only the queue's current lifetime:"
        " a rebuild or purge resets applied history.",
        "",
        fmt(_STATS_HEADERS),
        *(fmt(r) for r in rows),
    ]


def render_lines(
    report: PendingReport, *, include_all: bool = False, stats: bool = False
) -> list[str]:
    """The exact lines `openkos pending` prints, in order."""
    if not report.queue_present:
        lines = list(ABSENT_LINES)
    elif stats and not include_all:
        lines = _stats_lines(report)
    else:
        lines = _listing_lines(report, include_all=include_all)
        if stats:
            lines += ["", *_stats_lines(report)]
    attention = _attention_lines(report)
    if attention:
        lines += ["", *attention]
    return lines
