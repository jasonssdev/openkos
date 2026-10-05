"""The read-only report behind `openkos pending` (#1141).

Reads the pending-work queue (`.openkos/findings.db`) and the unattended job
record (`.openkos/jobs.db`) through their read-only openers: no lock, no model
call, no write, and neither file is ever created. An ABSENT queue is a state of
its own ("not computed"), never an empty queue: reporting the base as having
nothing pending when nothing has looked would be a false all-clear.

Only a row's kind, status, target ids and the resolving command are rendered.
A row's payload is never put in the report, because proposal text can carry a
person's words. Four payload fields are read for the listing only, because each
is a name or a verdict rather than prose: a volatility row's concept type (its
subject, as it has no target), a watch refusal's inbox path (the file to ingest)
an identity row's adjudication verdict (which verb can close it) and a relation
row's suggested type (whether it is a supersession, which has its own command).
"""

import json
import shlex
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkos import config
from openkos.application.budget import JOBS_DB_NAME
from openkos.config import WorkspaceLayout
from openkos.graph import sqlite_graph
from openkos.resolution import candidates
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

ABSENT_QUEUE_NOTICE = (
    "Pending-work queue not available: not computed yet (run `openkos daemon --once`)."
)

_RESOLVING_COMMAND = {
    "identity": "openkos duplicates --keep-distinct",
    "relation_type": "openkos curate --structure",
    "volatility": "openkos curate",
    "contradiction": "openkos contradictions",
    "revision": "openkos revisions",
    "watch_refusal": "openkos ingest <the refused file>",
}


_CAPPED_KINDS = {
    "identity": (candidates._MAX_CANDIDATE_GROUPS, "openkos duplicates"),
    "relation_type": (sqlite_graph._MAX_CANDIDATE_EDGES, "openkos suggest-relations"),
}
"""Kind -> (the advisor's per-run candidate cap, the verb that reports the
uncapped total). The advisors keep at most that many candidates per run and
the queue records no truncation, so a kind listed at the cap is the only trace
of one that bound."""


STRUCTURE_COMMAND = "openkos curate --structure"
"""The one command that reviews the waiting relation suggestions: `curate`
presents its Structure stage only on request."""


def relation_suggestions_waiting(items: Iterable[pq.PendingItem]) -> int:
    """How many OPEN `relation_type` rows of `items` are suggestions awaiting
    review. A `supersedes` row is not one: it is a decision with its own
    command (`openkos relate <newer> supersedes <older>`), listed by `pending`
    under it, and `curate`'s Structure stage never offered it."""
    return sum(
        1
        for item in items
        if item.kind == "relation_type"
        and item.status in pq.OPEN_STATUSES
        and _payload(item).get("suggested_type") != "supersedes"
    )


def waiting_line(count: int) -> str | None:
    """The sentence every surface uses to say suggestions wait, or `None` when
    none do (nothing to say is nothing printed)."""
    if count <= 0:
        return None
    return (
        f"{count} relation suggestion(s) waiting -- review them with "
        f"`{STRUCTURE_COMMAND}`."
    )


def resolving_command(kind: str) -> str:
    """The command that resolves a row of `kind`, with no row-specific
    arguments: the form the MCP gate discloses."""
    return _RESOLVING_COMMAND[kind]


def _payload(row: pq.PendingItem) -> dict[str, object]:
    try:
        decoded = json.loads(row.payload)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def row_subject(row: pq.PendingItem) -> str:
    """What a listed row is about: its targets, or for a volatility row (about a
    concept type, so it has no target) that type."""
    if row.targets:
        return ", ".join(row.targets)
    type_name = _payload(row).get("type_name")
    if row.kind == "volatility" and isinstance(type_name, str):
        return f"type {type_name}"
    return "(no subject)"


def row_alternative_command(row: pq.PendingItem) -> str | None:
    """A second way to close an identity row not yet judged the same: the
    keep-distinct ruling over its members, for a group the operator already
    knows is not one entity (a `adjudicate` judgment costs a model call). Other
    rows, and a row judged the same, have one command."""
    if row.kind != "identity" or not row.targets:
        return None
    adjudication = _payload(row).get("adjudication")
    if isinstance(adjudication, dict) and adjudication.get("verdict") == "same":
        return None
    flags = " ".join(f"--keep-distinct {shlex.quote(t)}" for t in row.targets)
    return f"openkos duplicates {flags}"


def row_resolving_command(row: pq.PendingItem, inbox: Path | None = None) -> str:
    """The command that resolves THIS row. `openkos duplicates` only lists
    groups, so an identity row names the verb that can close it: the merge walk
    for a group judged the same, otherwise the judgment walk (`adjudicate
    --apply`, whose prompt takes y / s / d), with the keep-distinct ruling as
    `row_alternative_command`. A watch refusal names the refused file: the watch records its
    path relative to the inbox, so `inbox` (the configured folder, when known)
    is joined on."""
    payload = _payload(row)
    if row.kind == "identity" and row.targets:
        adjudication = payload.get("adjudication")
        if isinstance(adjudication, dict) and adjudication.get("verdict") == "same":
            return "openkos adjudicate --apply"
        if len(row.targets) == 2:
            return "openkos adjudicate --apply (y merges, s skips, d records keep-distinct)"
        return (
            "openkos adjudicate --apply (prints the pairwise `openkos merge` commands)"
        )
    if row.kind == "relation_type" and payload.get("suggested_type") == "supersedes":
        newer = payload.get("effective_source_id")
        older = payload.get("effective_target_id")
        if isinstance(newer, str) and isinstance(older, str):
            return (
                f"openkos relate {shlex.quote(newer)} supersedes {shlex.quote(older)}"
            )
    inbox_path = payload.get("inbox_path")
    if row.kind == "watch_refusal" and isinstance(inbox_path, str) and inbox_path:
        refused = inbox / inbox_path if inbox is not None else inbox_path
        return f"openkos ingest {shlex.quote(str(refused))}"
    return _RESOLVING_COMMAND[row.kind]


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
    inbox: Path | None = None
    """The configured `unattended.inbox`, when it can be read: the base a watch
    refusal's recorded path is relative to."""


def _read_inbox(layout: WorkspaceLayout) -> Path | None:
    try:
        return config.read_config(layout.root).unattended.inbox
    except (OSError, ValueError):
        return None


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
        inbox=_read_inbox(layout),
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
    waiting = waiting_line(relation_suggestions_waiting(report.items))
    if waiting is not None:
        lines.append(waiting)
    for kind in pq.KINDS:
        rows = [i for i in visible if i.kind == kind]
        if not rows:
            continue
        lines.append("")
        lines.append(f"{kind} ({len(rows)})")
        capped = _CAPPED_KINDS.get(kind)
        open_rows = sum(1 for r in rows if r.status in pq.OPEN_STATUSES)
        if capped is not None and open_rows >= capped[0]:
            lines.append(
                f"  note: {capped[0]} is the per-run candidate cap, so more "
                f"may exist; `{capped[1]}` reports the total."
            )
        for row in rows:
            is_open = row.status in pq.OPEN_STATUSES
            lines.append(
                f"  - {row_subject(row)} [{'pending' if is_open else row.status}]"
            )
            if is_open:
                lines.append(f"    resolve: {row_resolving_command(row, report.inbox)}")
                alternative = row_alternative_command(row)
                if alternative is not None:
                    lines.append(f"    or: {alternative}")
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


@dataclass(frozen=True)
class QueueSnapshot:
    """What `next` and `status` need from the queue and the job record: the
    open rows and the single most recent job, each with an explicit
    availability so an ABSENT or unreadable store is never read as an empty
    one."""

    queue: Literal["present", "absent", "unreadable"]
    open_items: tuple[pq.PendingItem, ...]
    last_job: jobs.JobRecord | None
    jobs_unreadable: bool
    """`last_job is None` with this `False` means no unattended run is
    recorded (no `jobs.db`, or one with no jobs)."""


def _read_last_job(path: Path) -> tuple[jobs.JobRecord | None, bool]:
    try:
        conn = jobs.open_jobs_read_only(path)
    except jobs.JobsStoreUnreadableError:
        return None, True
    if conn is None:
        return None, False
    try:
        return jobs.last_job(conn), False
    except sqlite3.Error:
        return None, True
    finally:
        conn.close()


def read_snapshot(layout: WorkspaceLayout) -> QueueSnapshot:
    """Read the open queue rows and the most recent job, read-only, never
    creating either file and never raising: the readers behind `openkos
    pending`, narrowed to what the pure `next` / `status` services report."""
    try:
        present, items, _stats = _read_queue(layout.findings_db_path)
    except QueueUnavailableError:
        queue: Literal["present", "absent", "unreadable"] = "unreadable"
        open_items: tuple[pq.PendingItem, ...] = ()
    else:
        queue = "present" if present else "absent"
        open_items = tuple(i for i in items if i.status in pq.OPEN_STATUSES)
    last_job, jobs_unreadable = _read_last_job(layout.openkos_dir / JOBS_DB_NAME)
    return QueueSnapshot(
        queue=queue,
        open_items=open_items,
        last_job=last_job,
        jobs_unreadable=jobs_unreadable,
    )


_REMEDY = {
    "budget_exhausted": (
        "the rest runs on a later pass once the `unattended:` budget in "
        "openkos.yaml allows it"
    ),
    "timed_out": "the rest is retried on the next unattended run",
    "commit_failed": (
        "the next unattended run retries the commit; or commit the changed "
        "files yourself"
    ),
    "failed": "fix the cause, then run `openkos daemon --once`",
}


def attention_outcome(job: jobs.JobRecord | None) -> str | None:
    """The outcome of `job` when it needs a human, else `None`."""
    if job is not None and job.outcome in ATTENTION_OUTCOMES:
        return job.outcome
    return None


def attention_summary(job: jobs.JobRecord) -> str:
    """One line naming an attention-worthy job: its kind, outcome, end time,
    what was deferred, and the remedy. Never carries a row's payload."""
    outcome = job.outcome or ""
    detail = f" ({job.detail_code})" if job.detail_code else ""
    deferred = f", {job.units_deferred} deferred" if job.units_deferred else ""
    return (
        f"the last unattended {job.kind} job ended {outcome}{detail}"
        f"{deferred} at {job.ended_at or job.started_at}"
        f" -- {_REMEDY.get(outcome, 'see `openkos pending`')}"
    )


def open_row_breakdown(items: tuple[pq.PendingItem, ...]) -> str:
    """`2 identity, 1 revision`: per-kind counts of `items`, in `pq.KINDS`
    order, for kinds that have a row."""
    return ", ".join(
        f"{sum(1 for i in items if i.kind == kind)} {kind}"
        for kind in pq.KINDS
        if any(i.kind == kind for i in items)
    )


@dataclass(frozen=True)
class StatusLines:
    """The queue and unattended-job lines `openkos status` prints: those that
    belong under **Needs attention**, and informational ones that must not
    stop the section from saying nothing needs attention."""

    attention: tuple[str, ...]
    notices: tuple[str, ...]


def status_lines(snapshot: QueueSnapshot) -> StatusLines:
    """Render `snapshot` for `status`. A missing or unreadable queue or job
    record is "not available", never "nothing pending"."""
    attention: list[str] = []
    notices: list[str] = []
    if snapshot.queue == "absent":
        notices.append(ABSENT_QUEUE_NOTICE)
    elif snapshot.queue == "unreadable":
        notices.append(
            "Pending-work queue not available: findings.db could not be read."
        )
    elif not snapshot.open_items:
        notices.append("Pending-work queue: no open rows.")
    else:
        count = len(snapshot.open_items)
        attention.append(
            f"{count} open pending-work row{'' if count == 1 else 's'} "
            f"({open_row_breakdown(snapshot.open_items)}) -- "
            "run `openkos pending` to review."
        )
    job = snapshot.last_job
    if snapshot.jobs_unreadable:
        notices.append(
            "Unattended job record not available: jobs.db could not be read."
        )
    elif job is None:
        notices.append("No unattended run recorded.")
    elif attention_outcome(job) is not None:
        summary = attention_summary(job)
        attention.append(f"{summary[:1].upper()}{summary[1:]}")
    elif job.outcome is None:
        notices.append(
            f"Last unattended job: {job.kind}, in progress, started {job.started_at}."
        )
    else:
        notices.append(
            f"Last unattended job: {job.kind}, {job.outcome}, "
            f"ended {job.ended_at or job.started_at}."
        )
    return StatusLines(attention=tuple(attention), notices=tuple(notices))


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
