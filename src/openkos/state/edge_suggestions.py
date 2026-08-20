"""`.openkos/findings.db`, third tenant: durable persistence for
machine-computed EDGE-TYPING suggestions (issue #799), mirroring
`state.adjudications`' design decisions one table-family over.

`contradictions` has persisted its verdicts since #653 and `adjudicate`
since #779; `suggest-relations` re-spent one call per untyped edge on
every invocation -- and it is the most expensive of the three per run. In
the E2E session that filed this issue, `suggest-relations` paid 49 calls,
then `curate`'s Structure stage -- which the verb's own closing hint
names as the next step -- paid the same 49 again minutes later on an
unchanged bundle. This store closes that asymmetry with the SAME per-row
digest staleness contract: a suggestion is servable iff each endpoint's
CURRENT content hash equals what was stored when it was computed
(`state.vectorstore.content_hash` over raw bytes, supplied by the caller
at read time).

Same FILE as the findings and adjudication stores, deliberately: `purge`
deletes `findings.db` wholesale and `forget` sweeps it for purge-id
membership, so a third tenant in the same file inherits both erasure
paths instead of opening a new privacy surface. Separate TABLES, separate
module: the three families have different identities (an unordered pair
plus a merged-body discriminator, an N-member group, and an ORDERED pair)
and different consumers, and no module reads another's rows.

Two shape differences from the adjudication tenant, both forced by what
an edge is:

- **Direction is identity.** `pair_key_for` preserves the order it is
  given rather than sorting it. Half the relation vocabulary is
  asymmetric (`relations.ASYMMETRIC_RELATION_TYPES`), so `a -> b` and
  `b -> a` are genuinely different questions, and a verdict for one must
  never serve for the other.
- **A degrade is never stored.** `suggested_type` is non-optional here,
  while `resolution.edge_typing.EdgeSuggestion.suggested_type` is
  `str | None` -- that `None` is the fail-closed degrade (malformed
  reply, unparseable or invalid type), which is a FAILURE, not a verdict.
  Persisting one would cache a transport hiccup as a durable answer and
  never retry it. Callers filter before recording; the type signature is
  what makes that unforgettable.

`include_confidential` is stored per row and matched at serve time: a
suggestion computed with confidential endpoints excluded was computed
over a DIFFERENT graph projection than one computed with them included,
so the two must never serve for each other even when every digest
matches."""

import sqlite3
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

_CREATE_EDGE_SUGGESTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edge_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key TEXT NOT NULL,
    suggested_type TEXT NOT NULL,
    rationale TEXT NOT NULL,
    include_confidential INTEGER NOT NULL
)
"""

_CREATE_EDGE_SUGGESTION_DIGESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edge_suggestion_input_digests (
    suggestion_id INTEGER NOT NULL REFERENCES edge_suggestions(id),
    ordinal INTEGER NOT NULL,
    input_ref TEXT NOT NULL,
    digest TEXT NOT NULL
)
"""

_INSERT_EDGE_SUGGESTION_SQL = """
INSERT INTO edge_suggestions
    (pair_key, suggested_type, rationale, include_confidential)
VALUES (?, ?, ?, ?)
"""

_INSERT_DIGEST_SQL = """
INSERT INTO edge_suggestion_input_digests
    (suggestion_id, ordinal, input_ref, digest)
VALUES (?, ?, ?, ?)
"""

_SELECT_EDGE_SUGGESTIONS_SQL = """
SELECT id, pair_key, suggested_type, rationale, include_confidential
FROM edge_suggestions
ORDER BY id
"""

_SELECT_DIGESTS_SQL = """
SELECT input_ref, digest
FROM edge_suggestion_input_digests
WHERE suggestion_id = ?
ORDER BY ordinal
"""

_PAIR_KEY_SEPARATOR = "\n"
"""Source and target ids are joined with a newline, a byte no
bundle-relative concept id can carry (ids come from on-disk paths), so the
key can never be forged by a crafted id that embeds the separator."""


def pair_key_for(source_id: str, target_id: str) -> str:
    """The store's edge identity: `source` then `target`, in that order.

    Deliberately NOT sorted, unlike `adjudications.group_key_for`: the
    edge is directed and the asymmetric relation types answer the two
    directions differently, so collapsing them would serve a suggestion
    computed for one direction as the answer for the other."""
    return f"{source_id}{_PAIR_KEY_SEPARATOR}{target_id}"


@dataclass(frozen=True)
class InputDigest:
    """One `(input_ref, sha256)` row a suggestion was computed from --
    `input_ref` is an endpoint concept id; this module treats it as an
    opaque key, never resolving it to bytes itself (findings Decision 2)."""

    input_ref: str
    digest: str


@dataclass(frozen=True)
class PersistedEdgeSuggestion:
    """One edge-typing suggestion's durable shape, ready to persist.

    Named apart from `resolution.edge_typing.EdgeSuggestion` because it is
    a different thing: that one is the ephemeral per-run result carrying
    the `Edge` object and a nullable type; this one is the stored row,
    keyed by ids and carrying a type that is always present."""

    source_id: str
    target_id: str
    suggested_type: str
    """Always a `validate_relation_type`-accepted value. The fail-closed
    `None` degrade never reaches this store (see the module docstring)."""
    rationale: str
    include_confidential: bool
    input_digests: tuple[InputDigest, ...]


def record_edge_suggestions(
    conn: sqlite3.Connection, batch: Sequence[PersistedEdgeSuggestion]
) -> None:
    """Persist every `PersistedEdgeSuggestion` in `batch`, committing once
    -- no cross-row invariant exists (findings Decision 7), so one commit
    per call suffices.

    REPLACE semantics per pair key, mirroring `record_adjudications`: a
    fresh suggestion for an edge deletes that edge's superseded rows
    first, so re-runs and `--fresh` keep the store bounded by the LIVE
    candidate-edge set instead of appending an unbounded history nothing
    reads. Plain deletes, no VACUUM: superseding is bookkeeping, not the
    privacy erasure `delete_edge_suggestions_referencing` performs."""
    conn.execute(_CREATE_EDGE_SUGGESTIONS_TABLE_SQL)
    conn.execute(_CREATE_EDGE_SUGGESTION_DIGESTS_TABLE_SQL)
    for suggestion in batch:
        key = pair_key_for(suggestion.source_id, suggestion.target_id)
        superseded = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM edge_suggestions WHERE pair_key = ?", (key,)
            ).fetchall()
        ]
        if superseded:
            marks = ",".join("?" for _ in superseded)
            conn.execute(
                f"DELETE FROM edge_suggestion_input_digests "  # noqa: S608
                f"WHERE suggestion_id IN ({marks})",
                superseded,
            )
            conn.execute(
                f"DELETE FROM edge_suggestions WHERE id IN ({marks})",  # noqa: S608
                superseded,
            )
        cursor = conn.execute(
            _INSERT_EDGE_SUGGESTION_SQL,
            (
                key,
                suggestion.suggested_type,
                suggestion.rationale,
                1 if suggestion.include_confidential else 0,
            ),
        )
        suggestion_id = cursor.lastrowid
        for ordinal, digest in enumerate(suggestion.input_digests):
            conn.execute(
                _INSERT_DIGEST_SQL,
                (suggestion_id, ordinal, digest.input_ref, digest.digest),
            )
    conn.commit()


def open_edge_suggestions(
    conn: sqlite3.Connection,
) -> tuple[PersistedEdgeSuggestion, ...]:
    """Read every persisted suggestion, in insertion order. A store with
    no `edge_suggestions` table yet (a fresh `findings.db`, or one
    predating this slice) returns `()` rather than raising -- the same
    absent-table posture `findings.open_findings` and
    `adjudications.open_adjudications` take."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "edge_suggestions" not in tables:
        return ()
    results: list[PersistedEdgeSuggestion] = []
    for (
        suggestion_id,
        pair_key,
        suggested_type,
        rationale,
        include_confidential,
    ) in conn.execute(_SELECT_EDGE_SUGGESTIONS_SQL).fetchall():
        digests = tuple(
            InputDigest(input_ref=input_ref, digest=digest)
            for input_ref, digest in conn.execute(
                _SELECT_DIGESTS_SQL, (suggestion_id,)
            ).fetchall()
        )
        source_id, _, target_id = pair_key.partition(_PAIR_KEY_SEPARATOR)
        results.append(
            PersistedEdgeSuggestion(
                source_id=source_id,
                target_id=target_id,
                suggested_type=suggested_type,
                rationale=rationale,
                include_confidential=bool(include_confidential),
                input_digests=digests,
            )
        )
    return tuple(results)


def delete_edge_suggestions_referencing(
    conn: sqlite3.Connection, purge_ids: AbstractSet[str]
) -> int:
    """Privacy sweep, the findings and adjudication sweeps' triplet for
    this table family: a suggestion naming a `purge_ids` member on EITHER
    end is deleted -- its `rationale` can quote that endpoint's body
    verbatim -- digest child rows included, and the count removed is
    returned.

    Same erasure discipline as `findings.delete_findings_referencing`:
    after the deletes commit, `VACUUM` rebuilds the file without the
    freelist pages a plain DELETE leaves recoverable, and a CHECKED
    `wal_checkpoint(TRUNCATE)` clears the WAL sidecar -- a blocked
    checkpoint is reported through the row's `busy` column, never an
    exception, so it is raised here into the caller's fail-loud warning
    path rather than reported as success over silent residue.

    A store with no `edge_suggestions` table answers 0 rather than
    raising."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "edge_suggestions" not in tables or not purge_ids:
        return 0
    doomed = [
        row_id
        for row_id, pair_key in conn.execute(
            "SELECT id, pair_key FROM edge_suggestions"
        ).fetchall()
        if any(
            endpoint in purge_ids for endpoint in pair_key.split(_PAIR_KEY_SEPARATOR)
        )
    ]
    if not doomed:
        return 0
    doomed_marks = ",".join("?" for _ in doomed)
    # The interpolated fragment is "?" placeholder marks only -- every
    # VALUE travels through the parameter tuple (SQLite has no native
    # array binding for IN).
    if "edge_suggestion_input_digests" in tables:
        conn.execute(
            f"DELETE FROM edge_suggestion_input_digests "  # noqa: S608
            f"WHERE suggestion_id IN ({doomed_marks})",
            doomed,
        )
    conn.execute(
        f"DELETE FROM edge_suggestions WHERE id IN ({doomed_marks})",  # noqa: S608
        doomed,
    )
    conn.commit()
    conn.execute("VACUUM")
    busy, _wal_frames, _checkpointed = conn.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()
    if busy:
        raise sqlite3.OperationalError(
            "wal checkpoint busy: a concurrent reader held the WAL open, so "
            "deleted edge-suggestion bytes may remain in it"
        )
    return len(doomed)
