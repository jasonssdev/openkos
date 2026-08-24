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


def test_rubric_digest_round_trips(conn: sqlite3.Connection) -> None:
    """#838: the rubric digest a row was computed under persists and reads
    back verbatim -- the same one-column-wider shape `include_confidential`
    already has."""
    row_in = adjudications.Adjudication(
        member_ids=("concepts/a", "concepts/b"),
        verdict="same",
        confidence=0.9,
        rationale="stub rationale",
        include_confidential=False,
        input_digests=(
            adjudications.InputDigest(input_ref="concepts/a", digest="sha-a"),
        ),
        rubric_digest="sha256:feedface",
    )
    adjudications.record_adjudications(conn, [row_in])

    rows = adjudications.open_adjudications(conn)

    assert rows[0].rubric_digest == "sha256:feedface"


_PRE_838_CREATE_SQL = """
CREATE TABLE adjudications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL,
    include_confidential INTEGER NOT NULL
)
"""
"""The `adjudications` table shape every pre-#838 build created -- no
`rubric_digest` column. `CREATE TABLE IF NOT EXISTS` will not widen it, so
these tests pin the real migration path."""

_PRE_838_DIGESTS_CREATE_SQL = """
CREATE TABLE adjudication_input_digests (
    adjudication_id INTEGER NOT NULL REFERENCES adjudications(id),
    ordinal INTEGER NOT NULL,
    input_ref TEXT NOT NULL,
    digest TEXT NOT NULL
)
"""
"""Its sibling, unchanged by #838 -- a real pre-#838 store always carries
both tables, so the fixture creates both."""


def _create_pre_838_store(conn: sqlite3.Connection) -> None:
    conn.execute(_PRE_838_CREATE_SQL)
    conn.execute(_PRE_838_DIGESTS_CREATE_SQL)
    conn.execute(
        "INSERT INTO adjudications "
        "(group_key, verdict, confidence, rationale, include_confidential) "
        "VALUES ('concepts/x\ny', 'same', 0.8, 'old row', 0)"
    )
    conn.commit()


def test_record_migrates_a_pre_rubric_table(conn: sqlite3.Connection) -> None:
    """#838: recording into a store created by a pre-#838 build ALTERs the
    missing column in rather than raising -- and the pre-migration row
    reads back with `rubric_digest is None` (not servable), never a crash
    that degrades the whole store to a failed read."""
    _create_pre_838_store(conn)

    adjudications.record_adjudications(
        conn,
        [
            adjudications.Adjudication(
                member_ids=("concepts/a", "concepts/b"),
                verdict="different",
                confidence=0.7,
                rationale="new row",
                include_confidential=False,
                input_digests=(),
                rubric_digest="sha256:cafe",
            )
        ],
    )

    rows = adjudications.open_adjudications(conn)
    by_rationale = {row.rationale: row for row in rows}
    assert by_rationale["old row"].rubric_digest is None
    assert by_rationale["new row"].rubric_digest == "sha256:cafe"


def test_migration_race_loser_swallows_the_duplicate_column(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#838: two concurrent runs on one pre-#838 store can both observe the
    column missing; the loser's ALTER raises `duplicate column name` and
    must be a no-op, not a crash. Simulated by lying to the check -- the
    column already exists, the checker reports it missing, the ALTER
    collides -- while any OTHER OperationalError still propagates."""
    adjudications.record_adjudications(conn, [_adjudication()])
    monkeypatch.setattr(adjudications, "_has_rubric_digest_column", lambda _conn: False)

    adjudications.record_adjudications(conn, [_adjudication()])

    monkeypatch.undo()
    assert adjudications.open_adjudications(conn)


def test_open_tolerates_the_pre_migration_shape(conn: sqlite3.Connection) -> None:
    """#838: a read-only path over an un-migrated store must not raise --
    it reads the old shape and reports every row's `rubric_digest` as
    `None`, the fail-closed "unknown rubric" answer."""
    _create_pre_838_store(conn)

    rows = adjudications.open_adjudications(conn)

    assert len(rows) == 1
    assert rows[0].rubric_digest is None


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
