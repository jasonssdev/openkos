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
place staleness is decided.

`stale_derived_stores` (#381) is the read-only ADVISORY counterpart to
`reindex_gate`: same stored row, same comparison, but it never rebuilds and
never writes. It exists because a bundle write that happens outside every refresh path
(a hand edit, a verb whose refresh was skipped or failed) leaves
`fts.db`/`graph.db` behind, and the sole symptom is a quietly worse answer.
Callers use it to SAY so; the decision of what is stale stays here, in one
place.

The D2 binding contract is unchanged by that addition: `retrieval/answer.py`
still never computes or compares a manifest hash. The advisory lives at the
CLI seam that already owns the open-failure-to-`None` decision, so the
answering core keeps treating whatever handle it is given as fresh.
"""

import hashlib
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openkos import fsio
from openkos.model import okf
from openkos.state.readonly import open_read_only
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

SCHEMA_VERSION_KEY = "schema_version"
"""The `meta` key a store records its layout version under (`fts.db` and
`graph.db` both use it)."""

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


@dataclass(frozen=True)
class ManifestEntry:
    """One readable document of a bundle snapshot: its concept ID, the
    content hash of the bytes read, and the on-disk path those bytes came
    from. The path is carried because a concept ID is NFC-normalized and
    cannot always be mapped back to the file's actual (possibly NFD) name."""

    concept_id: str
    content_hash: str
    path: Path


def bundle_manifest_entries(bundle_dir: Path) -> list[ManifestEntry]:
    """Walk `okf._iter_docs(bundle_dir)` once and return one `ManifestEntry`
    per successfully readable document, in walk order.

    A doc with a `read_error`/`parse_error`, or that vanishes between the
    walk and the second `read_bytes` (a TOCTOU guard, mirrors `reindex`'s own
    second-read guard), contributes no entry -- matching its absence from the
    indexes this key gates."""
    entries: list[ManifestEntry] = []
    for scan in okf._iter_docs(bundle_dir):
        concept_id = okf.concept_id_for(scan.path, bundle_dir)
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        try:
            raw_bytes = scan.path.read_bytes()
        except OSError:
            continue
        entries.append(ManifestEntry(concept_id, content_hash(raw_bytes), scan.path))
    return entries


def manifest_digest(pairs: Iterable[tuple[str, str]]) -> str:
    """Return the sha256 hex digest of `(concept_id, content_hash)` pairs:
    sorted, then canonically joined (`f"{concept_id}\\x00{digest}\\n"` per
    pair), so the order the pairs arrive in never affects the result."""
    canonical = "".join(
        f"{concept_id}\x00{digest}\n" for concept_id, digest in sorted(pairs)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def bundle_manifest_hash(bundle_dir: Path) -> str:
    """Return the sha256 hex digest cache key for `bundle_dir`'s current
    document set (derived-index-cache: Bundle-Manifest-Hash Cache Key).

    Every readable doc contributes one `(concept_id, content_hash)` pair
    (see `bundle_manifest_entries`); the digest is order-independent
    (derived-index-cache: Walk order does not affect the manifest hash) --
    ANY added, edited, or removed document changes at least one pair and
    therefore the digest. It is also exactly the digest of a store's recorded
    `doc_manifest` pairs, which is what lets an incremental refresh verify
    that its recorded baseline is trustworthy.
    """
    return manifest_digest(
        (entry.concept_id, entry.content_hash)
        for entry in bundle_manifest_entries(bundle_dir)
    )


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
        fsio.mkdir_private(parent)
        fsio.touch_private(path)
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


def stale_derived_stores(
    bundle_dir: Path,
    stores: Sequence[tuple[str, Path]],
    expected_schema: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return the names of `stores` whose stored `manifest_hash` no longer
    matches `bundle_dir`'s current one, in the order given (#381).

    The read-only advisory twin of `reindex_gate`: identical comparison,
    zero writes, no rebuild. Callers render the returned names, so the order
    is the CALLER's, never discovery's.

    What counts as stale, and what deliberately does not:

    - **Absent store: not reported.** Absence is a different fault, and each
      caller already surfaces it on its own path (`query`'s unavailable
      hint, `status`' missing-`vectors.db` line). Reporting it here too
      would render one fault as two contradictory lines.
    - **No stored hash: reported.** A store predating the `manifest_hash`
      key, or created but never written, cannot PROVE it matches. Fail safe
      -- an index of unknown age is exactly what #381 is about.
    - **Schema version differs: reported (#1333).** `expected_schema` maps a
      store name to the layout version this code writes. A store recorded
      under another version was built by older code -- an older tokenizer,
      say -- and the bundle's manifest hash does not move with that, so the
      hash comparison alone would call it current forever. A store named
      there with no recorded version is stale too; a store not named is
      compared on its manifest hash only.
    - **Unreadable/corrupt store: reported, never raised.** It cannot answer
      the question either, and an advisory must never be what breaks the
      command it advises (mirrors `_open_fts_or_degrade`'s degrade posture).

    Opened `mode=ro` through a URI so the check cannot create the very index
    it was asked about -- `open_derived_connection` lazily CREATES both
    `.openkos/` and the database file, which would turn merely ASKING
    `status` whether the indexes are stale into materializing an empty one.
    The `path.exists()` guard runs first for the same reason, and doubles as
    the short-circuit that skips `bundle_manifest_hash` entirely when no
    store is on disk: with nothing to compare against, the walk buys
    nothing.
    """
    present = [(name, path) for name, path in stores if path.exists()]
    if not present:
        return ()

    current = bundle_manifest_hash(bundle_dir)
    wanted = expected_schema or {}
    stale: list[str] = []
    for name, path in present:
        try:
            conn = open_read_only(path)
            try:
                stored = read_manifest_hash(conn)
                version = (
                    conn.execute(_SELECT_META_SQL, (SCHEMA_VERSION_KEY,)).fetchone()
                    if name in wanted
                    else None
                )
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 -- any unreadable store degrades to "stale"
            stale.append(name)
            continue
        if stored != current or (
            name in wanted and (version is None or str(version[0]) != wanted[name])
        ):
            stale.append(name)
    return tuple(stale)


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
