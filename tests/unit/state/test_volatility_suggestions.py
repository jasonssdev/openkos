"""`state.volatility_suggestions`: every non-degraded tier answer, keyed on the
prompt it answered, so an answered question is never asked twice (#1332)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos.state import derived
from openkos.state import volatility_suggestions as store


def _row(type_name: str = "Event", **kw: object) -> store.PersistedVolatilitySuggestion:
    fields: dict[str, object] = {
        "type_name": type_name,
        "model": "m",
        "prompt_digest": "d1",
        "suggested_tier": "slow",
        "rationale": "changes rarely",
        "input_refs": ("events/a", "events/b"),
    }
    fields.update(kw)
    return store.PersistedVolatilitySuggestion(**fields)  # type: ignore[arg-type]


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = derived.open_derived_connection(tmp_path / "findings.db")
    yield c
    c.close()


def test_a_store_with_no_table_reads_empty(conn: sqlite3.Connection) -> None:
    assert store.open_volatility_suggestions(conn) == ()


def test_record_then_open_round_trips(conn: sqlite3.Connection) -> None:
    store.record_volatility_suggestions(conn, [_row(), _row("Place")])

    assert store.open_volatility_suggestions(conn) == (_row(), _row("Place"))


def test_a_new_answer_for_a_type_replaces_the_old_one(
    conn: sqlite3.Connection,
) -> None:
    store.record_volatility_suggestions(conn, [_row()])
    store.record_volatility_suggestions(
        conn, [_row(prompt_digest="d2", suggested_tier="fast")]
    )

    (only,) = store.open_volatility_suggestions(conn)
    assert (only.prompt_digest, only.suggested_tier) == ("d2", "fast")


def test_a_row_with_no_refs_round_trips(conn: sqlite3.Connection) -> None:
    store.record_volatility_suggestions(conn, [_row(input_refs=())])

    (only,) = store.open_volatility_suggestions(conn)
    assert only.input_refs == ()


def test_the_sweep_deletes_a_row_whose_prompt_carried_a_purged_concept(
    conn: sqlite3.Connection,
) -> None:
    store.record_volatility_suggestions(
        conn, [_row(), _row("Place", input_refs=("places/x",))]
    )

    removed = store.delete_volatility_suggestions_referencing(conn, {"events/b"})

    assert removed == 1
    assert [r.type_name for r in store.open_volatility_suggestions(conn)] == ["Place"]


def test_the_sweep_leaves_unrelated_rows_and_a_missing_table_alone(
    conn: sqlite3.Connection,
) -> None:
    assert store.delete_volatility_suggestions_referencing(conn, {"x"}) == 0
    store.record_volatility_suggestions(conn, [_row()])

    assert store.delete_volatility_suggestions_referencing(conn, {"zzz"}) == 0
    assert len(store.open_volatility_suggestions(conn)) == 1
