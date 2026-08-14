"""`.openkos/findings.db`: durable persistence for machine-computed
contradiction verdicts (pending-work design, Decision 1).

A finding is recomputable machine inference -- the opposite nature from an
operator's decision (`bundle.decisions`), which is why the two live in
separate stores with opposite policies (proposal: "Findings" vs
"Decisions"). This store therefore does **not** participate in
`state.derived.MANIFEST_HASH_KEY` gating: a findings row is not derivable
by a whole-store rebuild, so a whole-store staleness gate would be a lie.
Staleness is decided per row instead (Decision 2), from an ordered list of
`(input_ref, sha256)` digests recorded alongside the verdict -- the finding
is stale iff any row's CURRENT digest (supplied by the caller at read time,
via `state.vectorstore.content_hash` over raw bytes) differs from what was
stored when the verdict was computed.

`record_findings`/`open_findings` layer their own two tables on top of
`state.derived.open_derived_connection`'s shared `meta(key, value)` table,
mirroring `fts.py`/`graph/sqlite_graph.py`'s own precedent of layering a
domain-specific schema onto that shared opener.

This module never stores a `decision_key` or any pointer into
`bundle.decisions`: the two stores are joined only at READ time, by a
caller recomputing `bundle.decisions.decision_key_for` from a finding's
`pair_ids`/`merged_absorbed_id` (design Decision 7 -- no two-phase write,
because no cross-file invariant exists between them)."""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

_CREATE_FINDINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id_0 TEXT NOT NULL,
    pair_id_1 TEXT NOT NULL,
    merged_absorbed_id TEXT,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL
)
"""

_CREATE_INPUT_DIGESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS finding_input_digests (
    finding_id INTEGER NOT NULL REFERENCES findings(id),
    ordinal INTEGER NOT NULL,
    input_ref TEXT NOT NULL,
    digest TEXT NOT NULL
)
"""

_CREATE_CLAIMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS finding_claims (
    finding_id INTEGER NOT NULL REFERENCES findings(id),
    ordinal INTEGER NOT NULL,
    claim TEXT NOT NULL
)
"""

_INSERT_FINDING_SQL = """
INSERT INTO findings
    (pair_id_0, pair_id_1, merged_absorbed_id, verdict, confidence, rationale)
VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_INPUT_DIGEST_SQL = """
INSERT INTO finding_input_digests (finding_id, ordinal, input_ref, digest)
VALUES (?, ?, ?, ?)
"""

_INSERT_CLAIM_SQL = """
INSERT INTO finding_claims (finding_id, ordinal, claim)
VALUES (?, ?, ?)
"""

_SELECT_FINDINGS_SQL = """
SELECT id, pair_id_0, pair_id_1, merged_absorbed_id, verdict, confidence, rationale
FROM findings
ORDER BY id
"""

_SELECT_INPUT_DIGESTS_SQL = """
SELECT input_ref, digest
FROM finding_input_digests
WHERE finding_id = ?
ORDER BY ordinal
"""

_SELECT_CLAIMS_SQL = """
SELECT claim
FROM finding_claims
WHERE finding_id = ?
ORDER BY ordinal
"""


@dataclass(frozen=True)
class InputDigest:
    """One `(input_ref, sha256)` row a finding was computed from (Decision
    2). `input_ref` is a concept id for a typed-edge candidate's two file
    inputs, or a synthetic label (e.g. `"survivor_before"`) for a
    merged-body candidate's in-memory ledger strings -- this module treats
    it as an opaque key, never resolving it to bytes itself."""

    input_ref: str
    digest: str


@dataclass(frozen=True)
class Finding:
    """One `ContradictionVerdict`'s durable shape, ready to persist.
    `pair_ids`/`merged_absorbed_id` together are the SAME identity
    `bundle.decisions.decision_key_for` keys on (Decision 3) -- this module
    stores them verbatim but never computes or stores a `decision_key`
    itself; that join happens at read time, in the caller."""

    pair_ids: tuple[str, str]
    merged_absorbed_id: str | None
    verdict: str
    confidence: float
    rationale: str
    input_digests: tuple[InputDigest, ...]
    conflicting_claims: tuple[str, ...] = ()
    """The claims a `CONTRADICTS` verdict cited (#653): persisted so a
    served finding renders exactly like a freshly judged one. Defaulted
    empty -- `CONSISTENT`/`UNCERTAIN` never carry claims, and every
    pre-#653 construction site keeps working unchanged."""


@dataclass(frozen=True)
class PersistedFinding(Finding):
    """A `Finding` as read back, plus `stale` -- `True` iff `open_findings`
    was given a `current_digest` lookup and at least one `input_digests` row
    no longer matches it (Decision 2). Defaults to `False`: a caller that
    never asks for staleness gets a plain round trip, never a guess."""

    stale: bool = False


def record_findings(conn: sqlite3.Connection, batch: Sequence[Finding]) -> None:
    """Persist every `Finding` in `batch` as a new row, committing once
    (this store has no cross-row invariant a torn write could violate --
    Decision 7 -- so, unlike the two-phase merge ledger, one commit per call
    is sufficient rather than one commit per run)."""
    conn.execute(_CREATE_FINDINGS_TABLE_SQL)
    conn.execute(_CREATE_INPUT_DIGESTS_TABLE_SQL)
    conn.execute(_CREATE_CLAIMS_TABLE_SQL)
    for finding in batch:
        cursor = conn.execute(
            _INSERT_FINDING_SQL,
            (
                finding.pair_ids[0],
                finding.pair_ids[1],
                finding.merged_absorbed_id,
                finding.verdict,
                finding.confidence,
                finding.rationale,
            ),
        )
        finding_id = cursor.lastrowid
        for ordinal, input_digest in enumerate(finding.input_digests):
            conn.execute(
                _INSERT_INPUT_DIGEST_SQL,
                (finding_id, ordinal, input_digest.input_ref, input_digest.digest),
            )
        for ordinal, claim in enumerate(finding.conflicting_claims):
            conn.execute(_INSERT_CLAIM_SQL, (finding_id, ordinal, claim))
    conn.commit()


def _is_stale(
    input_digests: tuple[InputDigest, ...],
    current_digest: Callable[[str], str | None] | None,
) -> bool:
    """`True` iff any row's stored digest no longer matches the CURRENT
    digest `current_digest` reports for its `input_ref` (Decision 2: stale
    iff ANY row differs). `current_digest` returning `None` for a ref (the
    caller cannot currently determine it) is treated as unchanged -- a
    missing answer is not evidence of drift."""
    if current_digest is None:
        return False
    for row in input_digests:
        current = current_digest(row.input_ref)
        if current is not None and current != row.digest:
            return True
    return False


def open_findings(
    conn: sqlite3.Connection,
    *,
    current_digest: Callable[[str], str | None] | None = None,
) -> tuple[PersistedFinding, ...]:
    """Read every persisted finding, in insertion order.

    `current_digest`, when given, is called once per input row with its
    `input_ref`; a finding is marked `stale` iff any row's answer differs
    from what was stored (Decision 2). Omitted (the default), every row
    reads back with `stale=False` -- a plain round trip, never a guess.

    A store with no `findings`/`finding_input_digests` tables yet (a fresh
    `.openkos/findings.db`, or one predating this slice) returns `()`
    rather than raising."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "findings" not in tables:
        return ()
    has_claims_table = "finding_claims" in tables

    results: list[PersistedFinding] = []
    for (
        finding_id,
        pair_id_0,
        pair_id_1,
        merged_absorbed_id,
        verdict,
        confidence,
        rationale,
    ) in conn.execute(_SELECT_FINDINGS_SQL).fetchall():
        input_digests = tuple(
            InputDigest(input_ref, digest)
            for input_ref, digest in conn.execute(
                _SELECT_INPUT_DIGESTS_SQL, (finding_id,)
            ).fetchall()
        )
        # A store written before #653 has no `finding_claims` table --
        # claims read back empty rather than raising (the same absent-table
        # posture the whole-store `findings` check above takes).
        conflicting_claims: tuple[str, ...] = ()
        if has_claims_table:
            conflicting_claims = tuple(
                claim
                for (claim,) in conn.execute(
                    _SELECT_CLAIMS_SQL, (finding_id,)
                ).fetchall()
            )
        results.append(
            PersistedFinding(
                pair_ids=(pair_id_0, pair_id_1),
                merged_absorbed_id=merged_absorbed_id,
                verdict=verdict,
                confidence=confidence,
                rationale=rationale,
                input_digests=input_digests,
                conflicting_claims=conflicting_claims,
                stale=_is_stale(input_digests, current_digest),
            )
        )
    return tuple(results)
