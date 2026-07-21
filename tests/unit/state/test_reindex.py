"""Unit tests for `state/reindex.py`: the vector-store backfill orchestrator.

`reindex()` walks a bundle via `okf._iter_docs` (mirrors `fts.build_index`'s
walk), keys each doc by the same `concept_id` `forget`/`FtsHit`/`Citation`
use, embeds through the injected `Embedder` seam, and upserts into an
injected `VectorStore` -- gated by a `content_hash` cache so an unchanged doc
never triggers an `embed()` call. Every test here is hermetic: a fake
`Embedder` (call-counting, no network) and a real `VectorStoreDB` opened
against a `tmp_path` database (no Ollama, no CLI).
"""

import os
import sqlite3
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.llm.base import EMBED_DIM
from openkos.state import derived, fts, reindex, vectorstore


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


class _FakeEmbedder:
    """A call-counting fake `Embedder`: declares `Sequence[str]` verbatim
    (Engram #1363 -- a `list[str]` parameter would silently narrow the
    Protocol and mypy's structural check would catch the drift)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i)] * EMBED_DIM for i, _ in enumerate(texts)]

    @property
    def call_count(self) -> int:
        """Total number of `embed()` invocations (not total texts)."""
        return len(self.calls)

    @property
    def embedded_texts(self) -> list[str]:
        """Every text ever passed to `embed()`, flattened, call order
        preserved."""
        return [text for call in self.calls for text in call]


# --- Phase 4: bundle walk + concept identity + reserved-file exclusion -----


def test_reindex_discovered_docs_concept_id_matches_forgets_identity(
    tmp_path: Path,
) -> None:
    """A doc at `bundle/concepts/stoicism.md` is keyed by `concepts/stoicism`
    -- the same identity `forget`/`FtsHit`/`Citation` use (spec: Discovered
    doc's identity matches forget's identity)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()

    assert set(hashes) == {"concepts/stoicism"}


def test_reindex_reserved_files_are_never_embedded_or_upserted(
    tmp_path: Path,
) -> None:
    """`index.md`/`log.md` are excluded, mirroring `fts.build_index` (spec:
    Reserved files are never embedded)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text("# index\n", encoding="utf-8")
    (bundle_dir / "log.md").write_text("# log\n", encoding="utf-8")
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()

    assert set(hashes) == {"concepts/stoicism"}
    assert report.embedded == 1
    assert not any("index" in text or "log" in text for text in embedder.embedded_texts)


# --- Phase 4: content-hash cache gate ---------------------------------------


def test_reindex_unchanged_content_hash_is_cache_hit_with_zero_embed_calls(
    tmp_path: Path,
) -> None:
    """A doc whose stored `content_hash` matches its current on-disk hash is
    a cache-hit: zero `Embedder` calls, its stored vector unchanged (spec:
    Unchanged content_hash is a cache-hit with zero Ollama calls)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "stoicism.md"
    _write_doc(doc_path, title="Stoicism")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        first_embedder = _FakeEmbedder()
        reindex.reindex(bundle_dir, db, first_embedder)
        hashes_before = db.meta_hashes()

        second_embedder = _FakeEmbedder()
        report = reindex.reindex(bundle_dir, db, second_embedder)
        hashes_after = db.meta_hashes()

    assert second_embedder.call_count == 0
    assert report.cache_hits == 1
    assert report.embedded == 0
    assert hashes_after == hashes_before


def test_reindex_changed_content_reembeds_and_upserts(tmp_path: Path) -> None:
    """A doc whose current hash differs from `vector_meta` is re-embedded
    and re-upserted (spec: Changed content re-embeds and upserts)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "stoicism.md"
    _write_doc(doc_path, title="Stoicism", body="version one")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hash_before = db.meta_hashes()["concepts/stoicism"]

        _write_doc(doc_path, title="Stoicism", body="version two")
        embedder = _FakeEmbedder()
        report = reindex.reindex(bundle_dir, db, embedder)
        hash_after = db.meta_hashes()["concepts/stoicism"]

    assert embedder.call_count == 1
    assert report.embedded == 1
    assert report.cache_hits == 0
    assert hash_after != hash_before


def test_reindex_new_doc_with_no_vector_meta_row_is_embedded_and_inserted(
    tmp_path: Path,
) -> None:
    """A doc with no `vector_meta` row is embedded, its vector inserted, and
    `vector_meta` gains a row (spec: New doc is embedded and upserted)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        embedder = _FakeEmbedder()
        report = reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()

    assert embedder.call_count == 1
    assert report.embedded == 1
    assert set(hashes) == {"concepts/a"}


# --- Phase 4: prune ----------------------------------------------------------


def test_reindex_prunes_concept_removed_from_disk(tmp_path: Path) -> None:
    """A `vector_meta` row for a concept whose file was deleted from the
    bundle is pruned from both `vectors` and `vector_meta` (spec: Deleted
    doc is pruned from the store)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "gone.md"
    _write_doc(doc_path, title="Gone")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())
        assert "concepts/gone" in db.meta_hashes()

        doc_path.unlink()
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hashes = db.meta_hashes()
        vector_rows = db._conn.execute("SELECT concept_id FROM vectors").fetchall()

    assert "concepts/gone" not in hashes
    assert vector_rows == []
    assert report.pruned == 1


def test_reindex_does_not_prune_concepts_still_present_on_disk(
    tmp_path: Path,
) -> None:
    """A concept whose file still exists is never pruned, even across
    multiple `reindex` runs."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hashes = db.meta_hashes()

    assert report.pruned == 0
    assert set(hashes) == {"concepts/a"}
    assert report.prune_skipped is False


# --- Phase 4: --force --------------------------------------------------------


def test_reindex_force_reembeds_every_doc_even_when_hashes_match(
    tmp_path: Path,
) -> None:
    """`force=True` re-embeds and upserts every discovered document
    regardless of a matching `content_hash` (spec: --force re-embeds
    unchanged docs)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())

        embedder = _FakeEmbedder()
        report = reindex.reindex(bundle_dir, db, embedder, force=True)

    assert report.embedded == 2
    assert report.cache_hits == 0
    assert len(embedder.embedded_texts) == 2


# --- Phase 4: ReindexReport --------------------------------------------------


def test_reindex_report_totals_reflect_a_mixed_run(tmp_path: Path) -> None:
    """One `reindex()` call's `ReindexReport` accounts for every discovered
    doc: embedded (new), cache-hit (unchanged), and pruned (removed) --
    all in the same run."""
    bundle_dir = tmp_path / "bundle"
    unchanged_path = bundle_dir / "concepts" / "unchanged.md"
    removed_path = bundle_dir / "concepts" / "removed.md"
    _write_doc(unchanged_path, title="Unchanged")
    _write_doc(removed_path, title="Removed")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())

        removed_path.unlink()
        _write_doc(bundle_dir / "concepts" / "new.md", title="New")
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())

    assert report.embedded == 1  # new.md
    assert report.cache_hits == 1  # unchanged.md
    assert report.pruned == 1  # removed.md


def test_reindex_skips_unreadable_doc_and_reports_it(tmp_path: Path) -> None:
    """A doc that cannot be decoded as UTF-8 is skipped (not embedded, not
    upserted) and counted in `ReindexReport.skipped`, mirroring
    `fts.build_index`'s degrade-not-crash posture; a valid sibling doc still
    indexes."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "readable.md", title="Readable")
    (bundle_dir / "concepts" / "unreadable.md").write_bytes(b"\xff\xfe\x00\x01not-utf8")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hashes = db.meta_hashes()

    assert set(hashes) == {"concepts/readable"}
    assert report.skipped == 1
    assert report.embedded == 1


# --- Phase 4: walk-error prune guard ----------------------------------------


def test_walk_error_suppresses_pruning_for_the_whole_run(tmp_path: Path) -> None:
    """A directory-scan error during this run's bundle walk (e.g. a
    permission-denied subdirectory) skips the ENTIRE prune pass -- a
    concept whose file lives under that unreadable subtree must NOT be
    pruned, even though the walk didn't see it this run; the embed and
    cache-hit passes still complete normally for every doc the walk DID
    reach (spec: Walk error suppresses pruning for the whole run)."""
    if os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("permission-based read failures require a POSIX non-root user")
    bundle_dir = tmp_path / "bundle"
    locked_dir = bundle_dir / "locked"
    _write_doc(locked_dir / "hidden.md", title="Hidden")
    _write_doc(bundle_dir / "concepts" / "reachable.md", title="Reachable")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())
        assert "locked/hidden" in db.meta_hashes()
        assert "concepts/reachable" in db.meta_hashes()

        original_mode = stat.S_IMODE(locked_dir.stat().st_mode)
        locked_dir.chmod(0o000)
        try:
            embedder = _FakeEmbedder()
            report = reindex.reindex(bundle_dir, db, embedder)
            hashes = db.meta_hashes()
        finally:
            locked_dir.chmod(original_mode)

    assert "locked/hidden" in hashes
    assert report.pruned == 0
    assert report.prune_skipped is True
    # The reachable doc's cache-hit pass still ran normally despite the walk
    # error suppressing pruning -- only the prune pass is affected.
    assert "concepts/reachable" in hashes
    assert report.cache_hits == 1


def test_no_walk_errors_preserves_normal_pruning_behavior(tmp_path: Path) -> None:
    """A bundle whose walk completes with zero directory-scan errors prunes
    exactly as before this change (spec: No walk errors preserves normal
    pruning behavior)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "gone.md"
    _write_doc(doc_path, title="Gone")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())
        assert "concepts/gone" in db.meta_hashes()

        doc_path.unlink()
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hashes = db.meta_hashes()

    assert "concepts/gone" not in hashes
    assert report.pruned == 1
    assert report.prune_skipped is False


def test_reindex_skipped_doc_still_present_on_disk_is_not_pruned(
    tmp_path: Path,
) -> None:
    """An unreadable doc's file still exists on disk -- `reindex` never
    prunes it (pruning is reserved for docs no longer found by the walk at
    all), even though it cannot be embedded this run."""
    bundle_dir = tmp_path / "bundle"
    unreadable_path = bundle_dir / "concepts" / "unreadable.md"
    unreadable_path.parent.mkdir(parents=True)
    unreadable_path.write_bytes(b"\xff\xfe\x00\x01not-utf8")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hashes = db.meta_hashes()

    assert report.pruned == 0
    assert hashes == {}


# --- Phase 5 (Slice 5, PR1): FTS on-disk persistence gate --------------------


def _canary_row_exists(fts_db_path: Path) -> bool:
    """Probe whether a hand-inserted sentinel row survives a `reindex()`
    call -- if the `docs` table was DROPped and rebuilt, the sentinel is
    gone; if the run skipped the rebuild (cache-hit), it survives. A
    behavioral proxy for "was the whole FTS table rebuilt", robust against
    WAL/journal side-file churn that a raw byte-diff of `fts_db_path` would
    otherwise pick up even on a genuine skip."""
    conn = sqlite3.connect(str(fts_db_path))
    try:
        row = conn.execute(
            "SELECT concept_id FROM docs WHERE concept_id = 'zz-canary'"
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def _insert_canary_row(fts_db_path: Path) -> None:
    conn = sqlite3.connect(str(fts_db_path))
    try:
        conn.execute(
            "INSERT INTO docs (concept_id, title, description, tags, body) "
            "VALUES ('zz-canary', '', '', '', '')"
        )
        conn.commit()
    finally:
        conn.close()


def test_reindex_first_run_persists_fts_index_matching_build_index(
    tmp_path: Path,
) -> None:
    """A first `reindex()` call with `fts_db_path` set writes an on-disk FTS
    index containing the same rows an equivalent `build_index` call would
    produce (fts-state: Reindex persists the FTS index to disk)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"
    assert not fts_db_path.exists()

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)

    assert fts_db_path.exists()
    with fts.build_index(bundle_dir) as expected_idx:
        expected = {
            row[0]
            for row in expected_idx._conn.execute(
                "SELECT concept_id FROM docs"
            ).fetchall()
        }
    conn = sqlite3.connect(str(fts_db_path))
    on_disk = {row[0] for row in conn.execute("SELECT concept_id FROM docs").fetchall()}
    conn.close()

    assert on_disk == expected
    assert on_disk == {"concepts/stoicism"}


def test_reindex_unchanged_bundle_skips_fts_rebuild(tmp_path: Path) -> None:
    """A second `reindex()` run over an UNCHANGED bundle does not rebuild the
    FTS table at all (derived-index-cache: Unchanged bundle reuses the
    cached index)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)
        _insert_canary_row(fts_db_path)

        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)

    assert _canary_row_exists(fts_db_path)


def test_reindex_any_document_change_rebuilds_fts_index(tmp_path: Path) -> None:
    """Editing a single document invalidates the manifest, triggering a
    FULL FTS rebuild on the next `reindex()` run (derived-index-cache: Any
    document change invalidates the cache; Single-document edit triggers a
    full rebuild)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "stoicism.md"
    _write_doc(doc_path, title="Stoicism", body="version one")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)
        _insert_canary_row(fts_db_path)

        _write_doc(doc_path, title="Stoicism", body="version two")
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)

    assert not _canary_row_exists(fts_db_path)
    conn = sqlite3.connect(str(fts_db_path))
    rows = conn.execute("SELECT concept_id FROM docs").fetchall()
    conn.close()
    assert {row[0] for row in rows} == {"concepts/stoicism"}


def test_reindex_force_rebuilds_fts_even_when_manifest_unchanged(
    tmp_path: Path,
) -> None:
    """`force=True` rebuilds the FTS index even when the manifest hash is
    unchanged, mirroring the vector store's own `--force` semantics."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)
        _insert_canary_row(fts_db_path)

        reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path, force=True
        )

    assert not _canary_row_exists(fts_db_path)


def test_reindex_without_fts_db_path_never_touches_disk_for_fts(
    tmp_path: Path,
) -> None:
    """Omitting `fts_db_path` (the default) leaves `reindex()`'s FTS
    behavior a pure no-op -- no `.openkos/fts.db` is created, preserving
    every pre-Slice-5 caller's behavior unchanged."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())

    assert not (tmp_path / ".openkos" / "fts.db").exists()


def test_reindex_fts_meta_manifest_matches_derived_bundle_manifest_hash(
    tmp_path: Path,
) -> None:
    """The persisted `meta.manifest_hash` value equals
    `derived.bundle_manifest_hash(bundle_dir)` computed independently over
    the same bundle -- proves reindex stores the SAME digest this module
    computes, not an ad-hoc one."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)

    conn = sqlite3.connect(str(fts_db_path))
    stored = derived.read_manifest_hash(conn)
    conn.close()

    assert stored == derived.bundle_manifest_hash(bundle_dir)


def test_reindex_computes_bundle_manifest_hash_exactly_once_per_rebuild_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `reindex()` run that rebuilds the FTS index computes
    `derived.bundle_manifest_hash` exactly ONCE for that run -- the decision
    snapshot (`_reindex_fts`'s skip/rebuild comparison) and the persisted
    value must be the SAME walk, not two independently-taken snapshots of a
    bundle that could mutate between them (review correction, Finding C:
    triple-walk/TOCTOU). `write_fts_index` must receive and store the
    ALREADY-computed digest rather than recomputing its own."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"

    call_count = 0
    original_manifest_hash = derived.bundle_manifest_hash

    def _counting_manifest_hash(bundle_dir_arg: Path) -> str:
        nonlocal call_count
        call_count += 1
        return original_manifest_hash(bundle_dir_arg)

    monkeypatch.setattr(derived, "bundle_manifest_hash", _counting_manifest_hash)

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), fts_db_path=fts_db_path)

    assert call_count == 1
