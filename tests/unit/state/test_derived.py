"""Unit tests for `state/derived.py`: shared derived-store infrastructure.

`state/derived.py` provides the two primitives every persisted derived index
(`fts.db`, `graph.db`, ...) reuses: `bundle_manifest_hash` (the sha256 cache
key over a bundle's discovered `(concept_id, content_hash)` pairs, sorted for
order-stability) and `open_derived_connection` (a WAL/busy_timeout on-disk
opener mirroring `vectorstore.open_vector_store`'s lazy-create/cleanup
posture, plus the shared `meta(key, value)` table DDL every derived store's
manifest-hash row lives in).
"""

import sqlite3
from pathlib import Path

import pytest

from openkos.state import derived


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    body: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\ndescription: ''\n---\n{body}",
        encoding="utf-8",
    )


# --- bundle_manifest_hash -----------------------------------------------


def test_manifest_hash_is_order_stable_across_two_discovery_orders(
    tmp_path: Path,
) -> None:
    """The digest is identical for the same document SET regardless of
    on-disk discovery/insertion order (derived-index-cache: Walk order does
    not affect the manifest hash)."""
    first_dir = tmp_path / "first"
    _write_doc(first_dir / "concepts" / "aardvark.md", title="Aardvark", body="a")
    _write_doc(first_dir / "concepts" / "zebra.md", title="Zebra", body="z")

    second_dir = tmp_path / "second"
    # Same documents, written to disk in the OPPOSITE name order -- proves the
    # hash sorts before hashing rather than depending on `rglob`'s walk order.
    _write_doc(second_dir / "concepts" / "zebra.md", title="Zebra", body="z")
    _write_doc(second_dir / "concepts" / "aardvark.md", title="Aardvark", body="a")

    assert derived.bundle_manifest_hash(first_dir) == derived.bundle_manifest_hash(
        second_dir
    )


def test_manifest_hash_changes_when_a_document_is_edited(tmp_path: Path) -> None:
    """A single document's content change is enough to change the digest
    (derived-index-cache: Any document change invalidates the cache)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "stoicism.md"
    _write_doc(doc_path, title="Stoicism", body="version one")

    before = derived.bundle_manifest_hash(bundle_dir)
    _write_doc(doc_path, title="Stoicism", body="version two")
    after = derived.bundle_manifest_hash(bundle_dir)

    assert before != after


def test_manifest_hash_of_empty_bundle_is_deterministic(tmp_path: Path) -> None:
    """An empty bundle still produces a stable digest -- no documents means
    an empty pair set, not an error."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert derived.bundle_manifest_hash(bundle_dir) == derived.bundle_manifest_hash(
        bundle_dir
    )


# --- open_derived_connection ---------------------------------------------


def test_open_derived_connection_sets_wal_and_busy_timeout_and_creates_meta_table(
    tmp_path: Path,
) -> None:
    """The opened connection has `journal_mode=WAL`, a non-zero
    `busy_timeout`, and an idempotent `meta(key, value)` table (reindex-command:
    WAL mode is active on every derived connection)."""
    db_path = tmp_path / ".openkos" / "fts.db"

    conn = derived.open_derived_connection(db_path)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
        conn.commit()
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    finally:
        conn.close()

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) > 0
    assert rows == [("k", "v")]


def test_open_derived_connection_lazy_creates_parent_dir_only_on_success(
    tmp_path: Path,
) -> None:
    """`.openkos/` is created lazily, only once the open genuinely succeeds
    -- mirrors `open_vector_store`'s lazy-create posture."""
    parent = tmp_path / ".openkos"
    db_path = parent / "fts.db"
    assert not parent.exists()

    conn = derived.open_derived_connection(db_path)
    conn.close()

    assert parent.is_dir()
    assert db_path.exists()


def test_open_derived_connection_leaves_no_new_footprint_on_failure(
    tmp_path: Path,
) -> None:
    """A failure after the probe (e.g. a bad `connect` factory) leaves no new
    `.openkos/`/db footprint -- mirrors `open_vector_store`'s no-new-footprint
    guarantee on failure."""
    parent = tmp_path / ".openkos"
    db_path = parent / "fts.db"

    def failing_connect(path: str) -> sqlite3.Connection:
        raise sqlite3.OperationalError("simulated connect failure")

    with pytest.raises(sqlite3.OperationalError):
        derived.open_derived_connection(db_path, connect=failing_connect)

    assert not parent.exists()
    assert not db_path.exists()


# --- reindex_gate (Slice 5, PR2 REFACTOR: shared FTS+graph gate helper) -----


def test_reindex_gate_writes_on_first_call_with_no_stored_manifest(
    tmp_path: Path,
) -> None:
    """A store with no stored `manifest_hash` (first call ever) always
    triggers a write, passing the freshly computed digest through."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    db_path = tmp_path / ".openkos" / "stub.db"
    calls: list[tuple[Path, Path, str | None]] = []

    def _fake_write(
        path: Path, bundle_dir: Path, *, manifest_hash: str | None = None
    ) -> None:
        calls.append((path, bundle_dir, manifest_hash))

    derived.reindex_gate(bundle_dir, db_path, force=False, write=_fake_write)

    assert len(calls) == 1
    written_path, written_bundle_dir, written_digest = calls[0]
    assert written_path == db_path
    assert written_bundle_dir == bundle_dir
    assert written_digest == derived.bundle_manifest_hash(bundle_dir)


def test_reindex_gate_skips_write_when_manifest_unchanged(tmp_path: Path) -> None:
    """A stored `manifest_hash` matching the bundle's current digest skips
    the write entirely (derived-index-cache: Unchanged bundle reuses the
    cached index)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    db_path = tmp_path / ".openkos" / "stub.db"
    conn = derived.open_derived_connection(db_path)
    derived.write_manifest_hash(conn, derived.bundle_manifest_hash(bundle_dir))
    conn.commit()
    conn.close()
    calls: list[object] = []

    def _fake_write(
        path: Path, bundle_dir: Path, *, manifest_hash: str | None = None
    ) -> None:
        calls.append((path, bundle_dir, manifest_hash))

    derived.reindex_gate(bundle_dir, db_path, force=False, write=_fake_write)

    assert calls == []


def test_reindex_gate_writes_when_manifest_changed(tmp_path: Path) -> None:
    """A stored `manifest_hash` that no longer matches the bundle's current
    digest triggers a write (derived-index-cache: Any document change
    invalidates the cache)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism", body="v1")
    db_path = tmp_path / ".openkos" / "stub.db"
    conn = derived.open_derived_connection(db_path)
    derived.write_manifest_hash(conn, derived.bundle_manifest_hash(bundle_dir))
    conn.commit()
    conn.close()
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism", body="v2")
    calls: list[object] = []

    def _fake_write(
        path: Path, bundle_dir: Path, *, manifest_hash: str | None = None
    ) -> None:
        calls.append((path, bundle_dir, manifest_hash))

    derived.reindex_gate(bundle_dir, db_path, force=False, write=_fake_write)

    assert len(calls) == 1


def test_reindex_gate_force_writes_even_when_manifest_unchanged(
    tmp_path: Path,
) -> None:
    """`force=True` writes even when the stored manifest already matches."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    db_path = tmp_path / ".openkos" / "stub.db"
    conn = derived.open_derived_connection(db_path)
    derived.write_manifest_hash(conn, derived.bundle_manifest_hash(bundle_dir))
    conn.commit()
    conn.close()
    calls: list[object] = []

    def _fake_write(
        path: Path, bundle_dir: Path, *, manifest_hash: str | None = None
    ) -> None:
        calls.append((path, bundle_dir, manifest_hash))

    derived.reindex_gate(bundle_dir, db_path, force=True, write=_fake_write)

    assert len(calls) == 1


# --- is_lock_contention (reindex-lock-handling) -----------------------------


def test_sqlite_operational_error_supports_manual_errorcode_assignment() -> None:
    """Spike/decision test (task 1.1): confirms `sqlite_errorcode` is
    settable and readable on a MANUALLY constructed `sqlite3.OperationalError`
    on this interpreter (Python 3.13) -- this is what lets tests inject a
    lock-contention failure at any write surface without a real concurrent
    second connection. If this assumption ever regresses on a future
    interpreter, the documented fallback is a real 2nd-connection `BEGIN
    IMMEDIATE` + `busy_timeout=0` competing-lock scenario (design:
    sdd/reindex-lock-handling)."""
    exc = sqlite3.OperationalError("database is locked")
    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY

    assert exc.sqlite_errorcode == sqlite3.SQLITE_BUSY


def test_is_lock_contention_true_for_sqlite_busy() -> None:
    """`is_lock_contention` is `True` for `SQLITE_BUSY` (spec:
    reindex-command -- lock discriminated by errorcode, not message text)."""
    exc = sqlite3.OperationalError("database is locked")
    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY

    assert derived.is_lock_contention(exc) is True


def test_is_lock_contention_true_for_sqlite_locked() -> None:
    """`is_lock_contention` is `True` for `SQLITE_LOCKED` (a table-level
    lock, distinct from `SQLITE_BUSY`'s database-level lock -- both count
    as lock contention)."""
    exc = sqlite3.OperationalError("database table is locked")
    exc.sqlite_errorcode = sqlite3.SQLITE_LOCKED

    assert derived.is_lock_contention(exc) is True


def test_is_lock_contention_false_for_a_non_lock_operational_error() -> None:
    """A non-lock `OperationalError` (e.g. `SQLITE_ERROR`, the generic
    catch-all SQLite raises for something like a missing fts5 module) is
    NOT lock contention -- discrimination must be by errorcode, never by
    message substring (spec: A non-lock operational error is not
    mislabeled as lock contention)."""
    exc = sqlite3.OperationalError("no such module: fts5")
    exc.sqlite_errorcode = sqlite3.SQLITE_ERROR

    assert derived.is_lock_contention(exc) is False


def test_is_lock_contention_false_when_errorcode_was_never_set() -> None:
    """A manually-`raise`d `OperationalError` that never went through the
    real `sqlite3` driver (e.g. a test double simulating an unrelated
    failure) has no `sqlite_errorcode` attribute at all -- `is_lock_contention`
    degrades to `False` rather than raising `AttributeError`."""
    exc = sqlite3.OperationalError("some other failure")

    assert derived.is_lock_contention(exc) is False


# --- stale_derived_stores (#381) ----------------------------------------


def _seed_store(path: Path, digest: str | None) -> None:
    """Create a derived store at `path`, optionally stamping `digest` as its
    stored manifest hash."""
    conn = derived.open_derived_connection(path)
    try:
        if digest is not None:
            derived.write_manifest_hash(conn, digest)
            conn.commit()
    finally:
        conn.close()


def test_stale_reports_a_store_whose_stored_hash_no_longer_matches(
    tmp_path: Path,
) -> None:
    """The whole point of #381: a bundle edited after the last reindex leaves
    the store's stored digest behind, and that disagreement is what
    `stale_derived_stores` names."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A", body="one")
    db = tmp_path / ".openkos" / "fts.db"
    _seed_store(db, derived.bundle_manifest_hash(bundle))

    _write_doc(bundle / "concepts" / "a.md", title="A", body="two")

    assert derived.stale_derived_stores(bundle, (("fts", db),)) == ("fts",)


def test_stale_reports_nothing_when_the_stored_hash_still_matches(
    tmp_path: Path,
) -> None:
    """A store written by the last reindex over an unchanged bundle is fresh,
    and a fresh store must produce no warning anywhere."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A", body="one")
    db = tmp_path / ".openkos" / "fts.db"
    _seed_store(db, derived.bundle_manifest_hash(bundle))

    assert derived.stale_derived_stores(bundle, (("fts", db),)) == ()


def test_stale_ignores_a_store_that_does_not_exist(tmp_path: Path) -> None:
    """Absence is NOT staleness. A missing store is a different condition,
    already surfaced by each caller's own degrade path (`query`'s
    unavailable hint, `status`' missing-vectors line) -- reporting it here
    too would double-report one fault as two."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A", body="one")

    assert derived.stale_derived_stores(bundle, (("fts", tmp_path / "gone.db"),)) == ()


def test_stale_never_creates_the_store_it_was_asked_about(tmp_path: Path) -> None:
    """A read-only check must leave no footprint. `open_derived_connection`
    lazily CREATES both `.openkos/` and the database file, so the existence
    guard has to run before it -- otherwise merely asking `status` whether
    the indexes are stale would materialize an empty index."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A", body="one")
    absent = tmp_path / ".openkos" / "fts.db"

    derived.stale_derived_stores(bundle, (("fts", absent),))

    assert not absent.exists()
    assert not absent.parent.exists()


def test_stale_reports_a_store_that_has_no_stored_hash_at_all(
    tmp_path: Path,
) -> None:
    """A store predating the manifest key, or created but never written,
    cannot PROVE it matches the bundle. Fail safe: report it, so the user is
    told to reindex rather than silently trusting an index of unknown age."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A", body="one")
    db = tmp_path / ".openkos" / "fts.db"
    _seed_store(db, None)

    assert derived.stale_derived_stores(bundle, (("fts", db),)) == ("fts",)


def test_stale_reports_multiple_stores_in_the_order_given(tmp_path: Path) -> None:
    """Callers render these names in a message, so the order must be the
    caller's, not discovery's."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A", body="one")
    fts_db = tmp_path / ".openkos" / "fts.db"
    graph_db = tmp_path / ".openkos" / "graph.db"
    _seed_store(fts_db, "stale-digest")
    _seed_store(graph_db, "stale-digest")

    assert derived.stale_derived_stores(
        bundle, (("graph", graph_db), ("fts", fts_db))
    ) == ("graph", "fts")


def test_stale_reports_an_unreadable_store_rather_than_raising(
    tmp_path: Path,
) -> None:
    """A corrupt store cannot answer the question either, and an advisory
    check must never be what breaks the command it advises. Degrade to
    "stale", mirroring `_open_fts_or_degrade`'s posture."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A", body="one")
    db = tmp_path / ".openkos" / "fts.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not a sqlite database")

    assert derived.stale_derived_stores(bundle, (("fts", db),)) == ("fts",)


def test_stale_skips_the_bundle_walk_when_no_store_exists(tmp_path: Path) -> None:
    """With nothing on disk to compare against, the manifest walk buys
    nothing. Proven by pointing the check at a bundle whose walk would raise
    if it were attempted."""
    db = tmp_path / ".openkos" / "fts.db"

    assert (
        derived.stale_derived_stores(tmp_path / "no-such-bundle", (("fts", db),)) == ()
    )
