"""`.openkos/findings.db`, a fourth tenant: durable persistence for
decision-revision-detection's judge verdicts (#1014 piece a, design.md
Decision 2), mirroring `state.edge_suggestions`' shape one table-family
over.

Same FILE as the findings, adjudication, and edge-suggestion stores,
deliberately: `purge` deletes `findings.db` wholesale and `forget` sweeps
it for purge-id membership, so this fourth tenant inherits both erasure
paths instead of opening a new privacy surface. Separate TABLES, separate
module: this tenant's identity (an unordered Decision pair) and consumer
(the `revisions` verb) differ from the other three, and no module reads
another's rows -- the sibling-table isolation this module's own regression
test pins (spec: "Revision Findings Persist In Sibling Tables").

**The direction (holder) is never stored.** `pair_id_0 < pair_id_1`
(sorted, so REPLACE keys on identity, not on which side the judge happened
to name first); the stored `date_0`/`date_1`/`date_state_0`/`date_state_1`
columns are the only record of chronology, and
`resolution.decision_revision.pair_direction` recomputes direction from
them every time it is needed. There is no stored holder that could
disagree with the dates (ADR-0025).

**Privacy sweep, source-inclusive.** `delete_revision_findings_referencing`
deletes a finding whose `pair_id_0`, `pair_id_1`, OR any
`revision_finding_input_digests.input_ref` names a purge-set member --
including an `input_ref` of the form `"sources-of:<id>"`, matched on the
suffix. A revision finding's `rationale` and per-side quotes can embed
verbatim text from either Decision's body, or from a Source either
Decision's provenance reaches, so forgetting any of those three must
scrub it (the lesson from `a-second-tenant-must-join-the-privacy-sweep`:
join every column that can hold a concept id, not only the primary one)."""

import sqlite3
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime

_CREATE_REVISION_FINDINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS revision_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id_0 TEXT NOT NULL,
    pair_id_1 TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL,
    quote_0 TEXT,
    quote_1 TEXT,
    date_0 TEXT,
    date_1 TEXT,
    date_state_0 TEXT NOT NULL,
    date_state_1 TEXT NOT NULL,
    include_confidential INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_CREATE_REVISION_FINDING_DIGESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS revision_finding_input_digests (
    finding_id INTEGER NOT NULL REFERENCES revision_findings(id),
    ordinal INTEGER NOT NULL,
    input_ref TEXT NOT NULL,
    digest TEXT NOT NULL
)
"""

_INSERT_REVISION_FINDING_SQL = """
INSERT INTO revision_findings
    (pair_id_0, pair_id_1, verdict, confidence, rationale,
     quote_0, quote_1, date_0, date_1, date_state_0, date_state_1,
     include_confidential, prompt_version, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_DIGEST_SQL = """
INSERT INTO revision_finding_input_digests
    (finding_id, ordinal, input_ref, digest)
VALUES (?, ?, ?, ?)
"""

_SELECT_REVISION_FINDINGS_SQL = """
SELECT id, pair_id_0, pair_id_1, verdict, confidence, rationale,
       quote_0, quote_1, date_0, date_1, date_state_0, date_state_1,
       include_confidential, prompt_version
FROM revision_findings
ORDER BY id
"""

_SELECT_DIGESTS_SQL = """
SELECT input_ref, digest
FROM revision_finding_input_digests
WHERE finding_id = ?
ORDER BY ordinal
"""

_SOURCES_OF_PREFIX = "sources-of:"
"""The `revision_input_digests` `input_ref` form for provenance-path rows
(design.md Decision 2, ordinal 3-4): `f"{_SOURCES_OF_PREFIX}{decision_id}"`.
The sweep matches a purge-set member on the SUFFIX of this form, not just
an exact `input_ref` match, so forgetting a Decision also scrubs any
finding whose provenance path named it."""


@dataclass(frozen=True)
class InputDigest:
    """One `(input_ref, sha256)` row a revision finding was computed from
    -- this module treats `input_ref` as an opaque key, never resolving it
    to bytes itself (design.md Decision 2)."""

    input_ref: str
    digest: str


@dataclass(frozen=True)
class RevisionFinding:
    """One judge verdict's durable shape, ready to persist (design.md
    Decision 2, Interfaces). `pair_ids` need not arrive pre-sorted --
    `record_revision_findings` sorts defensively so the stored
    `pair_id_0 < pair_id_1` invariant always holds regardless of which
    side the judge named first."""

    pair_ids: tuple[str, str]
    verdict: str
    confidence: float
    rationale: str
    quotes: tuple[str | None, str | None]
    """Aligned with the SORTED `pair_ids`, not with however the caller
    passed them in -- `record_revision_findings` re-aligns both `quotes`
    and `dates`/`date_states` when it sorts."""
    dates: tuple[str | None, str | None]
    date_states: tuple[str, str]
    include_confidential: bool
    prompt_version: str
    input_digests: tuple[InputDigest, ...]


def record_revision_findings(
    conn: sqlite3.Connection, batch: Sequence[RevisionFinding]
) -> None:
    """Persist every `RevisionFinding` in `batch`, committing once (findings
    Decision 7: no cross-row invariant a torn write could violate, so one
    commit per call suffices).

    REPLACE semantics per sorted pair key: a fresh finding for a pair
    deletes that pair's earlier row (and its digest child rows) first, so
    re-runs and `--fresh` keep the store bounded by the latest verdict per
    pair instead of appending an unbounded history nothing reads. Plain
    deletes, no VACUUM: superseding is bookkeeping, not the privacy erasure
    `delete_revision_findings_referencing` performs."""
    conn.execute(_CREATE_REVISION_FINDINGS_TABLE_SQL)
    conn.execute(_CREATE_REVISION_FINDING_DIGESTS_TABLE_SQL)
    now = datetime.now(UTC).isoformat()
    for finding in batch:
        first, second = finding.pair_ids
        if first <= second:
            pair_id_0, pair_id_1 = first, second
            quote_0, quote_1 = finding.quotes
            date_0, date_1 = finding.dates
            date_state_0, date_state_1 = finding.date_states
        else:
            pair_id_0, pair_id_1 = second, first
            quote_1, quote_0 = finding.quotes
            date_1, date_0 = finding.dates
            date_state_1, date_state_0 = finding.date_states
        superseded = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM revision_findings "
                "WHERE pair_id_0 = ? AND pair_id_1 = ?",
                (pair_id_0, pair_id_1),
            ).fetchall()
        ]
        if superseded:
            marks = ",".join("?" for _ in superseded)
            conn.execute(
                f"DELETE FROM revision_finding_input_digests "  # noqa: S608
                f"WHERE finding_id IN ({marks})",
                superseded,
            )
            conn.execute(
                f"DELETE FROM revision_findings WHERE id IN ({marks})",  # noqa: S608
                superseded,
            )
        cursor = conn.execute(
            _INSERT_REVISION_FINDING_SQL,
            (
                pair_id_0,
                pair_id_1,
                finding.verdict,
                finding.confidence,
                finding.rationale,
                quote_0,
                quote_1,
                date_0,
                date_1,
                date_state_0,
                date_state_1,
                1 if finding.include_confidential else 0,
                finding.prompt_version,
                now,
            ),
        )
        finding_id = cursor.lastrowid
        for ordinal, digest in enumerate(finding.input_digests):
            conn.execute(
                _INSERT_DIGEST_SQL,
                (finding_id, ordinal, digest.input_ref, digest.digest),
            )
    conn.commit()


def open_revision_findings(
    conn: sqlite3.Connection,
) -> tuple[RevisionFinding, ...]:
    """Read every persisted revision finding, in insertion order. A store
    with no `revision_findings` table yet (a fresh `findings.db`, or one
    predating this slice) returns `()` rather than raising -- the same
    absent-table posture `findings.open_findings` and
    `edge_suggestions.open_edge_suggestions` take."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "revision_findings" not in tables:
        return ()
    results: list[RevisionFinding] = []
    for (
        finding_id,
        pair_id_0,
        pair_id_1,
        verdict,
        confidence,
        rationale,
        quote_0,
        quote_1,
        date_0,
        date_1,
        date_state_0,
        date_state_1,
        include_confidential,
        prompt_version,
    ) in conn.execute(_SELECT_REVISION_FINDINGS_SQL).fetchall():
        digests = tuple(
            InputDigest(input_ref=input_ref, digest=digest)
            for input_ref, digest in conn.execute(
                _SELECT_DIGESTS_SQL, (finding_id,)
            ).fetchall()
        )
        results.append(
            RevisionFinding(
                pair_ids=(pair_id_0, pair_id_1),
                verdict=verdict,
                confidence=confidence,
                rationale=rationale,
                quotes=(quote_0, quote_1),
                dates=(date_0, date_1),
                date_states=(date_state_0, date_state_1),
                include_confidential=bool(include_confidential),
                prompt_version=prompt_version,
                input_digests=digests,
            )
        )
    return tuple(results)


def delete_revision_findings_referencing(
    conn: sqlite3.Connection, purge_ids: AbstractSet[str]
) -> int:
    """Privacy sweep, this store's own arm of the `findings.db` sweep
    quartet: a revision finding naming a `purge_ids` member as EITHER
    `pair_id_0`/`pair_id_1`, OR as any child digest row's `input_ref`
    (exact match, or the `sources-of:<id>` suffix form), is deleted --
    digest child rows included -- and the count removed is returned.

    Same erasure discipline as `edge_suggestions.
    delete_edge_suggestions_referencing`: after the deletes commit,
    `VACUUM` rebuilds the file without the freelist pages a plain DELETE
    leaves recoverable, and a CHECKED `wal_checkpoint(TRUNCATE)` clears the
    WAL sidecar -- a blocked checkpoint is reported through the row's
    `busy` column, never an exception, so it is raised here into the
    caller's fail-loud warning path rather than reported as success over
    silent residue.

    A store with no `revision_findings` table, or an empty `purge_ids`,
    answers 0 rather than raising (and never VACUUMs)."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "revision_findings" not in tables or not purge_ids:
        return 0
    doomed = {
        finding_id
        for finding_id, pair_id_0, pair_id_1 in conn.execute(
            "SELECT id, pair_id_0, pair_id_1 FROM revision_findings"
        ).fetchall()
        if pair_id_0 in purge_ids or pair_id_1 in purge_ids
    }
    if "revision_finding_input_digests" in tables:
        for finding_id, input_ref in conn.execute(
            "SELECT finding_id, input_ref FROM revision_finding_input_digests"
        ).fetchall():
            if finding_id in doomed:
                continue
            if input_ref in purge_ids:
                doomed.add(finding_id)
                continue
            if (
                input_ref.startswith(_SOURCES_OF_PREFIX)
                and input_ref[len(_SOURCES_OF_PREFIX) :] in purge_ids
            ):
                doomed.add(finding_id)
    if not doomed:
        return 0
    doomed_ids = list(doomed)
    doomed_marks = ",".join("?" for _ in doomed_ids)
    # The interpolated fragment is "?" placeholder marks only -- every
    # VALUE travels through the parameter tuple (SQLite has no native
    # array binding for IN).
    if "revision_finding_input_digests" in tables:
        conn.execute(
            f"DELETE FROM revision_finding_input_digests "  # noqa: S608
            f"WHERE finding_id IN ({doomed_marks})",
            doomed_ids,
        )
    conn.execute(
        f"DELETE FROM revision_findings WHERE id IN ({doomed_marks})",  # noqa: S608
        doomed_ids,
    )
    conn.commit()
    conn.execute("VACUUM")
    busy, _wal_frames, _checkpointed = conn.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()
    if busy:
        raise sqlite3.OperationalError(
            "wal checkpoint busy: a concurrent reader held the WAL open, so "
            "deleted revision-finding bytes may remain in it"
        )
    return len(doomed_ids)
