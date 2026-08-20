"""`state.edge_suggestions`: durable persistence for edge-typing
suggestions (issue #799), mirroring `state.adjudications`' shape -- same
derived store, its own tables, per-row digest staleness at read time.

The one shape difference from the adjudication tenant is direction: an
edge is an ORDERED pair, and the asymmetric relation types make
`a -> b` a different question from `b -> a`, so the pair key preserves
the order it was given instead of sorting it."""

import sqlite3

import pytest

from openkos.state import edge_suggestions


@pytest.fixture
def conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _suggestion(
    *,
    source_id: str = "concepts/a",
    target_id: str = "concepts/b",
    suggested_type: str = "part_of",
    rationale: str = "stub rationale",
    include_confidential: bool = False,
    digests: tuple[tuple[str, str], ...] = (
        ("concepts/a", "sha-a"),
        ("concepts/b", "sha-b"),
    ),
) -> edge_suggestions.PersistedEdgeSuggestion:
    return edge_suggestions.PersistedEdgeSuggestion(
        source_id=source_id,
        target_id=target_id,
        suggested_type=suggested_type,
        rationale=rationale,
        include_confidential=include_confidential,
        input_digests=tuple(
            edge_suggestions.InputDigest(input_ref=ref, digest=digest)
            for ref, digest in digests
        ),
    )


def test_record_and_open_round_trip(conn: sqlite3.Connection) -> None:
    """A recorded suggestion reads back verbatim, in insertion order."""
    edge_suggestions.record_edge_suggestions(conn, [_suggestion()])

    rows = edge_suggestions.open_edge_suggestions(conn)

    assert len(rows) == 1
    row = rows[0]
    assert row.source_id == "concepts/a"
    assert row.target_id == "concepts/b"
    assert row.suggested_type == "part_of"
    assert row.rationale == "stub rationale"
    assert row.include_confidential is False
    assert tuple((d.input_ref, d.digest) for d in row.input_digests) == (
        ("concepts/a", "sha-a"),
        ("concepts/b", "sha-b"),
    )


def test_open_on_empty_store_returns_nothing(conn: sqlite3.Connection) -> None:
    """A store with no `edge_suggestions` table answers `()`, never raises."""
    assert edge_suggestions.open_edge_suggestions(conn) == ()


def test_record_replaces_superseded_rows_for_the_same_pair(
    conn: sqlite3.Connection,
) -> None:
    """A fresh suggestion for a pair replaces that pair's earlier row
    instead of appending an unbounded history nothing reads."""
    edge_suggestions.record_edge_suggestions(conn, [_suggestion()])
    edge_suggestions.record_edge_suggestions(
        conn, [_suggestion(suggested_type="caused_by", rationale="second look")]
    )

    rows = edge_suggestions.open_edge_suggestions(conn)

    assert len(rows) == 1
    assert rows[0].suggested_type == "caused_by"
    assert rows[0].rationale == "second look"
    digest_rows = conn.execute(
        "SELECT COUNT(*) FROM edge_suggestion_input_digests"
    ).fetchone()[0]
    assert digest_rows == 2, "superseded digest child rows must go too"


def test_the_reverse_pair_is_a_distinct_row(conn: sqlite3.Connection) -> None:
    """Direction is part of the identity: `a -> b` and `b -> a` are two
    different questions (the asymmetric types answer them differently), so
    recording the reverse must not supersede the forward row."""
    edge_suggestions.record_edge_suggestions(conn, [_suggestion()])
    edge_suggestions.record_edge_suggestions(
        conn,
        [
            _suggestion(
                source_id="concepts/b",
                target_id="concepts/a",
                suggested_type="produced_by",
            )
        ],
    )

    rows = edge_suggestions.open_edge_suggestions(conn)

    assert len(rows) == 2
    assert {(r.source_id, r.target_id) for r in rows} == {
        ("concepts/a", "concepts/b"),
        ("concepts/b", "concepts/a"),
    }


def test_delete_referencing_erases_matching_pairs(
    conn: sqlite3.Connection,
) -> None:
    """The privacy sweep drops any suggestion naming a purged id on either
    end -- its rationale can quote the endpoint's body verbatim."""
    edge_suggestions.record_edge_suggestions(
        conn,
        [
            _suggestion(),
            _suggestion(
                source_id="concepts/c",
                target_id="concepts/d",
                digests=(("concepts/c", "sha-c"), ("concepts/d", "sha-d")),
            ),
        ],
    )

    removed = edge_suggestions.delete_edge_suggestions_referencing(conn, {"concepts/b"})

    assert removed == 1
    rows = edge_suggestions.open_edge_suggestions(conn)
    assert len(rows) == 1
    assert (rows[0].source_id, rows[0].target_id) == ("concepts/c", "concepts/d")
    digest_refs = {
        row[0]
        for row in conn.execute(
            "SELECT input_ref FROM edge_suggestion_input_digests"
        ).fetchall()
    }
    assert digest_refs == {"concepts/c", "concepts/d"}


def test_delete_referencing_absent_table_is_a_noop(
    conn: sqlite3.Connection,
) -> None:
    """A store the suggestion tenant has never written answers 0."""
    assert edge_suggestions.delete_edge_suggestions_referencing(conn, {"x"}) == 0


def test_delete_referencing_empty_purge_set_is_a_noop(
    conn: sqlite3.Connection,
) -> None:
    """No purge ids means no sweep -- and no VACUUM."""
    edge_suggestions.record_edge_suggestions(conn, [_suggestion()])

    assert edge_suggestions.delete_edge_suggestions_referencing(conn, set()) == 0
    assert len(edge_suggestions.open_edge_suggestions(conn)) == 1


def test_pair_key_cannot_be_forged_by_an_embedded_separator(
    conn: sqlite3.Connection,
) -> None:
    """The separator is a byte no bundle-relative concept id can carry, so
    a crafted id can never collide with a different pair's key."""
    assert "\n" not in "concepts/a"
    forward = edge_suggestions.pair_key_for("concepts/a", "concepts/b")
    assert forward != edge_suggestions.pair_key_for("concepts/b", "concepts/a")
