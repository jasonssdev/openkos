"""`.openkos/findings.db`, a further tenant: the volatility tier the model
suggested for a concept TYPE, keyed on the exact prompt it answered.

`suggest-volatility` asks one question per concept type. Only a suggestion that
differs from the type's default earns a pending-work row, so on an unchanged
bundle the model was re-asked for every type whose answer was "keep it" -- on
every maintenance pass, and again when `curate` priced the same stage. This
store keeps every non-degraded answer so a question already answered is never
asked twice.

A row is servable iff the prompt about to be sent digests to what it was
computed from: the prompt carries the type, its default, the sampled bodies and
the rationale language, so any change to any of them re-asks, and nothing else
does. The model is part of the key, because a different model is a different
answer. A degrade (malformed reply, invalid tier) is a failure, not a verdict,
and is never stored: the type signature makes `suggested_tier` non-optional.

`input_refs` names the concept ids the sampled bodies came from, newline
joined (a byte no concept id carries). It exists only so the privacy sweep can
find a rationale that quotes a forgotten concept: type-level rows carry no
other link to a concept. Same file as the findings, so `purge` deletes it
wholesale; `forget` calls `delete_volatility_suggestions_referencing`."""

import sqlite3
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

_REFS_SEPARATOR = "\n"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS volatility_suggestions (
    type_name TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    prompt_digest TEXT NOT NULL,
    suggested_tier TEXT NOT NULL,
    rationale TEXT NOT NULL,
    input_refs TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class PersistedVolatilitySuggestion:
    type_name: str
    model: str
    prompt_digest: str
    suggested_tier: str
    """Always a valid tier: a degraded suggestion is never persisted."""
    rationale: str
    input_refs: tuple[str, ...]
    """The concept ids whose bodies the prompt carried (privacy sweep key)."""


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def record_volatility_suggestions(
    conn: sqlite3.Connection, batch: Sequence[PersistedVolatilitySuggestion]
) -> None:
    """Persist `batch`, one commit. REPLACE per type: the store stays bounded by
    the number of concept types, never an unbounded history nothing reads."""
    conn.execute(_CREATE_SQL)
    for row in batch:
        conn.execute(
            "INSERT OR REPLACE INTO volatility_suggestions"
            " (type_name, model, prompt_digest, suggested_tier, rationale, input_refs)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                row.type_name,
                row.model,
                row.prompt_digest,
                row.suggested_tier,
                row.rationale,
                _REFS_SEPARATOR.join(row.input_refs),
            ),
        )
    conn.commit()


def open_volatility_suggestions(
    conn: sqlite3.Connection,
) -> tuple[PersistedVolatilitySuggestion, ...]:
    """Every persisted suggestion, by type name. A store with no table yet
    answers `()` rather than raising."""
    if "volatility_suggestions" not in _tables(conn):
        return ()
    return tuple(
        PersistedVolatilitySuggestion(
            type_name=type_name,
            model=model,
            prompt_digest=digest,
            suggested_tier=tier,
            rationale=rationale,
            input_refs=tuple(refs.split(_REFS_SEPARATOR)) if refs else (),
        )
        for type_name, model, digest, tier, rationale, refs in conn.execute(
            "SELECT type_name, model, prompt_digest, suggested_tier, rationale,"
            " input_refs FROM volatility_suggestions ORDER BY type_name"
        ).fetchall()
    )


def delete_volatility_suggestions_referencing(
    conn: sqlite3.Connection, purge_ids: AbstractSet[str]
) -> int:
    """Privacy sweep: delete every suggestion whose prompt carried a `purge_ids`
    member's body (its rationale can quote it), then `VACUUM` and a CHECKED
    `wal_checkpoint(TRUNCATE)` so the bytes do not survive in a freelist page or
    the WAL. A store with no table answers 0."""
    if "volatility_suggestions" not in _tables(conn) or not purge_ids:
        return 0
    doomed = [
        type_name
        for type_name, refs in conn.execute(
            "SELECT type_name, input_refs FROM volatility_suggestions"
        ).fetchall()
        if any(ref in purge_ids for ref in refs.split(_REFS_SEPARATOR))
    ]
    if not doomed:
        return 0
    marks = ",".join("?" for _ in doomed)
    conn.execute(
        f"DELETE FROM volatility_suggestions WHERE type_name IN ({marks})",  # noqa: S608
        doomed,
    )
    conn.commit()
    conn.execute("VACUUM")
    busy, _frames, _checkpointed = conn.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()
    if busy:
        raise sqlite3.OperationalError(
            "wal checkpoint busy: a concurrent reader held the WAL open, so "
            "deleted volatility-suggestion bytes may remain in it"
        )
    return len(doomed)
