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
