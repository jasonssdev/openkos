"""Unit tests for `openkos.graph.sqlite_graph`: the in-memory node-edge
projection over the bundle.

`build_graph` mirrors `state/fts.py`'s `build_index` EXACTLY: a rebuild-per-run
`sqlite3(":memory:")` connection, a single `okf._iter_docs` pass, a
TOCTOU-guarded body re-read, and `skipped` notes for unreadable/unparseable
docs. Nodes are OKF concept ids (one per non-reserved doc); edges are
bundle-relative `[text](/….md)` markdown links extracted from doc bodies,
scoped to links that resolve to a KNOWN node id in the same projection --
external, non-bundle-relative, non-`.md`, and dangling-target links produce
NO edge, and the build never raises because of them.

Phase 2's tests query the raw `nodes`/`edges` tables directly via
`store._conn`, mirroring how `test_fts.py` exercises `idx._conn` directly
for its own low-level cases. Phase 3 (below) exercises the friendly
`nodes()`/`edges()`/`neighbors()` `GraphStore` query surface instead.
"""

import sqlite3
from pathlib import Path

import pytest

from openkos.graph import sqlite_graph
from openkos.graph.base import Edge, GraphStore
from openkos.model import okf

_REPO_ROOT = Path(__file__).resolve().parents[3]


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


def _node_ids(store: sqlite_graph.SqliteGraphStore) -> list[str]:
    rows = store._conn.execute(
        "SELECT concept_id FROM nodes ORDER BY concept_id"
    ).fetchall()
    return [row[0] for row in rows]


def _edge_rows(store: sqlite_graph.SqliteGraphStore) -> list[tuple[str, str, object]]:
    rows = store._conn.execute(
        "SELECT source_id, target_id, relation_type FROM edges "
        "ORDER BY source_id, target_id"
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


# --- Phase 2.1: node enumeration -------------------------------------------


def test_build_graph_creates_one_node_per_non_reserved_doc(tmp_path: Path) -> None:
    """Every non-reserved doc becomes exactly one node, keyed by its
    bundle-relative path with `.md` removed -- reserved files never appear."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    _write_doc(bundle_dir / "sources" / "call.md", doc_type="Source", title="Call")
    (bundle_dir / "index.md").write_text("# root\n", encoding="utf-8")

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = _node_ids(store)

    assert node_ids == ["concepts/stoicism", "sources/call"]


# --- Phase 2.2: edge extraction from a resolving link ----------------------


def test_bundle_relative_link_to_existing_node_creates_edge(tmp_path: Path) -> None:
    """A `[text](/concepts/x.md)` link whose target resolves to a known node
    becomes a directed edge with `relation_type IS NULL`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Epicureanism](/concepts/epicureanism.md) for the contrast.",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [("concepts/stoicism", "concepts/epicureanism", None)]


def test_link_with_anchor_still_resolves_after_anchor_is_stripped(
    tmp_path: Path,
) -> None:
    """A `#anchor` suffix on an otherwise-resolving link is stripped before
    matching against known node ids, and still produces an edge."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Epicureanism](/concepts/epicureanism.md#intro) here.",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [("concepts/stoicism", "concepts/epicureanism", None)]


# --- Phase 2.3: non-resolving links produce NO edge, build never raises ----


def test_external_relative_non_md_and_dangling_links_produce_no_edge(
    tmp_path: Path,
) -> None:
    """External URLs, links without a leading `/`, links not ending in
    `.md`, and links to a target that resolves to no known node all produce
    NO edge -- and the build does not raise because of any of them."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body=(
            "External: [SEP](https://plato.stanford.edu/entries/stoicism/)\n"
            "Relative, no leading slash: [rel](concepts/epicureanism.md)\n"
            "Non-.md target: [img](/assets/diagram.png)\n"
            "Dangling: [ghost](/concepts/does-not-exist.md)\n"
        ),
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == []


# --- Phase 2.4: duplicate (source, target) edges dedup before insert -------


def test_duplicate_source_target_edges_dedup_before_insert(tmp_path: Path) -> None:
    """Two separate links from the same doc to the same target collapse
    into a single edge row -- no duplicate `(source_id, target_id)` pairs."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body=(
            "First mention: [Epicureanism](/concepts/epicureanism.md).\n"
            "Second mention: [again](/concepts/epicureanism.md).\n"
        ),
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [("concepts/stoicism", "concepts/epicureanism", None)]


# --- Phase 2.5: TOCTOU -- second read/parse guard mirrors fts.py -----------


def test_build_graph_skips_doc_whose_second_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc that vanishes between `_iter_docs`'s first read and
    `build_graph`'s second body re-read (e.g. a concurrent `openkos forget`)
    is skipped and noted, never crashing the whole build -- mirrors
    `fts.build_index`'s TOCTOU-safe re-read guard."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stable.md", title="Stable")
    flaky_path = bundle_dir / "concepts" / "flaky.md"
    _write_doc(flaky_path, title="Flaky")

    read_counts: dict[Path, int] = {}
    original_read_text = Path.read_text

    def flaky_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        if self == flaky_path:
            read_counts[self] = read_counts.get(self, 0) + 1
            if read_counts[self] > 1:
                raise FileNotFoundError("simulated concurrent delete between reads")
        return original_read_text(
            self, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = _node_ids(store)

    assert node_ids == ["concepts/stable"]
    assert store.skipped == ["concepts/flaky.md: skipped (unreadable)"]


def test_build_graph_skips_doc_whose_second_parse_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc whose frontmatter becomes unparseable between `_iter_docs`'s
    first parse and `build_graph`'s second `okf.load_frontmatter` re-parse is
    skipped and noted, never crashing the whole build."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stable.md", title="Stable")
    _write_doc(bundle_dir / "concepts" / "flaky.md", title="Flaky")

    original_load_frontmatter = okf.load_frontmatter

    def flaky_load_frontmatter(text: str) -> tuple[dict[str, object], str]:
        if "title: Flaky" in text:
            raise ValueError("simulated corrupted frontmatter on re-read")
        return original_load_frontmatter(text)

    monkeypatch.setattr(okf, "load_frontmatter", flaky_load_frontmatter)

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = _node_ids(store)

    assert node_ids == ["concepts/stable"]
    assert store.skipped == ["concepts/flaky.md: skipped (unparseable frontmatter)"]


# --- Phase 2.6: rebuild determinism + connection lifecycle on exceptions ---


def test_rebuild_over_unchanged_bundle_is_deterministic(tmp_path: Path) -> None:
    """Building the SAME unchanged bundle twice yields an equivalent
    node/edge set both times -- no run-to-run drift from set/dict ordering."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Epicureanism](/concepts/epicureanism.md).",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as first:
        first_nodes, first_edges = _node_ids(first), _edge_rows(first)
    with sqlite_graph.build_graph(bundle_dir) as second:
        second_nodes, second_edges = _node_ids(second), _edge_rows(second)

    assert first_nodes == second_nodes == ["concepts/epicureanism", "concepts/stoicism"]
    assert (
        first_edges
        == second_edges
        == [("concepts/stoicism", "concepts/epicureanism", None)]
    )


class _FailingInsertConnection(sqlite3.Connection):
    """A `sqlite3.Connection` subclass that fails on `INSERT`, succeeds
    otherwise -- simulates a mid-build failure after the table DDL succeeds,
    to prove the connection is released rather than leaked (mirrors
    `test_fts.py::_FailingInsertConnection`, subclassed for the same
    C-extension reason: a real `sqlite3.Connection`'s `execute` cannot be
    monkeypatched directly)."""

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        """Raise `OperationalError` for `INSERT`, delegate everything else."""
        if sql.strip().startswith("INSERT"):
            raise sqlite3.OperationalError("simulated mid-build insert failure")
        return super().execute(sql, *args, **kwargs)  # type: ignore[arg-type]


def test_build_graph_closes_connection_on_mid_build_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception raised mid-build (after the tables exist, while
    inserting a node row) still closes the in-memory connection -- it must
    not leak, waiting only on GC, when `build_graph` never returns a
    `SqliteGraphStore` to own it."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    original_connect = sqlite3.connect
    captured: dict[str, sqlite3.Connection] = {}

    def fake_connect(
        database: str, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        conn = original_connect(database, factory=_FailingInsertConnection)
        captured["conn"] = conn
        return conn

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with pytest.raises(sqlite3.OperationalError):
        sqlite_graph.build_graph(bundle_dir)

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


# --- Integration: good-life-demo bundle fixture (runtime harness) ----------


def test_build_graph_over_good_life_demo_bundle_resolves_expected_edges() -> None:
    """Building over the demo bundle resolves the `## Related` backlinks
    into real edges between existing concept/person/decision/source nodes,
    including a derived concept's provenance backlink to its Source doc."""
    bundle_dir = _REPO_ROOT / "examples" / "good-life-demo" / "bundle"

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = _node_ids(store)
        edges = _edge_rows(store)

    assert "concepts/stoicism" in node_ids
    assert "sources/notes-on-the-enchiridion-2026-07-05" in node_ids
    assert (
        "concepts/stoicism",
        "concepts/epicureanism",
        None,
    ) in edges
    assert (
        "concepts/stoicism",
        "sources/notes-on-the-enchiridion-2026-07-05",
        None,
    ) in edges
    assert store.skipped == []


def test_build_graph_skips_unreadable_file(tmp_path: Path) -> None:
    """A file that cannot be decoded on `_iter_docs`'s FIRST pass is skipped
    and noted; a valid doc still becomes a node."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "readable.md", title="Readable")
    unreadable = bundle_dir / "concepts" / "unreadable.md"
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.write_bytes(b"\xff\xfe\x00\x01not-utf8")

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = _node_ids(store)

    assert node_ids == ["concepts/readable"]
    assert store.skipped == ["concepts/unreadable.md: skipped (unreadable)"]


def test_build_graph_skips_unparseable_frontmatter(tmp_path: Path) -> None:
    """A file with no parseable frontmatter on `_iter_docs`'s FIRST pass is
    skipped and noted; a valid doc still becomes a node."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "readable.md", title="Readable")
    (bundle_dir / "concepts" / "broken.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = _node_ids(store)

    assert node_ids == ["concepts/readable"]
    assert store.skipped == ["concepts/broken.md: skipped (unparseable frontmatter)"]


# --- Fix: exclude fenced code blocks from edge extraction -------------------


def test_link_inside_fenced_code_block_produces_no_edge_but_same_link_in_prose_does(
    tmp_path: Path,
) -> None:
    """Concept docs can embed raw ingested source material verbatim under a
    `## Source content` heading (see `okf.build_source_concept`). If that
    embedded content contains fenced code with markdown-link syntax pointing
    at an existing concept, it must NOT become an edge -- but the SAME link
    target in normal prose (or `## Related`) still resolves exactly as
    before."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "fenced-source.md",
        title="Fenced Source",
        body=(
            "## Source content\n\n"
            "```\n"
            "Raw ingested text mentioning "
            "[Epicureanism](/concepts/epicureanism.md) verbatim.\n"
            "```\n"
        ),
    )
    _write_doc(
        bundle_dir / "concepts" / "prose-source.md",
        title="Prose Source",
        body=(
            "## Related\n"
            "See [Epicureanism](/concepts/epicureanism.md) for the contrast.\n"
        ),
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert ("concepts/fenced-source", "concepts/epicureanism", None) not in edges
    assert ("concepts/prose-source", "concepts/epicureanism", None) in edges


def test_link_to_a_skipped_doc_produces_no_edge_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link's target doc exists on disk but is skipped during the build
    (e.g. a concurrent TOCTOU delete makes it unreadable on the second read,
    mirroring `test_build_graph_skips_doc_whose_second_read_fails`) -- its id
    never becomes a node, so the link produces NO edge, and the build itself
    does not raise."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Flaky](/concepts/flaky.md) for more.",
    )
    flaky_path = bundle_dir / "concepts" / "flaky.md"
    _write_doc(flaky_path, title="Flaky")

    read_counts: dict[Path, int] = {}
    original_read_text = Path.read_text

    def flaky_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        if self == flaky_path:
            read_counts[self] = read_counts.get(self, 0) + 1
            if read_counts[self] > 1:
                raise FileNotFoundError("simulated concurrent delete between reads")
        return original_read_text(
            self, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == []
    assert store.skipped == ["concepts/flaky.md: skipped (unreadable)"]


# --- Phase 3.1/3.2: GraphStore query surface --------------------------------


def test_sqlite_graph_store_satisfies_graphstore_protocol_at_runtime(
    tmp_path: Path,
) -> None:
    """`SqliteGraphStore` is now a genuine `GraphStore`: `isinstance` holds
    via `@runtime_checkable`, without any explicit inheritance."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        assert isinstance(store, GraphStore)


def test_nodes_returns_every_built_node_sorted(tmp_path: Path) -> None:
    """`nodes()` returns exactly the projection's node ids, in sorted
    (not insertion) order."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")
    _write_doc(bundle_dir / "sources" / "call.md", doc_type="Source", title="Call")

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = store.nodes()

    assert node_ids == [
        "concepts/epicureanism",
        "concepts/stoicism",
        "sources/call",
    ]


def test_nodes_on_empty_projection_returns_empty_list(tmp_path: Path) -> None:
    """An empty bundle (no docs at all) yields an empty, non-raising
    `nodes()` result."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True)

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = store.nodes()

    assert node_ids == []


def test_edges_returns_every_built_edge_as_edge_objects_sorted(
    tmp_path: Path,
) -> None:
    """`edges()` returns exactly the projection's edges as `Edge` instances,
    sorted by `(source_id, target_id)`, each with `relation_type is None`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body=(
            "See [Epicureanism](/concepts/epicureanism.md) and "
            "[Call](/sources/call.md)."
        ),
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")
    _write_doc(bundle_dir / "sources" / "call.md", doc_type="Source", title="Call")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = store.edges()

    assert edges == [
        Edge(source_id="concepts/stoicism", target_id="concepts/epicureanism"),
        Edge(source_id="concepts/stoicism", target_id="sources/call"),
    ]
    assert all(edge.relation_type is None for edge in edges)


def test_edges_on_empty_projection_returns_empty_list(tmp_path: Path) -> None:
    """A bundle with docs but no resolving links yields an empty, non-raising
    `edges()` result."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = store.edges()

    assert edges == []


def test_neighbors_returns_out_edge_targets_sorted(tmp_path: Path) -> None:
    """`neighbors(concept_id)` returns the out-neighbor node ids for a node
    with multiple out-edges, sorted."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body=(
            "See [Epicureanism](/concepts/epicureanism.md) and "
            "[Call](/sources/call.md)."
        ),
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")
    _write_doc(bundle_dir / "sources" / "call.md", doc_type="Source", title="Call")

    with sqlite_graph.build_graph(bundle_dir) as store:
        neighbor_ids = store.neighbors("concepts/stoicism")

    assert neighbor_ids == ["concepts/epicureanism", "sources/call"]


def test_neighbors_of_a_node_with_no_out_edges_returns_empty_list(
    tmp_path: Path,
) -> None:
    """A node with no out-edges (but that DOES exist as a node) returns
    `[]` from `neighbors()`, not a raise."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        neighbor_ids = store.neighbors("concepts/stoicism")

    assert neighbor_ids == []


def test_neighbors_of_an_unknown_node_id_returns_empty_list_without_raising(
    tmp_path: Path,
) -> None:
    """`neighbors()` on a concept id that is not even a node in the
    projection degrades to `[]` rather than raising."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        neighbor_ids = store.neighbors("concepts/does-not-exist")

    assert neighbor_ids == []


def test_query_surface_is_deterministic_across_repeated_calls_and_rebuilds(
    tmp_path: Path,
) -> None:
    """Calling `nodes()`/`edges()`/`neighbors()` twice on the SAME store
    yields identical results, and rebuilding the SAME unchanged bundle
    yields identical `nodes()`/`edges()` results too."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Epicureanism](/concepts/epicureanism.md).",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        assert store.nodes() == store.nodes()
        assert store.edges() == store.edges()
        assert store.neighbors("concepts/stoicism") == store.neighbors(
            "concepts/stoicism"
        )

    with sqlite_graph.build_graph(bundle_dir) as first:
        first_nodes, first_edges = first.nodes(), first.edges()
    with sqlite_graph.build_graph(bundle_dir) as second:
        second_nodes, second_edges = second.nodes(), second.edges()

    assert first_nodes == second_nodes
    assert first_edges == second_edges
