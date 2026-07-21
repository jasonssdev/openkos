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
