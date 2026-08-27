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

MVP-2 follow-up #5 adds a GENERIC `meta(key, value)` table -- distinct from
`vector_meta` above, which is the per-concept content_hash cache -- storing
one `('embedding_model', <tag>)` row via `read_model_tag`/`write_model_tag`.
`state/reindex.py`'s model-tag gate reads this to detect an absent or
changed embedding model and force a full re-embed of every concept (no vec0
`DROP`, just the existing `upsert_many` DELETE-then-INSERT path), keeping a
switched or freshly-adopted model from silently mixing incompatible vectors
in `vectors.db`. This new table NEVER pollutes `meta_hashes()`/the
content_hash cache -- the two tables are, and must stay, fully separate.
"""

import contextlib
import hashlib
import math
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol
from urllib.parse import quote

import sqlite_vec

from openkos.llm.base import EMBED_DIM

_CREATE_VECTORS_TABLE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0(
    embedding float[{EMBED_DIM}],
    concept_id TEXT,
    chunk_index INTEGER,
    content_hash TEXT
)
"""
"""#888: `chunk_index` is a metadata column, NOT part of a composite key --
`concept_id` alone still names the document (design D1). Confirmed against
the real 0.1.9 extension (design's "Orchestrator verification"): an INTEGER
metadata column is declarable, KNN returns it in the projection, and
`DELETE ... WHERE concept_id = ?` removes every one of a document's chunk
rows in one statement."""

_CREATE_DOC_VECTORS_TABLE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS doc_vectors USING vec0(
    embedding float[{EMBED_DIM}],
    concept_id TEXT
)
"""
"""#888 (D2): one derived row per document -- `normalize(mean(normalize(v_i)
for v_i in chunks)))` -- kept in a SEPARATE vec0 table so the document mean
never competes with its own chunks inside `query()`'s retrieval KNN (that
competition is the truncation-era defect re-entering through the back
door)."""

_CREATE_VECTOR_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vector_meta (
    concept_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 1
)
"""

_CREATE_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""
"""A GENERIC `(key, value)` table (MVP-2 follow-up #5), DISTINCT from
`vector_meta` above -- mirrors `state/derived.py`'s identically-shaped
`meta` table for `fts.db`/`graph.db`. `vector_meta` is the per-concept
content_hash cache `meta_hashes()`/`reindex`'s incremental gate reads;
`meta` is for whole-store, singleton settings (e.g. the embedding-model
tag) that must NEVER appear as a fake `concept_id` row in `vector_meta`
(which would pollute `meta_hashes()` and the content_hash cache gate)."""

_SELECT_META_SQL = "SELECT value FROM meta WHERE key = ?"

_UPSERT_META_SQL = "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)"

EMBEDDING_MODEL_KEY = "embedding_model"
"""The `meta.key` `reindex`'s model-tag gate reads/writes (MVP-2 follow-up
#5) -- the SAME generic `meta` table's `key` column `state/derived.py` uses
for `manifest_hash`, just a different row, in this store's own `meta` table
(the two derived stores each own a SEPARATE `meta` table -- no shared file,
no cross-store key collision possible)."""

# Confirmed against the real sqlite-vec 0.1.9 extension by the spike tests in
# `tests/unit/state/test_vectorstore.py` (Phase 1): DELETE-by-`concept_id`
# (a metadata column, not the vec0 `embedding` column) works directly, no
# rowid lookup needed. #888: this now removes every one of a document's
# chunk rows in the same one statement (re-confirmed by the design's
# "Orchestrator verification" spike).
_DELETE_VECTOR_BY_CONCEPT_ID_SQL = "DELETE FROM vectors WHERE concept_id = ?"

_DELETE_DOC_VECTOR_BY_CONCEPT_ID_SQL = "DELETE FROM doc_vectors WHERE concept_id = ?"

_INSERT_VECTOR_SQL = (
    "INSERT INTO vectors (embedding, concept_id, chunk_index, content_hash) "
    "VALUES (?, ?, ?, ?)"
)

_INSERT_DOC_VECTOR_SQL = "INSERT INTO doc_vectors (embedding, concept_id) VALUES (?, ?)"

_UPSERT_VECTOR_META_SQL = (
    "INSERT OR REPLACE INTO vector_meta (concept_id, content_hash, chunk_count) "
    "VALUES (?, ?, ?)"
)

_DELETE_VECTOR_META_BY_CONCEPT_ID_SQL = "DELETE FROM vector_meta WHERE concept_id = ?"

_QUERY_VECTORS_SQL = (
    "SELECT concept_id, distance FROM vectors "
    "WHERE embedding MATCH ? AND k = ? ORDER BY distance"
)
"""vec0 permits exactly one `ORDER BY distance` clause on a KNN query and
rejects a secondary sort key outright (`OperationalError: Only a single
'ORDER BY distance' clause is allowed on vec0 KNN queries`), so equidistant
rows come back in rowid -- i.e. insertion -- order. Measured against the
real extension, inserting the same two tied rows in opposite orders returns
them in opposite orders. Callers that need a reproducible order must break
ties themselves; `neighbors` and `query`'s collapse (#888) both do."""

_QUERY_DOC_VECTORS_SQL = (
    "SELECT concept_id, distance FROM doc_vectors "
    "WHERE embedding MATCH ? AND k = ? ORDER BY distance"
)

_SELECT_MAX_CHUNK_COUNT_SQL = "SELECT MAX(chunk_count) FROM vector_meta"

_SELECT_META_HASHES_SQL = "SELECT concept_id, content_hash FROM vector_meta"

_SELECT_DOC_VECTOR_BLOB_SQL = "SELECT embedding FROM doc_vectors WHERE concept_id = ?"
"""Read back one document's OWN derived vector blob (#888, D2), so
`neighbors` can run a k-NN from a `concept_id` alone without the caller
holding the vector -- `doc_vectors` replaces `vectors` as this lookup's
source table; `vectors` now holds per-CHUNK rows, several per document."""

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
    Extended additively a THIRD time, MVP-2 follow-up #5: `read_model_tag`/
    `write_model_tag` joined so `reindex`'s model-tag gate can detect an
    absent/changed embedding model and force a full re-embed, without
    touching the `Embedder` Protocol (`llm/base.py`) at all -- the tag lives
    entirely in `vectors.db`'s own generic `meta` table. Each Protocol
    growth grows the SHAPE -- any concrete implementer (`VectorStoreDB`, or
    a test fake assigned to this type) must now provide every method here;
    a fake missing one no longer satisfies it, since Python's structural
    Protocol typing requires ALL declared members, with no partial/optional
    subset."""

    def upsert(
        self, concept_id: str, embedding: Sequence[float], content_hash: str
    ) -> None:
        """Replace `concept_id`'s stored vector and hash with `embedding`/
        `content_hash`, committing once for this call."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def upsert_many(
        self, items: Sequence[tuple[str, Sequence[Sequence[float]], str]]
    ) -> None:
        """Replace MANY documents' chunk vectors in one call: each item is
        `(concept_id, chunk_vectors, content_hash)`, where `chunk_vectors`
        is ONE document's ordered list of per-chunk embeddings (#888 --
        widened from a single flat `Sequence[float]` per document to
        `Sequence[Sequence[float]]`, a deliberate, audited break: the
        alternative, a second `upsert_chunked` method, would leave the old
        write path able to store a truncated single vector, which is the
        defect this change closes). Every implementer deletes the
        document's existing rows first (matched on `concept_id` alone,
        never chunk count), inserts the new chunk rows, derives and stores
        one document-level vector, and updates `vector_meta`'s
        `chunk_count` -- WITHOUT committing; the caller commits once via
        `commit()` (Slice 5, follow-up #4: single commit per store per
        run)."""
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

    def read_model_tag(self) -> str | None:
        """Return the stored `embedding_model` tag, or `None` if absent
        (MVP-2 follow-up #5) -- a fresh store, or one that predates this
        follow-up."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def write_model_tag(self, tag: str) -> None:
        """Replace the stored `embedding_model` tag with `tag`, WITHOUT
        committing -- the caller commits once via `commit()`, alongside the
        run's other writes (MVP-2 follow-up #5; Slice 5 single-commit-per-run
        contract)."""
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


def _normalize(vector: Sequence[float]) -> list[float]:
    """L2-normalize `vector`. Returns `vector` unchanged (as a `list`) for a
    zero vector -- a degenerate input an `Embedder` should never produce,
    guarded here only to avoid a division by zero rather than to give it
    meaning."""
    values = list(vector)
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0.0:
        return values
    return [x / norm for x in values]


def _derive_document_vector(chunk_vectors: Sequence[Sequence[float]]) -> list[float]:
    """`normalize(mean(normalize(v_i) for v_i in chunk_vectors))` (embedding-
    chunking spec: Document Vector Is A Normalized Mean Of Normalized Chunk
    Vectors) -- unweighted by chunk length, so one disproportionately long
    boilerplate chunk cannot dominate the derived document vector. A single
    chunk's derived vector equals that chunk's own normalized vector
    (`mean` of one value is that value, and `normalize` is idempotent on an
    already-unit vector)."""
    normalized = [_normalize(v) for v in chunk_vectors]
    dim = len(normalized[0])
    mean = [sum(v[i] for v in normalized) / len(normalized) for i in range(dim)]
    return _normalize(mean)


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


def vector_store_is_empty(path: Path) -> bool:
    """Return whether `path` is absent OR present but has zero embedded
    concepts -- the "absent or empty" precondition issue #183's state 3
    (embeddings missing) keys on, across `status`/`suggest-relations`/
    `contradictions`.

    "Empty" means zero `vector_meta` rows (the cheapest available signal --
    a row count, not a full vector query), read over a plain read-only
    connection that never loads the `sqlite-vec` extension (mirrors
    `sqlite_graph.open_graph_store_readonly`'s `file:...?mode=ro` posture),
    so this check works even when the extension itself is unavailable. A
    file that exists but is not a valid SQLite database, or lacks
    `vector_meta` entirely, also counts as empty rather than raising."""
    if not path.exists():
        return True
    uri = f"file:{quote(str(path))}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        row = conn.execute("SELECT COUNT(*) FROM vector_meta").fetchone()
    except sqlite3.Error:
        return True
    finally:
        if conn is not None:
            conn.close()
    return bool(row is None or row[0] == 0)


def _migrate_legacy_vectors_shape_if_needed(conn: sqlite3.Connection) -> None:
    """Detect a pre-chunking `vectors` table (#888, design D1) by probing
    for `chunk_index`, and on detection drop and recreate it under the
    chunk-aware schema, clearing every `vector_meta` row.

    `CREATE VIRTUAL TABLE IF NOT EXISTS` silently no-ops when a table named
    `vectors` already exists under ANY shape -- including the legacy
    3-column one -- so the caller's own `_CREATE_VECTORS_TABLE_SQL` call
    just above this one is not enough by itself; this probe-and-migrate
    step is what actually upgrades an existing legacy store.

    Clearing `vector_meta` is mandatory, not tidiness (vector-store spec:
    Clearing vector_meta prevents a permanently empty store): a dropped
    `vectors` table with a SURVIVING content_hash cache would read every
    document as a cache-hit forever, and the store would never recover.
    Runs inside the caller's own schema-creation transaction -- no separate
    commit here."""
    try:
        conn.execute("SELECT chunk_index FROM vectors LIMIT 0")
        return
    except sqlite3.OperationalError:
        pass
    conn.execute("DROP TABLE vectors")
    conn.execute(_CREATE_VECTORS_TABLE_SQL)
    conn.execute("DELETE FROM vector_meta")


def _ensure_vector_meta_chunk_count_column(conn: sqlite3.Connection) -> None:
    """Add `vector_meta.chunk_count` to a store whose `vector_meta` table
    predates it (#888) -- `CREATE TABLE IF NOT EXISTS` no-ops against an
    already-existing table, same reasoning as
    `_migrate_legacy_vectors_shape_if_needed` above, so an explicit `ALTER
    TABLE` is what actually widens a pre-existing table. A store created
    fresh under the current `_CREATE_VECTOR_META_TABLE_SQL` already
    declares the column, so this is a no-op for it (`OperationalError:
    duplicate column name`, caught and ignored)."""
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute(
            "ALTER TABLE vector_meta ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 1"
        )


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
        _migrate_legacy_vectors_shape_if_needed(conn)
        conn.execute(_CREATE_DOC_VECTORS_TABLE_SQL)
        conn.execute(_CREATE_VECTOR_META_TABLE_SQL)
        _ensure_vector_meta_chunk_count_column(conn)
        conn.execute(_CREATE_META_TABLE_SQL)
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

        #888: redefined as the ONE-chunk case of `upsert_many` (zero
        production callers of `upsert` today) -- delegates to it with a
        single-item, single-chunk batch, then commits once here, since
        `upsert_many` itself never commits. A first upsert of a new
        `concept_id` leaves exactly one `vectors` row (`chunk_index=0`), one
        `doc_vectors` row, and one `vector_meta` row (`chunk_count=1`); a
        re-upsert leaves the SAME rows, now holding the new embedding/hash."""
        self.upsert_many([(concept_id, [embedding], content_hash)])
        self._conn.commit()

    def upsert_many(
        self, items: Sequence[tuple[str, Sequence[Sequence[float]], str]]
    ) -> None:
        """Replace MANY documents' chunk vectors in one call (#888; spec:
        Multi-Chunk Upsert Is Atomic And Orphan-Free), WITHOUT committing --
        the caller commits once via `commit()`, typically after also calling
        `prune_many` for the same run (Slice 5, follow-up #4).

        Per item: delete every existing row for `concept_id` across
        `vectors`, `doc_vectors`, and `vector_meta` (matched on `concept_id`
        alone -- the delete is chunk-count-blind, so a document re-embedded
        at a DIFFERENT chunk count never orphans old rows), insert one
        `vectors` row per chunk (`chunk_index` 0..N-1), derive and insert one
        `doc_vectors` row (`_derive_document_vector`), then upsert
        `vector_meta` with the new `chunk_count`."""
        for concept_id, chunk_vectors, content_hash in items:
            self._conn.execute(_DELETE_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
            self._conn.execute(_DELETE_DOC_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
            chunks = [list(v) for v in chunk_vectors]
            for chunk_index, vector in enumerate(chunks):
                blob = sqlite_vec.serialize_float32(vector)
                self._conn.execute(
                    _INSERT_VECTOR_SQL, (blob, concept_id, chunk_index, content_hash)
                )
            doc_vector = _derive_document_vector(chunks)
            doc_blob = sqlite_vec.serialize_float32(doc_vector)
            self._conn.execute(_INSERT_DOC_VECTOR_SQL, (doc_blob, concept_id))
            self._conn.execute(
                _UPSERT_VECTOR_META_SQL, (concept_id, content_hash, len(chunks))
            )

    def query(self, embedding: Sequence[float], k: int) -> list[VecHit]:
        """Return up to `k` `VecHit`s, AT MOST ONE per document, ascending
        `(distance, concept_id)` (spec: k-NN Query Data Flow).

        #888: `vectors` now holds several rows per document (one per
        chunk), so this over-fetches `k * max(chunk_count)` rows (reading
        `chunk_count` from `vector_meta` -- a small ordinary table, exactly
        what the column is for), keeps each `concept_id`'s MINIMUM
        distance, then re-sorts by `(distance, concept_id)` in Python (vec0
        refuses a secondary sort key; see `_QUERY_VECTORS_SQL`) before
        slicing to `k` documents.

        `embedding MATCH ? AND k = ? ORDER BY distance` against the empty
        `vectors` table returns zero rows -- `query` returns `[]` rather
        than raising (spec: Query against an empty store returns no
        results).

        RESIDUAL LIMIT, deliberately not papered over (spec: A k-th-boundary
        tie can still drop a whole document): the over-fetch factor comes
        from `MAX(chunk_count)`, not from the actual row distribution near
        the cut, so more rows can tie exactly at vec0's OWN internal k-th
        boundary than the over-fetch admits -- when every document has
        `chunk_count == 1` the factor is 1, giving zero headroom, and a tie
        between two different documents' single chunk rows is then vec0's
        own insertion-order choice, made before this method's Python
        re-sort ever runs. This fixes the order of what collapse SEES, not
        what vec0 hands it."""
        max_chunk_count = (
            self._conn.execute(_SELECT_MAX_CHUNK_COUNT_SQL).fetchone()[0] or 1
        )
        blob = sqlite_vec.serialize_float32(list(embedding))
        rows = self._conn.execute(
            _QUERY_VECTORS_SQL, (blob, k * max_chunk_count)
        ).fetchall()
        best: dict[str, float] = {}
        for row in rows:
            concept_id = str(row[0])
            distance = float(row[1])
            if concept_id not in best or distance < best[concept_id]:
                best[concept_id] = distance
        ordered = sorted(best.items(), key=lambda pair: (pair[1], pair[0]))[:k]
        return [VecHit(concept_id=cid, distance=dist) for cid, dist in ordered]

    def neighbors(self, concept_id: str, k: int) -> list[VecHit]:
        """Return up to `k` `VecHit`s nearest to `concept_id`'s OWN stored
        embedding, ascending distance (#183: candidate-edge sourcing).

        Deliberately NOT on the `VectorStore` Protocol. That Protocol is the
        narrow write/query surface every fake in the test suite already
        implements; adding a method there would break all of them for a
        capability only the real on-disk store can provide -- it reads a
        blob back out of `vectors`, which a dict-backed fake has no notion
        of.

        `concept_id` is INCLUDED in its own result set, at distance ~0.
        Self-exclusion belongs to `graph/proximity.py`, which also owns the
        similarity floor and the symmetry collapse; keeping it out here
        leaves this a thin, honest k-NN rather than a policy layer. Callers
        must therefore budget `k` for the anchor's own row.

        A `concept_id` with no stored embedding -- never embedded, or
        pruned -- returns `[]` rather than raising: the two cases are
        indistinguishable from here and neither is an error. An empty
        `doc_vectors` table likewise returns `[]`, mirroring `query`.

        #888 (D2): reads `doc_vectors` -- the derived one-row-per-document
        vector -- instead of `vectors`, which now holds several per-chunk
        rows per document. A document with zero stored chunks has no
        `doc_vectors` row, so it degrades through this SAME `row is None`
        branch -- no new failure path, and `graph/proximity.py`'s
        never-raises degrade is preserved by not touching it (vector-store
        spec: A zero-chunk document degrades to no neighbors).

        Results are re-sorted by `(distance, concept_id)` before returning.
        vec0 refuses a secondary sort key in SQL (see `_QUERY_VECTORS_SQL`),
        and its own tie order follows rowid, so without this a rebuild that
        re-inserted rows in a different sequence would hand `graph/proximity`
        a different ordering and silently change the graph projection.

        RESIDUAL LIMIT, deliberately not papered over: this fixes the ORDER
        of the rows vec0 returns, not WHICH rows it returns. The `k` cut
        happens inside the extension, so if more rows tie exactly at the
        `k`-th distance than fit, which of them arrive here is still vec0's
        choice. Fixing that would mean over-fetching by an unbounded amount.
        Exact ties require byte-identical embeddings -- realistically,
        duplicate or template documents -- so the residue is narrow, but it
        is not zero."""
        row = self._conn.execute(_SELECT_DOC_VECTOR_BLOB_SQL, (concept_id,)).fetchone()
        if row is None:
            return []
        rows = self._conn.execute(_QUERY_DOC_VECTORS_SQL, (row[0], k)).fetchall()
        hits = [VecHit(concept_id=str(r[0]), distance=float(r[1])) for r in rows]
        hits.sort(key=lambda hit: (hit.distance, hit.concept_id))
        return hits

    def meta_hashes(self) -> dict[str, str]:
        """Return `{concept_id: content_hash}` for every `vector_meta` row --
        the reindex orchestrator's content-hash cache gate reads this to
        decide which discovered docs are unchanged."""
        rows = self._conn.execute(_SELECT_META_HASHES_SQL).fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def prune(self, concept_id: str) -> None:
        """Remove `concept_id`'s rows from `vectors` (every chunk, #888),
        `doc_vectors`, and `vector_meta`, if present; a `concept_id` with no
        stored row is a no-op, not an error."""
        self._conn.execute(_DELETE_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
        self._conn.execute(_DELETE_DOC_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
        self._conn.execute(_DELETE_VECTOR_META_BY_CONCEPT_ID_SQL, (concept_id,))
        self._conn.commit()

    def prune_many(self, concept_ids: Sequence[str]) -> None:
        """Remove MANY `concept_id`s' rows from `vectors` (every chunk),
        `doc_vectors`, and `vector_meta` in one call (spec: Slice 5,
        follow-up #4 -- single commit per store per run; #888: extended to
        the `doc_vectors` table), reusing `prune`'s own per-item DELETE
        sequence for each id -- WITHOUT committing here; the caller commits
        once via `commit()`."""
        for concept_id in concept_ids:
            self._conn.execute(_DELETE_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
            self._conn.execute(_DELETE_DOC_VECTOR_BY_CONCEPT_ID_SQL, (concept_id,))
            self._conn.execute(_DELETE_VECTOR_META_BY_CONCEPT_ID_SQL, (concept_id,))

    def commit(self) -> None:
        """Commit the current transaction (spec: Slice 5, follow-up #4) --
        pairs with `upsert_many`/`prune_many`, which never commit on their
        own, so a caller can batch an entire run's writes into ONE commit."""
        self._conn.commit()

    def read_model_tag(self) -> str | None:
        """Return the stored `embedding_model` tag from the generic `meta`
        table, or `None` if absent (MVP-2 follow-up #5) -- reads ONLY the
        `meta` table, never `vector_meta` (the content_hash cache), so this
        is completely independent of `meta_hashes()`."""
        row = self._conn.execute(_SELECT_META_SQL, (EMBEDDING_MODEL_KEY,)).fetchone()
        return None if row is None else str(row[0])

    def write_model_tag(self, tag: str) -> None:
        """Upsert `tag` as the stored `embedding_model` value in the generic
        `meta` table (spec: Generic Meta Table -- write replaces prior tag,
        one row survives). Does NOT commit -- callers commit once alongside
        their own writes (Slice 5 single-commit-per-run contract)."""
        self._conn.execute(_UPSERT_META_SQL, (EMBEDDING_MODEL_KEY, tag))

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
