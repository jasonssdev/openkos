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

import hashlib
import sqlite3
from pathlib import Path

import pytest

from openkos.graph import sqlite_graph
from openkos.graph.base import Edge, GraphStore
from openkos.model import okf
from openkos.state import derived

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


def _write_doc_with_relations(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    relations: str,
    body: str = "",
) -> None:
    """Write a doc whose frontmatter includes a `relations:` block, given as
    raw YAML-shaped text (e.g. `"  - target: concepts/x\\n    type: depends_on\\n"`),
    mirroring `test_okf.py`'s raw-frontmatter-text fixture style for
    `relations:` (see `test_check_conformance_passes_on_well_formed_relations`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\nrelations:\n{relations}---\n{body}",
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
    ) -> str:
        if self == flaky_path:
            read_counts[self] = read_counts.get(self, 0) + 1
            if read_counts[self] > 1:
                raise FileNotFoundError("simulated concurrent delete between reads")
        return original_read_text(self, encoding=encoding, errors=errors)

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
    ) -> str:
        if self == flaky_path:
            read_counts[self] = read_counts.get(self, 0) + 1
            if read_counts[self] > 1:
                raise FileNotFoundError("simulated concurrent delete between reads")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == []
    assert store.skipped == ["concepts/flaky.md: skipped (unreadable)"]


# --- PR3 (typed-relationships): typed edges from `relations:` frontmatter --


def test_typed_relation_edge_carries_its_relation_type(tmp_path: Path) -> None:
    """A `relations:` entry whose `target` resolves to a known node becomes a
    typed edge carrying that entry's `type` as `relation_type` (spec: "Typed
    relation edge carries its relation_type"; task 3.1)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc_with_relations(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        relations="  - target: concepts/epicureanism\n    type: depends_on\n",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [
        ("concepts/stoicism", "concepts/epicureanism", "depends_on"),
    ]


def test_untyped_link_edge_remains_null_relation_type_without_relations_key(
    tmp_path: Path,
) -> None:
    """Approval test (safety net): a doc with no `relations:` key, whose body
    has an ordinary bundle-relative link, still produces an untyped edge with
    `relation_type IS NULL` -- unaffected by the new typed-edge second pass
    (spec: "Untyped-link edge remains NULL relation_type"; task 3.2)."""
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


def test_untyped_link_extraction_byte_identical_regression_when_relations_absent(
    tmp_path: Path,
) -> None:
    """Regression (approval test): for a mixed bundle of docs that all lack a
    `relations:` key, the untyped `_LINK_RE` node/edge set is byte-identical
    to what it was before this PR added the typed-edge second pass (task
    3.3)."""
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
        node_ids = _node_ids(store)
        edges = _edge_rows(store)

    assert node_ids == ["concepts/epicureanism", "concepts/stoicism", "sources/call"]
    assert edges == [
        ("concepts/stoicism", "concepts/epicureanism", None),
        ("concepts/stoicism", "sources/call", None),
    ]


def test_relation_entry_with_unresolvable_target_produces_no_typed_edge(
    tmp_path: Path,
) -> None:
    """A `relations:` entry whose `target` does not resolve to a known node
    id is dropped silently -- consistent with the existing untyped-link
    drop-if-unknown behavior -- while a second, resolvable entry on the same
    doc still produces its own typed edge (design: drop-if-unresolvable;
    task 3.4)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc_with_relations(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        relations=(
            "  - target: concepts/does-not-exist\n    type: references\n"
            "  - target: concepts/epicureanism\n    type: depends_on\n"
        ),
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [
        ("concepts/stoicism", "concepts/epicureanism", "depends_on"),
    ]


def test_typed_and_untyped_edge_between_same_pair_coexist_as_two_rows(
    tmp_path: Path,
) -> None:
    """A typed `relations:` edge and an untyped `_LINK_RE` body-link edge
    between the SAME `(source, target)` pair are DISTINCT rows -- deduping is
    keyed on `(source_id, target_id, relation_type)`, not `(source_id,
    target_id)` alone, so a `NULL` row and a typed row for the same pair both
    survive, with the `NULL` row sorted first (design: dedup key + `NULLs
    first` ordering; task 3.4/3.6)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc_with_relations(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        relations="  - target: concepts/epicureanism\n    type: depends_on\n",
        body="See [Epicureanism](/concepts/epicureanism.md) for the contrast.",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = store.edges()

    assert edges == [
        Edge(source_id="concepts/stoicism", target_id="concepts/epicureanism"),
        Edge(
            source_id="concepts/stoicism",
            target_id="concepts/epicureanism",
            relation_type="depends_on",
        ),
    ]


def test_malformed_relations_contributes_no_typed_edges_and_is_noted_in_skipped(
    tmp_path: Path,
) -> None:
    """A doc whose `relations:` frontmatter is malformed (here, a non-list
    scalar, which makes `okf.decode_relations` fail closed with
    `ValueError`) still becomes a node and never crashes the build, but
    contributes ZERO typed edges AND is recorded in `store.skipped` --
    mirroring the other skip paths' `_skip_note` format -- instead of
    degrading silently and unobservably."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\nrelations: not-a-list\n---\n",
        encoding="utf-8",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = _node_ids(store)
        edges = _edge_rows(store)

    assert node_ids == ["concepts/epicureanism", "concepts/stoicism"]
    assert edges == []
    assert store.skipped == ["concepts/stoicism.md: skipped (malformed relations)"]


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


# --- Spec: "Projection Is A Read-Only Derived Cache" ------------------------


def test_build_graph_never_touches_disk(tmp_path: Path) -> None:
    """`build_graph` (and exercising its query surface afterwards) creates no
    `.openkos/` directory, no `*.db` file, and no new path of any kind under
    the bundle -- the projection lives entirely in `sqlite3(":memory:")`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Epicureanism](/concepts/epicureanism.md).",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    before_paths = set(bundle_dir.rglob("*"))

    with sqlite_graph.build_graph(bundle_dir) as store:
        store.nodes()
        store.edges()
        for concept_id in store.nodes():
            store.neighbors(concept_id)
        db_file = store._conn.execute("PRAGMA database_list").fetchall()[0][2]

    after_paths = set(bundle_dir.rglob("*"))

    assert after_paths == before_paths
    assert not (bundle_dir / ".openkos").exists()
    assert list(bundle_dir.rglob("*.db")) == []
    assert db_file == ""  # sqlite3(":memory:") has no backing file at all


def test_build_graph_writes_nothing_to_the_bundle_bytes_and_mtime_unchanged() -> None:
    """Building the projection over a real bundle (and exercising its full
    query surface) leaves every bundle file's bytes and mtime byte-for-byte
    identical -- the derived cache never rewrites, touches, or adds to the
    on-disk bundle it was built from."""
    bundle_dir = _REPO_ROOT / "examples" / "good-life-demo" / "bundle"
    files = sorted(p for p in bundle_dir.rglob("*") if p.is_file())
    before = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in files
    }

    with sqlite_graph.build_graph(bundle_dir) as store:
        node_ids = store.nodes()
        store.edges()
        for concept_id in node_ids:
            store.neighbors(concept_id)

    after_files = sorted(p for p in bundle_dir.rglob("*") if p.is_file())
    after = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in after_files
    }

    assert after == before


# --- Phase 2 (Slice 5, PR2): on-disk persistence ----------------------------


def test_write_graph_store_persists_the_same_nodes_and_edges_build_graph_produces(
    tmp_path: Path,
) -> None:
    """`write_graph_store` writes an on-disk projection containing the SAME
    nodes/edges `build_graph` would produce in memory over the same bundle
    (graph-projection: Reindex persists the graph index to disk; one node
    per concept)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Epicureanism](/concepts/epicureanism.md).",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")
    db_path = tmp_path / ".openkos" / "graph.db"

    sqlite_graph.write_graph_store(db_path, bundle_dir)

    with sqlite_graph.build_graph(bundle_dir) as expected_store:
        expected_nodes = expected_store.nodes()
        expected_edges = expected_store.edges()

    conn = sqlite3.connect(str(db_path))
    on_disk_nodes = [
        row[0]
        for row in conn.execute("SELECT concept_id FROM nodes ORDER BY concept_id")
    ]
    on_disk_edges = [
        Edge(source_id=row[0], target_id=row[1], relation_type=row[2])
        for row in conn.execute(
            "SELECT source_id, target_id, relation_type FROM edges "
            "ORDER BY source_id, target_id, relation_type"
        )
    ]
    conn.close()

    assert on_disk_nodes == expected_nodes
    assert on_disk_edges == expected_edges
    assert on_disk_nodes == ["concepts/epicureanism", "concepts/stoicism"]


def test_write_graph_store_creates_no_footprint_before_first_call(
    tmp_path: Path,
) -> None:
    """No `.openkos/graph.db` exists before `write_graph_store` runs
    (derived-index-cache: No derived index before first reindex)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    db_path = tmp_path / ".openkos" / "graph.db"

    assert not db_path.exists()

    sqlite_graph.write_graph_store(db_path, bundle_dir)

    assert db_path.exists()


def test_write_graph_store_stores_a_caller_supplied_manifest_hash_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-supplied `manifest_hash` is stored as-is, never recomputed --
    mirrors `state/fts.py::write_fts_index`'s carried-over correction
    (Finding C): `state/reindex.py`'s decision digest and the persisted
    value must be the SAME bundle snapshot."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    db_path = tmp_path / ".openkos" / "graph.db"

    def _fail_if_called(bundle_dir_arg: Path) -> str:
        raise AssertionError(
            "bundle_manifest_hash must not be recomputed when manifest_hash is supplied"
        )

    monkeypatch.setattr(derived, "bundle_manifest_hash", _fail_if_called)

    sqlite_graph.write_graph_store(db_path, bundle_dir, manifest_hash="caller-digest")

    conn = sqlite3.connect(str(db_path))
    stored = derived.read_manifest_hash(conn)
    conn.close()
    assert stored == "caller-digest"


def test_write_graph_store_leaves_prior_projection_intact_on_mid_rebuild_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure mid-rebuild rolls back completely -- the PRIOR nodes/edges
    and PRIOR `meta.manifest_hash` survive untouched, mirroring
    `state/fts.py::write_fts_index`'s atomicity correction (Finding B):
    the DROP + rebuild + manifest write all happen inside one explicit
    transaction."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md", title="Stoicism", body="version one"
    )
    db_path = tmp_path / ".openkos" / "graph.db"

    sqlite_graph.write_graph_store(db_path, bundle_dir, manifest_hash="digest-one")
    conn = sqlite3.connect(str(db_path))
    prior_nodes = conn.execute("SELECT concept_id FROM nodes").fetchall()
    prior_manifest = derived.read_manifest_hash(conn)
    conn.close()
    assert prior_nodes == [("concepts/stoicism",)]
    assert prior_manifest == "digest-one"

    _write_doc(
        bundle_dir / "concepts" / "stoicism.md", title="Stoicism", body="version two"
    )
    original_populate = sqlite_graph._populate_graph_tables

    def _crashing_populate(conn: sqlite3.Connection, bundle_dir_arg: Path) -> list[str]:
        original_populate(conn, bundle_dir_arg)
        raise RuntimeError("simulated crash mid-rebuild")

    monkeypatch.setattr(sqlite_graph, "_populate_graph_tables", _crashing_populate)

    with pytest.raises(RuntimeError, match="simulated crash mid-rebuild"):
        sqlite_graph.write_graph_store(db_path, bundle_dir, manifest_hash="digest-two")

    conn = sqlite3.connect(str(db_path))
    nodes_after = conn.execute("SELECT concept_id FROM nodes").fetchall()
    manifest_after = derived.read_manifest_hash(conn)
    conn.close()

    assert nodes_after == prior_nodes
    assert manifest_after == prior_manifest


def test_build_graph_direct_call_still_creates_no_disk_footprint_after_persistence_added(
    tmp_path: Path,
) -> None:
    """A direct `build_graph(bundle_dir)` call remains entirely in-memory
    even though the on-disk writer path now exists (graph-projection:
    Projection never touches disk, regression)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    before = set(tmp_path.rglob("*"))

    with sqlite_graph.build_graph(bundle_dir) as store:
        store.nodes()

    after = set(tmp_path.rglob("*"))
    assert after == before
    assert not (bundle_dir / ".openkos").exists()


def test_open_graph_store_readonly_returns_none_when_absent(tmp_path: Path) -> None:
    """`open_graph_store_readonly` returns `None` for a non-existent path,
    never creating one (graph-projection: Persisted index read-only for
    non-reindex consumers)."""
    db_path = tmp_path / ".openkos" / "graph.db"

    assert sqlite_graph.open_graph_store_readonly(db_path) is None
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_open_graph_store_readonly_reads_persisted_data_without_writing(
    tmp_path: Path,
) -> None:
    """A read-only open reads the persisted nodes/edges correctly, and
    performs zero writes to the on-disk file (graph-projection: Persisted
    index read-only for non-reindex consumers)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="See [Epicureanism](/concepts/epicureanism.md).",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")
    db_path = tmp_path / ".openkos" / "graph.db"
    sqlite_graph.write_graph_store(db_path, bundle_dir)
    bytes_before = db_path.read_bytes()

    store = sqlite_graph.open_graph_store_readonly(db_path)
    assert store is not None
    nodes = store.nodes()
    edges = store.edges()
    store.close()

    assert nodes == ["concepts/epicureanism", "concepts/stoicism"]
    assert edges == [
        Edge(
            source_id="concepts/stoicism",
            target_id="concepts/epicureanism",
            relation_type=None,
        )
    ]
    assert db_path.read_bytes() == bytes_before


def test_open_graph_store_readonly_never_writes_even_on_write_attempt(
    tmp_path: Path,
) -> None:
    """A read-only handle's underlying connection refuses a write attempt --
    proves the open is genuinely read-only, not merely conventionally
    unused."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    db_path = tmp_path / ".openkos" / "graph.db"
    sqlite_graph.write_graph_store(db_path, bundle_dir)

    store = sqlite_graph.open_graph_store_readonly(db_path)
    assert store is not None
    with pytest.raises(sqlite3.OperationalError):
        store._conn.execute("INSERT INTO nodes (concept_id) VALUES ('x')")
    store.close()


def test_open_graph_store_readonly_raises_on_a_corrupt_existing_file(
    tmp_path: Path,
) -> None:
    """An EXISTING `graph.db` that is not a valid SQLite/`nodes`-table file
    raises a `sqlite3.Error` immediately at open time -- rather than only
    failing later on the first real query call -- so the CLI's
    open-or-degrade layer can catch it at a single, well-defined call site
    (Slice 5, PR3: query-command's absent-OR-unopenable/corrupt degrade
    trigger; mirrors `state/fts.py::open_fts_index_readonly`'s identical
    validation-probe posture)."""
    db_path = tmp_path / ".openkos" / "graph.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    with pytest.raises(sqlite3.Error):
        sqlite_graph.open_graph_store_readonly(db_path)


# --- reindex_graph (mirrors state/reindex.py's `_reindex_fts` gate) ---------


def _graph_canary_node_exists(graph_db_path: Path) -> bool:
    """Probe whether a hand-inserted sentinel node survives a
    `reindex_graph` call -- mirrors `test_reindex.py`'s FTS canary helper."""
    conn = sqlite3.connect(str(graph_db_path))
    try:
        row = conn.execute(
            "SELECT concept_id FROM nodes WHERE concept_id = 'zz-canary'"
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def _insert_graph_canary_node(graph_db_path: Path) -> None:
    conn = sqlite3.connect(str(graph_db_path))
    try:
        conn.execute("INSERT INTO nodes (concept_id) VALUES ('zz-canary')")
        conn.commit()
    finally:
        conn.close()


def test_reindex_graph_first_run_persists_store_matching_build_graph(
    tmp_path: Path,
) -> None:
    """A first `reindex_graph()` call writes an on-disk graph projection
    matching an equivalent `build_graph` call (graph-projection: Reindex
    persists the graph index to disk)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    graph_db_path = tmp_path / ".openkos" / "graph.db"
    assert not graph_db_path.exists()

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)

    assert graph_db_path.exists()
    with sqlite_graph.build_graph(bundle_dir) as expected_store:
        expected = set(expected_store.nodes())
    conn = sqlite3.connect(str(graph_db_path))
    on_disk = {row[0] for row in conn.execute("SELECT concept_id FROM nodes")}
    conn.close()

    assert on_disk == expected
    assert on_disk == {"concepts/stoicism"}


def test_reindex_graph_unchanged_bundle_skips_rebuild(tmp_path: Path) -> None:
    """A second `reindex_graph()` run over an UNCHANGED bundle does not
    rebuild the tables at all (derived-index-cache: Unchanged bundle reuses
    the cached index)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    graph_db_path = tmp_path / ".openkos" / "graph.db"

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)
    _insert_graph_canary_node(graph_db_path)

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)

    assert _graph_canary_node_exists(graph_db_path)


def test_reindex_graph_any_document_change_rebuilds_whole_index(
    tmp_path: Path,
) -> None:
    """Editing a single document invalidates the manifest, triggering a
    FULL rebuild on the next `reindex_graph()` run (derived-index-cache: Any
    document change invalidates the cache; Single-document edit triggers a
    full rebuild)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "stoicism.md"
    _write_doc(doc_path, title="Stoicism", body="version one")
    graph_db_path = tmp_path / ".openkos" / "graph.db"

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)
    _insert_graph_canary_node(graph_db_path)

    _write_doc(doc_path, title="Stoicism", body="version two")
    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)

    assert not _graph_canary_node_exists(graph_db_path)
    conn = sqlite3.connect(str(graph_db_path))
    rows = conn.execute("SELECT concept_id FROM nodes").fetchall()
    conn.close()
    assert {row[0] for row in rows} == {"concepts/stoicism"}


def test_reindex_graph_force_rebuilds_even_when_manifest_unchanged(
    tmp_path: Path,
) -> None:
    """`force=True` rebuilds even when the manifest hash is unchanged."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    graph_db_path = tmp_path / ".openkos" / "graph.db"

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)
    _insert_graph_canary_node(graph_db_path)

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path, force=True)

    assert not _graph_canary_node_exists(graph_db_path)


def test_reindex_graph_meta_manifest_matches_derived_bundle_manifest_hash(
    tmp_path: Path,
) -> None:
    """The persisted `meta.manifest_hash` equals
    `derived.bundle_manifest_hash(bundle_dir)` computed independently."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", title="Stoicism")
    graph_db_path = tmp_path / ".openkos" / "graph.db"

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)

    conn = sqlite3.connect(str(graph_db_path))
    stored = derived.read_manifest_hash(conn)
    conn.close()

    assert stored == derived.bundle_manifest_hash(bundle_dir)


def test_reindex_graph_edited_doc_stays_invisible_to_readonly_open_until_next_run(
    tmp_path: Path,
) -> None:
    """An edited doc's graph node/edge changes stay invisible to a
    read-only `open_graph_store_readonly` handle until the NEXT
    `reindex_graph` run -- no auto-refresh, no query-side recompute
    (derived-index-cache: Edited doc stays invisible to query until the
    next reindex)."""
    bundle_dir = tmp_path / "bundle"
    doc_path = bundle_dir / "concepts" / "stoicism.md"
    _write_doc(doc_path, title="Stoicism", body="version one")
    graph_db_path = tmp_path / ".openkos" / "graph.db"

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)

    _write_doc(bundle_dir / "concepts" / "new-target.md", title="New Target")
    _write_doc(
        doc_path, title="Stoicism", body="See [New Target](/concepts/new-target.md)."
    )

    store_before = sqlite_graph.open_graph_store_readonly(graph_db_path)
    assert store_before is not None
    edges_before = store_before.edges()
    store_before.close()

    sqlite_graph.reindex_graph(bundle_dir, graph_db_path)

    store_after = sqlite_graph.open_graph_store_readonly(graph_db_path)
    assert store_after is not None
    edges_after = store_after.edges()
    store_after.close()

    assert edges_before == []
    assert edges_after == [
        Edge(
            source_id="concepts/stoicism",
            target_id="concepts/new-target",
            relation_type=None,
        )
    ]
