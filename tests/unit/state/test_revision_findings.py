"""`state.revision_findings`: durable persistence for decision-revision
judge verdicts (#1014 piece a, Phase B Slice P3), mirroring
`state.edge_suggestions`' shape one table-family over.

The one shape difference from the edge-suggestion tenant is identity: a
revision finding's pair is UNORDERED (`pair_id_0 < pair_id_1`, sorted),
because direction is never stored (ADR-0025) -- unlike an edge, which is
directed and keeps the order it was given."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos.state import derived, revision_findings


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """A fresh `:memory:` connection, closed on teardown -- see
    `tests/unit/state/test_edge_suggestions.py`'s fixture docstring for why
    this must `yield`/close rather than bare-`return`."""
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def _finding(
    *,
    pair_ids: tuple[str, str] = ("concepts/a", "concepts/b"),
    verdict: str = "reverses",
    confidence: float = 0.9,
    rationale: str = "stub rationale",
    quotes: tuple[str | None, str | None] = ("quote a", "quote b"),
    dates: tuple[str | None, str | None] = ("2026-01-01", "2026-02-01"),
    date_states: tuple[str, str] = ("dated", "dated"),
    include_confidential: bool = False,
    prompt_version: str = "v1",
    digests: tuple[tuple[str, str], ...] = (
        ("concepts/a", "sha-a"),
        ("concepts/b", "sha-b"),
    ),
) -> revision_findings.RevisionFinding:
    return revision_findings.RevisionFinding(
        pair_ids=pair_ids,
        verdict=verdict,
        confidence=confidence,
        rationale=rationale,
        quotes=quotes,
        dates=dates,
        date_states=date_states,
        include_confidential=include_confidential,
        prompt_version=prompt_version,
        input_digests=tuple(
            revision_findings.InputDigest(input_ref=ref, digest=digest)
            for ref, digest in digests
        ),
    )


def test_record_revision_findings_replaces_per_sorted_pair_key(
    conn: sqlite3.Connection,
) -> None:
    """A fresh finding for the SAME sorted pair replaces that pair's
    earlier row instead of appending an unbounded history nothing reads --
    even when the caller names the pair in the OPPOSITE order, because
    identity is the sorted pair, not the order the judge happened to name
    the sides in. Recording a finding for a DIFFERENT pair keeps both, and
    `open_revision_findings` returns rows in insertion order."""
    revision_findings.record_revision_findings(conn, [_finding(verdict="reverses")])
    revision_findings.record_revision_findings(
        conn,
        [
            _finding(
                pair_ids=("concepts/b", "concepts/a"),  # reversed on purpose
                verdict="refines",
                rationale="second look",
            )
        ],
    )
    revision_findings.record_revision_findings(
        conn,
        [_finding(pair_ids=("concepts/c", "concepts/d"), verdict="reaffirms")],
    )

    rows = revision_findings.open_revision_findings(conn)

    assert [(row.pair_ids, row.verdict) for row in rows] == [
        (("concepts/a", "concepts/b"), "refines"),
        (("concepts/c", "concepts/d"), "reaffirms"),
    ]
    assert rows[0].rationale == "second look"
    digest_rows = conn.execute(
        "SELECT COUNT(*) FROM revision_finding_input_digests"
    ).fetchone()[0]
    assert digest_rows == 4, "superseded digest child rows must go too"


def test_record_revision_findings_round_trips_every_column_including_nulls(
    conn: sqlite3.Connection,
) -> None:
    """A finding with a missing quote/date on one side round-trips through
    `record_revision_findings`/`open_revision_findings` with every column
    preserved, NULLs included -- and every other column exactly as given."""
    revision_findings.record_revision_findings(
        conn,
        [
            _finding(
                verdict="refines",
                confidence=0.72,
                rationale="only one side is dated",
                quotes=(None, "verbatim text"),
                dates=(None, "2026-03-04"),
                date_states=("missing", "dated"),
                include_confidential=True,
                prompt_version="v2",
            )
        ],
    )

    (row,) = revision_findings.open_revision_findings(conn)

    assert row.pair_ids == ("concepts/a", "concepts/b")
    assert row.verdict == "refines"
    assert row.confidence == 0.72
    assert row.rationale == "only one side is dated"
    assert row.quotes == (None, "verbatim text")
    assert row.dates == (None, "2026-03-04")
    assert row.date_states == ("missing", "dated")
    assert row.include_confidential is True
    assert row.prompt_version == "v2"
    assert tuple((d.input_ref, d.digest) for d in row.input_digests) == (
        ("concepts/a", "sha-a"),
        ("concepts/b", "sha-b"),
    )


def test_open_revision_findings_on_a_fresh_connection_returns_empty_tuple(
    conn: sqlite3.Connection,
) -> None:
    """A connection with no prior write answers `()`, not an error -- the
    `CREATE TABLE IF NOT EXISTS`-on-every-write pattern from
    `state/edge_suggestions.py`."""
    assert revision_findings.open_revision_findings(conn) == ()


def test_delete_revision_findings_referencing_matches_pair_id_0(
    conn: sqlite3.Connection,
) -> None:
    """A finding whose `pair_id_0` is a purge-set member is deleted, along
    with its `revision_finding_input_digests` child rows; an unrelated
    finding survives. Its digests deliberately name neither
    "concepts/a" nor "sources-of:concepts/a", so only the `pair_id_0`
    column comparison -- not the input-digest arms -- can be what fires."""
    revision_findings.record_revision_findings(
        conn,
        [
            _finding(
                pair_ids=("concepts/a", "concepts/z"),
                digests=(("sources/unrelated", "sha-unrelated"),),
            ),
            _finding(
                pair_ids=("concepts/c", "concepts/d"),
                digests=(("concepts/c", "sha-c"), ("concepts/d", "sha-d")),
            ),
        ],
    )

    removed = revision_findings.delete_revision_findings_referencing(
        conn, {"concepts/a"}
    )

    assert removed == 1
    rows = revision_findings.open_revision_findings(conn)
    assert [row.pair_ids for row in rows] == [("concepts/c", "concepts/d")]
    digest_refs = {
        row[0]
        for row in conn.execute(
            "SELECT input_ref FROM revision_finding_input_digests"
        ).fetchall()
    }
    assert digest_refs == {"concepts/c", "concepts/d"}


def test_delete_revision_findings_referencing_matches_pair_id_1(
    conn: sqlite3.Connection,
) -> None:
    """Same as the `pair_id_0` case, but keyed on `pair_id_1` alone --
    kills an `OR` narrowed to only `pair_id_0`. Its digests deliberately
    name neither "concepts/z" nor "sources-of:concepts/z", so only the
    `pair_id_1` column comparison can be what fires."""
    revision_findings.record_revision_findings(
        conn,
        [
            _finding(
                pair_ids=("concepts/a", "concepts/z"),
                digests=(("sources/unrelated", "sha-unrelated"),),
            ),
            _finding(
                pair_ids=("concepts/c", "concepts/d"),
                digests=(("concepts/c", "sha-c"), ("concepts/d", "sha-d")),
            ),
        ],
    )

    removed = revision_findings.delete_revision_findings_referencing(
        conn, {"concepts/z"}
    )

    assert removed == 1
    rows = revision_findings.open_revision_findings(conn)
    assert [row.pair_ids for row in rows] == [("concepts/c", "concepts/d")]


def test_delete_revision_findings_referencing_matches_an_input_ref_source_id(
    conn: sqlite3.Connection,
) -> None:
    """A finding whose `revision_finding_input_digests.input_ref` names a
    purge-set member Source id -- neither Decision in the pair -- is
    deleted. Covers forget-command's "Forgetting a concept scrubs its
    persisted revision finding" at the Source-provenance level."""
    revision_findings.record_revision_findings(
        conn,
        [
            _finding(
                pair_ids=("concepts/a", "concepts/b"),
                digests=(
                    ("concepts/a", "sha-a"),
                    ("concepts/b", "sha-b"),
                    ("sources/leaked-source", "sha-source"),
                ),
            ),
            _finding(
                pair_ids=("concepts/c", "concepts/d"),
                digests=(("concepts/c", "sha-c"), ("concepts/d", "sha-d")),
            ),
        ],
    )

    removed = revision_findings.delete_revision_findings_referencing(
        conn, {"sources/leaked-source"}
    )

    assert removed == 1
    rows = revision_findings.open_revision_findings(conn)
    assert [row.pair_ids for row in rows] == [("concepts/c", "concepts/d")]


def test_delete_revision_findings_referencing_matches_sources_of_prefix_suffix(
    conn: sqlite3.Connection,
) -> None:
    """A finding whose `input_ref` reads `"sources-of:<purge-id>"` is
    deleted when `<purge-id>` is in the purge set, matched on the SUFFIX --
    kills an exact-match-only comparison that misses the `sources-of:`
    prefix form. The purge id (`concepts/purge-target`) names neither
    `pair_id_0`/`pair_id_1` nor any exact `input_ref` here, so the pair-id
    and exact-match arms cannot be what fires -- only the suffix arm can."""
    revision_findings.record_revision_findings(
        conn,
        [
            _finding(
                pair_ids=("concepts/x", "concepts/y"),
                digests=(
                    ("concepts/x", "sha-x"),
                    ("concepts/y", "sha-y"),
                    ("sources-of:concepts/purge-target", "sha-provenance"),
                ),
            ),
            _finding(
                pair_ids=("concepts/c", "concepts/d"),
                digests=(("concepts/c", "sha-c"), ("concepts/d", "sha-d")),
            ),
        ],
    )

    removed = revision_findings.delete_revision_findings_referencing(
        conn, {"concepts/unrelated-purge-id"}
    )
    assert removed == 0, "an unrelated purge id must not touch this finding"

    removed = revision_findings.delete_revision_findings_referencing(
        conn, {"concepts/purge-target"}
    )

    assert removed == 1
    rows = revision_findings.open_revision_findings(conn)
    assert [row.pair_ids for row in rows] == [("concepts/c", "concepts/d")]


def test_delete_revision_findings_referencing_runs_vacuum_and_checked_checkpoint(
    tmp_path: Path,
) -> None:
    """After a deletion, VACUUM ran and a subsequent
    `wal_checkpoint(TRUNCATE)` returned a checked non-`busy` row; a
    concurrent reader that pins the WAL makes the checkpoint report
    `busy=1`, which must RAISE instead of returning silently (the
    `edge_suggestions.py:237-299` checked-erasure precedent)."""
    db_path = tmp_path / ".openkos" / "findings.db"
    conn = derived.open_derived_connection(db_path)
    reader = derived.open_derived_connection(db_path)
    try:
        revision_findings.record_revision_findings(
            conn, [_finding(pair_ids=("concepts/target", "concepts/other"))]
        )

        removed = revision_findings.delete_revision_findings_referencing(
            conn, {"concepts/target"}
        )
        assert removed == 1

        revision_findings.record_revision_findings(
            conn, [_finding(pair_ids=("concepts/target2", "concepts/other2"))]
        )
        # A concurrent read transaction pins the WAL: TRUNCATE cannot
        # complete and reports busy=1 in its result row.
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM revision_findings").fetchall()

        with pytest.raises(sqlite3.OperationalError, match="checkpoint"):
            revision_findings.delete_revision_findings_referencing(
                conn, {"concepts/target2"}
            )
    finally:
        reader.close()
        conn.close()
