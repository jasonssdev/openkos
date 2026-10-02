"""The unattended job runner's core (MVP 4, job-runtime, ADR-0037).

Synchronous (ADR-0021): the daemon verb drives these functions one job at a
time and owns everything around them -- signal handlers, the poll loop, the log
file, the exit code. This module imports no `openkos.cli`, `typer` or `rich`
and never starts a subprocess; every effect it cannot own arrives through
`RunnerPorts`, which the verb wires from the CLI's resolvers.

**What a job is.** A job runs units of work in a fixed order and ends with
exactly ONE outcome recorded in `jobs.db` (`state/jobs.py`). The maintenance
job's units are the incremental derived refresh, the lint counts, and each
advisor stage in order; an advisor stage computes its proposals and enqueues them
through `application/queue_producers.py`. The runner performs no consequential
write: it never merges, relates, reconciles, forgets or sets a tier, it never
passes a consent flag, and `UnattendedPolicy` declines every write confirmation
a stage could be asked. A pass therefore changes nothing under `bundle/`.

**Cooperative control.** The stop flag and the deadline are checked between
units and immediately before a commit phase's write burst, never inside it
(`runtime.check_halt`). A unit already running is allowed to finish. Whatever a
halted job did not reach is recorded as deferred.

**Contention.** The commit section a stage receives takes the workspace lock
with jittered exponential backoff bounded by the lesser of a per-acquisition cap
and the job's remaining deadline; a derived-store `LockContention` from the
refresh is retried under the same bound. Exhaustion defers the unit, and the job
is `busy` only when nothing was completed.

**The budget.** `budget.start_budgeted_run` is called here and nowhere else. A
model-using stage is not started when nothing remains, and is handed the live
remainder so it can bound itself (`max_calls`).

**Commit failures.** A job that wrote files and could not commit them records
`commit_failed` plus the paths (`attempt_commit` / `record_commit_failure`); the
next `run_due_jobs` retries those still dirty FIRST.

**Extension point (unit 7.2).** The watch job is a third job kind. It plugs in
beside `run_maintenance_job`, reusing the job record helpers, `Deadline`, the
budget, `_commit_section` and `attempt_commit`, and is listed in `run_due_jobs`
between the commit-retry and the maintenance job (`JOB_ORDER`).

Logging goes through `logging.getLogger(__name__)` and carries ids, counts and
codes only -- never a path's content, document text, model output or rationale.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import random
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from openkos import config, lock
from openkos.application import budget as budget_module
from openkos.application import lint as lint_service
from openkos.application.lock_wait import (
    INITIAL_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    CommitSection,
    acquire_with_backoff,
)
from openkos.application.reindex_service import LockContention, ReindexRefused
from openkos.application.runtime import (
    Deadline,
    Halt,
    StopToken,
    UnattendedPolicy,
    check_halt,
)
from openkos.state import derived, jobs
from openkos.state import pending_queue as pq

if TYPE_CHECKING:
    from openkos.application.watch import WatchPorts

log = logging.getLogger(__name__)

JOB_ORDER: Final = ("commit-retry", "watch", "maintenance")
"""The order `run_due_jobs` runs due jobs in. `watch` is unit 7.2's."""

DEFAULT_WAIT_CAP_SECONDS: Final = 30.0
"""The most a single lock or derived-store acquisition waits, before the job's
remaining deadline is even considered."""

COMMIT_RETRY_MESSAGE: Final = "openkos: retry auto-commit of unattended writes"


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_now() -> datetime:
    return datetime.now().astimezone()


def default_lint_counts(layout: config.WorkspaceLayout) -> Mapping[str, int]:
    """Per-check finding counts of the workspace's lint report (no text)."""
    report = lint_service.build_lint_report(layout)
    return {
        f.name: len(value)
        for f in dataclasses.fields(report)
        if isinstance(value := getattr(report, f.name), list | tuple)
    }


# -- typed commit results -----------------------------------------------------------


@dataclass(frozen=True)
class Committed:
    sha: str | None


@dataclass(frozen=True)
class Skipped:
    """No commit was attempted and none can succeed by retrying until the
    workspace is fixed (`not_a_repository`, `identity_unset`)."""

    reason: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class CommitFailed:
    """A commit was attempted and failed (a git error or timeout)."""

    reason: str
    paths: tuple[str, ...]


CommitResult = Committed | Skipped | CommitFailed


# -- ports --------------------------------------------------------------------------


@dataclass(frozen=True)
class StageContext:
    """What an advisor stage receives. It owns no lock and no connection: the
    stage computes lock-free, enters `commit_section()` only to write, and asks
    for the queue lazily so a job with nothing to enqueue creates no store."""

    root: Path
    layout: config.WorkspaceLayout
    budget: budget_module.BudgetedRun
    policy: UnattendedPolicy
    commit_section: CommitSection
    queue: Callable[[], sqlite3.Connection]
    notify: Callable[[str], None] = lambda message: None
    """Where a stage says what its run did NOT cover (a candidate cap that bound,
    #1265): the runner's `announce` port, so an unattended pass never swallows a
    notice an attended verb would print."""


@dataclass(frozen=True)
class StageResult:
    deferred_by_bound: int = 0
    """Candidates the stage's `max_calls` bound left for a later pass."""


@dataclass(frozen=True)
class AdvisorStage:
    """One compute-and-enqueue unit. `run` computes the advisor's result with
    `max_calls=ctx.budget.remaining`, counts its client through
    `ctx.budget.wrap_chat_client`, and enqueues via `queue_producers`."""

    name: str
    run: Callable[[StageContext], StageResult | None]
    uses_model: bool = True


@dataclass(frozen=True)
class RunnerPorts:
    """Every effect the runner takes from its caller."""

    refresh_derived: Callable[[Path], object]
    """The incremental derived refresh; raises `ReindexRefused` subclasses."""
    commit_paths: Callable[[Path, Sequence[str], str], str | None]
    paths_dirty: Callable[[Path, Sequence[str]], bool]
    repo_root: Callable[[Path], Path | None]
    has_git_identity: Callable[[Path], bool]
    """The four git effects stay adapter-side: `application/` never imports
    `openkos.vcs` (the verb wires `vcs.git`'s functions here)."""
    advisor_stages: Sequence[AdvisorStage] = ()
    watch: WatchPorts | None = None
    """The watch job's ports; `None` keeps the watch off."""
    lint_counts: Callable[[config.WorkspaceLayout], Mapping[str, int]] = (
        default_lint_counts
    )
    now: Callable[[], datetime] = _local_now
    """Timezone-aware LOCAL time (the budget's day boundary)."""
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[float, float], float] = random.uniform
    wait_cap_seconds: float = DEFAULT_WAIT_CAP_SECONDS
    announce: Callable[[str], None] = lambda message: None
    """Tells a person watching one line of what the job is doing (the verb wires
    it to a TTY-gated stderr line; the default says nothing)."""


@dataclass(frozen=True)
class JobResult:
    kind: str
    outcome: str
    detail_code: str | None = None
    chat_calls: int = 0
    units_done: int = 0
    units_deferred: int = 0
    lint_counts: Mapping[str, int] | None = None
    recorded: bool = True
    """Whether the outcome reached `jobs.db` (it still ended if it did not)."""


# -- the job frame -------------------------------------------------------------------


class _Halted(Exception):
    """Raised before a burst or between retries when the stop flag or the
    deadline says no new work may start."""

    def __init__(self, halt: Halt) -> None:
        super().__init__(halt)
        self.halt = halt


@dataclass
class _Tally:
    done: int = 0
    total: int = 0
    bound_deferred: int = 0
    busy_deferred: int = 0
    budget_limit: str | None = None
    halt: Halt | None = None
    refusal: str | None = None
    failure: str | None = None
    busy_refresh: bool = False
    lint_counts: Mapping[str, int] | None = None

    @property
    def deferred(self) -> int:
        return max(0, self.total - self.done) + self.bound_deferred


def _commit_section(
    root: Path, ports: RunnerPorts, stop: StopToken, deadline: Deadline
) -> CommitSection:
    @contextlib.contextmanager
    def section() -> Iterator[None]:
        # Checked immediately BEFORE the lock and the burst, never inside it.
        halt = check_halt(stop, deadline)
        if halt is not None:
            raise _Halted(halt)
        wait = min(ports.wait_cap_seconds, deadline.remaining())
        with acquire_with_backoff(
            root,
            wait_seconds=wait,
            clock=ports.monotonic,
            sleep=ports.sleep,
            jitter=ports.jitter,
        ):
            yield

    return section


def _retry_derived_contention(
    action: Callable[[], object],
    *,
    ports: RunnerPorts,
    stop: StopToken,
    deadline: Deadline,
) -> None:
    """Run `action`, retrying `LockContention` with jittered exponential
    backoff bounded by the lesser of the cap and the remaining deadline."""
    end = ports.monotonic() + min(ports.wait_cap_seconds, deadline.remaining())
    delay = INITIAL_BACKOFF_SECONDS
    while True:
        try:
            action()
            return
        except LockContention:
            left = end - ports.monotonic()
            halt = check_halt(stop, deadline)
            if halt is not None:
                raise _Halted(halt) from None
            if left <= 0:
                raise
            ports.sleep(min(ports.jitter(delay / 2, delay), left))
            delay = min(delay * 2, MAX_BACKOFF_SECONDS)


def _open_job_record(
    layout: config.WorkspaceLayout,
) -> sqlite3.Connection | None:
    try:
        return jobs.open_jobs(layout.jobs_db_path)
    except jobs.JobsStoreUnreadableError:
        log.error("job record unreadable; no job started (delete it to reset)")
        return None


def _finish(
    conn: sqlite3.Connection,
    job_id: int,
    ports: RunnerPorts,
    *,
    outcome: str,
    detail_code: str | None,
    chat_calls: int,
    units_done: int,
    units_deferred: int,
) -> bool:
    try:
        jobs.finish_job(
            conn,
            job_id,
            outcome=outcome,
            ended_at=_utc_stamp(ports.now()),
            chat_calls=chat_calls,
            units_done=units_done,
            units_deferred=units_deferred,
            detail_code=detail_code,
        )
    except sqlite3.Error as exc:
        log.error(
            "job %s outcome not recorded (%s)", job_id, _snake(type(exc).__name__)
        )
        return False
    return True


def _unreadable_result(kind: str) -> JobResult:
    return JobResult(
        kind=kind,
        outcome="refused",
        detail_code="jobs_store_unreadable",
        recorded=False,
    )


# -- maintenance ---------------------------------------------------------------------


def _units(tally: _Tally, ports: RunnerPorts) -> None:
    tally.total = 2 + len(ports.advisor_stages)


def _run_maintenance_units(
    root: Path,
    layout: config.WorkspaceLayout,
    *,
    ports: RunnerPorts,
    stop: StopToken,
    deadline: Deadline,
    run_budget: budget_module.BudgetedRun,
    tally: _Tally,
) -> None:
    section = _commit_section(root, ports, stop, deadline)
    queue_conn: list[sqlite3.Connection] = []

    def queue() -> sqlite3.Connection:
        if not queue_conn:
            conn = derived.open_derived_connection(layout.findings_db_path)
            pq.ensure_schema(conn)
            queue_conn.append(conn)
        return queue_conn[0]

    ctx = StageContext(
        root=root,
        layout=layout,
        budget=run_budget,
        policy=UnattendedPolicy(calls_remaining=lambda: run_budget.remaining),
        commit_section=section,
        queue=queue,
        notify=ports.announce,
    )

    def gate() -> None:
        halt = check_halt(stop, deadline)
        if halt is not None:
            raise _Halted(halt)

    try:
        gate()
        ports.announce("maintenance: refreshing the index...")
        try:
            _retry_derived_contention(
                lambda: ports.refresh_derived(root),
                ports=ports,
                stop=stop,
                deadline=deadline,
            )
        except LockContention:
            tally.busy_refresh = True
            return
        except ReindexRefused as exc:
            tally.refusal = _snake(type(exc).__name__)
            return
        tally.done += 1

        gate()
        try:
            tally.lint_counts = dict(ports.lint_counts(layout))
        except Exception as exc:  # noqa: BLE001 -- counts are informational
            log.warning("lint counts unavailable (%s)", _snake(type(exc).__name__))
        tally.done += 1

        stage_count = len(ports.advisor_stages)
        for position, stage in enumerate(ports.advisor_stages, start=1):
            gate()
            if stage.uses_model and not run_budget.admit(1).admitted:
                tally.budget_limit = (
                    run_budget.admit(1).limit or budget_module.PASS_LIMIT_KEY
                )
                continue
            ports.announce(f"maintenance: {stage.name} ({position}/{stage_count})...")
            try:
                result = stage.run(ctx)
            except lock.WorkspaceBusyError:
                tally.busy_deferred += 1
                continue
            except _Halted:
                raise
            except Exception as exc:  # noqa: BLE001 -- recorded by class only
                tally.failure = _snake(type(exc).__name__)
                log.error("stage %s failed (%s)", stage.name, tally.failure)
                return
            tally.done += 1
            if result is not None and result.deferred_by_bound > 0:
                tally.bound_deferred += result.deferred_by_bound
                tally.budget_limit = (
                    run_budget.admit(1).limit or budget_module.PASS_LIMIT_KEY
                )
    except _Halted as halted:
        tally.halt = halted.halt
    finally:
        for conn in queue_conn:
            conn.close()


def _maintenance_outcome(tally: _Tally) -> tuple[str, str | None]:
    if tally.halt is not None:
        return tally.halt, None
    if tally.refusal is not None:
        return "refused", tally.refusal
    if tally.failure is not None:
        return "failed", tally.failure
    if tally.busy_refresh and tally.done == 0:
        return "busy", None
    if tally.budget_limit is not None:
        return "budget_exhausted", tally.budget_limit
    return "completed", None


def run_maintenance_job(
    root: Path,
    *,
    unattended: config.UnattendedConfig,
    stop: StopToken,
    ports: RunnerPorts,
) -> JobResult:
    """One maintenance pass: refresh, lint counts, advisor stages. Computes and
    enqueues only; records exactly one outcome."""
    layout = config.WorkspaceLayout(root)
    conn = _open_job_record(layout)
    if conn is None:
        return _unreadable_result("maintenance")
    try:
        try:
            run_budget = budget_module.start_budgeted_run(
                layout, unattended, ports.now()
            )
        except budget_module.BudgetRefused:
            job_id = jobs.start_job(conn, "maintenance", _utc_stamp(ports.now()))
            recorded = _finish(
                conn,
                job_id,
                ports,
                outcome="refused",
                detail_code="spend_record_unreadable",
                chat_calls=0,
                units_done=0,
                units_deferred=0,
            )
            return JobResult(
                "maintenance",
                "refused",
                "spend_record_unreadable",
                recorded=recorded,
            )
        deadline = Deadline(unattended.job_deadline_seconds, clock=ports.monotonic)
        job_id = jobs.start_job(conn, "maintenance", _utc_stamp(ports.now()))
        log.info("maintenance job %s started", job_id)
        tally = _Tally()
        _units(tally, ports)
        _run_maintenance_units(
            root,
            layout,
            ports=ports,
            stop=stop,
            deadline=deadline,
            run_budget=run_budget,
            tally=tally,
        )
        outcome, detail = _maintenance_outcome(tally)
        recorded = _finish(
            conn,
            job_id,
            ports,
            outcome=outcome,
            detail_code=detail,
            chat_calls=run_budget.spent,
            units_done=tally.done,
            units_deferred=tally.deferred,
        )
        log.info(
            "maintenance job %s ended: %s (calls=%d done=%d deferred=%d)",
            job_id,
            outcome,
            run_budget.spent,
            tally.done,
            tally.deferred,
        )
        return JobResult(
            "maintenance",
            outcome,
            detail,
            chat_calls=run_budget.spent,
            units_done=tally.done,
            units_deferred=tally.deferred,
            lint_counts=tally.lint_counts,
            recorded=recorded,
        )
    finally:
        conn.close()


# -- commits ---------------------------------------------------------------------------


def attempt_commit(
    root: Path, paths: Sequence[str], message: str, *, ports: RunnerPorts
) -> CommitResult:
    """The runner's autocommit port: a typed result instead of a warning.

    Never raises. `Skipped` means retrying cannot help until the workspace is
    fixed; `CommitFailed` means git was tried and refused or timed out."""
    rel = tuple(paths)
    if ports.repo_root(root) is None:
        return Skipped("not_a_repository", rel)
    if not ports.has_git_identity(root):
        return Skipped("identity_unset", rel)
    try:
        return Committed(ports.commit_paths(root, rel, message))
    except Exception as exc:  # noqa: BLE001 -- recorded by class only
        return CommitFailed(_snake(type(exc).__name__), rel)


def record_commit_failure(
    conn: sqlite3.Connection, job_id: int, result: Skipped | CommitFailed
) -> None:
    """Remember the paths a job wrote but could not commit, for the next job's
    first step."""
    jobs.record_uncommitted_paths(conn, job_id, result.paths)


def _recorded_paths(layout: config.WorkspaceLayout) -> tuple[str, ...]:
    try:
        conn = jobs.open_jobs_read_only(layout.jobs_db_path)
    except jobs.JobsStoreUnreadableError:
        return ()
    if conn is None:
        return ()
    try:
        return jobs.uncommitted_paths(conn)
    finally:
        conn.close()


def run_commit_retry_job(
    root: Path,
    *,
    unattended: config.UnattendedConfig,
    stop: StopToken,
    ports: RunnerPorts,
) -> JobResult | None:
    """Retry the commit of recorded paths that are still dirty. `None` when
    nothing is recorded (no job is made for nothing)."""
    layout = config.WorkspaceLayout(root)
    if not _recorded_paths(layout):
        return None
    conn = _open_job_record(layout)
    if conn is None:
        return _unreadable_result("commit-retry")
    try:
        recorded_paths = jobs.uncommitted_paths(conn)
        deadline = Deadline(unattended.job_deadline_seconds, clock=ports.monotonic)
        job_id = jobs.start_job(conn, "commit-retry", _utc_stamp(ports.now()))
        outcome, detail, done, deferred = _retry_commit(
            root, conn, recorded_paths, ports, stop, deadline
        )
        recorded = _finish(
            conn,
            job_id,
            ports,
            outcome=outcome,
            detail_code=detail,
            chat_calls=0,
            units_done=done,
            units_deferred=deferred,
        )
        log.info("commit-retry job %s ended: %s", job_id, outcome)
        return JobResult(
            "commit-retry",
            outcome,
            detail,
            units_done=done,
            units_deferred=deferred,
            recorded=recorded,
        )
    finally:
        conn.close()


def _retry_commit(
    root: Path,
    conn: sqlite3.Connection,
    recorded_paths: Sequence[str],
    ports: RunnerPorts,
    stop: StopToken,
    deadline: Deadline,
) -> tuple[str, str | None, int, int]:
    try:
        dirty = tuple(p for p in recorded_paths if ports.paths_dirty(root, [p]))
    except Exception as exc:  # noqa: BLE001 -- recorded by class only
        return "commit_failed", _snake(type(exc).__name__), 0, 1
    if not dirty:
        jobs.clear_uncommitted_paths(conn)
        return "completed", "already_committed", 1, 0
    section = _commit_section(root, ports, stop, deadline)
    try:
        with section():
            result = attempt_commit(root, dirty, COMMIT_RETRY_MESSAGE, ports=ports)
    except _Halted as halted:
        return halted.halt, None, 0, 1
    except lock.WorkspaceBusyError:
        return "busy", None, 0, 1
    if isinstance(result, Committed):
        jobs.clear_uncommitted_paths(conn)
        return "completed", None, 1, 0
    return "commit_failed", result.reason, 0, 1


# -- due jobs ----------------------------------------------------------------------------


def maintenance_due(
    root: Path, unattended: config.UnattendedConfig, now: datetime
) -> bool:
    """Whether `maintenance_interval_seconds` has elapsed since the last
    maintenance job BEGAN (or none ever did). Reads the record read-only."""
    layout = config.WorkspaceLayout(root)
    try:
        conn = jobs.open_jobs_read_only(layout.jobs_db_path)
    except jobs.JobsStoreUnreadableError:
        return True  # the job itself refuses and says why
    if conn is None:
        return True
    try:
        last = jobs.last_job(conn, "maintenance")
    finally:
        conn.close()
    if last is None:
        return True
    began = datetime.strptime(last.started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (now - began).total_seconds() >= unattended.maintenance_interval_seconds


def run_due_jobs(
    root: Path,
    *,
    unattended: config.UnattendedConfig,
    stop: StopToken,
    ports: RunnerPorts,
    maintenance_due: bool,
) -> tuple[JobResult, ...]:
    """Run every due job once, in `JOB_ORDER`: the commit-retry first (only
    when something is recorded), then the watch (only when the inbox holds a
    settled file), then maintenance when due. No new job starts
    once the stop flag is set."""
    results: list[JobResult] = []
    if not stop.is_set():
        retry = run_commit_retry_job(
            root, unattended=unattended, stop=stop, ports=ports
        )
        if retry is not None:
            results.append(retry)
    if not stop.is_set():
        from openkos.application import watch as watch_job  # circular at import time

        watched = watch_job.run_watch_job(
            root, unattended=unattended, stop=stop, ports=ports
        )
        if watched is not None:
            results.append(watched)
    if maintenance_due and not stop.is_set():
        results.append(
            run_maintenance_job(root, unattended=unattended, stop=stop, ports=ports)
        )
    return tuple(results)


__all__ = [
    "JOB_ORDER",
    "AdvisorStage",
    "CommitFailed",
    "CommitResult",
    "Committed",
    "JobResult",
    "RunnerPorts",
    "Skipped",
    "StageContext",
    "StageResult",
    "attempt_commit",
    "default_lint_counts",
    "maintenance_due",
    "record_commit_failure",
    "run_commit_retry_job",
    "run_due_jobs",
    "run_maintenance_job",
]
