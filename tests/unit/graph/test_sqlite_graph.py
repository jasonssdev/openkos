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
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.graph import sqlite_graph
from openkos.graph.base import Edge, GraphStore
from openkos.graph.proximity import ProximityPair
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


def _write_doc_with_provenance(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    provenance: list[str],
    body: str = "",
) -> None:
    """Write a doc whose frontmatter includes a `provenance:` list, given as
    concept ids (`sources/<slug>` or `concepts/<slug>`), mirroring
    `_write_doc_with_relations`'s raw-frontmatter-text fixture style."""
    path.parent.mkdir(parents=True, exist_ok=True)
    provenance_lines = "".join(f"  - {entry}\n" for entry in provenance)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\nprovenance:\n{provenance_lines}---\n{body}",
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


# --- Provenance-mirror synthesis (#135): body links typed derived_from -----


def test_provenance_mirror_body_link_is_typed_derived_from(tmp_path: Path) -> None:
    """A body link from a concept to a target that IS a member of the
    concept's `provenance:` frontmatter list is synthesized as a
    `derived_from` edge at projection time -- no `relations:` entry needed
    (spec: "Provenance-mirror link to a source is synthesized as
    derived_from"; task 1.1)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc_with_provenance(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        provenance=["sources/foo"],
        body="## Related\n\n[foo](/sources/foo.md) — source this was extracted from\n",
    )
    _write_doc(bundle_dir / "sources" / "foo.md", doc_type="Source", title="Foo")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [("concepts/stoicism", "sources/foo", "derived_from")]


def test_multi_entry_provenance_types_each_matching_target(tmp_path: Path) -> None:
    """A document with a two-entry `provenance:` list and matching body links
    to both targets has BOTH edges synthesized as `derived_from` (spec:
    "Multi-entry provenance list types each matching target"; task 1.2)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc_with_provenance(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        provenance=["sources/a", "sources/b"],
        body=(
            "## Related\n\n"
            "[a](/sources/a.md) — source this was extracted from\n"
            "[b](/sources/b.md) — source this was extracted from\n"
        ),
    )
    _write_doc(bundle_dir / "sources" / "a.md", doc_type="Source", title="A")
    _write_doc(bundle_dir / "sources" / "b.md", doc_type="Source", title="B")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [
        ("concepts/stoicism", "sources/a", "derived_from"),
        ("concepts/stoicism", "sources/b", "derived_from"),
    ]


def test_provenance_mirror_typing_keys_on_membership_not_source_prefix(
    tmp_path: Path,
) -> None:
    """A `query --save` concept whose `provenance:` list names another
    CONCEPT (not a `sources/` doc) and whose body links to it also gets
    `derived_from` -- typing is keyed on list membership, never a
    `sources/`-prefix check (spec: "Provenance-mirror link to a cited
    concept is also synthesized"; task 1.3)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc_with_provenance(
        bundle_dir / "concepts" / "saved-answer.md",
        title="Saved Answer",
        provenance=["concepts/bar"],
        body="## Related\n\n[bar](/concepts/bar.md) — concept this answer cites\n",
    )
    _write_doc(bundle_dir / "concepts" / "bar.md", title="Bar")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert edges == [("concepts/saved-answer", "concepts/bar", "derived_from")]


def test_genuine_link_outside_provenance_list_stays_untyped(tmp_path: Path) -> None:
    """A body link to a target that is NOT a member of the source's
    `provenance:` list remains `relation_type IS NULL`, even though the
    source document DOES carry a `provenance:` list (for a different target)
    (spec: "Genuine concept-to-concept link outside provenance stays
    untyped"; task 1.4)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc_with_provenance(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        provenance=["sources/foo"],
        body=(
            "## Related\n\n[foo](/sources/foo.md) — source this was extracted from\n\n"
            "See also [Epicureanism](/concepts/epicureanism.md).\n"
        ),
    )
    _write_doc(bundle_dir / "sources" / "foo.md", doc_type="Source", title="Foo")
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert ("concepts/stoicism", "concepts/epicureanism", None) in edges
    assert ("concepts/stoicism", "sources/foo", "derived_from") in edges


def test_existing_relations_typed_edge_unaffected_by_provenance_synthesis(
    tmp_path: Path,
) -> None:
    """An existing `relations:`-typed edge is untouched by provenance-mirror
    synthesis, even when the SAME target also happens to be a member of the
    source's `provenance:` list -- the typed pass and the untyped/provenance
    pass insert distinct rows (spec non-regression; task 1.5)."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n"
        "provenance:\n  - concepts/epicureanism\n"
        "relations:\n  - target: concepts/epicureanism\n    type: depends_on\n"
        "---\n[Epicureanism](/concepts/epicureanism.md)\n",
        encoding="utf-8",
    )
    _write_doc(bundle_dir / "concepts" / "epicureanism.md", title="Epicureanism")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = store.edges()

    assert edges == [
        Edge(
            source_id="concepts/stoicism",
            target_id="concepts/epicureanism",
            relation_type="depends_on",
        ),
        Edge(
            source_id="concepts/stoicism",
            target_id="concepts/epicureanism",
            relation_type="derived_from",
        ),
    ]


def test_dirty_provenance_degrades_to_empty_set_without_crashing(
    tmp_path: Path,
) -> None:
    """A non-list `provenance:` scalar degrades to an empty set (no matches,
    every body link stays untyped) rather than crashing the build; a
    non-string entry within an otherwise-list `provenance:` is dropped, also
    without crashing (spec/design: "dirty/dangling provenance degrades, no
    crash"; task 1.6)."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "scalar-provenance.md").write_text(
        "---\ntype: Concept\ntitle: Scalar Provenance\nprovenance: not-a-list\n"
        "---\n[Foo](/sources/foo.md)\n",
        encoding="utf-8",
    )
    (bundle_dir / "concepts" / "dirty-entries.md").write_text(
        "---\ntype: Concept\ntitle: Dirty Entries\n"
        "provenance:\n  - 42\n  - sources/foo\n"
        "---\n[Foo](/sources/foo.md)\n",
        encoding="utf-8",
    )
    _write_doc(bundle_dir / "sources" / "foo.md", doc_type="Source", title="Foo")

    with sqlite_graph.build_graph(bundle_dir) as store:
        edges = _edge_rows(store)

    assert ("concepts/scalar-provenance", "sources/foo", None) in edges
    assert ("concepts/dirty-entries", "sources/foo", "derived_from") in edges


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

    def _crashing_populate(
        conn: sqlite3.Connection,
        bundle_dir_arg: Path,
        *,
        candidates: sqlite_graph.CandidateSource | None = None,
    ) -> list[str]:
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


# --- Phase 2.4 (#183): pass 3, embedding-proximity candidate edges ----------


class _StubCandidateSource:
    """A `candidates` source returning fixed pairs, standing in for
    `graph.proximity.VectorProximitySource` -- no `vectors.db`, no sqlite-vec.

    Records the node ids it was handed so tests can prove pass 3 offers the
    source a deterministic, sorted view of the projection.

    Each entry is either a `(source, target)` 2-tuple -- defaulting to
    `distance=0.1`, preserving every slice-1 call site unedited -- or a
    `(source, target, distance)` 3-tuple, for slice 2's rank/cap tests
    (#378 slice 2, task 2.1.1)."""

    def __init__(
        self, pairs: Sequence[tuple[str, str] | tuple[str, str, float]]
    ) -> None:
        self._pairs = pairs
        self.received: list[list[str]] = []

    def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
        self.received.append(list(concept_ids))
        result = []
        for entry in self._pairs:
            if len(entry) == 3:
                s, t, distance = entry
            else:
                s, t = entry
                distance = 0.1
            result.append(ProximityPair(source_id=s, target_id=t, distance=distance))
        return result


def test_pass_three_inserts_untyped_candidate_rows(tmp_path: Path) -> None:
    """A candidate pair with no existing edge becomes ONE row with
    `relation_type = NULL` -- untyped, because proximity nominates a pair
    for a human to consider, it never claims what the relationship IS."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    source = _StubCandidateSource([("concepts/a", "concepts/b")])

    with sqlite_graph.build_graph(bundle, candidates=source) as store:
        rows = _edge_rows(store)

    assert rows == [("concepts/a", "concepts/b", None)]


def test_pass_three_is_a_no_op_when_no_source_is_given(tmp_path: Path) -> None:
    """`candidates=None` is the default and must leave the projection
    byte-identical to the pre-#183 two-pass build -- a bundle with no
    `vectors.db` still builds successfully with zero candidate rows."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")

    with sqlite_graph.build_graph(bundle) as store:
        rows = _edge_rows(store)

    assert rows == []


def test_pass_one_and_two_output_is_identical_with_and_without_a_source(
    tmp_path: Path,
) -> None:
    """Pass 3 is purely ADDITIVE: introducing a source must not perturb a
    single row that passes 1 and 2 produce. Anything else would make
    candidate edges a silent rewrite of the provenance graph."""
    bundle = tmp_path / "bundle"
    _write_doc(
        bundle / "concepts" / "a.md", title="A", body="See [B](/concepts/b.md).\n"
    )
    _write_doc(bundle / "concepts" / "b.md", title="B")
    _write_doc_with_relations(
        bundle / "concepts" / "c.md",
        title="C",
        relations="  - target: concepts/b\n    type: relates_to\n",
    )

    with sqlite_graph.build_graph(bundle) as store:
        without = _edge_rows(store)
    source = _StubCandidateSource([("concepts/a", "concepts/c")])
    with sqlite_graph.build_graph(bundle, candidates=source) as store:
        with_source = _edge_rows(store)

    assert without == [
        ("concepts/a", "concepts/b", None),
        ("concepts/c", "concepts/b", "relates_to"),
    ]
    # Every pre-existing row survives untouched; only the candidate is added.
    assert with_source == sorted([*without, ("concepts/a", "concepts/c", None)])


def test_pass_three_skips_a_pair_that_already_has_a_body_link_edge(
    tmp_path: Path,
) -> None:
    """A pair already joined by a body link needs no candidate row --
    duplicating it would double-count the pair in `graph_edge_summary` and
    put the same relationship in the review queue twice. Deduped in BOTH
    directions, because a body link is directed while proximity is not."""
    bundle = tmp_path / "bundle"
    _write_doc(
        bundle / "concepts" / "a.md", title="A", body="See [B](/concepts/b.md).\n"
    )
    _write_doc(bundle / "concepts" / "b.md", title="B")
    # Reversed relative to the body link, to prove direction does not matter.
    source = _StubCandidateSource([("concepts/b", "concepts/a")])

    with sqlite_graph.build_graph(bundle, candidates=source) as store:
        rows = _edge_rows(store)

    assert rows == [("concepts/a", "concepts/b", None)]


def test_pass_three_skips_a_pair_that_already_has_a_typed_relations_edge(
    tmp_path: Path,
) -> None:
    """A pair a human already typed via `relations:` must not gain an
    untyped candidate row. It would be filtered out of suggestions anyway
    (`_candidate_edges` excludes already-typed pairs), but it would still
    inflate `graph_edge_summary`'s total and trigger the state-2b message
    for a pair nobody needs to look at again."""
    bundle = tmp_path / "bundle"
    _write_doc_with_relations(
        bundle / "concepts" / "a.md",
        title="A",
        relations="  - target: concepts/b\n    type: relates_to\n",
    )
    _write_doc(bundle / "concepts" / "b.md", title="B")
    source = _StubCandidateSource([("concepts/b", "concepts/a")])

    with sqlite_graph.build_graph(bundle, candidates=source) as store:
        rows = _edge_rows(store)

    assert rows == [("concepts/a", "concepts/b", "relates_to")]


def test_pass_three_ignores_pairs_whose_endpoints_are_not_nodes(
    tmp_path: Path,
) -> None:
    """A stale `vectors.db` can name a concept the bundle no longer holds.
    Such a pair is dropped rather than creating a dangling edge."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    source = _StubCandidateSource([("concepts/a", "concepts/forgotten")])

    with sqlite_graph.build_graph(bundle, candidates=source) as store:
        rows = _edge_rows(store)

    assert rows == []


def test_pass_three_row_order_is_deterministic_and_canonical(
    tmp_path: Path,
) -> None:
    """Rows are inserted sorted and collapsed to one canonical `(min, max)`
    direction per unordered pair, so two builds over the same bundle produce
    an identical projection regardless of the source's emission order."""
    bundle = tmp_path / "bundle"
    for name in ("a", "b", "c"):
        _write_doc(bundle / "concepts" / f"{name}.md", title=name.upper())
    forward = _StubCandidateSource(
        [("concepts/c", "concepts/a"), ("concepts/b", "concepts/a")]
    )
    reversed_order = _StubCandidateSource(
        [("concepts/a", "concepts/b"), ("concepts/a", "concepts/c")]
    )

    with sqlite_graph.build_graph(bundle, candidates=forward) as store:
        first = _edge_rows(store)
    with sqlite_graph.build_graph(bundle, candidates=reversed_order) as store:
        second = _edge_rows(store)

    assert first == [
        ("concepts/a", "concepts/b", None),
        ("concepts/a", "concepts/c", None),
    ]
    assert first == second
    assert forward.received == [["concepts/a", "concepts/b", "concepts/c"]]


# --- Phase 1 (#378 slice 1): Source-exclusion from the pass-3 seed set -----


def test_pass_three_excludes_source_docs_from_the_seed_node_set(
    tmp_path: Path,
) -> None:
    """A `Source` document must never be offered to `candidates.pairs(...)`
    as an anchor -- only Concept-typed (non-Source) ids reach the seed set,
    proving the exclusion narrows the ANCHOR LIST, not just the row guards."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", doc_type="Concept", title="A")
    _write_doc(bundle / "concepts" / "b.md", doc_type="Concept", title="B")
    _write_doc(bundle / "sources" / "call.md", doc_type="Source", title="Call")
    stub = _StubCandidateSource([("concepts/a", "concepts/b")])

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        _edge_rows(store)

    assert stub.received == [["concepts/a", "concepts/b"]]


def test_pass_three_drops_a_candidate_pair_originating_from_a_source(
    tmp_path: Path,
) -> None:
    """Even if a candidate source nominates a pair with a Source as the
    proposing endpoint, pass 3 must drop it -- a Source MUST NOT propose a
    candidate edge."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", doc_type="Concept", title="A")
    _write_doc(bundle / "sources" / "call.md", doc_type="Source", title="Call")
    stub = _StubCandidateSource([("sources/call", "concepts/a")])

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    assert rows == []


def test_pass_three_drops_a_candidate_pair_targeting_a_source(
    tmp_path: Path,
) -> None:
    """The receiving-direction case: `VectorProximitySource.pairs` queries
    the whole `vectors.db` and never filters its own hits against the
    `concept_ids` it was handed, so a Source can still come back as a
    *neighbor* even when it was never offered as an anchor. The row guards
    (not the anchor list alone) must catch this -- a Source MUST NOT receive
    a candidate edge."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", doc_type="Concept", title="A")
    _write_doc(bundle / "sources" / "call.md", doc_type="Source", title="Call")
    stub = _StubCandidateSource([("concepts/a", "sources/call")])

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    assert rows == []


def test_candidate_report_is_empty_when_every_nomination_is_filtered_out(
    tmp_path: Path,
) -> None:
    """The zero-survivor branch: a candidate source ran, but every pair it
    nominated was dropped by the Source guards. `produced` counts what
    survived filtering, not what was nominated, so both counts must read
    zero -- and the CLI verbs must therefore stay silent rather than print a
    "0 of 0" notice. Distinct from the `candidates=None` case, where pass 3
    never runs at all (#378)."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", doc_type="Concept", title="A")
    _write_doc(bundle / "sources" / "call.md", doc_type="Source", title="Call")
    stub = _StubCandidateSource(
        [("sources/call", "concepts/a"), ("concepts/a", "sources/call")]
    )

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)
        report = store.candidate_report

    assert rows == []
    assert report == sqlite_graph.CandidateReport(produced=0, retained=0)


def test_pass_three_still_seeds_concept_to_concept_pairs(tmp_path: Path) -> None:
    """A Source document elsewhere in the bundle must not perturb an
    unrelated Concept<->Concept candidate pair."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", doc_type="Concept", title="A")
    _write_doc(bundle / "concepts" / "b.md", doc_type="Concept", title="B")
    _write_doc(bundle / "sources" / "call.md", doc_type="Source", title="Call")
    stub = _StubCandidateSource([("concepts/a", "concepts/b")])

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    assert rows == [("concepts/a", "concepts/b", None)]


def test_source_exclusion_leaves_the_derived_from_provenance_mirror_intact(
    tmp_path: Path,
) -> None:
    """Passes 1 and 2 -- including the Concept->Source `derived_from`
    provenance mirror -- must remain unaffected by the pass-3 Source
    exclusion filter."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "sources" / "foo.md", doc_type="Source", title="Foo")
    _write_doc_with_provenance(
        bundle / "concepts" / "a.md",
        title="A",
        provenance=["sources/foo"],
        body="## Related\n\nSee [foo](/sources/foo.md).\n",
    )
    stub = _StubCandidateSource([])

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    assert rows == [("concepts/a", "sources/foo", "derived_from")]


# --- Phase 2.1 (#378 slice 2): rank, cap, and report candidate output ------


def test_pass_three_truncates_to_the_candidate_cap(tmp_path: Path) -> None:
    """60 stub pairs must never yield more than `_MAX_CANDIDATE_EDGES` (50)
    candidate rows -- the ceiling bounds output at any bundle size."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "hub.md", title="Hub")
    pairs: list[tuple[str, str, float]] = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        _write_doc(bundle / "concepts" / f"{leaf_id}.md", title=leaf_id)
        pairs.append(("concepts/hub", f"concepts/{leaf_id}", index * 0.001))
    stub = _StubCandidateSource(pairs)

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    assert len(rows) == sqlite_graph._MAX_CANDIDATE_EDGES


def test_pass_three_retains_the_closest_candidates_by_distance(
    tmp_path: Path,
) -> None:
    """An over-cap set with mixed distances must retain the SMALLEST-distance
    candidates -- the farthest, weakest pairs are the ones dropped."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "hub.md", title="Hub")
    pairs: list[tuple[str, str, float]] = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        _write_doc(bundle / "concepts" / f"{leaf_id}.md", title=leaf_id)
        pairs.append(("concepts/hub", f"concepts/{leaf_id}", index * 0.001))
    stub = _StubCandidateSource(pairs)

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    retained_targets = {row[1] for row in rows}
    expected_targets = {f"concepts/leaf{index:03d}" for index in range(1, 51)}
    assert retained_targets == expected_targets


def test_pass_three_breaks_distance_ties_by_pair_id(tmp_path: Path) -> None:
    """Equal distances must be broken by lexicographic `(source_id,
    target_id)` ordering, matching `pairs()`'s own `sorted(best)` tie rule."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "hub.md", title="Hub")
    pairs: list[tuple[str, str, float]] = []
    for index in range(1, 56):
        leaf_id = f"leaf{index:03d}"
        _write_doc(bundle / "concepts" / f"{leaf_id}.md", title=leaf_id)
        pairs.append(("concepts/hub", f"concepts/{leaf_id}", 0.1))
    stub = _StubCandidateSource(pairs)

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    retained_targets = {row[1] for row in rows}
    expected_targets = {f"concepts/leaf{index:03d}" for index in range(1, 51)}
    assert retained_targets == expected_targets


def test_pass_three_collapses_a_duplicate_nomination_to_its_smaller_distance(
    tmp_path: Path,
) -> None:
    """A single canonical `(min, max)` pair can be nominated more than once
    -- e.g. from both anchors' own k-NN queries -- with different
    distances. The smaller distance must be kept, mirroring
    `proximity.py`'s own tie rule, so `produced`/ranking reflect the pair's
    TRUE closeness rather than whichever nomination happened to be seen
    last."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    # Same canonical pair nominated twice: once far, once close. The close
    # nomination is listed SECOND, so a naive "last write wins" collapse
    # would happen to get this right by accident -- the report's count
    # (one pair, not two rows) is the real proof the collapse ran.
    stub = _StubCandidateSource(
        [
            ("concepts/a", "concepts/b", 0.9),
            ("concepts/a", "concepts/b", 0.1),
        ]
    )

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)
        report = store.candidate_report

    assert rows == [("concepts/a", "concepts/b", None)]
    assert report.produced == 1
    assert report.retained == 1
    assert report.pairs == (("concepts/a", "concepts/b"),)


def test_pass_three_keeps_the_first_distance_when_a_later_duplicate_is_farther(
    tmp_path: Path,
) -> None:
    """The other half of the best-distance collapse: a duplicate nomination
    with a LARGER distance than the one already kept must not overwrite
    it."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    stub = _StubCandidateSource(
        [
            ("concepts/a", "concepts/b", 0.1),
            ("concepts/a", "concepts/b", 0.9),
        ]
    )

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)
        report = store.candidate_report

    assert rows == [("concepts/a", "concepts/b", None)]
    assert report.produced == 1
    assert report.retained == 1
    assert report.pairs == (("concepts/a", "concepts/b"),)


def test_pass_three_drops_a_self_pair(tmp_path: Path) -> None:
    """A candidate source that (mis)nominates a concept paired with itself
    must never produce a self-loop edge or count toward the report."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    stub = _StubCandidateSource(
        [
            ("concepts/a", "concepts/a", 0.01),
            ("concepts/a", "concepts/b", 0.5),
        ]
    )

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)
        report = store.candidate_report

    assert rows == [("concepts/a", "concepts/b", None)]
    assert report.produced == 1
    assert report.retained == 1
    assert report.pairs == (("concepts/a", "concepts/b"),)


def test_pass_three_reports_the_pre_cap_total_when_truncating(
    tmp_path: Path,
) -> None:
    """`store.candidate_report` must carry the exact pre-cap (`produced`) and
    post-cap (`retained`) counts when the ceiling truncates the set."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "hub.md", title="Hub")
    pairs: list[tuple[str, str, float]] = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        _write_doc(bundle / "concepts" / f"{leaf_id}.md", title=leaf_id)
        pairs.append(("concepts/hub", f"concepts/{leaf_id}", index * 0.001))
    stub = _StubCandidateSource(pairs)

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        report = store.candidate_report

    assert report.produced == 60
    assert report.retained == 50
    assert len(report.pairs) == 60


def test_pass_three_reports_no_truncation_under_the_cap(tmp_path: Path) -> None:
    """Under the ceiling, `produced == retained` -- the truncation notice a
    caller derives from this report must be suppressed."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    stub = _StubCandidateSource([("concepts/a", "concepts/b", 0.1)])

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        report = store.candidate_report

    assert report.produced == 1
    assert report.retained == 1
    assert report.pairs == (("concepts/a", "concepts/b"),)


def test_dedup_against_earlier_passes_runs_before_the_cap(tmp_path: Path) -> None:
    """A duplicate of a pass-1/pass-2 edge at the CLOSEST distance must not
    consume a ceiling slot -- it must be dropped BEFORE the cap is applied,
    per `contradiction.py`'s post-review HIGH correction. If the cap ran
    first, the duplicate would displace the 50th-closest genuinely eligible
    leaf; this test pins that the leaf survives instead."""
    bundle = tmp_path / "bundle"
    _write_doc(
        bundle / "concepts" / "hub.md",
        title="Hub",
        body="See [Dup](/concepts/dup.md).\n",
    )
    _write_doc(bundle / "concepts" / "dup.md", title="Dup")
    pairs: list[tuple[str, str, float]] = [
        # Duplicates the pass-1 body-link edge above, at the closest
        # distance of the whole stub set.
        ("concepts/hub", "concepts/dup", 0.0001),
    ]
    for index in range(1, 55):
        leaf_id = f"leaf{index:03d}"
        _write_doc(bundle / "concepts" / f"{leaf_id}.md", title=leaf_id)
        pairs.append(("concepts/hub", f"concepts/{leaf_id}", 0.01 + index * 0.001))
    stub = _StubCandidateSource(pairs)

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)
        report = store.candidate_report

    # The only "dup" row present is the pass-1 body-link edge itself
    # (relation_type=None from the link pass) -- the candidate duplicate
    # must never appear as a second row for the same pair.
    dup_rows = [row for row in rows if row[1] == "concepts/dup"]
    candidate_targets = {row[1] for row in rows if row[1] != "concepts/dup"}
    # 54 leaf candidates survive dedup (the duplicate is dropped before the
    # cap), so `produced` reflects 54, not 55 -- and the cap still retains
    # exactly 50, the 50 CLOSEST leaves, INCLUDING leaf050 -- which a
    # cap-before-dedup bug would have displaced with the duplicate.
    assert report.produced == 54
    assert report.retained == 50
    assert dup_rows == [("concepts/hub", "concepts/dup", None)]
    assert "concepts/leaf050" in candidate_targets
    assert len(candidate_targets) == 50


def test_under_cap_insertion_order_is_unchanged(tmp_path: Path) -> None:
    """Under the cap, retained rows must be byte-identical to the
    pre-slice-2 build: inserted in ID-sorted order, never distance order --
    the on-disk projection's byte identity depends on insertion order, so
    the retained slice is re-sorted by id before insert."""
    bundle = tmp_path / "bundle"
    for name in ("a", "b", "c"):
        _write_doc(bundle / "concepts" / f"{name}.md", title=name.upper())
    # Farthest pair listed first, closest listed second -- id order must win
    # over both distance order and stub emission order.
    stub = _StubCandidateSource(
        [
            ("concepts/c", "concepts/a", 0.5),
            ("concepts/b", "concepts/a", 0.05),
        ]
    )

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        rows = _edge_rows(store)

    assert rows == [
        ("concepts/a", "concepts/b", None),
        ("concepts/a", "concepts/c", None),
    ]


def test_candidate_report_pairs_are_ranked_pre_cap_and_slice_to_retained(
    tmp_path: Path,
) -> None:
    """`CandidateReport.pairs` carries every deduped, Source-excluded
    candidate pair BEFORE the cap, ranked by distance ascending / id
    tie-break -- the SAME order the cap slices -- so `pairs[:retained]`
    reproduces exactly the retained set (#378 correction: a caller needs the
    raw pairs, not just the two counts, to re-derive a sensitivity-filtered
    truncation notice without a second graph read)."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "hub.md", title="Hub")
    pairs: list[tuple[str, str, float]] = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        _write_doc(bundle / "concepts" / f"{leaf_id}.md", title=leaf_id)
        pairs.append(("concepts/hub", f"concepts/{leaf_id}", index * 0.001))
    stub = _StubCandidateSource(pairs)

    with sqlite_graph.build_graph(bundle, candidates=stub) as store:
        report = store.candidate_report

    assert len(report.pairs) == report.produced == 60
    retained_pairs = report.pairs[: report.retained]
    assert len(retained_pairs) == report.retained == 50
    # Canonical pair key is (min, max); "concepts/hub" < "concepts/leafNNN"
    # lexically, so it is always the first element of every pair.
    retained_targets = {target for _source, target in retained_pairs}
    expected_targets = {f"concepts/leaf{index:03d}" for index in range(1, 51)}
    assert retained_targets == expected_targets


def test_pass_three_ranking_and_truncation_is_deterministic_across_two_builds(
    tmp_path: Path,
) -> None:
    """An over-cap bundle must retain the SAME candidate edges, in the same
    order, with the same `candidate_report`, across two independent builds
    -- mirrors `test_pass_three_row_order_is_deterministic_and_canonical`
    for the ranked/capped path (#378 slice 2)."""
    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "hub.md", title="Hub")
    pairs: list[tuple[str, str, float]] = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        _write_doc(bundle / "concepts" / f"{leaf_id}.md", title=leaf_id)
        pairs.append(("concepts/hub", f"concepts/{leaf_id}", index * 0.001))

    with sqlite_graph.build_graph(
        bundle, candidates=_StubCandidateSource(pairs)
    ) as store:
        first_rows = _edge_rows(store)
        first_report = store.candidate_report
    with sqlite_graph.build_graph(
        bundle, candidates=_StubCandidateSource(pairs)
    ) as store:
        second_rows = _edge_rows(store)
        second_report = store.candidate_report

    assert first_rows == second_rows
    assert first_report == second_report
    assert first_report.produced == 60
    assert first_report.retained == 50


# --- Phase 3.4 (#183): zero-candidate success path, through the real seam --


@pytest.mark.parametrize("state", ["absent", "empty"])
def test_build_graph_succeeds_with_no_usable_vectors_db(
    tmp_path: Path, state: str
) -> None:
    """A bundle whose embeddings are not usable still builds -- successfully,
    with zero candidate rows and no exception.

    Driven through the REAL CLI seam (`_open_proximity_or_degrade`), not the
    stub the pass-3 tests use: the stub proves pass 3's logic, this proves
    the wiring production actually executes. A regression here would mean
    every `suggest-relations`/`contradictions`/`status` run on a workspace
    that has not been reindexed yet crashes instead of degrading.

    Both states matter and are NOT the same code path: `absent` never opens
    a store at all, while `empty` opens one, finds zero rows, and must still
    decline -- the CLI's "embeddings missing" message keys on that same
    absent-OR-empty predicate."""
    from openkos.cli.main import _open_proximity_or_degrade
    from openkos.state.vectorstore import open_vector_store

    bundle = tmp_path / "bundle"
    _write_doc(bundle / "concepts" / "a.md", title="A")
    _write_doc(bundle / "concepts" / "b.md", title="B")
    vectors_db = tmp_path / ".openkos" / "vectors.db"
    if state == "empty":
        with open_vector_store(vectors_db):
            pass  # schema created, nothing embedded
        assert vectors_db.exists()

    source = _open_proximity_or_degrade(vectors_db)

    assert source is None
    with sqlite_graph.build_graph(bundle, candidates=source) as store:
        assert _edge_rows(store) == []
        assert len(_node_ids(store)) == 2
