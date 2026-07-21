"""On-disk sqlite-vec vector store: schema, lifecycle, and data flow.

Mirrors `state/fts.py`'s guarded-open / connection-ownership posture, but
persists to disk instead of `:memory:`: `open_vector_store` loads the
`sqlite-vec` extension into a `sqlite3.Connection`, creates the `vectors`
vec0 virtual table (idempotently) plus a `vector_meta` companion table for
hash-keyed lookups, and hands the open connection to a `VectorStoreDB`
context manager that owns it thereafter. `.openkos/` is created LAZILY
here, on first SUCCESSFUL open -- never by `init` (embedding-vector-store
spec: No Init-Time Side Effect), and never as a side effect of a failed
open: ANY failure (not just `VecUnavailable`) leaves no new on-disk
footprint (single-level cleanup invariant -- only `.openkos/`/`vectors.db`
artifacts THIS call created are removed; the enclosing workspace root and
any pre-existing `vectors.db` are never touched).

Slice 2a shipped this module as additive infrastructure only (lifecycle-only
`VectorStore` Protocol, no data flow, no consumer). Slice 2b makes the seam
real: `upsert`/`query` on `VectorStoreDB`, plus the `meta_hashes`/`prune`
cache accessors `state/reindex.py` needs, with the `VectorStore` Protocol
extended additively to match. The confirmed vec0 0.1.9 semantics (a spike
test in `tests/unit/state/test_vectorstore.py`, gated on
`probe_vec_loadable()`, proved both hold against the real extension): a
plain `DELETE FROM vectors WHERE concept_id = ?` works directly against the
metadata column (no rowid indirection needed), and
`embedding MATCH ? AND k = ? ORDER BY distance` returns `(concept_id,
distance)` rows ordered nearest-first.
"""

import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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

# Confirmed against the real sqlite-vec 0.1.9 extension by the spike tests in
# `tests/unit/state/test_vectorstore.py` (Phase 1): DELETE-by-`concept_id`
# (a metadata column, not the vec0 `embedding` column) works directly, no
# rowid lookup needed.
_DELETE_VECTOR_BY_CONCEPT_ID_SQL = "DELETE FROM vectors WHERE concept_id = ?"

_INSERT_VECTOR_SQL = (
    "INSERT INTO vectors (embedding, concept_id, content_hash) VALUES (?, ?, ?)"
)

_UPSERT_VECTOR_META_SQL = (
    "INSERT OR REPLACE INTO vector_meta (concept_id, content_hash) VALUES (?, ?)"
)

_DELETE_VECTOR_META_BY_CONCEPT_ID_SQL = "DELETE FROM vector_meta WHERE concept_id = ?"

_QUERY_VECTORS_SQL = (
    "SELECT concept_id, distance FROM vectors "
    "WHERE embedding MATCH ? AND k = ? ORDER BY distance"
)

_SELECT_META_HASHES_SQL = "SELECT concept_id, content_hash FROM vector_meta"

_BUSY_TIMEOUT_MS = 5000
"""Busy-timeout (milliseconds) set on the `vectors.db` connection, matching
`state/derived.py`'s `_BUSY_TIMEOUT_MS` for `fts.db`/`graph.db` -- keeps all
three on-disk derived stores consistent (Slice 5, follow-up #4)."""


class VecUnavailable(RuntimeError):
    """Raised when the `sqlite-vec` extension cannot be loaded into SQLite
    (missing `enable_load_extension` support, or the loader/DDL fails)."""


@dataclass(frozen=True)
class VecHit:
    """One `query` result: an OKF concept ID and its vec0 KNN distance.

    Mirrors `state/fts.py`'s `FtsHit` shape."""

    concept_id: str
    """The OKF concept ID (bundle-relative path, `.md` suffix removed)."""
    distance: float
    """The vec0 KNN distance -- lower is more similar."""


class VectorStore(Protocol):
    """A vector store handle's seam (structural, mirrors `Embedder`/
    `LLMBackend`, `llm/base.py`).

    Extended additively in Slice 2b: `upsert`/`query`/`meta_hashes`/`prune`
    joined the Slice 2a lifecycle-only (`close()`) contract. Extended
    additively AGAIN in Slice 5, follow-up #4: `upsert_many`/`prune_many`/
    `commit` joined so `state/reindex.py` can batch an entire run's writes
    into ONE commit instead of one per document, without changing `upsert`/
    `prune`'s own existing per-call-commits contract for any other caller.
    Each Protocol growth grows the SHAPE -- any concrete implementer
    (`VectorStoreDB`, or a test fake assigned to this type) must now provide
    every method here; a fake missing one no longer satisfies it, since
    Python's structural Protocol typing requires ALL declared members, with
    no partial/optional subset."""

    def upsert(
        self, concept_id: str, embedding: Sequence[float], content_hash: str
    ) -> None:
        """Replace `concept_id`'s stored vector and hash with `embedding`/
        `content_hash`, committing once for this call."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def upsert_many(self, items: Sequence[tuple[str, Sequence[float], str]]) -> None:
        """Replace MANY `(concept_id, embedding, content_hash)` rows in one
        call, WITHOUT committing -- the caller commits once via `commit()`
        (Slice 5, follow-up #4: single commit per store per run)."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def query(self, embedding: Sequence[float], k: int) -> list[VecHit]:
        """Return up to `k` `VecHit`s nearest to `embedding`, ascending
        distance."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def meta_hashes(self) -> dict[str, str]:
        """Return `{concept_id: content_hash}` for every stored row."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def prune(self, concept_id: str) -> None:
        """Remove `concept_id`'s stored vector and hash, if present,
        committing once for this call."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def prune_many(self, concept_ids: Sequence[str]) -> None:
        """Remove MANY `concept_id`s' stored vectors/hashes in one call,
        WITHOUT committing -- the caller commits once via `commit()` (Slice
        5, follow-up #4: single commit per store per run)."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def commit(self) -> None:
        """Commit the current transaction (Slice 5, follow-up #4) -- pairs
        with `upsert_many`/`prune_many`, which never commit on their own."""
        ...  # pragma: no cover -- Protocol stub body, never executed

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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
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

    def upsert(
        self, concept_id: str, embedding: Sequence[float], content_hash: str
    ) -> None:
        """Replace `concept_id`'s stored vector and hash with `embedding`/
        `content_hash` (spec: Vector Upsert Data Flow).

        Serializes `embedding` via `sqlite_vec.serialize_float32`, deletes
        any existing `vectors` row for `concept_id` (confirmed safe against
        the metadata column by the Phase 1 spike -- no rowid lookup needed),
        inserts the new row, then `INSERT OR REPLACE`s the matching
        `vector_meta` row, and commits once. A first upsert of a new
        `concept_id` leaves exactly one row in each table; a re-upsert
        leaves the SAME one row, now holding the new embedding/hash."""
        blob = sqlite_vec.serialize_float32(list(embedding))
        self._conn.execute(_DELETE_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
        self._conn.execute(_INSERT_VECTOR_SQL, (blob, concept_id, content_hash))
        self._conn.execute(_UPSERT_VECTOR_META_SQL, (concept_id, content_hash))
        self._conn.commit()

    def upsert_many(self, items: Sequence[tuple[str, Sequence[float], str]]) -> None:
        """Replace MANY `(concept_id, embedding, content_hash)` rows in one
        call (spec: Slice 5, follow-up #4 -- single commit per store per
        run), reusing `upsert`'s own per-item DELETE-then-INSERT sequence
        for each item -- WITHOUT committing here; the caller commits once
        via `commit()`, typically after also calling `prune_many` for the
        same run."""
        for concept_id, embedding, content_hash in items:
            blob = sqlite_vec.serialize_float32(list(embedding))
            self._conn.execute(_DELETE_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
            self._conn.execute(_INSERT_VECTOR_SQL, (blob, concept_id, content_hash))
            self._conn.execute(_UPSERT_VECTOR_META_SQL, (concept_id, content_hash))

    def query(self, embedding: Sequence[float], k: int) -> list[VecHit]:
        """Return up to `k` `VecHit`s nearest to `embedding`, ascending
        distance (spec: k-NN Query Data Flow).

        `embedding MATCH ? AND k = ? ORDER BY distance` against the empty
        `vectors` table returns zero rows -- `query` returns `[]` rather
        than raising (spec: Query against an empty store returns no
        results)."""
        blob = sqlite_vec.serialize_float32(list(embedding))
        rows = self._conn.execute(_QUERY_VECTORS_SQL, (blob, k)).fetchall()
        return [VecHit(concept_id=str(row[0]), distance=float(row[1])) for row in rows]

    def meta_hashes(self) -> dict[str, str]:
        """Return `{concept_id: content_hash}` for every `vector_meta` row --
        the reindex orchestrator's content-hash cache gate reads this to
        decide which discovered docs are unchanged."""
        rows = self._conn.execute(_SELECT_META_HASHES_SQL).fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def prune(self, concept_id: str) -> None:
        """Remove `concept_id`'s row from both `vectors` and `vector_meta`,
        if present; a `concept_id` with no stored row is a no-op, not an
        error."""
        self._conn.execute(_DELETE_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
        self._conn.execute(_DELETE_VECTOR_META_BY_CONCEPT_ID_SQL, (concept_id,))
        self._conn.commit()

    def prune_many(self, concept_ids: Sequence[str]) -> None:
        """Remove MANY `concept_id`s' rows from both `vectors` and
        `vector_meta` in one call (spec: Slice 5, follow-up #4 -- single
        commit per store per run), reusing `prune`'s own per-item DELETE
        sequence for each id -- WITHOUT committing here; the caller commits
        once via `commit()`."""
        for concept_id in concept_ids:
            self._conn.execute(_DELETE_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
            self._conn.execute(_DELETE_VECTOR_META_BY_CONCEPT_ID_SQL, (concept_id,))

    def commit(self) -> None:
        """Commit the current transaction (spec: Slice 5, follow-up #4) --
        pairs with `upsert_many`/`prune_many`, which never commit on their
        own, so a caller can batch an entire run's writes into ONE commit."""
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection.

        Idempotent (spec: Idempotent Double-Close): `sqlite3.Connection.close()`
        is itself safe to call more than once (CPython's sqlite3 module
        documents `close()` as a no-op on an already-closed connection), so
        no guard is needed here beyond delegating straight through -- a
        second `close()` call, whether direct or via a second `with` block
        exit, never raises."""
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
