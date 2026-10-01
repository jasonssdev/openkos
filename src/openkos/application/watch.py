"""The watch job: imports settled files from the configured inbox (MVP 4,
folder-watch, ADR-0038).

The inbox is EXTERNAL to the workspace and read-only to the engine: this
module lists, stats and reads inbox files and never creates, renames, moves,
touches or deletes one. What it learns about each file lives in `jobs.db`
(`state/jobs.py` watch observations), a disposable cache.

**Settling.** An observation keeps `(size, mtime_ns, first_stable_at)`. A new
or changed stat restarts the clock; a file is a candidate only once the same
stat has been observed for `quiet_seconds` (the injected `RunnerPorts.now`
clock, the injected `WatchPorts.stat`). Only candidates are hashed, and a file
whose stat is unchanged since it was handled is neither re-read nor re-hashed.

**Import.** A candidate is admitted under `max_sources_per_pass` and the call
budget (by estimate, whole), then goes through `ingest_source` with the
runner's policy. Its commit section re-hashes the file under the lock and
refuses to proceed when the bytes differ from the digest that was admitted:
nothing is written and the file is deferred, so a save that lands after the
quiet window is never imported half-seen.

Refusals (`RawImmutabilityRefused`, over-budget sources) are RECORDED as
observation outcomes here; turning them into `watch_refusal` queue rows is a
separate unit.

Everything a job cannot import for budget, stop, deadline, lock contention or
a moved file stays a candidate for the next job.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import logging
import os
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from openkos import config, lock
from openkos.application import budget as budget_module
from openkos.application import ingest_service as svc
from openkos.application import runner
from openkos.application.lock_wait import CommitSection
from openkos.application.runtime import Deadline, Halt, StopToken, check_halt
from openkos.extraction.concept import estimate_extraction_calls
from openkos.state import jobs

if TYPE_CHECKING:
    from openkos.application.runner import RunnerPorts

log = logging.getLogger(__name__)

SOURCES_LIMIT_KEY: Final = "max_sources_per_pass"

IMPORTED: Final = "imported"
REFUSED: Final = "refused"
EXCEEDS_BUDGET: Final = "exceeds_budget"
FAILED: Final = "failed"
DEFERRED: Final = "deferred"
CHANGED: Final = "changed"
"""An observation outcome: `changed` (or `changed:<prior outcome>`) marks a
stat that moved since the file was last handled and has not settled yet."""

_TERMINAL: Final = frozenset({IMPORTED, REFUSED, EXCEEDS_BUDGET, FAILED})
"""Outcomes final for the bytes observed: the file is left alone until its
stat changes."""

_STAMP: Final = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class FileStat:
    size: int
    mtime_ns: int


def _list_files(inbox: Path) -> Sequence[Path]:
    """Regular files under `inbox`, recursively, skipping dot-entries,
    symlinks and anything that is not a regular file; sorted by path."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(inbox, followlinks=False):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".") and not (Path(dirpath) / d).is_symlink()
        )
        for name in filenames:
            path = Path(dirpath) / name
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            found.append(path)
    return sorted(found)


def _stat(path: Path) -> FileStat:
    st = path.stat()
    return FileStat(size=st.st_size, mtime_ns=st.st_mtime_ns)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _estimate_calls(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    return estimate_extraction_calls(text, source_title=path.stem).calls


@dataclass(frozen=True)
class WatchPorts:
    """What the watch job takes from its caller (the daemon verb wires it)."""

    ingest_ports: Callable[[CommitSection, budget_module.BudgetedRun], svc.IngestPorts]
    """Builds the `IngestPorts` for one file around the commit section the
    watch hands it, with the budget so the chat client is counted."""
    list_files: Callable[[Path], Sequence[Path]] = _list_files
    stat: Callable[[Path], FileStat] = _stat
    hash_file: Callable[[Path], str] = _hash_file
    estimate_calls: Callable[[Path], int] = _estimate_calls


class _NotSettled(Exception):
    """The file's bytes changed between admission and the commit phase."""


@dataclass
class _Tally:
    done: int = 0
    deferred: int = 0
    budget_limit: str | None = None
    halt: Halt | None = None
    failure: str | None = None
    busy: bool = False


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_STAMP)


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC)


@dataclass(frozen=True)
class _Candidate:
    path: Path
    obs: jobs.WatchObservation
    digest: str


def _poll(
    conn: sqlite3.Connection,
    inbox: Path,
    *,
    quiet_seconds: int,
    ports: RunnerPorts,
    watch: WatchPorts,
) -> list[_Candidate]:
    """Update the observations from one listing; return the settled candidates
    in path order. Stats every file, reads and hashes none."""
    now = ports.now()
    known = {o.path: o for o in jobs.observations(conn)}
    seen: set[str] = set()
    candidates: list[_Candidate] = []
    for path in watch.list_files(inbox):
        key = path.relative_to(inbox).as_posix()
        try:
            st = watch.stat(path)
        except OSError:
            continue
        seen.add(key)
        obs = known.get(key)
        if obs is None or (obs.size, obs.mtime_ns) != (st.size, st.mtime_ns):
            prior = obs.outcome if obs is not None else None
            if prior is not None and prior.startswith(CHANGED):
                marker = prior
            else:
                marker = CHANGED if prior is None else f"{CHANGED}:{prior}"
            jobs.upsert_observation(
                conn,
                jobs.WatchObservation(
                    path=key,
                    size=st.size,
                    mtime_ns=st.mtime_ns,
                    first_stable_at=_stamp(now),
                    digest=obs.digest if obs is not None else None,
                    outcome=marker,
                ),
            )
            continue
        if obs.outcome in _TERMINAL:
            continue
        if (now - _parse(obs.first_stable_at)).total_seconds() < quiet_seconds:
            continue
        try:
            digest, settled_prior = _resolve_digest(path, obs, watch)
        except OSError:
            continue  # vanished or unreadable: the next poll decides
        if settled_prior is not None:
            jobs.upsert_observation(
                conn,
                dataclasses.replace(obs, digest=digest, outcome=settled_prior),
            )
            continue
        candidates.append(_Candidate(path, obs, digest))
    gone = [k for k in known if k not in seen]
    if gone:
        jobs.forget_observations(conn, gone)
    return candidates


def _resolve_digest(
    path: Path, obs: jobs.WatchObservation, watch: WatchPorts
) -> tuple[str, str | None]:
    """The file's digest, and the prior outcome when the bytes still match what
    that outcome was recorded for (so nothing starts)."""
    outcome = obs.outcome or ""
    if obs.digest is not None and not outcome.startswith(CHANGED):
        return obs.digest, None  # deferred earlier: reuse its admitted digest
    digest = watch.hash_file(path)
    if outcome.startswith(f"{CHANGED}:") and digest == obs.digest:
        prior = outcome.split(":", 1)[1]
        if prior in _TERMINAL:
            return digest, prior
    return digest, None


def _record(
    conn: sqlite3.Connection,
    cand: _Candidate,
    *,
    digest: str | None,
    outcome: str,
) -> None:
    jobs.upsert_observation(
        conn, dataclasses.replace(cand.obs, digest=digest, outcome=outcome)
    )


def _import_one(
    root: Path,
    cand: _Candidate,
    digest: str,
    *,
    base_section: CommitSection,
    run_budget: budget_module.BudgetedRun,
    watch: WatchPorts,
) -> None:
    @contextlib.contextmanager
    def guarded() -> Iterator[None]:
        with base_section():
            # Re-hash under the lock, immediately before anything is written.
            if watch.hash_file(cand.path) != digest:
                raise _NotSettled
            yield

    svc.ingest_source(
        root,
        cand.path,
        svc.IngestPolicy(skip_confirmation=True),
        ports=watch.ingest_ports(guarded, run_budget),
        confirm=None,
    )


def _run_candidates(
    root: Path,
    conn: sqlite3.Connection,
    candidates: Sequence[_Candidate],
    *,
    unattended: config.UnattendedConfig,
    stop: StopToken,
    deadline: Deadline,
    run_budget: budget_module.BudgetedRun,
    ports: RunnerPorts,
    watch: WatchPorts,
    tally: _Tally,
) -> None:
    base_section = runner._commit_section(root, ports, stop, deadline)
    admitted = 0
    for index, cand in enumerate(candidates):
        rest = len(candidates) - index
        halt = check_halt(stop, deadline)
        if halt is not None:
            tally.halt, tally.deferred = halt, tally.deferred + rest
            return
        if admitted >= unattended.max_sources_per_pass:
            tally.budget_limit = SOURCES_LIMIT_KEY
            tally.deferred += rest
            return
        digest = cand.digest
        try:
            estimate = watch.estimate_calls(cand.path)
        except OSError:
            continue  # vanished or unreadable: the next poll decides
        verdict = run_budget.admit(estimate)
        if not verdict.admitted:
            if verdict.never_fits:
                _record(conn, cand, digest=digest, outcome=EXCEEDS_BUDGET)
                tally.done += 1
                continue
            tally.budget_limit = verdict.limit or budget_module.PASS_LIMIT_KEY
            tally.deferred += rest
            return
        admitted += 1
        try:
            _import_one(
                root,
                cand,
                digest,
                base_section=base_section,
                run_budget=run_budget,
                watch=watch,
            )
        except runner._Halted as halted:
            _record(conn, cand, digest=digest, outcome=DEFERRED)
            tally.halt, tally.deferred = halted.halt, tally.deferred + rest
            return
        except _NotSettled:
            _record(conn, cand, digest=None, outcome=DEFERRED)
            tally.deferred += 1
        except lock.WorkspaceBusyError:
            _record(conn, cand, digest=digest, outcome=DEFERRED)
            tally.deferred += 1
            tally.busy = True
        except svc.DriftDetected:
            _record(conn, cand, digest=digest, outcome=DEFERRED)
            tally.deferred += 1
        except svc.RawImmutabilityRefused:
            _record(conn, cand, digest=digest, outcome=REFUSED)
            tally.done += 1
        except Exception as exc:  # noqa: BLE001 -- recorded by class only
            tally.failure = runner._snake(type(exc).__name__)
            log.error("watch import failed (%s)", tally.failure)
            _record(conn, cand, digest=digest, outcome=FAILED)
        else:
            _record(conn, cand, digest=digest, outcome=IMPORTED)
            tally.done += 1


def _outcome(tally: _Tally) -> tuple[str, str | None]:
    if tally.halt is not None:
        return tally.halt, None
    if tally.failure is not None:
        return "failed", tally.failure
    if tally.busy and tally.done == 0:
        return "busy", None
    if tally.budget_limit is not None:
        return "budget_exhausted", tally.budget_limit
    return "completed", None


def run_watch_job(
    root: Path,
    *,
    unattended: config.UnattendedConfig,
    stop: StopToken,
    ports: RunnerPorts,
) -> runner.JobResult | None:
    """Poll the inbox once and import its settled files. `None` when the watch
    is off or nothing is settled and unhandled (no job is made for nothing)."""
    watch = ports.watch
    inbox = unattended.inbox
    if watch is None or inbox is None:
        return None
    layout = config.WorkspaceLayout(root)
    conn = runner._open_job_record(layout)
    if conn is None:
        return runner._unreadable_result("watch")
    try:
        candidates = _poll(
            conn,
            inbox,
            quiet_seconds=unattended.quiet_seconds,
            ports=ports,
            watch=watch,
        )
        if not candidates:
            return None
        return _run_job(root, layout, conn, candidates, unattended, stop, ports, watch)
    finally:
        conn.close()


def _run_job(
    root: Path,
    layout: config.WorkspaceLayout,
    conn: sqlite3.Connection,
    candidates: Sequence[_Candidate],
    unattended: config.UnattendedConfig,
    stop: StopToken,
    ports: RunnerPorts,
    watch: WatchPorts,
) -> runner.JobResult:
    try:
        run_budget = budget_module.start_budgeted_run(layout, unattended, ports.now())
    except budget_module.BudgetRefused:
        job_id = jobs.start_job(conn, "watch", runner._utc_stamp(ports.now()))
        recorded = runner._finish(
            conn,
            job_id,
            ports,
            outcome="refused",
            detail_code="spend_record_unreadable",
            chat_calls=0,
            units_done=0,
            units_deferred=0,
        )
        return runner.JobResult(
            "watch", "refused", "spend_record_unreadable", recorded=recorded
        )
    deadline = Deadline(unattended.job_deadline_seconds, clock=ports.monotonic)
    job_id = jobs.start_job(conn, "watch", runner._utc_stamp(ports.now()))
    log.info("watch job %s started (%d candidates)", job_id, len(candidates))
    tally = _Tally()
    _run_candidates(
        root,
        conn,
        candidates,
        unattended=unattended,
        stop=stop,
        deadline=deadline,
        run_budget=run_budget,
        ports=ports,
        watch=watch,
        tally=tally,
    )
    outcome, detail = _outcome(tally)
    recorded = runner._finish(
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
        "watch job %s ended: %s (calls=%d done=%d deferred=%d)",
        job_id,
        outcome,
        run_budget.spent,
        tally.done,
        tally.deferred,
    )
    return runner.JobResult(
        "watch",
        outcome,
        detail,
        chat_calls=run_budget.spent,
        units_done=tally.done,
        units_deferred=tally.deferred,
        recorded=recorded,
    )


__all__ = ["FileStat", "WatchPorts", "run_watch_job"]
