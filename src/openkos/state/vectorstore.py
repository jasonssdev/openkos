"""On-disk sqlite-vec vector store scaffolding (Slice 2a).

Mirrors `state/fts.py`'s guarded-open / connection-ownership posture, but
persists to disk instead of `:memory:`: `open_vector_store` loads the
`sqlite-vec` extension into a `sqlite3.Connection`, creates the `vectors`
vec0 virtual table (idempotently) plus a `vector_meta` companion table for
hash-keyed lookups, and hands the open connection to a `VectorStoreDB`
context manager that owns it thereafter.

This slice is additive infrastructure only: `VectorStore` is a lifecycle-only
Protocol (`close()`), there is no vec0 upsert/query data flow, and nothing in
the engine calls `open_vector_store` yet. `.openkos/` is created LAZILY here,
on first SUCCESSFUL open -- never by `init` (embedding-vector-store spec: No
Init-Time Side Effect), and never as a side effect of a failed open: ANY
failure (not just `VecUnavailable`) leaves no new on-disk footprint.
"""

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Protocol

import sqlite_vec

from openkos.llm.base import EMBED_DIM

_CREATE_VECTORS_TABLE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0(
    embedding float[{EMBED_DIM}],
    concept_id TEXT,
    content_hash TEXT
)
"""

_CREATE_VECTOR_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vector_meta (
    concept_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL
)
"""


class VecUnavailable(RuntimeError):
    """Raised when the `sqlite-vec` extension cannot be loaded into SQLite
    (missing `enable_load_extension` support, or the loader/DDL fails)."""


class VectorStore(Protocol):
    """A vector store handle's lifecycle seam (structural, mirrors
    `Embedder`/`LLMBackend`, `llm/base.py`).

    Lifecycle-only for Slice 2a: no upsert/query stub is pre-declared here
    (YAGNI -- there is no 2a consumer). Slice 2b extends this Protocol
    additively; a fake matching `close()` alone remains valid."""

    def close(self) -> None:
        """Release the underlying resource."""
        ...  # pragma: no cover -- Protocol stub body, never executed


def content_hash(data: bytes) -> str:
    """Return the stable sha256 hex digest of raw `.md` file bytes.

    Hashes `data` verbatim -- no encoding normalization -- so identical byte
    sequences always hash identically and any byte-level change (including
    one invisible to a text diff, e.g. a stray CR) changes the digest."""
    return hashlib.sha256(data).hexdigest()


def _guarded_vec_load(conn: sqlite3.Connection) -> None:
    """Shared guarded sqlite-vec load sequence: enable extension loading,
    load the extension, then immediately re-disable loading (security:
    closes the SQL-level `load_extension()` surface again once `sqlite_vec`
    is in).

    Maps `AttributeError` (this SQLite build has no `enable_load_extension`
    at all) and `sqlite3.Error` (extension loading is compiled out/disabled)
    to `VecUnavailable`. Does NOT touch `conn`'s lifecycle either way --
    `probe_vec_loadable` and `_load_vec_extension` have different
    connection-ownership rules and each owns its own connection's
    lifecycle around this call."""
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.Error) as exc:
        raise VecUnavailable(
            "the sqlite-vec extension could not be loaded into this "
            "Python's SQLite build"
        ) from exc


def probe_vec_loadable() -> bool:
    """Return whether `sqlite-vec` can be loaded into this Python's SQLite
    build. Never raises for an ordinary failure: probes against a throwaway
    `:memory:` connection and reports `False` for ANY ordinary exception --
    not only the mapped `VecUnavailable`, but also an exception type the
    guarded load doesn't map (e.g. `MemoryError`, or a future
    `sqlite_vec.load` raising something new). This matters because `doctor`
    calls `probe_vec_loadable()` with no surrounding try/except before
    rendering any check, so an unmapped exception here would otherwise crash
    the whole `doctor` command instead of degrading to a single failed
    check. `KeyboardInterrupt`/`SystemExit` are not ordinary failures and
    still propagate. Creates no files -- safe to call unconditionally,
    including from `doctor`."""
    conn = sqlite3.connect(":memory:")
    try:
        try:
            _guarded_vec_load(conn)
        except Exception:
            return False
        return True
    finally:
        conn.close()


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    """Guarded sqlite-vec load: delegates the load+map sequence to
    `_guarded_vec_load`, then owns `conn`'s lifecycle around it -- ANY
    failure (whether mapped to `VecUnavailable`, or an unanticipated
    exception type the guard above doesn't map, e.g. `MemoryError` or a
    future `sqlite_vec.load` raising something new) closes `conn` first, so
    a failed load never leaks the connection (mirrors `fts.py`'s
    build-failure guard). Only a successful load leaves `conn` open."""
    try:
        _guarded_vec_load(conn)
    except BaseException:
        conn.close()
        raise


def open_vector_store(
    path: Path,
    *,
    connect: Callable[[str], sqlite3.Connection] = sqlite3.connect,
) -> "VectorStoreDB":
    """Open (creating if needed) the vector store database at `path`.

    Checks `sqlite-vec` loadability on a throwaway `:memory:` connection
    (opened via the same injected `connect` factory) BEFORE any filesystem
    mutation: a `VecUnavailable` failure at this stage leaves no new on-disk
    footprint at all, since `path.parent` (`.openkos/`) is created and
    `path` is connected ONLY once the extension is confirmed loadable and
    the throwaway probe connection is discarded. `.openkos/` creation is
    therefore scoped to a SUCCESSFUL open -- this remains the ONLY place
    that directory is created; `init` never creates it. The extension is
    then loaded again on the real connection to `path` (default
    `sqlite3.connect`, overridable for hermetic tests via `connect`), and
    the `vectors` vec0 table plus the `vector_meta` companion table are
    created idempotently. Re-opening an already-initialized database is a
    no-op migration.

    The no-new-footprint guarantee extends past the probe stage: ANY
    failure after the probe passes (a `path.parent.mkdir` error, a real-path
    `connect` failure, an extension-load failure on the real connection, or
    a schema DDL error) leaves no new on-disk footprint either. Only
    artifacts THIS call created are cleaned up before the exception is
    re-raised -- a `.openkos/` directory or `vectors.db` file that pre-dated
    this call is always left untouched, regardless of where the failure
    happens."""
    probe_conn = connect(":memory:")
    _load_vec_extension(probe_conn)
    probe_conn.close()

    parent = path.parent
    parent_preexisted = parent.exists()
    db_preexisted = path.exists()
    conn: sqlite3.Connection | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        conn = connect(str(path))
        _load_vec_extension(conn)
        conn.execute(_CREATE_VECTORS_TABLE_SQL)
        conn.execute(_CREATE_VECTOR_META_TABLE_SQL)
        conn.commit()
    except BaseException:
        if conn is not None:
            conn.close()
        if not db_preexisted and path.exists():
            path.unlink()
        if not parent_preexisted and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
        raise
    return VectorStoreDB(conn)


class VectorStoreDB:
    """A vector store handle; owns its `sqlite3` connection.

    A context manager (mirrors `FtsIndex`): `with open_vector_store(path) as
    db: ...` closes the connection on block exit."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Wrap an already-initialized `conn`."""
        self._conn = conn

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "VectorStoreDB":
        """Return `self` -- the connection is already open by construction."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection on block exit, regardless of exception state."""
        self.close()
