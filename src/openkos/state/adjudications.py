"""`.openkos/findings.db`, second tenant: durable persistence for
machine-computed ADJUDICATION verdicts (issue #779), mirroring
`state.findings`' design decisions one table-family over.

`contradictions` has persisted its verdicts since #653, so a repeat run on
an unchanged bundle costs zero model calls; `adjudicate` re-spent one call
per candidate group on every invocation -- three times in one E2E session
for the same 8 stable verdicts, including the run whose only purpose was
to test the `--confirm-count` rail. This store closes that asymmetry with
the SAME per-row digest staleness contract: an adjudication is servable
iff every member's CURRENT content hash equals what was stored when the
verdict was computed (`state.vectorstore.content_hash` over raw bytes,
supplied by the caller at read time).

Same FILE as the findings store, deliberately: `purge` deletes
`findings.db` wholesale and `forget` sweeps it for purge-id membership, so
a second tenant in the same file inherits both erasure paths instead of
opening a new privacy surface. Separate TABLES, separate module: the two
verdict families have different identities (an unordered pair plus a
merged-body discriminator vs an N-member group) and different consumers,
and neither module reads the other's rows.

`include_confidential` is stored per row and matched at serve time: a
verdict computed with confidential members excluded was computed over a
DIFFERENT prompt than one computed with them included, so the two must
never serve for each other even when every digest matches.

`rubric_digest` (#838) is the same argument generalized: a verdict
computed under a different judgment RUBRIC -- the adjudication system
prompt plus the deterministic post-parse rule that can change a verdict
-- was also computed over a different prompt, a much larger difference
than a member subset. It is stored per row
(`resolution.adjudication.rubric_digest` at persist time) and matched at
serve time; a row carrying `None` (written before the column existed)
is never servable, because a verdict from an unknown rubric is not a
verdict this build would produce. The re-spend is proportional and
targeted: it happens exactly when the rubric actually changed, and a
build that changes no prompt re-judges nothing."""

import sqlite3
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

_CREATE_ADJUDICATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS adjudications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL,
    include_confidential INTEGER NOT NULL,
    rubric_digest TEXT
)
"""

_CREATE_ADJUDICATION_DIGESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS adjudication_input_digests (
    adjudication_id INTEGER NOT NULL REFERENCES adjudications(id),
    ordinal INTEGER NOT NULL,
    input_ref TEXT NOT NULL,
    digest TEXT NOT NULL
)
"""

_INSERT_ADJUDICATION_SQL = """
INSERT INTO adjudications
    (group_key, verdict, confidence, rationale, include_confidential,
     rubric_digest)
VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_DIGEST_SQL = """
INSERT INTO adjudication_input_digests
    (adjudication_id, ordinal, input_ref, digest)
VALUES (?, ?, ?, ?)
"""

_SELECT_ADJUDICATIONS_SQL = """
SELECT id, group_key, verdict, confidence, rationale, include_confidential,
       rubric_digest
FROM adjudications
ORDER BY id
"""

_SELECT_ADJUDICATIONS_PRE_838_SQL = """
SELECT id, group_key, verdict, confidence, rationale, include_confidential,
       NULL
FROM adjudications
ORDER BY id
"""
"""The same projection over a store a pre-#838 build created: the missing
`rubric_digest` column is synthesized as NULL, so a read-only path never
raises over the old shape and every pre-migration row reads as "unknown
rubric" -- the fail-closed, not-servable answer."""

_SELECT_DIGESTS_SQL = """
SELECT input_ref, digest
FROM adjudication_input_digests
WHERE adjudication_id = ?
ORDER BY ordinal
"""

_GROUP_KEY_SEPARATOR = "\n"
"""Member ids are joined with a newline, a byte no bundle-relative concept
id can carry (ids come from on-disk paths), so the key can never be forged
by a crafted id that embeds the separator."""


def group_key_for(member_ids: Sequence[str]) -> str:
    """The store's group identity: member ids joined in their given order
    (`CandidateGroup.member_ids` is already sorted ascending, so equal
    groups key equally without re-sorting here)."""
    return _GROUP_KEY_SEPARATOR.join(member_ids)


@dataclass(frozen=True)
class InputDigest:
    """One `(input_ref, sha256)` row an adjudication was computed from --
    `input_ref` is a member concept id; this module treats it as an opaque
    key, never resolving it to bytes itself (findings Decision 2)."""

    input_ref: str
    digest: str


@dataclass(frozen=True)
class Adjudication:
    """One adjudication verdict's durable shape, ready to persist."""

    member_ids: tuple[str, ...]
    verdict: str
    confidence: float
    rationale: str
    include_confidential: bool
    input_digests: tuple[InputDigest, ...]
    rubric_digest: str | None = None
    """The judgment-rubric fingerprint this verdict was computed under
    (#838, `resolution.adjudication.rubric_digest`). `None` on a row read
    from a store a pre-#838 build wrote -- an unknown rubric, which the
    serve gate treats as never servable. Defaulted so every pre-#838
    construction site keeps working unchanged, mirroring
    `findings.Finding.conflicting_claims`' precedent."""


def _ensure_rubric_digest_column(conn: sqlite3.Connection) -> None:
    """#838's migration, in the ONE place the tables are created: `CREATE
    TABLE IF NOT EXISTS` will not add a column to an existing table, so a
    store a pre-#838 build created is widened here. Rows written before
    the column existed keep NULL -- the serve gate's "unknown rubric",
    never servable.

    The check-then-ALTER pair is racy across processes: two concurrent
    runs against the same pre-#838 store can both observe the column
    missing, and the second ALTER raises `duplicate column name`. The
    loser's ALTER is a no-op in intent, so exactly that error is
    swallowed; any other OperationalError still propagates into the
    caller's fail-open advisory path."""
    if _has_rubric_digest_column(conn):
        return
    try:
        conn.execute("ALTER TABLE adjudications ADD COLUMN rubric_digest TEXT")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def record_adjudications(
    conn: sqlite3.Connection, batch: Sequence[Adjudication]
) -> None:
    """Persist every `Adjudication` in `batch`, committing once -- no
    cross-row invariant exists (findings Decision 7), so one commit per
    call suffices.

    REPLACE semantics per group key (#779 review, three lenses): a fresh
    verdict for a group deletes that group's superseded rows first, so
    re-judges and `--fresh` runs keep the store bounded by the LIVE group
    set instead of appending an unbounded history nothing reads. Plain
    deletes, no VACUUM: superseding is bookkeeping, not the privacy
    erasure `delete_adjudications_referencing` performs."""
    conn.execute(_CREATE_ADJUDICATIONS_TABLE_SQL)
    conn.execute(_CREATE_ADJUDICATION_DIGESTS_TABLE_SQL)
    _ensure_rubric_digest_column(conn)
    for adjudication in batch:
        superseded = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM adjudications WHERE group_key = ?",
                (group_key_for(adjudication.member_ids),),
            ).fetchall()
        ]
        if superseded:
            marks = ",".join("?" for _ in superseded)
            conn.execute(
                f"DELETE FROM adjudication_input_digests "  # noqa: S608
                f"WHERE adjudication_id IN ({marks})",
                superseded,
            )
            conn.execute(
                f"DELETE FROM adjudications WHERE id IN ({marks})",  # noqa: S608
                superseded,
            )
        cursor = conn.execute(
            _INSERT_ADJUDICATION_SQL,
            (
                group_key_for(adjudication.member_ids),
                adjudication.verdict,
                adjudication.confidence,
                adjudication.rationale,
                1 if adjudication.include_confidential else 0,
                adjudication.rubric_digest,
            ),
        )
        adjudication_id = cursor.lastrowid
        for ordinal, digest in enumerate(adjudication.input_digests):
            conn.execute(
                _INSERT_DIGEST_SQL,
                (adjudication_id, ordinal, digest.input_ref, digest.digest),
            )
    conn.commit()


def _has_rubric_digest_column(conn: sqlite3.Connection) -> bool:
    """Whether the `adjudications` table carries #838's `rubric_digest`
    column -- `False` on a store a pre-#838 build created. PRAGMA, not a
    speculative SELECT: probing by exception would swallow the very error
    class the read paths are meant to fail loudly on."""
    return "rubric_digest" in {
        row[1] for row in conn.execute("PRAGMA table_info(adjudications)").fetchall()
    }


def open_adjudications(conn: sqlite3.Connection) -> tuple[Adjudication, ...]:
    """Read every persisted adjudication, in insertion order. A store with
    no `adjudications` table yet (a fresh `findings.db`, or one predating
    this slice) returns `()` rather than raising -- the same absent-table
    posture `findings.open_findings` takes.

    A store whose table predates #838's `rubric_digest` column is read
    through the pre-838 projection (the column synthesized as NULL) rather
    than degrading the whole store to a failed read -- the issue's own
    requirement: the read path tolerates the pre-migration shape."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "adjudications" not in tables:
        return ()
    select_sql = (
        _SELECT_ADJUDICATIONS_SQL
        if _has_rubric_digest_column(conn)
        else _SELECT_ADJUDICATIONS_PRE_838_SQL
    )
    results: list[Adjudication] = []
    for (
        adjudication_id,
        group_key,
        verdict,
        confidence,
        rationale,
        include_confidential,
        rubric_digest,
    ) in conn.execute(select_sql).fetchall():
        digests = tuple(
            InputDigest(input_ref=input_ref, digest=digest)
            for input_ref, digest in conn.execute(
                _SELECT_DIGESTS_SQL, (adjudication_id,)
            ).fetchall()
        )
        results.append(
            Adjudication(
                member_ids=tuple(group_key.split(_GROUP_KEY_SEPARATOR)),
                verdict=verdict,
                confidence=confidence,
                rationale=rationale,
                include_confidential=bool(include_confidential),
                input_digests=digests,
                rubric_digest=rubric_digest,
            )
        )
    return tuple(results)


def delete_adjudications_referencing(
    conn: sqlite3.Connection, purge_ids: AbstractSet[str]
) -> int:
    """Privacy sweep, the findings sweep's twin for this table family: an
    adjudication whose member set names a `purge_ids` member is deleted --
    its `rationale` can quote the member's body verbatim -- digest child
    rows included, and the count removed is returned.

    Same erasure discipline as `findings.delete_findings_referencing`:
    after the deletes commit, `VACUUM` rebuilds the file without the
    freelist pages a plain DELETE leaves recoverable, and a CHECKED
    `wal_checkpoint(TRUNCATE)` clears the WAL sidecar -- a blocked
    checkpoint is reported through the row's `busy` column, never an
    exception, so it is raised here into the caller's fail-loud warning
    path rather than reported as success over silent residue.

    A store with no `adjudications` table answers 0 rather than raising."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "adjudications" not in tables or not purge_ids:
        return 0
    doomed = [
        row_id
        for row_id, group_key in conn.execute(
            "SELECT id, group_key FROM adjudications"
        ).fetchall()
        if any(member in purge_ids for member in group_key.split(_GROUP_KEY_SEPARATOR))
    ]
    if not doomed:
        return 0
    doomed_marks = ",".join("?" for _ in doomed)
    # The interpolated fragment is "?" placeholder marks only -- every
    # VALUE travels through the parameter tuple (SQLite has no native
    # array binding for IN).
    if "adjudication_input_digests" in tables:
        conn.execute(
            f"DELETE FROM adjudication_input_digests "  # noqa: S608
            f"WHERE adjudication_id IN ({doomed_marks})",
            doomed,
        )
    conn.execute(
        f"DELETE FROM adjudications WHERE id IN ({doomed_marks})",  # noqa: S608
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
            "deleted adjudication bytes may remain in it"
        )
    return len(doomed)
