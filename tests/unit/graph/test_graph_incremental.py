"""Per-document graph refresh (`sqlite_graph.refresh_graph_store`): an edit
rewrites only the touched documents' nodes and edges, links to targets that
did not exist are recovered when the target appears, the proximity-candidate
pass is recomputed globally, and every path ends content-identical to a whole
rebuild of the same bundle."""

import sqlite3
import zlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.graph import sqlite_graph
from openkos.graph.proximity import ProximityPair
from openkos.state import derived
from tests.unit.conftest import make_locked_error

_TABLES = ("nodes", "edges", "doc_outlinks", "node_facts", "doc_manifest", "meta")


def _write(
    bundle: Path,
    name: str,
    body: str = "",
    *,
    doc_type: str = "Concept",
    extra: str = "",
) -> Path:
    path = bundle / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {name}\n{extra}---\n{body}",
        encoding="utf-8",
    )
    return path


def _link(target: str) -> str:
    return f"see [{target}](/{target}.md)\n"


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    for i in range(5):
        _write(bundle, f"concepts/doc{i}", f"body {i}")
    return bundle


class _AllPairs:
    """A global candidate source: nominates every pair among the ids it is
    handed, with a deterministic distance, so the `_MAX_CANDIDATE_EDGES`
    ceiling binds on a bundle of a dozen documents."""

    def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
        ids = sorted(concept_ids)
        return [
            ProximityPair(
                source_id=a,
                target_id=b,
                distance=(zlib.crc32(f"{a}|{b}".encode()) % 1000) / 1000,
            )
            for i, a in enumerate(ids)
            for b in ids[i + 1 :]
        ]


def _state(db: Path) -> dict[str, list[tuple[object, ...]]]:
    """Everything a rebuild is supposed to determine, minus rowids."""
    conn = sqlite3.connect(db)
    try:
        return {
            table: sorted(
                conn.execute(f"SELECT * FROM {table}").fetchall(),  # noqa: S608
                key=repr,
            )
            for table in _TABLES
        }
    finally:
        conn.close()


def _edges(db: Path) -> list[tuple[str, str, str | None]]:
    conn = sqlite3.connect(db)
    try:
        return [
            (str(s), str(t), r)
            for s, t, r in conn.execute(
                "SELECT source_id, target_id, relation_type FROM edges "
                "ORDER BY source_id, target_id, relation_type"
            )
        ]
    finally:
        conn.close()


def _assert_equals_rebuild(
    db: Path,
    bundle: Path,
    tmp_path: Path,
    candidates: sqlite_graph.CandidateSource | None = None,
) -> None:
    reference = tmp_path / "reference" / "graph.db"
    reference.unlink(missing_ok=True)
    sqlite_graph.write_graph_store(reference, bundle, candidates=candidates)
    assert _state(db) == _state(reference)
    # The in-memory oracle: the persisted store agrees with `build_graph`.
    with sqlite_graph.build_graph(bundle, candidates=candidates) as oracle:
        assert _edges(db) == [
            (e.source_id, e.target_id, e.relation_type) for e in oracle.edges()
        ]


def _refresh(
    db: Path,
    bundle: Path,
    *,
    force: bool = False,
    candidates: sqlite_graph.CandidateSource | None = None,
) -> str:
    return sqlite_graph.refresh_graph_store(
        db, bundle, force=force, candidates=candidates
    )


def test_first_refresh_builds_then_unchanged_then_edit_is_incremental(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    assert _refresh(db, bundle) == "rebuilt"
    assert _refresh(db, bundle) == "unchanged"
    _write(bundle, "concepts/doc0", "edited " + _link("concepts/doc1"))
    assert _refresh(db, bundle) == "incremental"
    assert ("concepts/doc0", "concepts/doc1", None) in _edges(db)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_one_edit_rewrites_only_that_documents_rows(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _write(bundle, "concepts/doc1", _link("concepts/doc2"))
    _write(bundle, "concepts/doc3", _link("concepts/doc4"))
    _refresh(db, bundle)

    def rowids() -> dict[str, int]:
        conn = sqlite3.connect(db)
        try:
            return {
                str(s): int(r)
                for s, r in conn.execute("SELECT source_id, rowid FROM doc_outlinks")
            }
        finally:
            conn.close()

    before = rowids()
    _write(bundle, "concepts/doc1", "no more link")
    assert _refresh(db, bundle) == "incremental"
    after = rowids()
    assert after["concepts/doc3"] == before["concepts/doc3"]  # untouched row kept
    assert "concepts/doc1" not in after  # the edited document's outlink is gone
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_link_to_a_not_yet_existing_target_is_recovered_when_it_appears(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _write(bundle, "concepts/doc0", _link("concepts/late"))
    _refresh(db, bundle)
    assert not any(t == "concepts/late" for _, t, _ in _edges(db))

    _write(bundle, "concepts/late", "I exist now")
    assert _refresh(db, bundle) == "incremental"
    assert ("concepts/doc0", "concepts/late", None) in _edges(db)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_typed_relation_to_a_not_yet_existing_target_is_recovered(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _write(
        bundle,
        "concepts/doc0",
        extra="relations:\n  - target: concepts/late\n    type: depends_on\n",
    )
    _refresh(db, bundle)
    _write(bundle, "concepts/late", "arrives")
    assert _refresh(db, bundle) == "incremental"
    assert ("concepts/doc0", "concepts/late", "depends_on") in _edges(db)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_provenance_mirror_is_synthesised_for_a_recovered_link(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _write(
        bundle,
        "concepts/doc0",
        _link("sources/late"),
        extra="provenance:\n  - sources/late\n",
    )
    _refresh(db, bundle)
    _write(bundle, "sources/late", "raw", doc_type="Source")
    assert _refresh(db, bundle) == "incremental"
    assert ("concepts/doc0", "sources/late", "derived_from") in _edges(db)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_removed_target_drops_its_inbound_and_outbound_edges(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _write(bundle, "concepts/doc0", _link("concepts/doc1"))
    _write(bundle, "concepts/doc1", _link("concepts/doc2"))
    _refresh(db, bundle)
    assert ("concepts/doc0", "concepts/doc1", None) in _edges(db)

    (bundle / "concepts" / "doc1.md").unlink()
    assert _refresh(db, bundle) == "incremental"
    edges = _edges(db)
    assert not any("concepts/doc1" in (s, t) for s, t, _ in edges)
    _assert_equals_rebuild(db, bundle, tmp_path)

    # The dangling link survives as an outlink: the target coming back
    # re-creates the inbound edge, as a whole rebuild would.
    _write(bundle, "concepts/doc1", "back")
    assert _refresh(db, bundle) == "incremental"
    assert ("concepts/doc0", "concepts/doc1", None) in _edges(db)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_two_touched_documents_linking_each_other_yield_no_duplicate_edges(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    _write(bundle, "concepts/doc0", _link("concepts/doc1"))
    _write(bundle, "concepts/doc1", _link("concepts/doc0"))
    assert _refresh(db, bundle) == "incremental"
    edges = _edges(db)
    assert len(edges) == len(set(edges)) == 2
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_a_self_link_is_one_edge(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    _write(bundle, "concepts/doc0", _link("concepts/doc0"))
    _refresh(db, bundle)
    assert _edges(db) == [("concepts/doc0", "concepts/doc0", None)]
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_candidate_ceiling_is_unchanged_by_an_incremental_refresh(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    for i in range(12):  # 66 pairs: above the 50-edge ceiling
        _write(bundle, f"concepts/n{i:02d}", f"body {i}")
    db, cands = tmp_path / ".openkos" / "graph.db", _AllPairs()
    _refresh(db, bundle, candidates=cands)
    assert len(_edges(db)) == sqlite_graph._MAX_CANDIDATE_EDGES

    _write(bundle, "concepts/n03", "edited")
    assert _refresh(db, bundle, candidates=cands) == "incremental"
    assert len(_edges(db)) == sqlite_graph._MAX_CANDIDATE_EDGES
    _assert_equals_rebuild(db, bundle, tmp_path, cands)


def test_a_new_link_displaces_a_candidate_pair_and_a_removed_one_restores_it(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    for i in range(4):
        _write(bundle, f"concepts/n{i}", f"body {i}")
    db, cands = tmp_path / ".openkos" / "graph.db", _AllPairs()
    _refresh(db, bundle, candidates=cands)
    pair = ("concepts/n0", "concepts/n1")
    assert (*pair, None) in _edges(db)

    _write(bundle, "concepts/n0", _link("concepts/n1"))
    _refresh(db, bundle, candidates=cands)
    assert _edges(db).count((*pair, None)) == 1  # the link, not link + candidate
    assert ("concepts/n1", "concepts/n0", None) not in _edges(db)
    _assert_equals_rebuild(db, bundle, tmp_path, cands)

    _write(bundle, "concepts/n0", "no link now")
    _refresh(db, bundle, candidates=cands)
    assert (*pair, None) in _edges(db)  # restored as a candidate
    _assert_equals_rebuild(db, bundle, tmp_path, cands)


def test_a_candidate_pass_ranks_globally_across_untouched_documents(
    tmp_path: Path,
) -> None:
    """Touching one document can change which pairs among OTHER documents sit
    inside the ceiling, because ranking is global."""
    bundle = tmp_path / "bundle"
    for i in range(12):
        _write(bundle, f"concepts/n{i:02d}", f"body {i}")
    db, cands = tmp_path / ".openkos" / "graph.db", _AllPairs()
    _refresh(db, bundle, candidates=cands)
    before = _edges(db)
    _write(bundle, "concepts/n00", "now a Source", doc_type="Source")
    assert _refresh(db, bundle, candidates=cands) == "incremental"
    assert _edges(db) != before
    _assert_equals_rebuild(db, bundle, tmp_path, cands)


def test_a_source_document_never_joins_the_candidate_pass(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    for i in range(3):
        _write(bundle, f"concepts/n{i}", f"body {i}")
    db, cands = tmp_path / ".openkos" / "graph.db", _AllPairs()
    _refresh(db, bundle, candidates=cands)
    _write(bundle, "sources/s", "raw", doc_type="Source")
    _refresh(db, bundle, candidates=cands)
    assert not any("sources/s" in (s, t) for s, t, _ in _edges(db))
    _assert_equals_rebuild(db, bundle, tmp_path, cands)


def test_quarantined_source_withholds_pairs_incrementally(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _write(
        bundle,
        "sources/q",
        "raw",
        doc_type="Source",
        extra="extraction_notice: judge_unavailable\n",
    )
    for i in range(3):
        _write(bundle, f"concepts/n{i}", f"b{i}", extra="provenance:\n  - sources/q\n")
    db, cands = tmp_path / ".openkos" / "graph.db", _AllPairs()
    _refresh(db, bundle, candidates=cands)
    _write(bundle, "concepts/n9", "healthy", extra="")
    _refresh(db, bundle, candidates=cands)
    _assert_equals_rebuild(db, bundle, tmp_path, cands)


def test_dropping_the_candidate_source_removes_candidate_edges(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    for i in range(3):
        _write(bundle, f"concepts/n{i}", f"body {i}")
    _write(bundle, "concepts/n0", _link("concepts/n1"))
    db = tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle, candidates=_AllPairs())
    _write(bundle, "concepts/n2", "edit")
    assert _refresh(db, bundle) == "incremental"
    assert _edges(db) == [("concepts/n0", "concepts/n1", None)]
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_recorded_pairs_reproduce_the_stored_digest(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    _write(bundle, "concepts/doc0", "edit")
    _refresh(db, bundle)
    conn = sqlite3.connect(db)
    pairs = [
        (str(c), str(h))
        for c, h in conn.execute("SELECT concept_id, content_hash FROM doc_manifest")
    ]
    stored = derived.read_manifest_hash(conn)
    conn.close()
    assert len(pairs) == 5
    assert derived.manifest_digest(pairs) == stored


def test_a_store_with_no_recorded_pairs_rebuilds_once(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE doc_manifest")
    conn.commit()
    conn.close()
    _write(bundle, "concepts/doc0", "edit")
    assert _refresh(db, bundle) == "rebuilt"
    _write(bundle, "concepts/doc0", "edit again")
    assert _refresh(db, bundle) == "incremental"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_a_store_from_before_outlinks_rebuilds(tmp_path: Path) -> None:
    """A pre-change store has `nodes`/`edges`/`meta` only."""
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    conn = sqlite3.connect(db)
    for table in ("doc_outlinks", "node_facts", "doc_manifest"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    _write(bundle, "concepts/doc0", "edit")
    assert _refresh(db, bundle) == "rebuilt"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_a_different_schema_version_rebuilds(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    _write(bundle, "concepts/doc0", "edit")
    assert _refresh(db, bundle) == "rebuilt"
    _write(bundle, "concepts/doc0", "edit again")
    assert _refresh(db, bundle) == "incremental"


def test_recorded_pairs_that_do_not_reproduce_the_digest_rebuild(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE doc_manifest SET content_hash = 'tampered' "
        "WHERE concept_id = 'concepts/doc1'"
    )
    conn.commit()
    conn.close()
    _write(bundle, "concepts/doc0", "edit")
    assert _refresh(db, bundle) == "rebuilt"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_a_node_no_recorded_document_accounts_for_forces_a_rebuild(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO nodes (concept_id) VALUES ('ghost')")
    conn.commit()
    conn.close()
    _write(bundle, "concepts/doc0", "edit")
    assert _refresh(db, bundle) == "rebuilt"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_force_rebuilds_even_when_unchanged(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    assert _refresh(db, bundle, force=True) == "rebuilt"
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_failed_incremental_rolls_back_and_falls_back_to_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    _write(bundle, "concepts/doc0", "edit one")
    _write(bundle, "concepts/doc1", "edit two")

    real = sqlite_graph._read_doc
    calls: list[Path] = []

    def flaky(path: Path) -> object:
        calls.append(path)
        if len(calls) == 2:  # fail midway through the per-document update
            raise RuntimeError("boom")
        return real(path)

    monkeypatch.setattr(sqlite_graph, "_read_doc", flaky)
    assert _refresh(db, bundle) == "rebuilt"
    assert len(calls) > 2  # the update really started, then the rebuild ran
    monkeypatch.setattr(sqlite_graph, "_read_doc", real)
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_lock_contention_during_incremental_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    _write(bundle, "concepts/doc0", "edit")
    before = _state(db)

    def locked(*_a: object, **_k: object) -> None:
        raise make_locked_error()

    monkeypatch.setattr(sqlite_graph, "_read_doc", locked)
    with pytest.raises(sqlite3.OperationalError):
        _refresh(db, bundle)
    assert _state(db) == before


def test_a_failure_mid_update_leaves_no_partial_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback runs on a rolled-back store: even if the rebuild is the
    thing that then fails, the prior projection is untouched."""
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _write(bundle, "concepts/doc0", _link("concepts/doc1"))
    _refresh(db, bundle)
    before = _state(db)
    _write(bundle, "concepts/doc0", "edit")

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(sqlite_graph, "_read_doc", boom)
    monkeypatch.setattr(sqlite_graph, "_populate_graph_tables", boom)
    with pytest.raises(RuntimeError):
        _refresh(db, bundle)
    assert _state(db) == before


def test_an_unreadable_document_is_manifested_but_has_no_node(
    tmp_path: Path,
) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    path = _write(bundle, "concepts/doc0", "edit")
    # Valid frontmatter at the walk, corrupt by the second read.
    real = sqlite_graph._read_doc

    def corrupt(p: Path) -> object:
        return "unparseable frontmatter" if p == path else real(p)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(sqlite_graph, "_read_doc", corrupt)
        assert _refresh(db, bundle) == "incremental"
    finally:
        monkeypatch.undo()
    conn = sqlite3.connect(db)
    nodes = {str(r[0]) for r in conn.execute("SELECT concept_id FROM nodes")}
    conn.close()
    assert "concepts/doc0" not in nodes
    assert len(nodes) == 4


def test_empty_bundle_then_first_documents_are_incremental(tmp_path: Path) -> None:
    bundle, db = tmp_path / "bundle", tmp_path / ".openkos" / "graph.db"
    bundle.mkdir()
    assert _refresh(db, bundle) == "rebuilt"
    _write(bundle, "concepts/a", _link("concepts/b"))
    _write(bundle, "concepts/b", "hi")
    assert _refresh(db, bundle) == "incremental"
    assert _edges(db) == [("concepts/a", "concepts/b", None)]
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_reindex_graph_uses_the_per_document_path(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    sqlite_graph.reindex_graph(bundle, db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO nodes (concept_id) VALUES ('sentinel-not-rebuilt')")
    conn.commit()
    conn.close()
    # A rebuild would drop the sentinel; the per-document path keeps it only
    # if the trust check passes -- it must not, so assert the rebuild here ...
    _write(bundle, "concepts/doc0", "edit")
    sqlite_graph.reindex_graph(bundle, db)
    conn = sqlite3.connect(db)
    nodes = {str(r[0]) for r in conn.execute("SELECT concept_id FROM nodes")}
    conn.close()
    assert "sentinel-not-rebuilt" not in nodes
    _assert_equals_rebuild(db, bundle, tmp_path)


def test_staleness_probe_stays_hash_only(tmp_path: Path) -> None:
    bundle, db = _bundle(tmp_path), tmp_path / ".openkos" / "graph.db"
    _refresh(db, bundle)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE doc_manifest")  # the probe must not need it
    conn.commit()
    conn.close()
    assert derived.stale_derived_stores(bundle, [("graph", db)]) == ()
    _write(bundle, "concepts/doc0", "edit")
    assert derived.stale_derived_stores(bundle, [("graph", db)]) == ("graph",)
