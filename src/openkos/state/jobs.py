"""`.openkos/jobs.db`: the unattended runner's job record.

DISPOSABLE operational state, never a source of truth. It holds three things
-- one row per job (kind, outcome, times, model calls, units done/deferred),
the workspace-relative paths a job wrote but could not commit, and the
watcher's per-file observations -- and nothing else. It never holds document
text, model output or rationale: only paths, counts, hashes and codes.

Deleting it loses job history and the current day's budget counters and
nothing more; no knowledge under `bundle/`, `raw/` or `bundle/.state/`, and
nothing in git, depends on it. So the two failure shapes are deliberately
asymmetric:

- **Absent** is the normal first state. `open_jobs` recreates it, owner-only,
  with no history and no error; `open_jobs_read_only` reports `None` (there
  is no record, which is not an error and not a history to invent).
- **Present but unreadable** is refused with `JobsStoreUnreadableError`, which
  names deleting the file as the remedy. The store is never deleted or
  repaired on the caller's behalf: guessing the day's spend from a corrupt
  file is worse than a reset the user chose.

Layered on `derived.open_derived_connection`, so the file is created `0600`
inside a `0700` `.openkos/` (#1135) and a failed first open leaves no
footprint. No verb opens this store yet.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from openkos.state import derived
from openkos.state.readonly import open_read_only

_CREATE_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('watch','maintenance','commit-retry')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT CHECK (outcome IN ('completed','budget_exhausted','timed_out',
        'stopped','busy','commit_failed','refused','failed')),
    detail_code TEXT,
    chat_calls INTEGER NOT NULL DEFAULT 0,
    units_done INTEGER NOT NULL DEFAULT 0,
    units_deferred INTEGER NOT NULL DEFAULT 0
)
"""
_CREATE_PATHS_SQL = """
CREATE TABLE IF NOT EXISTS job_uncommitted_paths (
    job_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (job_id, path)
)
"""
_CREATE_OBSERVATIONS_SQL = """
CREATE TABLE IF NOT EXISTS watch_observations (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    first_stable_at TEXT NOT NULL,
    digest TEXT,
    outcome TEXT
)
"""

_SELECT_JOBS = (
    "SELECT id, kind, started_at, ended_at, outcome, detail_code, "
    "chat_calls, units_done, units_deferred FROM jobs"
)


class JobsStoreUnreadableError(Exception):
    """`jobs.db` exists but cannot be read as a job record.

    The message names deleting the file as the remedy, because the record is
    disposable and a deliberate reset is the only honest recovery."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"the job record {path} exists but cannot be read ({reason}); "
            "it is disposable operational state, so delete it to start a "
            "fresh one (this loses only job history and today's counters)"
        )


@dataclass(frozen=True)
class JobRecord:
    id: int
    kind: str
    started_at: str
    ended_at: str | None
    outcome: str | None
    detail_code: str | None
    chat_calls: int
    units_done: int
    units_deferred: int


@dataclass(frozen=True)
class WatchObservation:
    path: str
    size: int
    mtime_ns: int
    first_stable_at: str
    digest: str | None
    outcome: str | None


def _is_unreadable(exc: sqlite3.Error) -> bool:
    """A database-level fault (not a database, malformed), as opposed to an
    `OperationalError` (locked, no such table), which is contention or a
    missing schema and says nothing about the file's integrity."""
    return isinstance(exc, sqlite3.DatabaseError) and not isinstance(
        exc, sqlite3.OperationalError
    )


def open_jobs(path: Path) -> sqlite3.Connection:
    """Open (creating if absent) the job record at `path`.

    Absent: recreated empty and owner-only, no history, no error. Present but
    unreadable: `JobsStoreUnreadableError`, the file left exactly as found."""
    try:
        conn = derived.open_derived_connection(path)
    except sqlite3.Error as exc:
        if _is_unreadable(exc):
            raise JobsStoreUnreadableError(path, str(exc)) from exc
        raise
    try:
        for ddl in (_CREATE_JOBS_SQL, _CREATE_PATHS_SQL, _CREATE_OBSERVATIONS_SQL):
            conn.execute(ddl)
        conn.commit()
    except BaseException as exc:
        conn.close()
        if isinstance(exc, sqlite3.Error) and _is_unreadable(exc):
            raise JobsStoreUnreadableError(path, str(exc)) from exc
        raise
    return conn


def open_jobs_read_only(path: Path) -> sqlite3.Connection | None:
    """Open the job record read-only for `status` / `next` / `pending`.

    Never creates the file and never writes. Returns `None` when there is no
    record (absent, or an empty file with no schema yet): "no history" is not
    an error. Present but unreadable raises `JobsStoreUnreadableError`."""
    if not path.exists():
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = open_read_only(path)
        conn.execute("SELECT 1 FROM jobs LIMIT 1").fetchall()
        conn.execute("SELECT 1 FROM job_uncommitted_paths LIMIT 1").fetchall()
        conn.execute("SELECT 1 FROM watch_observations LIMIT 1").fetchall()
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc):
            return None
        raise JobsStoreUnreadableError(path, str(exc)) from exc
    return conn


# --- jobs ------------------------------------------------------------------


def start_job(conn: sqlite3.Connection, kind: str, started_at: str) -> int:
    """Record a job's start and return its id. The outcome stays unset until
    `finish_job`; a job that never finishes is visible as an open row."""
    cur = conn.execute(
        "INSERT INTO jobs (kind, started_at) VALUES (?, ?)", (kind, started_at)
    )
    conn.commit()
    if cur.lastrowid is None:  # pragma: no cover -- INSERT always sets it
        raise sqlite3.DatabaseError("job insert returned no row id")
    return cur.lastrowid


def finish_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    outcome: str,
    ended_at: str,
    chat_calls: int,
    units_done: int,
    units_deferred: int,
    detail_code: str | None = None,
) -> None:
    """Record a job's single outcome. `detail_code` is a short machine code
    (e.g. `max_sources_per_pass`), never free text."""
    conn.execute(
        "UPDATE jobs SET outcome = ?, ended_at = ?, chat_calls = ?, "
        "units_done = ?, units_deferred = ?, detail_code = ? WHERE id = ?",
        (
            outcome,
            ended_at,
            chat_calls,
            units_done,
            units_deferred,
            detail_code,
            job_id,
        ),
    )
    conn.commit()


def _job(row: Sequence[object]) -> JobRecord:
    return JobRecord(
        id=int(str(row[0])),
        kind=str(row[1]),
        started_at=str(row[2]),
        ended_at=None if row[3] is None else str(row[3]),
        outcome=None if row[4] is None else str(row[4]),
        detail_code=None if row[5] is None else str(row[5]),
        chat_calls=int(str(row[6])),
        units_done=int(str(row[7])),
        units_deferred=int(str(row[8])),
    )


def recent_jobs(conn: sqlite3.Connection, limit: int = 20) -> tuple[JobRecord, ...]:
    """The most recent jobs, newest first."""
    rows = conn.execute(_SELECT_JOBS + " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return tuple(_job(r) for r in rows)


def last_job(conn: sqlite3.Connection, kind: str | None = None) -> JobRecord | None:
    """The newest job, optionally of one kind."""
    if kind is None:
        row = conn.execute(_SELECT_JOBS + " ORDER BY id DESC LIMIT 1").fetchone()
    else:
        row = conn.execute(
            _SELECT_JOBS + " WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
    return None if row is None else _job(row)


def chat_calls_since(conn: sqlite3.Connection, since: str) -> int:
    """Sum of model chat calls by jobs started at or after `since` (an ISO
    timestamp the caller derives from its own clock and day boundary). An
    empty or freshly recreated record sums to zero."""
    row = conn.execute(
        "SELECT COALESCE(SUM(chat_calls), 0) FROM jobs WHERE started_at >= ?",
        (since,),
    ).fetchone()
    return int(row[0])


# --- uncommitted paths -----------------------------------------------------


def record_uncommitted_paths(
    conn: sqlite3.Connection, job_id: int, paths: Sequence[str]
) -> None:
    """Remember the workspace-relative `paths` a job wrote but could not
    commit, so the next job can retry them."""
    conn.executemany(
        "INSERT OR IGNORE INTO job_uncommitted_paths (job_id, path) VALUES (?, ?)",
        [(job_id, p) for p in paths],
    )
    conn.commit()


def uncommitted_paths(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every recorded uncommitted path, de-duplicated and sorted."""
    rows = conn.execute(
        "SELECT DISTINCT path FROM job_uncommitted_paths ORDER BY path"
    ).fetchall()
    return tuple(str(r[0]) for r in rows)


def clear_uncommitted_paths(
    conn: sqlite3.Connection, paths: Sequence[str] | None = None
) -> None:
    """Forget recorded paths once committed (all of them when `paths` is
    `None`)."""
    if paths is None:
        conn.execute("DELETE FROM job_uncommitted_paths")
    else:
        conn.executemany(
            "DELETE FROM job_uncommitted_paths WHERE path = ?", [(p,) for p in paths]
        )
    conn.commit()


# --- watch observations ----------------------------------------------------


def upsert_observation(conn: sqlite3.Connection, obs: WatchObservation) -> None:
    """Insert or replace the observation for `obs.path`."""
    conn.execute(
        "INSERT INTO watch_observations "
        "(path, size, mtime_ns, first_stable_at, digest, outcome) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET size = excluded.size, "
        "mtime_ns = excluded.mtime_ns, first_stable_at = excluded.first_stable_at, "
        "digest = excluded.digest, outcome = excluded.outcome",
        (
            obs.path,
            obs.size,
            obs.mtime_ns,
            obs.first_stable_at,
            obs.digest,
            obs.outcome,
        ),
    )
    conn.commit()


def observations(conn: sqlite3.Connection) -> tuple[WatchObservation, ...]:
    """Every watch observation, ordered by path."""
    rows = conn.execute(
        "SELECT path, size, mtime_ns, first_stable_at, digest, outcome "
        "FROM watch_observations ORDER BY path"
    ).fetchall()
    return tuple(
        WatchObservation(
            path=str(r[0]),
            size=int(r[1]),
            mtime_ns=int(r[2]),
            first_stable_at=str(r[3]),
            digest=None if r[4] is None else str(r[4]),
            outcome=None if r[5] is None else str(r[5]),
        )
        for r in rows
    )


def forget_observations(conn: sqlite3.Connection, paths: Sequence[str]) -> None:
    """Drop observations for files that left the inbox."""
    conn.executemany(
        "DELETE FROM watch_observations WHERE path = ?", [(p,) for p in paths]
    )
    conn.commit()
