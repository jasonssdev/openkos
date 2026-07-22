"""Shared test infrastructure for `tests/unit/**` (reindex-lock-handling).

`make_locked_error`/`make_non_lock_operational_error` build `sqlite3.
OperationalError` instances shaped like real SQLite failures, for injecting
at any write surface (store open, `upsert_many`/commit, `BEGIN IMMEDIATE`)
without needing a genuine concurrent second connection holding a lock.
Manual `sqlite_errorcode` assignment is confirmed reliable on this
interpreter (Python 3.13) by
`tests/unit/state/test_derived.py::test_sqlite_operational_error_supports_manual_errorcode_assignment`
(task 1.1's decision test) -- if that assumption ever regresses on a future
interpreter, the documented fallback is a real 2nd-connection `BEGIN
IMMEDIATE` + `busy_timeout=0` competing-lock scenario (design:
sdd/reindex-lock-handling), not a change to these helpers' call sites.
"""

import sqlite3


def make_locked_error(
    message: str = "database is locked",
    *,
    errorcode: int = sqlite3.SQLITE_BUSY,
) -> sqlite3.OperationalError:
    """Build an `OperationalError` classified as lock contention by
    `state.derived.is_lock_contention` -- `errorcode` defaults to
    `SQLITE_BUSY` (database-level lock); pass `sqlite3.SQLITE_LOCKED` for
    the table-level variant."""
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = errorcode
    return exc


def make_non_lock_operational_error(
    message: str = "no such module: fts5",
) -> sqlite3.OperationalError:
    """Build an `OperationalError` NOT classified as lock contention (e.g.
    a genuine fts5-module-absent failure) -- `errorcode` is `SQLITE_ERROR`,
    proving `is_lock_contention`-based discrimination never mislabels a
    non-lock operational failure as lock contention."""
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = sqlite3.SQLITE_ERROR
    return exc
