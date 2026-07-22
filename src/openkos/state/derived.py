"""Shared derived-index-store infrastructure: bundle manifest hashing and the
WAL/busy_timeout on-disk connection opener every persisted derived store
(`fts.db`, `graph.db`, ...) reuses.

`bundle_manifest_hash` is the cache key `reindex` computes and compares --
and ONLY `reindex` computes it (derived-index-cache spec, D2): a digest over
the sorted set of `(concept_id, content_hash)` pairs for every successfully
readable document in the bundle, reusing the shipped
`vectorstore.content_hash` primitive. Sorting by `concept_id` before joining
makes the digest independent of `okf._iter_docs`'s on-disk walk order --
the same document set always hashes identically regardless of discovery
order. A doc that fails to read/parse (mirrors `fts.build_index`/
`reindex`'s own degrade-not-crash posture) simply contributes no pair,
exactly as it contributes no row/vector to the indexes this cache key gates.

`open_derived_connection` mirrors `vectorstore.open_vector_store`'s
lazy-create-on-success / single-level-cleanup-on-failure posture: `.openkos/`
is created ONLY once the open genuinely succeeds, and any failure (a
`path.parent.mkdir` error, a `connect` failure, or a DDL error) leaves no new
on-disk footprint -- only artifacts THIS call created are removed. Unlike
`open_vector_store`, this opener sets `PRAGMA journal_mode=WAL` and a
`busy_timeout` (reindex-command: WAL/busy-timeout PRAGMAs) and creates a
generic `meta(key, value)` table rather than a domain-specific schema --
`fts.py`/`graph/sqlite_graph.py` layer their own tables on top of the SAME
connection this returns. The `manifest_hash` row in that table is the ONLY
place staleness is gated; query-time code never calls
`bundle_manifest_hash` at all.
"""

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from openkos.model import okf
from openkos.state.vectorstore import content_hash

_BUSY_TIMEOUT_MS = 5000
"""Busy-timeout (milliseconds) set on every derived on-disk connection --
gives a concurrent writer/reader up to 5s to retry against SQLite's `SQLITE_BUSY`
before raising, reducing contention among the on-disk derived stores."""

MANIFEST_HASH_KEY = "manifest_hash"
"""The `meta.key` reindex reads/writes to gate whole-index rebuild."""

_CREATE_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_SELECT_META_SQL = "SELECT value FROM meta WHERE key = ?"

_UPSERT_META_SQL = "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)"


def is_lock_contention(exc: sqlite3.OperationalError) -> bool:
    """Return `True` when `exc` represents a SQLite lock-contention failure
    (`SQLITE_BUSY`/`SQLITE_LOCKED`), discriminated by `exc.sqlite_errorcode`
    -- NOT by matching the exception's message text, which is fragile and
    would silently break on a SQLite build/locale that phrases the message
    differently (reindex-lock-handling design, decision 1).

    `sqlite_errorcode` is populated by the `sqlite3` module on the errors IT
    raises; on Python 3.13 it is also reliably settable on a manually
    constructed `OperationalError` (confirmed by
    `tests/unit/state/test_derived.py`'s spike test), which is how tests
    inject a lock-contention failure at any write surface without a real
    concurrent second connection holding the lock.

    A locked `vectors.db`/`fts.db`/`graph.db` write raises exactly this
    shape; every reindex write surface (`cli/main.py`'s two error ladders)
    and `state/fts.py`'s `CREATE VIRTUAL TABLE` catch reuse this ONE
    predicate so a locked store is never misclassified (e.g. as
    `FtsUnavailable`) and always gets the SAME uniform retry message.

    Reads `sqlite_errorcode` via `getattr` (default `None`, never
    `SQLITE_BUSY`/`SQLITE_LOCKED`) rather than direct attribute access: a
    manually-`raise`d `OperationalError` that never went through the real
    `sqlite3` driver (e.g. a test double simulating a DIFFERENT failure,
    like `tests/unit/state/test_fts.py`'s no-fts5-module connection) has no
    `sqlite_errorcode` attribute at all, and must degrade to `False` rather
    than raising `AttributeError` here."""
    return getattr(exc, "sqlite_errorcode", None) in (
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    )


def bundle_manifest_hash(bundle_dir: Path) -> str:
    """Return the sha256 hex digest cache key for `bundle_dir`'s current
    document set (derived-index-cache: Bundle-Manifest-Hash Cache Key).

    Walks `okf._iter_docs(bundle_dir)` once (the SAME walk `fts.build_index`/
    `reindex` use), skipping any doc with a `read_error`/`parse_error` or
    that vanishes between the walk and this second `read_bytes` (a TOCTOU
    guard, mirrors `reindex`'s own second-read guard) -- such a doc
    contributes no pair, matching its absence from the indexes this key
    gates. Every remaining doc contributes one `(concept_id, content_hash)`
    pair; the pairs are SORTED before being canonically joined
    (`f"{concept_id}\\x00{digest}\\n"` per pair) and hashed, so the walk's
    on-disk discovery order never affects the result (derived-index-cache:
    Walk order does not affect the manifest hash) -- ANY added, edited, or
    removed document changes at least one pair and therefore the digest.
    """
    pairs: list[tuple[str, str]] = []
    for scan in okf._iter_docs(bundle_dir):
        concept_id = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        try:
            raw_bytes = scan.path.read_bytes()
        except OSError:
            continue
        pairs.append((concept_id, content_hash(raw_bytes)))

    canonical = "".join(
        f"{concept_id}\x00{digest}\n" for concept_id, digest in sorted(pairs)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def open_derived_connection(
    path: Path,
    *,
    connect: Callable[[str], sqlite3.Connection] = sqlite3.connect,
) -> sqlite3.Connection:
    """Open (creating if needed) the derived-store database at `path`.

    Mirrors `vectorstore.open_vector_store`'s lazy-create/cleanup contract:
    `.openkos/` is created ONLY on a successful open, and ANY failure (a
    `mkdir` error, a `connect` failure, or a PRAGMA/DDL error) leaves no new
    on-disk footprint -- only artifacts THIS call created are removed before
    the exception is re-raised; a pre-existing parent directory or database
    file is never touched. On success, sets `PRAGMA journal_mode=WAL` and a
    `busy_timeout` of `_BUSY_TIMEOUT_MS` (reindex-command: WAL/busy-timeout
    PRAGMAs active on every derived connection), then creates the shared
    `meta(key, value)` table idempotently and commits once. Callers layer
    their own domain-specific tables on top of the returned connection.
    """
    parent = path.parent
    parent_preexisted = parent.exists()
    db_preexisted = path.exists()
    conn: sqlite3.Connection | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        conn = connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
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
    return conn


def read_manifest_hash(conn: sqlite3.Connection) -> str | None:
    """Return the stored `manifest_hash` meta value, or `None` if absent
    (a freshly created store, or one from before this slice)."""
    row = conn.execute(_SELECT_META_SQL, (MANIFEST_HASH_KEY,)).fetchone()
    return None if row is None else str(row[0])


def write_manifest_hash(conn: sqlite3.Connection, digest: str) -> None:
    """Upsert `digest` as the stored `manifest_hash` meta value. Does NOT
    commit -- callers commit once alongside their own writes (reindex-command:
    single commit per store per run)."""
    conn.execute(_UPSERT_META_SQL, (MANIFEST_HASH_KEY, digest))


class DerivedStoreWriter(Protocol):
    """The shape `reindex_gate` calls on a manifest mismatch (or `force`):
    every persisted derived store's writer (`fts.write_fts_index`,
    `sqlite_graph.write_graph_store`) satisfies this structurally."""

    def __call__(
        self, path: Path, bundle_dir: Path, *, manifest_hash: str | None = None
    ) -> None:
        """Fully rebuild the store at `path` for `bundle_dir`, storing
        `manifest_hash` verbatim (never recomputing it) when given."""
        ...  # pragma: no cover -- Protocol stub body, never executed


def reindex_gate(
    bundle_dir: Path, db_path: Path, *, force: bool, write: DerivedStoreWriter
) -> None:
    """Shared manifest-gate-and-rebuild helper reused by every persisted
    derived store's reindex orchestration (fts-state/graph-projection alike)
    -- extracted so `state/reindex.py`'s FTS gate and `graph/sqlite_graph.py`'s
    graph gate share ONE implementation instead of two near-identical copies
    (review carry-over: task 2.11 REFACTOR).

    Reads the PREVIOUSLY stored `meta.manifest_hash` at `db_path` (lazily
    creating `.openkos/` on first call, mirroring `open_vector_store`), then
    computes the bundle's CURRENT manifest hash via `bundle_manifest_hash` --
    this comparison is the ONLY place staleness is decided anywhere in the
    system (D2 binding contract): a match (and no `force`) skips the write
    entirely; a mismatch (or absent stored hash, or `force`) calls
    `write(db_path, bundle_dir, manifest_hash=new_manifest)` -- the SAME
    digest computed here for the decision, so it is never recomputed a
    second/third time inside `write` (review correction carried over from
    PR1's Finding C: triple-walk/TOCTOU).

    Deliberately store-agnostic: lives in `state/derived.py` (canonical
    layer) rather than `state/reindex.py`, so BOTH the canonical-layer FTS
    gate and the derived-layer (`openkos.graph`) graph gate can import and
    reuse it without `state/reindex.py` ever importing `openkos.graph` --
    canonical must never depend on derived (docs/architecture.md); derived
    depending on canonical (this module) is the allowed direction.
    """
    conn = open_derived_connection(db_path)
    try:
        current_manifest = read_manifest_hash(conn)
    finally:
        conn.close()

    new_manifest = bundle_manifest_hash(bundle_dir)
    if force or current_manifest != new_manifest:
        write(db_path, bundle_dir, manifest_hash=new_manifest)
