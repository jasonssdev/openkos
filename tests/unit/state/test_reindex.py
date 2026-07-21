"""Unit tests for `state/reindex.py`: the vector-store backfill orchestrator.

`reindex()` walks a bundle via `okf._iter_docs` (mirrors `fts.build_index`'s
walk), keys each doc by the same `concept_id` `forget`/`FtsHit`/`Citation`
use, embeds through the injected `Embedder` seam, and upserts into an
injected `VectorStore` -- gated by a `content_hash` cache so an unchanged doc
never triggers an `embed()` call. Every test here is hermetic: a fake
`Embedder` (call-counting, no network) and a real `VectorStoreDB` opened
against a `tmp_path` database (no Ollama, no CLI).
"""

from collections.abc import Sequence
from pathlib import Path

from openkos.llm.base import EMBED_DIM
from openkos.state import reindex, vectorstore


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
