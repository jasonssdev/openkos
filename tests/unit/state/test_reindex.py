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
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.state import derived, fts, reindex, vectorstore


def _tagged(model_tag: str) -> str:
    """The persisted `vectors.db` tag for `model_tag`, per `reindex`'s
    embed-composition-scheme suffix (`reindex.EMBED_COMPOSITION_TAG`,
    #554 migration) -- a helper so tests assert the SAME composition
    `reindex()` itself uses rather than a hardcoded, driftable literal."""
    return f"{model_tag}#{reindex.EMBED_COMPOSITION_TAG}"


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    body: str = "",
    sensitivity: str | None = "private",
) -> None:
    """Write a bundle document.

    `sensitivity` defaults to `private` because that is what a real ingested
    document carries, and because #922 made the value load-bearing here: the
    embed gate is fail-closed, so a fixture with no `sensitivity` key is
    withheld from a non-local backend. Pass `sensitivity=None` to write the
    key-less document deliberately and exercise that fail-closed path.

    It never changes the embedded TEXT -- `_compose_header` reads title,
    description and tags only -- so the composition assertions elsewhere in
    this file are unaffected."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sensitivity_line = "" if sensitivity is None else f"sensitivity: {sensitivity}\n"
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\ndescription: ''\n"
        f"{sensitivity_line}---\n{body}",
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


class _FaultyEmbedder:
    """A call-counting fake `Embedder` that raises a configured exception for
    any text CONTAINING a marker substring (the doc body, since the full
    text passed to `embed()` also carries frontmatter), embedding everything
    else normally -- proves `reindex`'s per-doc embed loop (design D2) calls
    `embed()` once per queued doc (asserted via the `len(texts) == 1` guard)
    and isolates a poison doc's failure from its siblings."""

    def __init__(self, fail_markers: dict[str, Exception]) -> None:
        self._fail_markers = fail_markers
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        assert len(texts) == 1, (
            "reindex() must embed one queued doc per call (per-doc grain, design D2)"
        )
        text = texts[0]
        for marker, exc in self._fail_markers.items():
            if marker in text:
                raise exc
        return [[float(len(self.calls))] * EMBED_DIM]

    @property
    def call_count(self) -> int:
        """Total number of `embed()` invocations (not total texts)."""
        return len(self.calls)


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


# --- Phase 7 (Slice 5, follow-up #4): single commit per run for vectors.db --


def test_reindex_commits_vectors_exactly_once_when_docs_are_embedded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `reindex()` run that embeds multiple NEW docs commits `vectors.db`
    exactly ONCE, not once per document (reindex-command: single run
    performs one commit per store, not once per document)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    _write_doc(bundle_dir / "concepts" / "c.md", title="C")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        commit_calls = 0
        original_commit = db.commit

        def _counting_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()

        monkeypatch.setattr(db, "commit", _counting_commit)

        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hashes = db.meta_hashes()

    assert commit_calls == 1
    assert report.embedded == 3
    assert set(hashes) == {"concepts/a", "concepts/b", "concepts/c"}


def test_reindex_does_not_commit_vectors_when_nothing_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second `reindex()` run over an UNCHANGED bundle (zero docs to embed,
    zero docs to prune) never calls `commit()` at all -- "at most once per
    run" degrades to zero when there is nothing to write."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())

        commit_calls = 0

        def _counting_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1

        monkeypatch.setattr(db, "commit", _counting_commit)

        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())

    assert commit_calls == 0
    assert report.embedded == 0
    assert report.cache_hits == 1
    assert report.pruned == 0


def test_reindex_commits_vectors_exactly_once_when_both_embedding_and_pruning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single `reindex()` run that BOTH embeds a new doc AND prunes a
    removed doc still commits `vectors.db` exactly ONCE -- the embed batch
    and the prune batch share ONE commit, not two."""
    bundle_dir = tmp_path / "bundle"
    doc_gone = bundle_dir / "concepts" / "gone.md"
    _write_doc(doc_gone, title="Gone")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())
        doc_gone.unlink()
        _write_doc(bundle_dir / "concepts" / "new.md", title="New")

        commit_calls = 0
        original_commit = db.commit

        def _counting_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()

        monkeypatch.setattr(db, "commit", _counting_commit)

        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        hashes = db.meta_hashes()

    assert commit_calls == 1
    assert report.embedded == 1
    assert report.pruned == 1
    assert set(hashes) == {"concepts/new"}


# --- Phase 8 (MVP-2 follow-up #5): embedding-model tag gate ------------------


def test_reindex_model_tag_mismatch_forces_full_reembed_and_persists_new_tag(
    tmp_path: Path,
) -> None:
    """A stored `embedding_model` tag that no longer matches the CURRENT
    `model_tag` param forces a full re-embed of every discovered concept
    this run (bypassing the content_hash cache entirely), and the new tag
    is persisted (spec: reindex-command Embedding-Model Tag Gate Forces
    Full Re-Embed On Mismatch)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), model_tag="qwen3-embedding:0.6b"
        )
        hashes_before = db.meta_hashes()

        embedder = _FakeEmbedder()
        report = reindex.reindex(bundle_dir, db, embedder, model_tag="nomic-embed-text")
        stored_tag = db.read_model_tag()
        hashes_after = db.meta_hashes()

    assert report.embedded == 2
    assert report.cache_hits == 0
    assert (
        embedder.call_count == 2
    )  # per-doc grain (design D2): one embed() call per doc
    assert stored_tag == _tagged("nomic-embed-text")
    assert hashes_after == hashes_before  # same content_hash values, re-embedded anyway


def test_reindex_absent_model_tag_forces_one_reembed_then_self_heals(
    tmp_path: Path,
) -> None:
    """A pre-follow-up-#5 `vectors.db` (no `embedding_model` row at all)
    forces exactly ONE full re-embed on the FIRST `reindex()` call that
    passes a `model_tag`, then self-heals: the SECOND call with the SAME
    `model_tag` is a normal incremental (cache-hit) run (spec:
    reindex-command -- absent tag forces one re-embed then self-heals)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        assert db.read_model_tag() is None

        first_embedder = _FakeEmbedder()
        first_report = reindex.reindex(
            bundle_dir, db, first_embedder, model_tag="qwen3-embedding:0.6b"
        )

        second_embedder = _FakeEmbedder()
        second_report = reindex.reindex(
            bundle_dir, db, second_embedder, model_tag="qwen3-embedding:0.6b"
        )

    assert first_report.embedded == 1
    assert first_report.cache_hits == 0
    assert second_report.embedded == 0
    assert second_report.cache_hits == 1


def test_reindex_matching_model_tag_leaves_content_hash_gate_unchanged(
    tmp_path: Path,
) -> None:
    """A `model_tag` that ALREADY matches the stored tag leaves the existing
    content_hash cache-hit/re-embed behavior completely unchanged: an
    unchanged doc is still a cache-hit, zero `Embedder` calls (spec:
    reindex-command Content-Hash Cache Gate, unmodified scenario)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), model_tag="qwen3-embedding:0.6b"
        )

        embedder = _FakeEmbedder()
        report = reindex.reindex(
            bundle_dir, db, embedder, model_tag="qwen3-embedding:0.6b"
        )

    assert report.embedded == 0
    assert report.cache_hits == 1
    assert embedder.call_count == 0


def test_reindex_model_tag_none_stays_inert_for_back_compat(tmp_path: Path) -> None:
    """Omitting `model_tag` (the default `None`) makes the tag gate a pure
    no-op: no forced re-embed, no tag ever written -- every pre-follow-up-#5
    caller's behavior is completely unchanged (spec: reindex() Accepts An
    Explicit Model Tag Parameter -- `None` default preserves back-compat)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())

        embedder = _FakeEmbedder()
        report = reindex.reindex(bundle_dir, db, embedder)
        stored_tag = db.read_model_tag()

    assert report.embedded == 0
    assert report.cache_hits == 1
    assert embedder.call_count == 0
    assert stored_tag is None


def test_reindex_model_tag_write_shares_the_single_commit_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisting the new `embedding_model` tag on a mismatch shares the
    SAME single commit the embed batch already uses -- not a second,
    separate commit (Slice 5 single-commit-per-run contract, extended to
    cover the tag write too)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        commit_calls = 0
        original_commit = db.commit

        def _counting_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()

        monkeypatch.setattr(db, "commit", _counting_commit)

        reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), model_tag="qwen3-embedding:0.6b"
        )
        stored_tag = db.read_model_tag()

    assert commit_calls == 1
    assert stored_tag == _tagged("qwen3-embedding:0.6b")


def test_reindex_empty_bundle_with_absent_tag_still_persists_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EMPTY bundle (nothing to embed, nothing to prune) still persists
    the `model_tag` when none was stored before -- the broadened commit
    condition (`to_embed or to_prune or tag_written`) covers this edge case
    the plain `to_embed or to_prune` check would otherwise miss."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        commit_calls = 0
        original_commit = db.commit

        def _counting_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()

        monkeypatch.setattr(db, "commit", _counting_commit)

        report = reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), model_tag="qwen3-embedding:0.6b"
        )
        stored_tag = db.read_model_tag()

    assert report.embedded == 0
    assert commit_calls == 1
    assert stored_tag == _tagged("qwen3-embedding:0.6b")


def test_reindex_model_tag_mismatch_does_not_trigger_fts_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `model_tag` mismatch that forces a full VECTOR re-embed does NOT
    trigger an FTS rebuild -- the FTS gate stays keyed purely on the bundle
    manifest hash, completely independent of the embedding model (design D5
    -- Slice-5 separation, PINNED; reindex-command: model-tag gate lives
    ONLY in the vector pass)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"

    with vectorstore.open_vector_store(vectors_db_path) as db:
        reindex.reindex(
            bundle_dir,
            db,
            _FakeEmbedder(),
            fts_db_path=fts_db_path,
            model_tag="qwen3-embedding:0.6b",
        )
        fts_bytes_before = fts_db_path.read_bytes()

        reindex.reindex(
            bundle_dir,
            db,
            _FakeEmbedder(),
            fts_db_path=fts_db_path,
            model_tag="a-completely-different-model",
        )
        fts_bytes_after = fts_db_path.read_bytes()

    assert fts_bytes_after == fts_bytes_before


def test_bundle_manifest_hash_is_unaffected_by_the_model_tag(tmp_path: Path) -> None:
    """`derived.bundle_manifest_hash` -- the FTS/graph gate's cache key --
    is computed PURELY from bundle content and never references the
    embedding-model tag at all: the SAME bundle produces the IDENTICAL
    manifest digest regardless of which `model_tag` a `reindex()` call in
    between used (spec: vector-store Model Tag Independent Of The
    Bundle-Manifest Hash; design D5, PINNED). Proves the non-coupling
    directly at the function that would have to change if it were coupled."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    digest_before = derived.bundle_manifest_hash(bundle_dir)

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), model_tag="model-one")
        reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), model_tag="a-totally-different-model"
        )

    digest_after = derived.bundle_manifest_hash(bundle_dir)

    assert digest_after == digest_before


def test_pre_slice_vectors_db_self_heals_in_exactly_one_reindex_run(
    tmp_path: Path,
) -> None:
    """Runtime-harness integration proof (Phase 5): a GENUINELY pre-slice
    `vectors.db` -- vectors written directly via `upsert()`, never through a
    `model_tag`-aware `reindex()` call, simulating a real production
    database from before this follow-up existed -- self-heals in EXACTLY
    ONE `reindex()` run once a `model_tag` is finally supplied: that first
    call force-re-embeds every concept (even though their content_hash
    already matched) and persists the tag; every SUBSEQUENT call with the
    SAME tag is then a normal incremental cache-hit run, with zero further
    forced re-embeds."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism", body="v1")
    _write_doc(bundle_dir / "concepts" / "epictetus.md", title="Epictetus", body="v1")
    db_path = tmp_path / ".openkos" / "vectors.db"

    # Simulate a genuinely pre-slice vectors.db: vectors upserted directly,
    # with no model_tag ever having been passed to reindex() at all.
    pre_slice_hash_stoicism = vectorstore.content_hash(
        b"whatever raw bytes produced this vector"
    )
    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/stoicism", [0.1] * EMBED_DIM, pre_slice_hash_stoicism)
        db.upsert("concepts/epictetus", [0.2] * EMBED_DIM, "pre-slice-hash-epictetus")
        assert db.read_model_tag() is None

    with vectorstore.open_vector_store(db_path) as db:
        heal_embedder = _FakeEmbedder()
        heal_report = reindex.reindex(
            bundle_dir, db, heal_embedder, model_tag="qwen3-embedding:0.6b"
        )
        healed_tag = db.read_model_tag()

        steady_embedder = _FakeEmbedder()
        steady_report = reindex.reindex(
            bundle_dir, db, steady_embedder, model_tag="qwen3-embedding:0.6b"
        )

    assert heal_report.embedded == 2
    assert heal_report.cache_hits == 0
    assert healed_tag == _tagged("qwen3-embedding:0.6b")
    assert steady_report.embedded == 0
    assert steady_report.cache_hits == 2
    assert steady_embedder.call_count == 0


# --- Phase 8 review correction: stranded concept on a partial model switch --


def test_reindex_model_tag_mismatch_with_a_skipped_doc_does_not_persist_the_new_tag(
    tmp_path: Path,
) -> None:
    """CRITICAL review finding: if a model-tag mismatch run cannot re-embed
    EVERY discovered doc (one hits the transient skip path), the new tag
    must NOT be persisted -- otherwise the skipped doc's stale old-model
    vector would silently survive forever (the next run would see a
    MATCHING tag and treat the doc as a content_hash cache-hit). Instead:
    a partial (skipped > 0) model-change run keeps re-forcing the full
    re-embed on every subsequent call until one run finally covers every
    doc (skipped == 0), at which point the tag is finally persisted (spec:
    reindex-command Embedding-Model Tag Persisted Only After A Complete
    Re-Embed)."""
    bundle_dir = tmp_path / "bundle"
    a_path = bundle_dir / "concepts" / "a.md"
    b_path = bundle_dir / "concepts" / "b.md"
    _write_doc(a_path, title="A")
    _write_doc(b_path, title="B")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), model_tag="old-model")
        assert db.read_model_tag() == _tagged("old-model")

        # b.md now hits the transient skip path (undecodable bytes) during
        # the model-switch run.
        b_path.write_bytes(b"\xff\xfe\x00\x01not-utf8")

        first_embedder = _FakeEmbedder()
        first_report = reindex.reindex(
            bundle_dir, db, first_embedder, model_tag="new-model"
        )
        tag_after_first = db.read_model_tag()

        # Tag still wasn't persisted -- b.md is STILL unreadable -- so a
        # second call with the SAME new model_tag keeps forcing a.md's
        # re-embed too (safe/expensive, not silently wrong).
        second_embedder = _FakeEmbedder()
        second_report = reindex.reindex(
            bundle_dir, db, second_embedder, model_tag="new-model"
        )
        tag_after_second = db.read_model_tag()

        # b.md becomes readable again -- this run finally covers every doc.
        _write_doc(b_path, title="B")
        third_embedder = _FakeEmbedder()
        third_report = reindex.reindex(
            bundle_dir, db, third_embedder, model_tag="new-model"
        )
        tag_after_third = db.read_model_tag()

        # A normal incremental run: the tag now matches, both docs cache-hit.
        fourth_embedder = _FakeEmbedder()
        fourth_report = reindex.reindex(
            bundle_dir, db, fourth_embedder, model_tag="new-model"
        )

    assert first_report.embedded == 1  # a.md only
    assert first_report.skipped == 1  # b.md
    assert first_report.model_reembedded is True
    assert tag_after_first == _tagged("old-model")  # NOT persisted -- b.md was skipped

    assert second_report.embedded == 1  # a.md re-forced again
    assert second_report.skipped == 1  # b.md still unreadable
    assert second_report.model_reembedded is True
    assert tag_after_second == _tagged("old-model")  # still NOT persisted

    assert third_report.embedded == 2  # a.md AND b.md, both covered this run
    assert third_report.skipped == 0
    assert third_report.model_reembedded is True
    assert tag_after_third == _tagged("new-model")  # NOW persisted -- run was complete

    assert fourth_report.embedded == 0
    assert fourth_report.cache_hits == 2
    assert fourth_report.model_reembedded is False
    assert fourth_embedder.call_count == 0


def test_reindex_model_reembedded_flag_reflects_the_tag_gate_only(
    tmp_path: Path,
) -> None:
    """`ReindexReport.model_reembedded` is `True` exactly when a model-tag
    mismatch forced this run's re-embed, and `False` for an ordinary
    content-change run (even one that embeds every doc) -- it is NOT a
    generic "something was embedded" flag (review WARNING finding:
    observability for the model-tag force)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="v1")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        ordinary_report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        assert ordinary_report.model_reembedded is False

        _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="v2")
        content_change_report = reindex.reindex(bundle_dir, db, _FakeEmbedder())
        assert content_change_report.embedded == 1
        assert content_change_report.model_reembedded is False

        tag_mismatch_report = reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), model_tag="some-model"
        )

    assert tag_mismatch_report.model_reembedded is True


# --- Phase 9 (reindex-embedding-resilience): per-doc embed isolation -------


def test_reindex_one_poison_doc_survives_as_partial_progress_run(
    tmp_path: Path,
) -> None:
    """A single doc whose embed call raises the generic transient
    `OllamaError` (retries already exhausted at the `OllamaClient` layer) is
    isolated: the other queued doc still embeds, upserts, and commits; the
    poison doc counts as `embed_failed`, NOT `skipped`; and `reindex` does
    not raise (spec: reindex-command Per-Doc Embed Failure Is Isolated, Not
    Fatal -- one poison doc among many survives)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="poison")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B", body="fine")
    embedder = _FaultyEmbedder({"poison": OllamaError("EOF after retries")})

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()

    assert report.embedded == 1
    assert report.embed_failed == 1
    assert report.skipped == 0
    assert set(hashes) == {"concepts/b"}


def test_reindex_every_doc_transiently_fails_leaves_empty_embed_pass_not_a_crash(
    tmp_path: Path,
) -> None:
    """Every queued doc's embed call transiently fails: `embedded` is `0`,
    `embed_failed` equals the queued count, no exception propagates (spec:
    Every queued doc transiently fails leaves an empty embed pass, not a
    crash)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="poison-a")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B", body="poison-b")
    embedder = _FaultyEmbedder(
        {
            "poison-a": OllamaError("EOF after retries"),
            "poison-b": OllamaError("EOF after retries"),
        }
    )

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()

    assert report.embedded == 0
    assert report.embed_failed == 2
    assert hashes == {}


def test_reindex_ollama_unavailable_mid_loop_is_reraised_not_counted_as_embed_failed(
    tmp_path: Path,
) -> None:
    """`OllamaUnavailable` raised mid-loop is FATAL, not a per-doc skip: it
    propagates unchanged, the remaining queued docs are never processed, and
    nothing from this interrupted run is committed (spec: Unreachable Ollama
    mid-embed-loop is fatal, not a per-doc skip)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="fine")
    _write_doc(bundle_dir / "concepts" / "z.md", title="Z", body="unreachable")
    embedder = _FaultyEmbedder(
        {"unreachable": OllamaUnavailable("Ollama not reachable")}
    )

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        with pytest.raises(OllamaUnavailable):
            reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()

    assert hashes == {}


def test_reindex_ollama_model_not_found_mid_loop_is_reraised_not_counted_as_embed_failed(
    tmp_path: Path,
) -> None:
    """`OllamaModelNotFound` raised mid-loop is FATAL, not a per-doc skip: it
    propagates unchanged (spec: Missing embedding model mid-embed-loop is
    fatal, not a per-doc skip)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="fine")
    _write_doc(bundle_dir / "concepts" / "z.md", title="Z", body="missing-model")
    embedder = _FaultyEmbedder(
        {"missing-model": OllamaModelNotFound("model not installed")}
    )

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        with pytest.raises(OllamaModelNotFound):
            reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()

    assert hashes == {}


def test_reindex_dimension_mismatch_mid_loop_propagates_and_stays_unrecorded(
    tmp_path: Path,
) -> None:
    """`OllamaEmbeddingDimensionMismatch` raised mid-loop is FATAL, not a
    per-doc skip: it propagates out of `reindex()` unchanged, `embed_failed`
    stays `0`, and nothing from this interrupted run is committed -- no
    `upsert_many`/`commit`/`write_model_tag` (task 3.1; spec: Dimension
    mismatch mid-embed-loop is fatal, not a per-doc skip)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="fine")
    _write_doc(bundle_dir / "concepts" / "z.md", title="Z", body="wrong-dim")
    embedder = _FaultyEmbedder(
        {"wrong-dim": OllamaEmbeddingDimensionMismatch("expected 1024, got 768")}
    )

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        with pytest.raises(OllamaEmbeddingDimensionMismatch):
            reindex.reindex(bundle_dir, db, embedder)
        hashes = db.meta_hashes()
        stored_tag = db.read_model_tag()

    assert hashes == {}
    assert stored_tag is None


def test_reindex_dimension_mismatch_ordering_precedes_generic_ollama_error_catch(
    tmp_path: Path,
) -> None:
    """Safety-critical ordering guard (task 3.2): `reindex()` must check
    `OllamaEmbeddingDimensionMismatch` BEFORE the broad `except OllamaError`
    clause. A test that only asserts the exception type raised would still
    pass if the clauses were reversed (a bare `except OllamaError` also
    catches the subclass) -- this test instead proves the ORDERING by
    asserting the doc was NEVER counted as `embed_failed` and no further
    queued docs were processed after the raise, which only holds if the
    dimension-mismatch branch is checked first."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="fine")
    _write_doc(bundle_dir / "concepts" / "z.md", title="Z", body="wrong-dim")
    _write_doc(bundle_dir / "concepts" / "zz.md", title="ZZ", body="never-reached")
    embedder = _FaultyEmbedder(
        {"wrong-dim": OllamaEmbeddingDimensionMismatch("expected 1024, got 768")}
    )

    with (
        vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db,
        pytest.raises(OllamaEmbeddingDimensionMismatch),
    ):
        reindex.reindex(bundle_dir, db, embedder)

    # If the ordering were reversed (generic `except OllamaError` checked
    # first), the subclass would still match it -- BUT `reindex()`'s current
    # generic branch increments `embed_failed` and `continue`s instead of
    # re-raising, so the loop would proceed to "zz.md" (never-reached) and
    # `embed()` would be called a THIRD time. Asserting exactly 2 calls
    # (a.md succeeds, z.md raises) proves the fatal branch stopped the loop
    # immediately -- reversing the clauses would make this assertion fail.
    assert embedder.call_count == 2


def test_reindex_dimension_mismatch_message_is_permanent_never_will_retry(
    tmp_path: Path,
) -> None:
    """The stderr-bound message on this fatal path names it a permanent
    dimension mismatch and never claims "will retry next run" (task 3.3/
    3.4): the propagated exception's own message (built in `llm/ollama.py`,
    D7) is what any caller prints unchanged, so it must already satisfy this
    wording constraint by the time it leaves `reindex()`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "z.md", title="Z", body="wrong-dim")
    embedder = _FaultyEmbedder(
        {
            "wrong-dim": OllamaEmbeddingDimensionMismatch(
                "Ollama returned an embedding row of length 768, expected "
                "exactly 1024 (EMBED_DIM) -- this is a permanent dimension "
                "mismatch caused by the configured embedding model, not a "
                "transient failure; it will not heal by retrying."
            )
        }
    )

    with (
        vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db,
        pytest.raises(OllamaEmbeddingDimensionMismatch) as excinfo,
    ):
        reindex.reindex(bundle_dir, db, embedder)

    message = str(excinfo.value)
    assert "permanent" in message
    assert "will retry next run" not in message


def test_reindex_model_tag_mismatch_with_an_embed_failed_doc_converges_across_runs(
    tmp_path: Path,
) -> None:
    """Multi-run convergence for the `embed_failed` half of the tag-persist
    gate (mirrors the `skipped` convergence test above, ~982-1057): a
    model-switch run whose `embed_failed > 0` withholds the tag; the NEXT
    run, once the previously-poisoned doc embeds successfully, finally
    covers every doc (`skipped == 0 AND embed_failed == 0`) and persists
    the tag -- proving the self-heal actually CONVERGES for `embed_failed`,
    not merely that it withholds once (fix 4, cheap WARNING follow-up)."""
    bundle_dir = tmp_path / "bundle"
    a_path = bundle_dir / "concepts" / "a.md"
    b_path = bundle_dir / "concepts" / "b.md"
    _write_doc(a_path, title="A", body="fine")
    _write_doc(b_path, title="B", body="poison")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        # b.md transiently fails to embed during the model-switch run.
        first_embedder = _FaultyEmbedder({"poison": OllamaError("EOF after retries")})
        first_report = reindex.reindex(
            bundle_dir, db, first_embedder, model_tag="new-model"
        )
        tag_after_first = db.read_model_tag()

        # b.md now embeds fine (the transient failure has cleared) -- a
        # second call with the SAME model_tag still forces a full re-embed
        # (the tag was never persisted) and this time covers every doc.
        second_embedder = _FakeEmbedder()
        second_report = reindex.reindex(
            bundle_dir, db, second_embedder, model_tag="new-model"
        )
        tag_after_second = db.read_model_tag()

        # A normal incremental run: the tag now matches, both docs cache-hit.
        third_embedder = _FakeEmbedder()
        third_report = reindex.reindex(
            bundle_dir, db, third_embedder, model_tag="new-model"
        )

    assert first_report.embedded == 1  # a.md only
    assert first_report.embed_failed == 1  # b.md
    assert first_report.skipped == 0
    assert first_report.model_reembedded is True
    assert tag_after_first is None  # withheld -- b.md was NOT embedded

    assert second_report.embedded == 2  # a.md AND b.md, both covered this run
    assert second_report.skipped == 0
    assert second_report.embed_failed == 0
    assert second_report.model_reembedded is True
    assert tag_after_second == _tagged("new-model")  # NOW persisted -- run was complete

    assert third_report.embedded == 0
    assert third_report.cache_hits == 2
    assert third_report.model_reembedded is False
    assert third_embedder.call_count == 0


def test_reindex_tag_persist_gate_withheld_when_embed_failed_even_if_skipped_zero(
    tmp_path: Path,
) -> None:
    """The model-tag-persist gate withholds the new tag when `embed_failed >
    0`, even though `skipped == 0` -- the union-keyed gate (spec:
    Embedding-Model Tag Gate Forces Full Re-Embed On Mismatch, MODIFIED:
    `skipped == 0 AND embed_failed == 0`)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="fine")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B", body="poison")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        embedder = _FaultyEmbedder({"poison": OllamaError("EOF after retries")})
        report = reindex.reindex(
            bundle_dir, db, embedder, model_tag="qwen3-embedding:0.6b"
        )
        stored_tag = db.read_model_tag()

    assert report.skipped == 0
    assert report.embed_failed == 1
    assert stored_tag is None  # withheld -- NOT "qwen3-embedding:0.6b"


# --- `on_progress` per-embedded-doc progress hook (issue #190) ---------------


def test_reindex_invokes_on_progress_once_per_queued_doc_in_walk_order(
    tmp_path: Path,
) -> None:
    """`on_progress` fires once per QUEUED doc, in walk order, AFTER that
    doc's individual `embedder.embed` attempt resolves -- carrying `(index,
    total, concept_id)` with a 1-based `index` and `total` equal to the
    number of docs queued for embedding this run (issue #190, mirroring
    `suggest_edge_types`'s #134 contract). A cache-hit doc is never queued,
    so a fully cached re-run fires nothing at all."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "alpha.md", title="Alpha")
    _write_doc(bundle_dir / "concepts" / "beta.md", title="Beta")
    seen: list[tuple[int, int, str]] = []

    def _cb(index: int, total: int, concept_id: str) -> None:
        seen.append((index, total, concept_id))

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), on_progress=_cb)

        assert seen == [
            (1, 2, "concepts/alpha"),
            (2, 2, "concepts/beta"),
        ]

        seen.clear()
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder(), on_progress=_cb)

    assert report.cache_hits == 2
    assert seen == []


def test_reindex_on_progress_fires_for_transient_embed_failure_too(
    tmp_path: Path,
) -> None:
    """A doc whose individual embed call fails with the generic transient
    `OllamaError` (isolated as `embed_failed`, not fatal) STILL fires its
    callback -- the attempt is what takes the time, and skipping it would
    make the progress counter silently stall on a poison doc (issue #190)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "alpha.md", title="Alpha", body="poison body")
    _write_doc(bundle_dir / "concepts" / "beta.md", title="Beta", body="fine body")
    embedder = _FaultyEmbedder({"poison body": OllamaError("transient failure")})
    seen: list[tuple[int, int, str]] = []

    def _cb(index: int, total: int, concept_id: str) -> None:
        seen.append((index, total, concept_id))

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder, on_progress=_cb)

    assert report.embed_failed == 1
    assert report.embedded == 1
    assert seen == [
        (1, 2, "concepts/alpha"),
        (2, 2, "concepts/beta"),
    ]


def test_reindex_on_progress_never_fires_for_a_doc_behind_a_fatal_failure(
    tmp_path: Path,
) -> None:
    """A FATAL embed failure (`OllamaUnavailable`) re-raises immediately --
    the failing doc's callback never fires, and neither does any later
    doc's (issue #190: the hook reports resolved attempts, never invents
    progress past an aborted run)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "alpha.md", title="Alpha", body="fine body")
    _write_doc(bundle_dir / "concepts" / "beta.md", title="Beta", body="poison body")
    embedder = _FaultyEmbedder({"poison body": OllamaUnavailable("server down")})
    seen: list[tuple[int, int, str]] = []

    def _cb(index: int, total: int, concept_id: str) -> None:
        seen.append((index, total, concept_id))

    with (
        vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db,
        pytest.raises(OllamaUnavailable),
    ):
        reindex.reindex(bundle_dir, db, embedder, on_progress=_cb)

    assert seen == [(1, 2, "concepts/alpha")]


def test_reindex_on_progress_exception_propagates(tmp_path: Path) -> None:
    """An exception raised inside `on_progress` propagates to the caller
    unswallowed -- it is the caller's own callback (issue #190)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "alpha.md", title="Alpha")

    def _boom(index: int, total: int, concept_id: str) -> None:
        raise RuntimeError("callback failed")

    with (
        vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db,
        pytest.raises(RuntimeError, match="callback failed"),
    ):
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), on_progress=_boom)


# --- Phase 10 (#554): composed embed text, not raw frontmatter+body bytes --


def test_reindex_embed_text_composes_title_description_tags_body(
    tmp_path: Path,
) -> None:
    """Closes #554: the text handed to the Embedder is composed from
    title, description, tags, and body -- the SAME field set `fts.py`'s
    `_populate_docs_table` indexes (fts.py:220-234) -- rather than the
    document's raw frontmatter+body bytes verbatim (spec: reindex-command
    Composed Embed Text Replaces Raw-Bytes Embedding, scenario "Embed text
    matches FTS's field composition")."""
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "a.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "type: Concept\n"
        "title: Stoicism\n"
        "description: A school of Hellenistic philosophy\n"
        "tags:\n"
        "  - philosophy\n"
        "  - ethics\n"
        "sensitivity: private\n"
        "---\n"
        "Stoicism teaches virtue as the only true good.\n",
        encoding="utf-8",
    )
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder)

    assert report.embedded == 1
    assert embedder.call_count == 1
    (embedded_text,) = embedder.calls[0]
    assert "Stoicism" in embedded_text
    assert "A school of Hellenistic philosophy" in embedded_text
    assert "philosophy" in embedded_text
    assert "ethics" in embedded_text
    assert "Stoicism teaches virtue as the only true good." in embedded_text
    # The raw frontmatter block itself is NOT embedded verbatim -- proves
    # this is composed text, not the document's raw bytes.
    assert "type: Concept" not in embedded_text
    assert "---" not in embedded_text


def test_reindex_embed_text_drops_fields_outside_the_composed_set(
    tmp_path: Path,
) -> None:
    """A document with no ledger history embeds identically to before,
    content-wise: its title/description/tags/body content is present, but
    an arbitrary OTHER frontmatter key never reaches the embed text --
    proves the composition is bounded to exactly the four fields `fts.py`
    indexes, not "everything except the ledger" (spec scenario: "A document
    with no ledger history embeds identically to before, content-wise")."""
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "a.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "type: Concept\n"
        "title: A\n"
        "description: ''\n"
        "sensitivity: private\n"
        "custom_marker_field: SHOULD_NOT_BE_EMBEDDED\n"
        "---\n"
        "Body text.\n",
        encoding="utf-8",
    )
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        reindex.reindex(bundle_dir, db, embedder)

    (embedded_text,) = embedder.calls[0]
    assert "A" in embedded_text
    assert "Body text." in embedded_text
    assert "SHOULD_NOT_BE_EMBEDDED" not in embedded_text


def test_reindex_embed_composition_change_forces_full_reembed_via_model_tag(
    tmp_path: Path,
) -> None:
    """#554 migration: every vector already stored under the OLD
    (pre-composition, raw-bytes) scheme must be force-re-embedded exactly
    once when this composition scheme ships -- reusing the SAME
    embedding-model-tag gate that already forces a full re-embed on an
    actual model change, rather than inventing a second, parallel version
    marker (design: "find it and use it; do not invent a parallel one").
    A `vectors.db` carrying only the BARE model tag (as every pre-this-
    change `reindex()` call would have written) is indistinguishable, by
    tag alone, from a fresh one -- so `model_tag` alone must still force a
    complete re-embed here."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        # Simulate a genuinely pre-#554 vectors.db: the BARE model tag
        # only, no composition-scheme marker, as every reindex() call
        # before this change would have written.
        db.write_model_tag("qwen3-embedding:0.6b")
        db.commit()

        embedder = _FakeEmbedder()
        report = reindex.reindex(
            bundle_dir, db, embedder, model_tag="qwen3-embedding:0.6b"
        )

        steady_embedder = _FakeEmbedder()
        steady_report = reindex.reindex(
            bundle_dir, db, steady_embedder, model_tag="qwen3-embedding:0.6b"
        )

    assert report.embedded == 1
    assert report.model_reembedded is True
    # Self-heals: the SECOND call with the same model_tag is an ordinary
    # incremental run -- the new composed tag was persisted, so this does
    # not force a re-embed on every future run.
    assert steady_report.embedded == 0
    assert steady_report.cache_hits == 1
    assert steady_report.model_reembedded is False
    assert steady_embedder.call_count == 0


# --- Phase 11 (#888): body chunking, per-chunk failure isolation ------------


class _CountingChunkEmbedder:
    """Embeds one text per call (per-chunk grain, design D6); raises `exc`
    on the `fail_on_call` -th call overall (1-based), succeeds on every
    other call, returning a distinct vector per call so a stored row can be
    traced back to which call produced it."""

    def __init__(
        self, *, fail_on_call: int | None = None, exc: Exception | None = None
    ) -> None:
        self.calls: list[list[str]] = []
        self._fail_on_call = fail_on_call
        self._exc = exc

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        assert len(texts) == 1, "reindex() must embed one chunk per call"
        if self._fail_on_call == len(self.calls):
            assert self._exc is not None
            raise self._exc
        return [[float(len(self.calls))] * EMBED_DIM]

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _long_body(num_chunks_hint: int, *, line_length: int = 7_000) -> str:
    """A body whose lines are each `line_length` chars -- with the
    production `_EMBED_CHUNK_TARGET = 12_000` and a one-character header
    (`title="T"`, empty description/tags), two `line_length`-char lines
    exceed the ~11,999-char per-chunk budget, so `_chunk_lines` places
    exactly ONE line per chunk: `num_chunks_hint` lines -> that many
    chunks, deterministically."""
    return "\n".join("X" * line_length for _ in range(num_chunks_hint))


def test_chunk_body_is_lossless_and_empty_body_yields_no_body_chunks() -> None:
    """S4: rejoining chunks with `"\\n"` reproduces the original body
    byte-for-byte; an empty body yields ZERO body chunks (the header-alone
    case is `_chunk_embed_texts`'s job) (embedding-chunking spec: Chunk
    Coverage Is Lossless)."""
    body = _long_body(5)

    chunks = reindex._chunk_body(body, header="T")

    assert len(chunks) == 5
    assert "\n".join(chunks) == body
    assert reindex._chunk_body("", header="T") == []


def test_chunk_embed_texts_repeats_header_and_empty_body_yields_one_chunk() -> None:
    """embedding-chunking spec: Body-Only Chunking With A Repeated Header --
    every chunk's text starts with the identical header; an empty body
    still yields exactly ONE chunk, containing the header alone."""
    metadata: dict[str, object] = {"title": "T", "description": "", "tags": []}

    texts = reindex._chunk_embed_texts(metadata, _long_body(3))
    assert len(texts) == 3
    assert all(text.startswith("T") for text in texts)

    empty_body_texts = reindex._chunk_embed_texts(metadata, "")
    assert empty_body_texts == ["T"]


def test_reindex_short_document_issues_exactly_one_embed_call(
    tmp_path: Path,
) -> None:
    """A document whose composed body fits within one chunking window
    issues exactly one embed call (embedding-chunking spec: A short
    document produces exactly one chunk)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="short body")
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder)

    assert report.embedded == 1
    assert report.embed_calls == 1
    assert embedder.call_count == 1


def test_reindex_long_document_issues_multiple_lossless_embed_calls(
    tmp_path: Path,
) -> None:
    """A document whose body exceeds the embedder's window is embedded via
    multiple chunk calls, none of them truncating -- every embeddable byte
    of the body reaches exactly one chunk's embed text (embedding-chunking
    spec: A long document is embedded via multiple chunk calls, none of
    them truncating)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="T", body=_long_body(5))
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder)

    assert report.embedded == 1
    assert report.embed_calls == 5
    assert embedder.call_count == 5
    combined = "".join(embedder.embedded_texts)
    assert combined.count("X" * 7_000) == 5


def test_reindex_chunk_3_of_5_fails_stores_nothing_for_that_document(
    tmp_path: Path,
) -> None:
    """S8: chunk 3 of 5 fails on the generic transient `OllamaError` ->
    `embed_failed += 1`, ZERO rows written for that document (not a partial
    mean over the 4 surviving chunks), and prior stored rows are untouched
    -- asserted against the STORED state, not merely the counter
    (reindex-command spec: A partially-failed document stores no partial
    document vector)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "a.md"
    _write_doc(doc_path, title="T", body=_long_body(5))
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        embedder = _CountingChunkEmbedder(
            fail_on_call=3, exc=OllamaError("EOF after retries")
        )
        report = reindex.reindex(bundle_dir, db, embedder)

        vector_rows = db._conn.execute(
            "SELECT COUNT(*) FROM vectors WHERE concept_id = ?", ("concepts/a",)
        ).fetchone()[0]
        doc_vector_rows = db._conn.execute(
            "SELECT COUNT(*) FROM doc_vectors WHERE concept_id = ?", ("concepts/a",)
        ).fetchone()[0]
        meta_row = db._conn.execute(
            "SELECT content_hash FROM vector_meta WHERE concept_id = ?",
            ("concepts/a",),
        ).fetchone()

    assert report.embedded == 0
    assert report.embed_failed == 1
    assert report.skipped == 0
    assert vector_rows == 0
    assert doc_vector_rows == 0
    assert meta_row is None


def test_reindex_chunk_failure_leaves_prior_stored_rows_untouched(
    tmp_path: Path,
) -> None:
    """A document already stored from a PRIOR run, then re-embedded with a
    chunk failure on THIS run, keeps its prior rows exactly as they were --
    the failed re-embed neither deletes nor overwrites them."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "a.md"
    _write_doc(doc_path, title="T", body="short stable body")
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder())
        prior_hash = db.meta_hashes()["concepts/a"]

        # Force a re-embed attempt (content unchanged -> use force=True) that
        # fails on this document's one and only chunk.
        embedder = _CountingChunkEmbedder(
            fail_on_call=1, exc=OllamaError("EOF after retries")
        )
        report = reindex.reindex(bundle_dir, db, embedder, force=True)
        after_hash = db.meta_hashes()["concepts/a"]

    assert report.embed_failed == 1
    assert after_hash == prior_hash


def test_reindex_dimension_mismatch_on_a_non_first_chunk_is_still_fatal(
    tmp_path: Path,
) -> None:
    """S9: the FATAL ladder (`OllamaUnavailable`/`OllamaModelNotFound`/
    `OllamaEmbeddingDimensionMismatch`) is checked BEFORE the generic
    `OllamaError` handler INSIDE the chunk loop -- a mismatch raised on
    chunk 2 of a multi-chunk document still propagates and aborts the run,
    not merely one raised on a document's first chunk
    (`reindex.py:340-349`'s safety-critical ordering, restored for the
    chunk loop)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="T", body=_long_body(3))
    embedder = _CountingChunkEmbedder(
        fail_on_call=2,
        exc=OllamaEmbeddingDimensionMismatch("expected 1024, got 768"),
    )

    with (
        vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db,
        pytest.raises(OllamaEmbeddingDimensionMismatch),
    ):
        reindex.reindex(bundle_dir, db, embedder)


def test_reindex_still_commits_exactly_once_regardless_of_chunk_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S10: a run embedding a multi-chunk document still commits `vectors.db`
    exactly ONCE, not once per chunk."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="T", body=_long_body(5))
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        commit_calls = 0
        original_commit = db.commit

        def _counting_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()

        monkeypatch.setattr(db, "commit", _counting_commit)

        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())

    assert commit_calls == 1
    assert report.embedded == 1
    assert report.embed_calls == 5


def test_reindex_report_embed_calls_exceeds_embedded_for_a_chunked_document(
    tmp_path: Path,
) -> None:
    """Cost honesty (design D6): `ReindexReport.embed_calls` exceeds
    `embedded` whenever any document produced more than one chunk -- without
    it, "N embedded" understates the run's real work by the chunk
    multiplier."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="T", body=_long_body(4))

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())

    assert report.embedded == 1
    assert report.embed_calls == 4
    assert report.embed_calls > report.embedded


def test_reindex_on_progress_fires_once_per_document_not_per_chunk(
    tmp_path: Path,
) -> None:
    """`on_progress` keeps its `(index, total, concept_id)` contract, firing
    once per QUEUED DOCUMENT after its own chunk set resolves -- a 5-chunk
    document must not fire it 5 times (design D6: "a 40-chunk document now
    looks stalled for 40 calls" -- the calls are silent, the callback is
    not)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="T", body=_long_body(5))
    _write_doc(bundle_dir / "concepts" / "b.md", title="B", body="short")
    progress_calls: list[tuple[int, int, str]] = []

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        reindex.reindex(
            bundle_dir,
            db,
            _FakeEmbedder(),
            on_progress=lambda i, t, c: progress_calls.append((i, t, c)),
        )

    assert len(progress_calls) == 2
    assert {c for _, _, c in progress_calls} == {"concepts/a", "concepts/b"}


def test_reindex_effective_model_tag_is_reported_on_the_report(
    tmp_path: Path,
) -> None:
    """`ReindexReport.effective_model_tag` carries `_effective_model_tag
    (model_tag)` -- the CLI's corrected disclosure compares THIS against the
    previously stored tag, never the bare configured model name."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder(), model_tag="bge-m3")

    assert report.effective_model_tag == "bge-m3#chunk-v1"


def test_reindex_effective_model_tag_is_none_when_model_tag_is_none(
    tmp_path: Path,
) -> None:
    """Omitting `model_tag` leaves `effective_model_tag` `None`, matching
    the tag gate's own pure-no-op default."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder())

    assert report.effective_model_tag is None


# --- #922: the embed gate. `sensitivity` governs EGRESS, and a non-local
# embedding backend IS egress ------------------------------------------------


def test_reindex_withholds_a_confidential_doc_from_a_non_local_backend(
    tmp_path: Path,
) -> None:
    """The embed path had NO sensitivity check at all. A remote `OLLAMA_HOST`
    got a warning (#199) and the document text was sent anyway -- including
    `confidential`, against the project's own "confidential never leaves the
    device" guarantee.

    `sensitivity.should_block` is the one shared authority the five `llm.chat`
    seams already delegate to (#240); embedding is a sixth seam that was never
    taught. `local_exemption=False` means the backend is not verifiably this
    machine, so the send is egress and must not happen."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "open.md", title="Open", body="public body")
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="salary figures",
        sensitivity="confidential",
    )
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder, local_exemption=False)

    assert report.withheld_confidential == 1
    assert report.embedded == 1
    # The point of the whole change: the bytes never reached the embedder.
    assert not any("salary figures" in text for text in embedder.embedded_texts)
    assert any("public body" in text for text in embedder.embedded_texts)


def test_reindex_embeds_a_confidential_doc_under_the_local_exemption(
    tmp_path: Path,
) -> None:
    """A local backend is not egress, so nothing is withheld -- the default
    local-first experience is unchanged, which is what keeps this a boundary
    rather than a downgrade."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="salary figures",
        sensitivity="confidential",
    )
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder, local_exemption=True)

    assert report.withheld_confidential == 0
    assert report.embedded == 1
    assert any("salary figures" in text for text in embedder.embedded_texts)


@pytest.mark.parametrize(
    ("sensitivity", "why"),
    [
        (None, "the key is absent"),
        ("", "the value is blank"),
        ("   ", "the value is whitespace"),
    ],
)
def test_reindex_fails_closed_on_an_unusable_sensitivity(
    tmp_path: Path, sensitivity: str | None, why: str
) -> None:
    """A security-relevant signal that is simply missing must fail CLOSED.
    `okf._rank(None)` and `okf._rank("")` both resolve to `private`, which is
    the right default for a merge floor and the wrong answer here --
    `blocks_llm_send` exists precisely to not delegate the absent case."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="body text",
        sensitivity=sensitivity,
    )
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder, local_exemption=False)

    assert report.withheld_confidential == 1, why
    assert report.embedded == 0
    assert embedder.call_count == 0


def test_reindex_does_not_over_block_ordinary_documents(
    tmp_path: Path,
) -> None:
    """The gate must be a boundary, not a blanket. `public` and `private`
    both rank below `confidential`, so a remote backend still embeds the
    documents it was always allowed to -- otherwise the fix would read as
    "reindex stopped working against a remote host"."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "p.md", title="P", sensitivity="public")
    _write_doc(bundle_dir / "concepts" / "q.md", title="Q", sensitivity="private")
    embedder = _FakeEmbedder()

    with vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db") as db:
        report = reindex.reindex(bundle_dir, db, embedder, local_exemption=False)

    assert report.withheld_confidential == 0
    assert report.embedded == 2


def test_reindex_leaves_a_withheld_docs_existing_vector_in_place(
    tmp_path: Path,
) -> None:
    """A withheld document is still ON DISK, so it must not be pruned.

    Its stored vector was computed locally and lives in a local store, so
    keeping it leaks nothing -- while pruning it would silently destroy work
    the moment an operator pointed `OLLAMA_HOST` at a colleague's machine,
    and destroying data is not what a privacy boundary is for."""
    bundle_dir = tmp_path / "bundle"
    doc = bundle_dir / "concepts" / "secret.md"
    _write_doc(doc, title="Secret", body="body", sensitivity="confidential")
    store = tmp_path / ".openkos" / "vectors.db"

    # First run: local backend, so the doc is embedded and stored.
    with vectorstore.open_vector_store(store) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), local_exemption=True)

    # Second run: remote backend, and `force=True` so the doc genuinely
    # reaches the gate rather than short-circuiting as a cache hit. This is
    # the sharper case: the operator explicitly asked for a re-embed, the
    # gate must still hold, and the stored vector must survive being denied.
    embedder = _FakeEmbedder()
    with vectorstore.open_vector_store(store) as db:
        report = reindex.reindex(
            bundle_dir, db, embedder, force=True, local_exemption=False
        )
        assert "concepts/secret" in db.meta_hashes()

    assert report.withheld_confidential == 1
    assert report.pruned == 0
    assert embedder.call_count == 0


def test_reindex_does_not_report_a_cache_hit_as_withheld(
    tmp_path: Path,
) -> None:
    """The gate sits AFTER the content-hash cache check, and the ordering is
    a claim rather than an accident: a cache hit performs zero embed calls,
    so there is no send for the gate to prevent. Counting it as withheld
    would report a privacy action that never had egress to stop, and would
    make the number climb on every routine run against a remote backend
    until it meant nothing."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="body",
        sensitivity="confidential",
    )
    store = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(store) as db:
        reindex.reindex(bundle_dir, db, _FakeEmbedder(), local_exemption=True)

    with vectorstore.open_vector_store(store) as db:
        report = reindex.reindex(bundle_dir, db, _FakeEmbedder(), local_exemption=False)

    assert report.cache_hits == 1
    assert report.withheld_confidential == 0


def test_reindex_withholds_the_model_tag_when_a_doc_was_withheld(
    tmp_path: Path,
) -> None:
    """The tag-persist gate is a self-healing mechanism, and a withheld doc
    breaks it the same way a `skipped` or `embed_failed` doc does.

    A withheld doc keeps its stale old-model vector. Persisting the new tag
    anyway would make the NEXT run see a matching tag, fall through to the
    content_hash comparison, find it unchanged, and treat it as a cache
    hit -- stranding it on the old model permanently, even after the operator
    points the backend back at this machine. Withholding the tag keeps
    `model_changed` true until a run finally covers every document."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", sensitivity="private")
    _write_doc(bundle_dir / "concepts" / "s.md", title="S", sensitivity="confidential")
    store = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(store) as db:
        report = reindex.reindex(
            bundle_dir, db, _FakeEmbedder(), model_tag="bge-m3", local_exemption=False
        )
        assert db.read_model_tag() is None

    assert report.withheld_confidential == 1
    assert report.model_reembedded is True
