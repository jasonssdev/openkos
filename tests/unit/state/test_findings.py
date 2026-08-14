"""Unit tests for `state/findings.py`: `.openkos/findings.db`'s schema,
`record_findings`/`open_findings`, and per-input staleness evaluation
(pending-work design, Decision 1 & Decision 2; tasks A1.1-A1.4).

Findings are recomputable machine inference, so this store does NOT
participate in `derived.MANIFEST_HASH_KEY` gating -- staleness is decided
per finding, from an ordered list of `(input_ref, sha256)` rows, never from
one whole-store digest (design Decision 2).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos.state import derived, findings
from openkos.state.vectorstore import content_hash


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / ".openkos" / "findings.db"
    connection = derived.open_derived_connection(db_path)
    yield connection
    connection.close()


# --- A1.1: record_findings writes a row; open_findings reads it back ------


def test_record_and_open_round_trips_verdict_confidence_rationale(
    conn: sqlite3.Connection,
) -> None:
    findings.record_findings(
        conn,
        [
            findings.Finding(
                pair_ids=("concepts/a", "concepts/b"),
                merged_absorbed_id=None,
                verdict="contradicts",
                confidence=0.9,
                rationale="stated conflicting claims",
                input_digests=(
                    findings.InputDigest("concepts/a", content_hash(b"a body")),
                    findings.InputDigest("concepts/b", content_hash(b"b body")),
                ),
            )
        ],
    )

    (row,) = findings.open_findings(conn)

    assert row.pair_ids == ("concepts/a", "concepts/b")
    assert row.merged_absorbed_id is None
    assert row.verdict == "contradicts"
    assert row.confidence == pytest.approx(0.9)
    assert row.rationale == "stated conflicting claims"
    assert row.input_digests == (
        findings.InputDigest("concepts/a", content_hash(b"a body")),
        findings.InputDigest("concepts/b", content_hash(b"b body")),
    )
    assert row.stale is False


def test_open_findings_on_an_empty_store_returns_nothing(
    conn: sqlite3.Connection,
) -> None:
    assert findings.open_findings(conn) == ()


def test_findings_db_does_not_gate_via_manifest_hash(
    conn: sqlite3.Connection,
) -> None:
    """Decision 1: findings do not participate in `MANIFEST_HASH_KEY`
    gating -- a fresh store has no stored manifest hash at all."""
    assert derived.read_manifest_hash(conn) is None


# --- A1.3/A1.4: per-input staleness is evaluated per finding, not per store


def test_mutating_one_finding_input_marks_only_that_finding_stale(
    conn: sqlite3.Connection,
) -> None:
    original_a = content_hash(b"concept a body")
    original_x = content_hash(b"concept x body")
    findings.record_findings(
        conn,
        [
            findings.Finding(
                pair_ids=("concepts/a", "concepts/b"),
                merged_absorbed_id=None,
                verdict="contradicts",
                confidence=0.8,
                rationale="a vs b",
                input_digests=(
                    findings.InputDigest("concepts/a", original_a),
                    findings.InputDigest("concepts/b", content_hash(b"concept b body")),
                ),
            ),
            findings.Finding(
                pair_ids=("concepts/x", "concepts/y"),
                merged_absorbed_id=None,
                verdict="consistent",
                confidence=0.5,
                rationale="x vs y",
                input_digests=(
                    findings.InputDigest("concepts/x", original_x),
                    findings.InputDigest("concepts/y", content_hash(b"concept y body")),
                ),
            ),
        ],
    )

    # Concept `a`'s content changed -- only its input row's digest differs now.
    mutated_a = content_hash(b"concept a body -- EDITED")
    current = {
        "concepts/a": mutated_a,
        "concepts/b": content_hash(b"concept b body"),
        "concepts/x": original_x,
        "concepts/y": content_hash(b"concept y body"),
    }

    rows = findings.open_findings(conn, current_digest=current.get)
    by_pair = {row.pair_ids: row for row in rows}

    assert by_pair[("concepts/a", "concepts/b")].stale is True
    assert by_pair[("concepts/x", "concepts/y")].stale is False


def test_staleness_is_not_evaluated_without_a_current_digest_lookup(
    conn: sqlite3.Connection,
) -> None:
    """No `current_digest` supplied -- a caller doing a plain round trip
    (e.g. the round-trip test above) never asks for staleness, so every row
    reads back fresh rather than raising or silently guessing."""
    findings.record_findings(
        conn,
        [
            findings.Finding(
                pair_ids=("concepts/a", "concepts/b"),
                merged_absorbed_id=None,
                verdict="contradicts",
                confidence=0.9,
                rationale="stub",
                input_digests=(findings.InputDigest("concepts/a", content_hash(b"a")),),
            )
        ],
    )

    (row,) = findings.open_findings(conn)
    assert row.stale is False


def test_typed_edge_and_merged_body_finding_over_the_same_pair_stay_distinct(
    conn: sqlite3.Connection,
) -> None:
    """`merged_absorbed_id` is the sole discriminator (Decision 3) -- two
    findings sharing the same `pair_ids` but different `merged_absorbed_id`
    must round-trip as two distinct rows."""
    findings.record_findings(
        conn,
        [
            findings.Finding(
                pair_ids=("concepts/a", "concepts/a"),
                merged_absorbed_id=None,
                verdict="contradicts",
                confidence=0.9,
                rationale="typed edge",
                input_digests=(),
            ),
            findings.Finding(
                pair_ids=("concepts/a", "concepts/a"),
                merged_absorbed_id="concepts/absorbed",
                verdict="consistent",
                confidence=0.1,
                rationale="merged body",
                input_digests=(),
            ),
        ],
    )

    rows = findings.open_findings(conn)
    assert len(rows) == 2
    by_absorbed = {row.merged_absorbed_id: row for row in rows}
    assert by_absorbed[None].rationale == "typed edge"
    assert by_absorbed["concepts/absorbed"].rationale == "merged body"


# --- #653: conflicting_claims round-trip -----------------------------------


def test_record_and_open_round_trips_conflicting_claims(
    conn: sqlite3.Connection,
) -> None:
    """#653: a served CONTRADICTS verdict must render its cited claims
    exactly like a freshly judged one, so the store persists them --
    ordered, per finding."""
    findings.record_findings(
        conn,
        [
            findings.Finding(
                pair_ids=("concepts/a", "concepts/b"),
                merged_absorbed_id=None,
                verdict="contradicts",
                confidence=0.9,
                rationale="r",
                conflicting_claims=("first claim", "second claim"),
                input_digests=(findings.InputDigest("concepts/a", content_hash(b"a")),),
            )
        ],
    )

    (row,) = findings.open_findings(conn)

    assert row.conflicting_claims == ("first claim", "second claim")


def test_open_findings_tolerates_a_store_predating_the_claims_table(
    conn: sqlite3.Connection,
) -> None:
    """A findings.db written before #653 has no `finding_claims` table;
    reading it back yields empty claims, never an error."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS findings (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    pair_id_0 TEXT NOT NULL,\n"
        "    pair_id_1 TEXT NOT NULL,\n"
        "    merged_absorbed_id TEXT,\n"
        "    verdict TEXT NOT NULL,\n"
        "    confidence REAL NOT NULL,\n"
        "    rationale TEXT NOT NULL\n"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS finding_input_digests (\n"
        "    finding_id INTEGER NOT NULL REFERENCES findings(id),\n"
        "    ordinal INTEGER NOT NULL,\n"
        "    input_ref TEXT NOT NULL,\n"
        "    digest TEXT NOT NULL\n"
        ")"
    )
    conn.execute(
        "INSERT INTO findings\n"
        "    (pair_id_0, pair_id_1, merged_absorbed_id, verdict, confidence,"
        " rationale)\n"
        "VALUES ('concepts/a', 'concepts/b', NULL, 'contradicts', 0.9, 'r')"
    )
    conn.commit()

    (row,) = findings.open_findings(conn)

    assert row.conflicting_claims == ()
