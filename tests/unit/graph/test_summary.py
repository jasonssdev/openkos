"""Unit tests for `openkos.graph.summary.graph_edge_summary`: a read-only
`(total, typed)` count of concept-to-concept edges over the graph
projection, feeding the three-state message vocabulary Slice 0 wires into
`status`/`suggest-relations`/`contradictions` (issue #183, design.md's
"Slice 0 -- three-state message vocabulary").

`graph_edge_summary` builds the graph the SAME way `build_graph` does (one
`sqlite3(":memory:")` projection, closed before returning) -- it never
mutates the bundle and never leaves a connection open.
"""

from pathlib import Path

from openkos.graph.sqlite_graph import build_graph
from openkos.graph.summary import graph_edge_summary


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    body: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n{body}",
        encoding="utf-8",
    )


def _write_doc_with_relations(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    relations: str,
    body: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\nrelations:\n{relations}---\n{body}",
        encoding="utf-8",
    )


def test_graph_edge_summary_zero_edges(tmp_path: Path) -> None:
    """A bundle with concepts but no links or `relations:` entries reports
    `(0, 0)` -- state 1's precondition."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")

    total, typed = graph_edge_summary(bundle_dir)

    assert (total, typed) == (0, 0)


def test_graph_edge_summary_some_typed(tmp_path: Path) -> None:
    """A bundle with one untyped body-link edge and one typed `relations:`
    edge reports `(2, 1)` -- total includes both, typed counts only the
    `relation_type`-carrying row."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="See also [B](/concepts/b.md).\n",
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    _write_doc_with_relations(
        bundle_dir / "concepts" / "c.md",
        title="C",
        relations="  - target: concepts/b\n    type: relates_to\n",
    )

    total, typed = graph_edge_summary(bundle_dir)

    assert (total, typed) == (2, 1)


def test_graph_edge_summary_all_typed(tmp_path: Path) -> None:
    """A bundle whose only edges are typed `relations:` entries reports
    `total == typed`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")
    _write_doc_with_relations(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations="  - target: concepts/b\n    type: relates_to\n",
    )

    total, typed = graph_edge_summary(bundle_dir)

    assert (total, typed) == (1, 1)


def test_graph_edge_summary_excludes_concept_to_source_derived_from(
    tmp_path: Path,
) -> None:
    """A Concept's `## Related` backlink to its Source (a `derived_from`
    provenance-mirror edge, `okf.build_concept`) is NOT a concept-to-concept
    edge -- it must not inflate `total`/`typed`, even though it is a real
    row in the graph projection (issue #183, Fix 1)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "sources" / "s.md", doc_type="Source", title="S")
    path = bundle_dir / "concepts" / "a.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: Concept\ntitle: A\nprovenance:\n  - sources/s\n---\n"
        "See [S](/sources/s.md).\n",
        encoding="utf-8",
    )

    total, typed = graph_edge_summary(bundle_dir)

    assert (total, typed) == (0, 0)


def test_graph_edge_summary_empty_bundle(tmp_path: Path) -> None:
    """A bundle directory with no docs at all reports `(0, 0)` rather than
    raising."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    total, typed = graph_edge_summary(bundle_dir)

    assert (total, typed) == (0, 0)


def test_graph_edge_summary_with_supplied_store_matches_own_build(
    tmp_path: Path,
) -> None:
    """Supplying an already-open `store` returns the same `(total, typed)`
    tuple as letting `graph_edge_summary` open its own build over the same
    bundle (graph-projection-reuse)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        body="See also [B](/concepts/b.md).\n",
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B")

    with build_graph(bundle_dir) as store:
        supplied_result = graph_edge_summary(bundle_dir, store=store)

    assert supplied_result == graph_edge_summary(bundle_dir)


def test_graph_edge_summary_does_not_close_supplied_store(tmp_path: Path) -> None:
    """`graph_edge_summary` must never close a store it did not open itself
    (graph-projection-reuse ownership rule): the caller's store stays usable
    after the call returns."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A")

    with build_graph(bundle_dir) as store:
        graph_edge_summary(bundle_dir, store=store)
        # A closed sqlite3 connection raises ProgrammingError on further use.
        store.edges()
