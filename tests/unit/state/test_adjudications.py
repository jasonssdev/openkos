"""`state.adjudications`: durable persistence for adjudication verdicts
(issue #779), mirroring `state.findings`' shape -- same derived store, its
own tables, per-row digest staleness at read time."""

import sqlite3

import pytest

from openkos.state import adjudications


@pytest.fixture
def conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _adjudication(
    *,
    member_ids: tuple[str, ...] = ("concepts/a", "concepts/b"),
    verdict: str = "same",
    confidence: float = 0.9,
    rationale: str = "stub rationale",
    include_confidential: bool = False,
    digests: tuple[tuple[str, str], ...] = (
        ("concepts/a", "sha-a"),
        ("concepts/b", "sha-b"),
    ),
) -> adjudications.Adjudication:
    return adjudications.Adjudication(
        member_ids=member_ids,
        verdict=verdict,
        confidence=confidence,
        rationale=rationale,
        include_confidential=include_confidential,
        input_digests=tuple(
            adjudications.InputDigest(input_ref=ref, digest=digest)
            for ref, digest in digests
        ),
    )


def test_record_and_open_round_trip(conn: sqlite3.Connection) -> None:
    """A recorded adjudication reads back verbatim, in insertion order."""
    adjudications.record_adjudications(conn, [_adjudication()])

    rows = adjudications.open_adjudications(conn)

    assert len(rows) == 1
    row = rows[0]
    assert row.member_ids == ("concepts/a", "concepts/b")
    assert row.verdict == "same"
    assert row.confidence == 0.9
    assert row.rationale == "stub rationale"
    assert row.include_confidential is False
    assert tuple((d.input_ref, d.digest) for d in row.input_digests) == (
        ("concepts/a", "sha-a"),
        ("concepts/b", "sha-b"),
    )


def test_open_on_empty_store_returns_nothing(conn: sqlite3.Connection) -> None:
    """A store with no adjudication tables yet answers `()` -- the same
    absent-table posture `state.findings.open_findings` takes."""
    assert adjudications.open_adjudications(conn) == ()


def test_delete_referencing_erases_matching_groups(
    conn: sqlite3.Connection,
) -> None:
    """The forget privacy sweep: an adjudication whose member set names a
    purge id is deleted -- rationale text can quote the member's body --
    and unrelated rows survive. (VACUUM/WAL truncation are exercised
    against the real derived opener in the CLI tests; a bare :memory:
    connection has no WAL.)"""
    adjudications.record_adjudications(
        conn,
        [
            _adjudication(member_ids=("concepts/a", "concepts/b")),
            _adjudication(
                member_ids=("concepts/x", "concepts/y"),
                digests=(("concepts/x", "sx"), ("concepts/y", "sy")),
            ),
        ],
    )

    removed = adjudications.delete_adjudications_referencing(conn, {"concepts/b"})

    assert removed == 1
    rows = adjudications.open_adjudications(conn)
    assert len(rows) == 1
    assert rows[0].member_ids == ("concepts/x", "concepts/y")


def test_delete_referencing_absent_table_is_a_noop(
    conn: sqlite3.Connection,
) -> None:
    assert adjudications.delete_adjudications_referencing(conn, {"x"}) == 0


def test_record_replaces_superseded_rows_for_the_same_group(
    conn: sqlite3.Connection,
) -> None:
    """REPLACE semantics (#779 review): re-recording a group's verdict
    deletes the superseded rows first, so the store stays bounded by the
    live group set instead of appending unbounded history."""
    adjudications.record_adjudications(conn, [_adjudication(rationale="first")])
    adjudications.record_adjudications(conn, [_adjudication(rationale="second")])

    rows = adjudications.open_adjudications(conn)

    assert len(rows) == 1
    assert rows[0].rationale == "second"
    digest_rows = conn.execute(
        "SELECT COUNT(*) FROM adjudication_input_digests"
    ).fetchone()[0]
    assert digest_rows == 2, "superseded digest child rows must be deleted too"
