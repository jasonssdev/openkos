"""Unit tests for `retrieval/history.py`: the bounded revision-history walk
(superseded-history-in-query, slice 2a, design.md Decision 2).

The walk is pure BFS over an INJECTED `read` callable -- these tests use a
dict-backed `FakeReader` that also records every concept id it was asked to
read, so a test can assert an edge was never followed without touching a
real bundle. No caller wires `walk_history` into `answer()` yet (slice 2b);
these tests exercise it directly.
"""

from collections.abc import Mapping
from datetime import date

from openkos.event_dates import ResolvedEventDate
from openkos.retrieval import history


def _meta(*relations: tuple[str, str]) -> dict[str, object]:
    """A frontmatter dict with a `relations:` list of `(target, type)`
    pairs -- `okf.decode_relations`'s on-disk shape."""
    return {
        "relations": [
            {"target": target, "type": rel_type} for target, rel_type in relations
        ]
    }


class FakeReader:
    """A dict-backed `read` callable standing in for `answer._guarded_read`
    (slice 2b). Entries map a concept id to either its `(metadata, body)`
    pair or `None` (a gate refusal or an unreadable file). Every id it was
    asked to read is recorded in `calls`, in request order, so a test can
    assert an edge was never followed."""

    def __init__(
        self, entries: Mapping[str, tuple[dict[str, object], str] | None]
    ) -> None:
        self._entries = entries
        self.calls: list[str] = []

    def __call__(self, concept_id: str) -> tuple[dict[str, object], str] | None:
        self.calls.append(concept_id)
        return self._entries.get(concept_id)


def test_walk_order_mixed_edges_and_siblings() -> None:
    """`supersedes` edges come before `revises` edges, then ascending
    concept id -- and BFS is level by level: a depth-2 sibling group is
    fully ordered by its own parents' BFS order, never a global sort
    across levels."""
    successor_metadata = _meta(
        ("b", "supersedes"), ("a", "supersedes"), ("c", "revises")
    )
    reader = FakeReader(
        {
            "a": (_meta(), "body a"),
            "b": (_meta(), "body b"),
            "c": (_meta(), "body c"),
        }
    )
    walk = history.walk_history("S", successor_metadata, read=reader, skip=frozenset())
    assert [p.concept_id for p in walk.predecessors] == ["a", "b", "c"]
    assert [p.role for p in walk.predecessors] == [
        "superseded",
        "superseded",
        "refined",
    ]

    # Two depth-1 parents, one child each: the whole depth-1 level is
    # attached before the depth-2 child, regardless of which parent it
    # descends from.
    level_metadata = _meta(("p", "supersedes"), ("q", "revises"))
    reader_levels = FakeReader(
        {
            "p": (_meta(("p1", "supersedes")), "body p"),
            "q": (_meta(("q1", "revises")), "body q"),
            "p1": (_meta(), "body p1"),
            "q1": (_meta(), "body q1"),
        }
    )
    level_walk = history.walk_history(
        "S", level_metadata, read=reader_levels, skip=frozenset()
    )
    assert [p.concept_id for p in level_walk.predecessors] == ["p", "q", "p1"]
    assert [p.role for p in level_walk.predecessors] == [
        "superseded",
        "refined",
        "superseded",
    ]


def test_holder_id_names_immediate_edge_owner() -> None:
    """A chain successor -> P (depth 1) -> Q (depth 2): P's holder is the
    successor, but Q's holder is P -- the immediate edge owner, never the
    top-level successor for a depth-2+ node."""
    successor_metadata = _meta(("P", "supersedes"))
    reader = FakeReader(
        {
            "P": (_meta(("Q", "revises")), "body P"),
            "Q": (_meta(), "body Q"),
        }
    )
    walk = history.walk_history("S", successor_metadata, read=reader, skip=frozenset())
    by_id = {p.concept_id: p for p in walk.predecessors}
    assert by_id["P"].holder_id == "S"
    assert by_id["Q"].holder_id == "P"


def test_depth_bound() -> None:
    """A 4-long chain attaches exactly 3 predecessors (through depth 3)
    and sets `truncated=True`; a 3-long chain attaches all 3 and leaves
    `truncated=False`."""
    reader = FakeReader(
        {
            "P1": (_meta(("P2", "supersedes")), "body P1"),
            "P2": (_meta(("P3", "supersedes")), "body P2"),
            "P3": (_meta(("P4", "supersedes")), "body P3"),
            "P4": (_meta(), "body P4"),
        }
    )
    walk = history.walk_history(
        "S", _meta(("P1", "supersedes")), read=reader, skip=frozenset()
    )
    assert [p.concept_id for p in walk.predecessors] == ["P1", "P2", "P3"]
    assert walk.truncated is True

    reader_short = FakeReader(
        {
            "Q1": (_meta(("Q2", "supersedes")), "body Q1"),
            "Q2": (_meta(("Q3", "supersedes")), "body Q2"),
            "Q3": (_meta(), "body Q3"),
        }
    )
    walk_short = history.walk_history(
        "S", _meta(("Q1", "supersedes")), read=reader_short, skip=frozenset()
    )
    assert [p.concept_id for p in walk_short.predecessors] == ["Q1", "Q2", "Q3"]
    assert walk_short.truncated is False


def test_block_cap() -> None:
    """A successor with 5 direct predecessors attaches exactly 3 (in
    order) and sets `truncated=True`; a successor with exactly 3 direct
    predecessors attaches all 3 and leaves `truncated=False`. The cap
    check sits BEFORE the read (design.md Decision 2 step 3): a capped
    target is never read at all, not merely never attached."""
    five_ids = ["p1", "p2", "p3", "p4", "p5"]
    successor_metadata = _meta(*[(cid, "supersedes") for cid in five_ids])
    reader = FakeReader({cid: (_meta(), f"body {cid}") for cid in five_ids})
    walk = history.walk_history("S", successor_metadata, read=reader, skip=frozenset())
    assert [p.concept_id for p in walk.predecessors] == ["p1", "p2", "p3"]
    assert walk.truncated is True
    assert reader.calls == ["p1", "p2", "p3"]

    three_ids = ["p1", "p2", "p3"]
    three_metadata = _meta(*[(cid, "supersedes") for cid in three_ids])
    reader3 = FakeReader({cid: (_meta(), f"body {cid}") for cid in three_ids})
    walk3 = history.walk_history("S", three_metadata, read=reader3, skip=frozenset())
    assert [p.concept_id for p in walk3.predecessors] == ["p1", "p2", "p3"]
    assert walk3.truncated is False


def test_cycle_guard() -> None:
    """A mutual `revises` cycle (A revises B, B revises A) terminates
    without looping, attaching both once each; a self-edge (A revises A)
    is ignored and contributes no block."""
    reader = FakeReader(
        {
            "A": (_meta(("B", "revises")), "body A"),
            "B": (_meta(("A", "revises")), "body B"),
        }
    )
    walk = history.walk_history(
        "S", _meta(("A", "revises")), read=reader, skip=frozenset()
    )
    assert [p.concept_id for p in walk.predecessors] == ["A", "B"]

    reader_self = FakeReader({"A": (_meta(("A", "revises")), "body A")})
    walk_self = history.walk_history(
        "S", _meta(("A", "revises")), read=reader_self, skip=frozenset()
    )
    assert [p.concept_id for p in walk_self.predecessors] == ["A"]


def test_skip_set() -> None:
    """A member of the passed `skip` set is neither attached as a
    predecessor nor expanded -- its own outbound edges are never
    enqueued, proven by the fake reader's call log staying empty."""
    reader = FakeReader(
        {
            "P": (_meta(("Q", "supersedes")), "body P"),
            "Q": (_meta(), "body Q"),
        }
    )
    walk = history.walk_history(
        "S", _meta(("P", "supersedes")), read=reader, skip=frozenset({"P"})
    )
    assert walk.predecessors == ()
    assert reader.calls == []


def test_dead_end_on_refused_read() -> None:
    """The fake reader returns `None` for one node (a gate refusal or an
    unreadable file): that node is not attached, and a target reachable
    only through it is never requested."""
    reader = FakeReader({"P": None, "Q": (_meta(), "body Q")})
    walk = history.walk_history(
        "S", _meta(("P", "supersedes")), read=reader, skip=frozenset()
    )
    assert walk.predecessors == ()
    assert reader.calls == ["P"]
    assert "Q" not in reader.calls


def test_date_phrase_and_label_note_strings() -> None:
    """`date_phrase` for the three resolver states, and `label_note` for
    the three (role, currency) rows of design.md Decision 5's table --
    exact wording, pinned against drift."""
    dated = ResolvedEventDate(
        state="dated", earliest=date(2026, 7, 14), latest=date(2026, 7, 14)
    )
    multiple = ResolvedEventDate(
        state="multiple", earliest=date(2026, 7, 1), latest=date(2026, 7, 14)
    )
    missing = ResolvedEventDate(state="missing", earliest=None, latest=None)
    none_reached = ResolvedEventDate(state="none-reached", earliest=None, latest=None)

    assert history.date_phrase(dated) == "event date 2026-07-14"
    assert history.date_phrase(multiple) == "event dates 2026-07-01 to 2026-07-14"
    assert history.date_phrase(missing) == "event date unknown"
    assert history.date_phrase(none_reached) == "event date unknown"

    assert history.label_note("superseded", "H", dated, True) == (
        " (earlier version, superseded by concept_id: H; event date 2026-07-14; "
        "no longer current)"
    )
    assert history.label_note("refined", "H", dated, True) == (
        " (earlier version, refined by concept_id: H; event date 2026-07-14; "
        "still current)"
    )
    assert history.label_note("refined", "H", dated, False) == (
        " (earlier version, refined by concept_id: H; event date 2026-07-14; "
        "no longer current)"
    )
