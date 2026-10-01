"""Per-document FTS refresh (`fts.refresh_fts_index`): one edit rewrites one
document's rows, a store without a trustworthy baseline rebuilds whole, and
every path ends content-identical to a whole rebuild of the same bundle."""

import sqlite3
from pathlib import Path

import pytest

from openkos.state import derived, fts
from tests.unit.conftest import make_locked_error


def _write(bundle: Path, name: str, body: str, *, title: str | None = None) -> Path:
    path = bundle / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title or name}\ntags: [t1, t2]\n---\n{body}",
        encoding="utf-8",
    )
    return path


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    for i in range(5):
        _write(bundle, f"concepts/doc{i}", f"body number {i} alpha{i}")
    return bundle


def _query(db: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        return sorted(conn.execute(sql).fetchall())
    finally:
        conn.close()


def _state(db: Path) -> dict[str, object]:
    """Everything a rebuild is supposed to determine, minus rowids."""
    return {
        "docs": _query(
            db, "SELECT concept_id, title, description, tags, body FROM docs"
        ),
        "manifest": _query(db, "SELECT concept_id, content_hash FROM doc_manifest"),
        "meta": _query(db, "SELECT key, value FROM meta"),
    }


def _rowids(db: Path) -> dict[str, int]:
    return {
        str(c): int(str(r)) for c, r in _query(db, "SELECT concept_id, rowid FROM docs")
    }


def _assert_equals_rebuild(db: Path, bundle: Path, tmp_path: Path) -> None:
    reference = tmp_path / "reference" / "fts.db"
    reference.unlink(missing_ok=True)
    fts.write_fts_index(reference, bundle)
    assert _state(db) == _state(reference)


def test_unchanged_bundle_writes_nothing(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    assert fts.refresh_fts_index(db, bundle) == "rebuilt"  # first build
    before = _rowids(db)
    assert fts.refresh_fts_index(db, bundle) == "unchanged"
    assert _rowids(db) == before


def test_one_edit_rewrites_one_documents_rows(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    before = _rowids(db)

    _write(bundle, "concepts/doc2", "completely new text zebra")

    assert fts.refresh_fts_index(db, bundle) == "incremental"
    after = _rowids(db)
    untouched = {cid for cid in before if cid != "concepts/doc2"}
    assert {cid: after[cid] for cid in untouched} == {
        cid: before[cid] for cid in untouched
    }
    assert after["concepts/doc2"] != before["concepts/doc2"]
    idx = fts.open_fts_index_readonly(db)
    assert idx is not None
    with idx:
        assert [h.concept_id for h in idx.search("zebra")] == ["concepts/doc2"]
        assert idx.search("alpha2") == []
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_add_change_remove_sequence_matches_a_rebuild_at_every_step(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)

    def add() -> None:
        _write(bundle, "concepts/new", "freshly added kiwi")

    def change() -> None:
        _write(bundle, "concepts/doc0", "edited mango", title="Renamed")

    def remove() -> None:
        (bundle / "concepts/doc1.md").unlink()

    def mixed() -> None:
        _write(bundle, "concepts/new", "edited again papaya")
        (bundle / "concepts/doc3.md").unlink()
        _write(bundle, "other/x", "nested plum")

    for step in (add, change, remove, mixed):
        step()
        assert fts.refresh_fts_index(db, bundle) == "incremental"
        stored = [
            v
            for k, v in _query(db, "SELECT key, value FROM meta")
            if k == derived.MANIFEST_HASH_KEY
        ]
        assert stored == [derived.bundle_manifest_hash(bundle)]
        _assert_equals_rebuild(db, bundle, tmp_path)


def test_document_turning_unparseable_is_removed_like_a_rebuild(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    (bundle / "concepts/doc1.md").write_text("---\n: : [broken\n---\nx", "utf-8")

    assert fts.refresh_fts_index(db, bundle) == "incremental"
    assert "concepts/doc1" not in _rowids(db)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_pre_change_store_rebuilds_once_then_goes_incremental(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    conn = sqlite3.connect(db)  # downgrade to the pre-change layout
    conn.execute("DROP TABLE doc_manifest")
    conn.execute("DELETE FROM meta WHERE key = ?", (fts.SCHEMA_VERSION_KEY,))
    conn.commit()
    conn.close()

    _write(bundle, "concepts/doc0", "first edit")
    assert fts.refresh_fts_index(db, bundle) == "rebuilt"
    _assert_equals_rebuild(db, bundle, tmp_path)

    _write(bundle, "concepts/doc0", "second edit")
    assert fts.refresh_fts_index(db, bundle) == "incremental"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_manifest_mismatch_rebuilds_and_repairs(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE doc_manifest SET content_hash = 'bogus' WHERE rowid = 1")
    conn.commit()
    conn.close()

    _write(bundle, "concepts/doc4", "edit")
    assert fts.refresh_fts_index(db, bundle) == "rebuilt"
    _assert_equals_rebuild(db, bundle, tmp_path)
    _write(bundle, "concepts/doc4", "edit again")
    assert fts.refresh_fts_index(db, bundle) == "incremental"


def test_schema_version_change_rebuilds(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = '0' WHERE key = ?", (fts.SCHEMA_VERSION_KEY,))
    conn.commit()
    conn.close()

    _write(bundle, "concepts/doc4", "edit")
    assert fts.refresh_fts_index(db, bundle) == "rebuilt"
    versions = [
        v
        for k, v in _query(db, "SELECT key, value FROM meta")
        if k == fts.SCHEMA_VERSION_KEY
    ]
    assert versions == [fts.SCHEMA_VERSION]
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_recorded_pairs_reproduce_the_manifest_hash(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.write_fts_index(db, bundle)
    pairs = [
        (str(c), str(h))
        for c, h in _query(db, "SELECT concept_id, content_hash FROM doc_manifest")
    ]
    conn = sqlite3.connect(db)
    stored = derived.read_manifest_hash(conn)
    conn.close()
    assert len(pairs) == 5
    assert derived.manifest_digest(pairs) == stored


def test_force_rebuilds_even_when_unchanged(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    assert fts.refresh_fts_index(db, bundle, force=True) == "rebuilt"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_failed_incremental_rolls_back_and_falls_back_to_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    _write(bundle, "concepts/doc0", "edit one")
    _write(bundle, "concepts/doc1", "edit two")

    real_row = fts._doc_row
    calls: list[str] = []

    def flaky(path: Path, concept_id: str) -> object:
        calls.append(concept_id)
        if len(calls) == 2:  # fail midway through the per-document update
            raise RuntimeError("boom")
        return real_row(path, concept_id)

    monkeypatch.setattr(fts, "_doc_row", flaky)
    assert fts.refresh_fts_index(db, bundle) == "rebuilt"
    assert len(calls) > 2  # the update really started, then the rebuild ran
    monkeypatch.setattr(fts, "_doc_row", real_row)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_lock_contention_during_incremental_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    _write(bundle, "concepts/doc0", "edit")
    before = _state(db)

    def locked(*_a: object, **_k: object) -> None:
        raise make_locked_error()

    monkeypatch.setattr(fts, "_doc_row", locked)
    with pytest.raises(sqlite3.OperationalError):
        fts.refresh_fts_index(db, bundle)
    assert _state(db) == before


def test_empty_bundle_then_first_document_is_incremental(tmp_path: Path) -> None:
    bundle, db = tmp_path / "bundle", tmp_path / ".openkos" / "fts.db"
    bundle.mkdir()
    assert fts.refresh_fts_index(db, bundle) == "rebuilt"
    _write(bundle, "concepts/a", "hello")
    assert fts.refresh_fts_index(db, bundle) == "incremental"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_row_no_document_accounts_for_forces_a_rebuild(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "fts.db"
    fts.refresh_fts_index(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO docs (concept_id, title, description, tags, body) "
        "VALUES ('ghost', '', '', '', 'ghost')"
    )
    conn.commit()
    conn.close()

    _write(bundle, "concepts/doc0", "edit")
    assert fts.refresh_fts_index(db, bundle) == "rebuilt"
    assert "ghost" not in _rowids(db)
    _assert_equals_rebuild(db, bundle, tmp_path)
