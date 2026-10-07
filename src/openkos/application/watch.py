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
runner's policy. A file the ingest finds unchanged (nothing written, no model
call) gives its source slot back, so a backlog of already-imported files never
starves a new one (#1265). Its commit section re-hashes the file under the lock and
refuses to proceed when the bytes differ from the digest that was admitted:
nothing is written and the file is deferred, so a save that lands after the
quiet window is never imported half-seen.

**Versions (ADR-0041).** The import policy sets `version_changed`, so a file whose
bytes changed after import becomes a new raw copy and Source rather than a
refusal; the supersession the ingest proposes is enqueued as a `relation_type`
row (`queue_producers.enqueue_source_supersession`), never written, because
writing it deprecates the earlier Source.

Refusals (an over-budget source, or a `RawImmutabilityRefused` the service still
raises, such as a row an older engine left open) are recorded as
observation outcomes AND upserted as one `watch_refusal` queue row per source
(`queue_producers`), so a person sees them in `openkos pending` once rather than
as an error on every save. A row is retired as `stale` when the file's bytes
become importable again (a successful import of that path) or the file has been
gone from the inbox for a whole quiet window (the window is what lets a rename
land its new name first, so the row can move to `applied` instead); the ingest
service moves it to `applied` when a raw copy with the refused bytes lands.

**Derived indexes.** After its last import a job runs the runner's incremental
refresh (`RunnerPorts.refresh_derived`) once, so what it imported is searchable
without waiting for the maintenance job. A refresh that cannot run is advised,
never an import failure.

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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from openkos import config, fsio, lock
from openkos.application import budget as budget_module
from openkos.application import digest, runner
from openkos.application import ingest as application_ingest
from openkos.application import ingest_service as svc
from openkos.application import pending as application_pending
from openkos.application import queue_producers as producers
from openkos.application.lock_wait import CommitSection
from openkos.application.reindex_service import ReindexRefused
from openkos.application.runtime import Deadline, Halt, StopToken, check_halt
from openkos.bundle import source_titles
from openkos.extraction.concept import estimate_extraction_calls
from openkos.state import derived, jobs
from openkos.state import pending_queue as pq

if TYPE_CHECKING:
    from openkos.application.runner import RunnerPorts

log = logging.getLogger(__name__)

SOURCES_LIMIT_KEY: Final = "max_sources_per_pass"

IMPORTED: Final = "imported"
REFUSED: Final = "refused"
EXCEEDS_BUDGET: Final = "exceeds_budget"
FAILED: Final = "failed"
DEFERRED: Final = "deferred"
GONE: Final = "gone:"
"""An observation outcome prefix: a refused (or over-budget) file left the
inbox; the observation is kept for one quiet window before its row is retired."""
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
    """Text-source files under `inbox`, recursively, skipping dot-entries,
    symlinks, anything that is not a regular file, and (the sweep allowlist
    `ingest <dir>` applies, `fsio.is_text_source`) every other extension;
    sorted by path. The skipped count is logged, never silent."""
    found: list[Path] = []
    skipped = 0
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
            if not fsio.is_text_source(path):
                skipped += 1
                continue
            found.append(path)
    if skipped:
        log.info("watch: %d non-text file(s) in the inbox skipped", skipped)
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
    decoded = fsio.read_source_text(path)
    # Not text -> no model call; the ingest degrades to a Source-only run.
    text = decoded.text if decoded is not None else ""
    return estimate_extraction_calls(text, source_title=path.stem).calls


def _log_notice(message: str) -> None:
    log.warning("%s", message)


_POLICY = svc.IngestPolicy(skip_confirmation=True, version_changed=True)
"""How the watch ingests a file, and how it asks whether that ingest would be a
no-op: one policy, so the probe and the run cannot disagree."""

_PREFIX = "openkos daemon: watch: "
_INGEST_PREFIX = "openkos ingest: "


def _daemon_line(name: str, message: str) -> str:
    """An ingest advisory line under the daemon's own prefix, naming the file it
    is about (the unattended path never prints `openkos ingest:`, #1265)."""
    body = message.removeprefix(_INGEST_PREFIX)
    return f"{_PREFIX}'{name}': {body}"


def _outcome_line(name: str, outcome: svc.IngestOutcome) -> str:
    """The one line an import's result earns: the file, and what happened."""
    if isinstance(outcome, svc.IngestUnchanged):
        return f"{_PREFIX}'{name}' unchanged -- already imported; nothing to do."
    new_version = any(s.reason == "new_version" for s in outcome.supersessions)
    head = f"{_PREFIX}'{name}' imported" + (" as a new version" if new_version else "")
    parts = []
    if outcome.derived_count:
        noun = "object" if outcome.derived_count == 1 else "objects"
        parts.append(f"{outcome.derived_count} new {noun}")
    if outcome.attached:
        parts.append(f"{len(outcome.attached)} revised")
    return f"{head} -- {', '.join(parts) if parts else 'Source only'}."


class _WatchObserver(svc.IngestObserver):
    """Surfaces what an unattended import would otherwise swallow (#1224): a
    legacy-encoding read, a source that ended with no extractable text, the
    advisories an attended ingest prints from its extraction report, and a
    sign of life while the model works (#1331)."""

    def __init__(self, name: str, watch: WatchPorts) -> None:
        self._name = name
        self._watch = watch
        self._notify = watch.notify

    def notice(self, message: str) -> None:
        if message == svc.UNCHANGED_NOTICE:
            return  # `_outcome_line` words it, without a hint that is not the daemon's
        self._notify(_daemon_line(self._name, message))

    def extraction_starting(self) -> None:
        self._notify(
            f"{_PREFIX}'{self._name}': extracting derived objects "
            "(waiting on the LLM)..."
        )

    @contextlib.contextmanager
    def extraction_progress(self) -> Iterator[svc.PhaseHook | None]:
        yield self._watch.phase_hook(self._name)

    def staged(self, staged: application_ingest.StagedDerivedObjects) -> None:
        if staged.report is None and staged.skip_reason == "no-extractable-text":
            self._notify(
                f"openkos daemon: watch: '{self._name}' has no extractable text "
                "(binary, or not decodable as text); its Source was kept "
                "without concepts."
            )
        for line in self._watch.staged_notices(staged):
            self._notify(f"{_PREFIX}'{self._name}': {line}")


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
    notify: Callable[[str], None] = _log_notice
    """Where an import's advisory lines go (the daemon verb wires stderr)."""
    staged_notices: Callable[
        [application_ingest.StagedDerivedObjects], Sequence[str]
    ] = lambda staged: ()
    """The advisory lines (no prefix) an attended ingest prints from one file's
    extraction report; the daemon verb wires the CLI's wording so the two
    surfaces cannot drift (#1331)."""
    outcome_notices: Callable[[svc.IngestOutcome], Sequence[str]] = lambda outcome: ()
    """The run-level summary lines (no prefix) an attended ingest prints after
    a file lands, e.g. the torn-classification count (#1331)."""
    phase_hook: Callable[[str], svc.PhaseHook | None] = lambda name: None
    """Builds the extraction phase-label hook for the named file, or `None`
    for none (the daemon verb gates it on a TTY, like its maintenance
    progress)."""


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
    imported: int = 0
    ledger: digest.ActionLedger = field(default_factory=digest.ActionLedger)
    """The commits the job's imports made (#1268)."""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_STAMP)


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC)


@dataclass(frozen=True)
class _Candidate:
    path: Path
    obs: jobs.WatchObservation
    digest: str


def _may_have_row(outcome: str | None) -> bool:
    """Whether a file with this outcome may own an open `watch_refusal` row."""
    return (outcome or "").split(":")[-1] in (REFUSED, EXCEEDS_BUDGET)


def _poll(
    conn: sqlite3.Connection,
    inbox: Path,
    *,
    quiet_seconds: int,
    ports: RunnerPorts,
    watch: WatchPorts,
) -> tuple[list[_Candidate], list[str], int]:
    """Update the observations from one listing; return the settled candidates
    in path order, the inbox paths whose refusal row is due to be retired
    (gone for a whole quiet window), and how many files are still inside their
    quiet window. Stats every file, reads and hashes none."""
    now = ports.now()
    known = {o.path: o for o in jobs.observations(conn)}
    seen: set[str] = set()
    candidates: list[_Candidate] = []
    waiting = 0
    for path in watch.list_files(inbox):
        key = path.relative_to(inbox).as_posix()
        try:
            st = watch.stat(path)
        except OSError:
            continue
        seen.add(key)
        obs = known.get(key)
        if obs is not None and (obs.outcome or "").startswith(GONE):
            # It came back inside the window: the refusal stands as it was.
            obs = dataclasses.replace(obs, outcome=(obs.outcome or "")[len(GONE) :])
            jobs.upsert_observation(conn, obs)
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
            waiting += 1
            continue
        if obs.outcome in _TERMINAL:
            continue
        if (now - _parse(obs.first_stable_at)).total_seconds() < quiet_seconds:
            waiting += 1
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
    forget: list[str] = []
    expired: list[str] = []
    for key, obs in known.items():
        if key in seen:
            continue
        outcome = obs.outcome or ""
        if outcome.startswith(GONE):
            if (now - _parse(obs.first_stable_at)).total_seconds() >= quiet_seconds:
                expired.append(key)
        elif _may_have_row(outcome):
            base = outcome.split(":")[-1]
            jobs.upsert_observation(
                conn,
                dataclasses.replace(
                    obs, first_stable_at=_stamp(now), outcome=f"{GONE}{base}"
                ),
            )
        else:
            forget.append(key)
    if forget:
        jobs.forget_observations(conn, forget)
    return candidates, expired, waiting


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


class _Queue:
    """The pending-work queue (`findings.db`) for one watch job, opened lazily so
    a job that refuses nothing never creates the file."""

    def __init__(self, layout: config.WorkspaceLayout) -> None:
        self._layout = layout
        self._conn: sqlite3.Connection | None = None

    @property
    def bundle_dir(self) -> Path:
        return self._layout.bundle_dir

    def writer(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = derived.open_derived_connection(self._layout.findings_db_path)
            pq.ensure_schema(self._conn)
        return self._conn

    def existing(self) -> sqlite3.Connection | None:
        """The connection when a queue already exists; never creates one."""
        if self._conn is None and not self._layout.findings_db_path.is_file():
            return None
        return self.writer()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _source_id_for(path: Path) -> str:
    """The Source concept id an ingest of `path` would give it."""
    return f"sources/{source_titles.slugify(path.stem)}"


def _file_refusal(
    conn: sqlite3.Connection,
    queue: _Queue,
    cand: _Candidate,
    *,
    reason: str,
    source_id: str,
    section: CommitSection,
    tally: _Tally,
    rest: int,
) -> bool:
    """Upsert the one `watch_refusal` row for `cand`, then record the outcome.
    `False` when a stop or deadline halted the job (the file stays a
    candidate)."""
    outcome = REFUSED if reason == producers.REASON_SOURCE_CHANGED else EXCEEDS_BUDGET
    try:
        producers.enqueue_watch_refusal(
            queue.writer(),
            source_id=source_id,
            inbox_path=cand.obs.path,
            digest=cand.digest,
            reason=reason,
            bundle_dir=queue.bundle_dir,
            commit_section=section,
        )
    except runner._Halted as halted:
        _record(conn, cand, digest=cand.digest, outcome=DEFERRED)
        tally.halt, tally.deferred = halted.halt, tally.deferred + rest
        return False
    except lock.WorkspaceBusyError:
        _record(conn, cand, digest=cand.digest, outcome=DEFERRED)
        tally.deferred += 1
        tally.busy = True
        return True
    except (sqlite3.Error, OSError):
        tally.failure = "queue_unwritable"
        log.error("watch refusal not queued (%s)", tally.failure)
        _record(conn, cand, digest=cand.digest, outcome=DEFERRED)
        return True
    _record(conn, cand, digest=cand.digest, outcome=outcome)
    tally.done += 1
    return True


def _retire_rows(
    queue: _Queue, inbox_paths: Sequence[str], section: CommitSection
) -> bool:
    """Retire the open refusal rows for these inbox paths. `False` when it could
    not be done now (a halt, contention or an unreadable queue): the caller
    leaves its bookkeeping so the next job retries."""
    try:
        conn = queue.existing()
        if conn is None:
            return True
        for path in inbox_paths:
            producers.retire_watch_refusals(conn, path, commit_section=section)
    except (runner._Halted, lock.WorkspaceBusyError, sqlite3.Error, OSError):
        return False
    return True


def _queue_supersessions(
    queue: _Queue, outcome: svc.IngestOutcome, section: CommitSection
) -> None:
    """Enqueue each supersession the import proposes as a pending-work row
    (ADR-0041: unattended work never writes the relation, because writing it
    deprecates the earlier Source). The import has already landed, so a failure
    here is logged with the command that records it by hand, never raised."""
    for supersession in outcome.supersessions:
        try:
            producers.enqueue_source_supersession(
                queue.writer(),
                source_id=supersession.source_id,
                previous_id=supersession.previous_id,
                reason=supersession.reason,
                current_digest=application_pending.current_finding_digest(
                    queue.bundle_dir
                ),
                bundle_dir=queue.bundle_dir,
                commit_section=section,
            )
        except (runner._Halted, lock.WorkspaceBusyError, sqlite3.Error, OSError):
            log.warning(
                "supersession not queued; record it with: %s",
                svc.supersede_command(supersession),
            )


def _import_one(
    root: Path,
    cand: _Candidate,
    digest: str,
    *,
    base_section: CommitSection,
    run_budget: budget_module.BudgetedRun,
    watch: WatchPorts,
    ledger: digest.ActionLedger,
) -> svc.IngestOutcome:
    @contextlib.contextmanager
    def guarded() -> Iterator[None]:
        with base_section():
            # Re-hash under the lock, immediately before anything is written.
            if watch.hash_file(cand.path) != digest:
                raise _NotSettled
            yield

    ingest_ports = watch.ingest_ports(guarded, run_budget)
    return svc.ingest_source(
        root,
        cand.path,
        _POLICY,
        ports=dataclasses.replace(
            ingest_ports, autocommit=ledger.wrap(ingest_ports.autocommit)
        ),
        observer=_WatchObserver(cand.path.name, watch),
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
    queue: _Queue,
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
        # A file the ingest would find unchanged costs no model call and no
        # write, so it meets neither the call budget nor the source budget
        # (#1265). Decided by the ingest's own Phase A, not a second detector.
        unchanged = svc.is_unchanged(
            root, cand.path, _POLICY, ports=watch.ingest_ports(base_section, run_budget)
        )
        verdict = budget_module.Admission(admitted=True)
        if not unchanged:
            try:
                estimate = watch.estimate_calls(cand.path)
            except OSError:
                continue  # vanished or unreadable: the next poll decides
            verdict = run_budget.admit(estimate)
        if not verdict.admitted:
            if verdict.never_fits:
                if not _file_refusal(
                    conn,
                    queue,
                    cand,
                    reason=producers.REASON_EXCEEDS_BUDGET,
                    source_id=_source_id_for(cand.path),
                    section=base_section,
                    tally=tally,
                    rest=rest,
                ):
                    return
                continue
            tally.budget_limit = verdict.limit or budget_module.PASS_LIMIT_KEY
            tally.deferred += rest
            return
        if not unchanged:
            admitted += 1
        refusal: svc.RawImmutabilityRefused | None = None
        outcome: svc.IngestOutcome | None = None
        try:
            outcome = _import_one(
                root,
                cand,
                digest,
                base_section=base_section,
                run_budget=run_budget,
                watch=watch,
                ledger=tally.ledger,
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
        except svc.RawImmutabilityRefused as refused:
            refusal = refused
        except Exception as exc:  # noqa: BLE001 -- recorded by class only
            tally.failure = runner._snake(type(exc).__name__)
            log.error("watch import failed (%s)", tally.failure)
            _record(conn, cand, digest=digest, outcome=FAILED)
        else:
            # The bytes import cleanly again: a row that refused this path is moot.
            if not _retire_rows(queue, [cand.obs.path], base_section):
                log.warning("watch refusal row not retired; it stays open")
            _queue_supersessions(queue, outcome, base_section)
            _record(conn, cand, digest=digest, outcome=IMPORTED)
            watch.notify(_outcome_line(cand.path.name, outcome))
            for line in watch.outcome_notices(outcome):
                watch.notify(f"{_PREFIX}'{cand.path.name}': {line}")
            tally.done += 1
            tally.imported += 1
            if isinstance(outcome, svc.IngestUnchanged):
                # Nothing was written and no model was contacted (#773's
                # convergence short-circuit): it is not an import, so it must
                # not use up the pass's source budget (#1265).
                if not unchanged:  # the probe missed it (a race): give the slot back
                    admitted -= 1
                tally.imported -= 1
        if refusal is not None and not _file_refusal(
            conn,
            queue,
            cand,
            reason=producers.REASON_SOURCE_CHANGED,
            source_id=refusal.source_id or _source_id_for(cand.path),
            section=base_section,
            tally=tally,
            rest=rest,
        ):
            return


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
    queue = _Queue(layout)
    try:
        candidates, expired, waiting = _poll(
            conn,
            inbox,
            quiet_seconds=unattended.quiet_seconds,
            ports=ports,
            watch=watch,
        )
        if waiting:
            _say_waiting(ports, waiting, unattended.quiet_seconds)
        result = None
        if candidates:
            result = _run_job(
                root, layout, conn, candidates, unattended, stop, ports, watch, queue
            )
        if expired:
            _retire_gone(root, conn, queue, expired, unattended, stop, ports)
        return result
    finally:
        queue.close()
        conn.close()


def _say_waiting(ports: RunnerPorts, waiting: int, quiet_seconds: int) -> None:
    """Say that files were seen but have not settled. Settling stays
    observation-based (ADR-0038: the same stat for a whole window, seen by this
    engine), so a first run only starts the clock; without this line it ends
    silently and looks like a failure to import."""
    noun = "file" if waiting == 1 else "files"
    message = (
        f"{waiting} {noun} seen in the inbox; will import after {quiet_seconds}s quiet"
    )
    log.info("watch: %s", message)
    ports.announce(message)


def _retire_gone(
    root: Path,
    conn: sqlite3.Connection,
    queue: _Queue,
    expired: Sequence[str],
    unattended: config.UnattendedConfig,
    stop: StopToken,
    ports: RunnerPorts,
) -> None:
    """Retire the rows of files gone for a whole quiet window, AFTER the imports
    so a rename's new name has had its chance to land first and apply the row."""
    deadline = Deadline(unattended.job_deadline_seconds, clock=ports.monotonic)
    section = runner._commit_section(root, ports, stop, deadline)
    if _retire_rows(queue, expired, section):
        jobs.forget_observations(conn, list(expired))


def _refresh_derived(
    root: Path,
    stop: StopToken,
    deadline: Deadline,
    ports: RunnerPorts,
    watch: WatchPorts,
) -> None:
    """Bring the derived indexes up to date with what the job imported, once per
    job (the incremental refresh the maintenance job runs, so a file dropped in
    the inbox is searchable now rather than a maintenance interval later). The
    imports are already durable, so a refresh that cannot run is advised, never
    raised: the next maintenance pass catches the indexes up."""
    try:
        runner._retry_derived_contention(
            lambda: ports.refresh_derived(root),
            ports=ports,
            stop=stop,
            deadline=deadline,
        )
    except (ReindexRefused, runner._Halted) as exc:
        log.warning("watch: derived indexes not refreshed (%s)", type(exc).__name__)
        watch.notify(
            "openkos daemon: watch: the derived indexes were not refreshed after "
            "the import; the next maintenance pass or `openkos reindex` does it."
        )


def _run_job(
    root: Path,
    layout: config.WorkspaceLayout,
    conn: sqlite3.Connection,
    candidates: Sequence[_Candidate],
    unattended: config.UnattendedConfig,
    stop: StopToken,
    ports: RunnerPorts,
    watch: WatchPorts,
    queue: _Queue,
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
        queue=queue,
        tally=tally,
    )
    if tally.imported:
        _refresh_derived(root, stop, deadline, ports, watch)
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
        actions=tuple(tally.ledger.actions),
    )


BudgetedRun = budget_module.BudgetedRun
"""Re-exported so the CLI composition root can type the port it builds without
importing the budget module (only the runner may install the counting wrapper)."""

__all__ = ["BudgetedRun", "FileStat", "WatchPorts", "run_watch_job"]
