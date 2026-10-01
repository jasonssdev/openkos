"""`.openkos/findings.db`, a fifth tenant: the pending-work queue (#1141,
`mvp4-unattended-foundations` design Decision 4, ADR-0037).

One row per consequential proposal an advisor produced and a human has not
yet resolved. The queue is DERIVED: deleting `findings.db` loses no human
decision (declines live in `bundle/.state/decisions/`), and re-running the
advisors repopulates every open row. Same FILE as the other tenants,
deliberately, so `purge` and `forget` reach it through the paths they already
have; separate TABLES and a separate module.

**Writes happen inside a commit phase.** Every mutating function takes the
`CommitSection` port (`application.lock_wait`) and enters it around its own
transaction; this module never takes the workspace lock itself. A late enqueue
therefore cannot resurrect a row a `forget` is erasing (workspace-lock: "Writes
To A Store That Can Hold Private Text Happen Under The Lock").

**Keys are kind-scoped and proposal-derived.** `contradiction` and `identity`
keys are exactly the keys the decline sidecars use
(`bundle.decisions.decision_key_for` -- whose `merged_absorbed_id` is the sole
discriminator between a typed-edge and a merged-body candidate over one pair --
and `identity_decision_key_for`), so a recorded human ruling addresses the same
proposal the row stands for. The stored `decision_key` is `"<kind>:<body>"`, so
two kinds can never collide. The other kinds hash their natural subject.

**Producers never resolve.** There is deliberately no `applied`/`declined`
writer here: only human-facing write paths resolve a row, and a producer may
only insert, refresh, or retire a row as `stale`.

**Sweep readiness (unit 4.4).** `pending_item_targets` names every concept a
row mentions, and `pending_item_input_digests` records every `(input_ref,
digest)` the proposal was computed from; both are indexed so the `forget` /
`purge` sweep can find rows by target id (`concept_id`), by `input_ref`
(including `sources-of:<id>` forms), and by digest, without parsing payloads.
`find_item_ids_referencing` is that lookup, read-only; the erasing sweep is not
here."""

import contextlib
import hashlib
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from openkos.bundle import decisions as bundle_decisions

CommitSection = Callable[[], AbstractContextManager[None]]
"""Structurally `application.lock_wait.CommitSection`: the state layer never
imports `application`, so the port is restated, not imported."""

KINDS: Final = (
    "identity",
    "relation_type",
    "volatility",
    "contradiction",
    "revision",
    "watch_refusal",
)
OPEN_STATUSES: Final = ("pending", "claimed")

_KEY_HEX_CHARS: Final = 32

_CREATE_ITEMS_SQL = """
CREATE TABLE IF NOT EXISTS pending_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('identity','relation_type',
        'volatility','contradiction','revision','watch_refusal')),
    producer TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','claimed',
        'applied','declined','stale')),
    resolution TEXT CHECK (resolution IN ('as_proposed','modified',
        'declined','stale')),
    resolved_by TEXT,
    claimed_by TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT
)
"""

_CREATE_ONE_OPEN_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS pending_items_one_open
    ON pending_items(decision_key) WHERE status IN ('pending','claimed')
"""

_CREATE_TARGETS_SQL = """
CREATE TABLE IF NOT EXISTS pending_item_targets (
    item_id INTEGER NOT NULL REFERENCES pending_items(id),
    ordinal INTEGER NOT NULL,
    concept_id TEXT NOT NULL
)
"""

_CREATE_DIGESTS_SQL = """
CREATE TABLE IF NOT EXISTS pending_item_input_digests (
    item_id INTEGER NOT NULL REFERENCES pending_items(id),
    ordinal INTEGER NOT NULL,
    input_ref TEXT NOT NULL,
    digest TEXT NOT NULL
)
"""

_SCHEMA_STATEMENTS: Final = (
    _CREATE_ITEMS_SQL,
    _CREATE_ONE_OPEN_INDEX_SQL,
    _CREATE_TARGETS_SQL,
    _CREATE_DIGESTS_SQL,
    # Sweep lookups (unit 4.4): by target id, by input ref, by digest.
    "CREATE INDEX IF NOT EXISTS pending_item_targets_concept"
    " ON pending_item_targets(concept_id)",
    "CREATE INDEX IF NOT EXISTS pending_item_input_digests_ref"
    " ON pending_item_input_digests(input_ref)",
    "CREATE INDEX IF NOT EXISTS pending_item_input_digests_digest"
    " ON pending_item_input_digests(digest)",
    "CREATE INDEX IF NOT EXISTS pending_items_payload_digest"
    " ON pending_items(payload_digest)",
)


class UpsertOutcome(StrEnum):
    INSERTED = "inserted"
    UNCHANGED = "unchanged"
    REPLACED = "replaced"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class InputDigest:
    """One `(input_ref, sha256)` a proposal was computed from; `input_ref` is
    opaque here, as in the sibling tenants."""

    input_ref: str
    digest: str


@dataclass(frozen=True)
class Proposal:
    """One advisor proposal, ready to enqueue. `key_body` comes from one of the
    `*_key` constructors; `payload` is canonical JSON the producer built;
    `targets` is every concept the row names (a sweep matches on it)."""

    kind: str
    key_body: str
    producer: str
    payload: str
    targets: tuple[str, ...]
    input_digests: tuple[InputDigest, ...] = ()
    merged_absorbed_id: str | None = None
    """Kept on the proposal (not stored) so a contradiction's decline lookup
    can be answered without re-deriving the key; informational otherwise."""

    @property
    def decision_key(self) -> str:
        return f"{self.kind}:{self.key_body}"


@dataclass(frozen=True)
class PendingItem:
    id: int
    decision_key: str
    kind: str
    producer: str
    payload: str
    payload_digest: str
    status: str
    claimed_by: str | None
    created_at: str
    last_seen_at: str
    targets: tuple[str, ...]
    resolution: str | None = None


@dataclass(frozen=True)
class KindStats:
    """Lifetime counters of one kind (`openkos pending --stats`)."""

    kind: str
    enqueued: int
    as_proposed: int
    modified: int
    declined: int
    stale: int
    open: int

    @property
    def resolved_by_a_human(self) -> int:
        return self.as_proposed + self.modified + self.declined


def _hashed(namespace: str, *parts: str) -> str:
    payload = namespace + "\n" + "\n".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_KEY_HEX_CHARS]


def contradiction_key(pair_ids: tuple[str, str], merged_absorbed_id: str | None) -> str:
    """The sidecar's own key, so a recorded decline addresses the row."""
    return bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)


def identity_key(member_ids: Sequence[str]) -> str:
    """The kept-distinct key (sorted member set)."""
    return bundle_decisions.identity_decision_key_for(member_ids)


def relation_type_key(source_id: str, target_id: str) -> str:
    """The untyped edge, unordered: the edge, not the suggested type, is the
    proposal's subject."""
    first, second = sorted((source_id, target_id))
    return _hashed("relation_type/v1", first, second)


def volatility_key(type_name: str) -> str:
    return _hashed("volatility/v1", type_name)


def revision_key(pair_ids: tuple[str, str]) -> str:
    first, second = sorted(pair_ids)
    return _hashed("revision/v1", first, second)


def watch_refusal_key(source_id: str) -> str:
    return _hashed("watch_refusal/v1", source_id)


def payload_digest_for(proposal: Proposal) -> str:
    """sha256 over the payload and the ordered input digests."""
    parts = [proposal.payload]
    parts.extend(f"{d.input_ref}\t{d.digest}" for d in proposal.input_digests)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the queue tables idempotently (first write creates them)."""
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()


def queue_exists(conn: sqlite3.Connection) -> bool:
    """Whether the queue has ever been computed: an absent queue is not an
    empty queue."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_items'"
    ).fetchone()
    return row is not None


def _now() -> datetime:
    return datetime.now(UTC)


def decline_in_force(bundle_dir: Path, proposal: Proposal) -> bool:
    """Whether the proposal's key has a decline in force in
    `bundle/.state/decisions/`. Only `contradiction` and `identity` have a
    decision sidecar, owned by one of the proposal's named concepts."""
    if proposal.kind not in ("contradiction", "identity"):
        return False
    # Every named concept is probed, not only the sorted-first: a merged-body
    # row also names its absorbed concept, which can sort ahead of the owner.
    for target in sorted(set(proposal.targets)):
        records: Sequence[Any] = (
            bundle_decisions.read_decisions(target, bundle_dir)
            if proposal.kind == "contradiction"
            else bundle_decisions.read_identity_decisions(target, bundle_dir)
        )
        if any(
            record.decision_key == proposal.key_body and record.state == "declined"
            for record in records
        ):
            return True
    return False


@contextlib.contextmanager
def _transaction(
    conn: sqlite3.Connection, commit_section: CommitSection
) -> Iterator[None]:
    """Enter the commit phase, then one immediate transaction inside it."""
    with commit_section():
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def _insert(
    conn: sqlite3.Connection, proposal: Proposal, digest: str, now: str
) -> None:
    cursor = conn.execute(
        "INSERT INTO pending_items (decision_key, kind, producer, payload,"
        " payload_digest, status, created_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (
            proposal.decision_key,
            proposal.kind,
            proposal.producer,
            proposal.payload,
            digest,
            now,
            now,
        ),
    )
    item_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO pending_item_targets (item_id, ordinal, concept_id)"
        " VALUES (?, ?, ?)",
        [(item_id, i, t) for i, t in enumerate(proposal.targets)],
    )
    conn.executemany(
        "INSERT INTO pending_item_input_digests (item_id, ordinal, input_ref, digest)"
        " VALUES (?, ?, ?, ?)",
        [
            (item_id, i, d.input_ref, d.digest)
            for i, d in enumerate(proposal.input_digests)
        ],
    )


def upsert_proposal(
    conn: sqlite3.Connection,
    proposal: Proposal,
    *,
    commit_section: CommitSection,
    bundle_dir: Path,
    clock: Callable[[], datetime] = _now,
) -> UpsertOutcome:
    """Enqueue `proposal`: nothing when a decline is in force; refresh the
    last-seen time of an open row with the same digest; retire an open row
    with a different digest as `stale` and insert a new `pending` one; else
    insert. At most one open row per key (the unique partial index)."""
    if proposal.kind not in KINDS:
        raise ValueError(f"unknown pending-work kind: {proposal.kind!r}")
    # Read the decline outside the DB transaction; it lives in the bundle.
    suppressed = decline_in_force(bundle_dir, proposal)
    digest = payload_digest_for(proposal)
    now = clock().isoformat()
    with _transaction(conn, commit_section):
        if suppressed:
            return UpsertOutcome.SUPPRESSED
        open_row = conn.execute(
            "SELECT id, payload_digest FROM pending_items"
            " WHERE decision_key = ? AND status IN ('pending','claimed')",
            (proposal.decision_key,),
        ).fetchone()
        if open_row is None:
            _insert(conn, proposal, digest, now)
            return UpsertOutcome.INSERTED
        if open_row[1] == digest:
            conn.execute(
                "UPDATE pending_items SET last_seen_at = ? WHERE id = ?",
                (now, open_row[0]),
            )
            return UpsertOutcome.UNCHANGED
        conn.execute(
            "UPDATE pending_items SET status='stale', resolution='stale',"
            " claimed_by=NULL, resolved_at=? WHERE id = ?",
            (now, open_row[0]),
        )
        _insert(conn, proposal, digest, now)
        return UpsertOutcome.REPLACED


def retire_unseen(
    conn: sqlite3.Connection,
    kind: str,
    seen_keys: Collection[str],
    *,
    complete: bool,
    commit_section: CommitSection,
    clock: Callable[[], datetime] = _now,
) -> int:
    """Retire as `stale` every open row of `kind` whose `decision_key` is not in
    `seen_keys` (the proposal no longer exists). A run that was not COMPLETE
    retires nothing: a truncated run cannot tell absent from not-reached."""
    if not complete:
        return 0
    now = clock().isoformat()
    with _transaction(conn, commit_section):
        rows = conn.execute(
            "SELECT id, decision_key FROM pending_items"
            " WHERE kind = ? AND status IN ('pending','claimed')",
            (kind,),
        ).fetchall()
        doomed = [row[0] for row in rows if row[1] not in seen_keys]
        conn.executemany(
            "UPDATE pending_items SET status='stale', resolution='stale',"
            " claimed_by=NULL, resolved_at=? WHERE id = ?",
            [(now, item_id) for item_id in doomed],
        )
    return len(doomed)


def _platform() -> str:
    """`sys.platform` read through a function so mypy does not narrow the
    per-OS branches below to the host it runs on (with `warn_unreachable`,
    the other OS's branch would otherwise be flagged on every CI runner)."""
    return sys.platform


@lru_cache(maxsize=1)
def boot_id() -> str:
    """An identifier of the current boot, so a pid reused after a reboot is not
    mistaken for a live claimant. Empty when the platform offers none."""
    try:
        platform = _platform()
        if platform.startswith("linux"):
            return (
                Path("/proc/sys/kernel/random/boot_id")
                .read_text(encoding="utf-8")
                .strip()
            )
        if platform == "darwin":
            out = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
            return hashlib.sha256(out.strip().encode()).hexdigest()[:16]
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def current_claimant() -> str:
    """`"<pid>@<boot-id>"` for this process."""
    return f"{os.getpid()}@{boot_id()}"


def claimant_alive(claimant: str) -> bool:
    """Whether the process named by a `claimed_by` value still exists on this
    boot. Unparseable claimants read as dead (the safe direction: the row
    returns to the queue)."""
    pid_text, _, claimed_boot = claimant.partition("@")
    try:
        pid = int(pid_text)
    except ValueError:
        return False
    if pid <= 0:
        return False
    if claimed_boot and boot_id() and claimed_boot != boot_id():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def claim_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    claimant: str,
    commit_section: CommitSection,
    alive: Callable[[str], bool] = claimant_alive,
) -> bool:
    """Mark an open row `claimed` by `claimant` while a human-facing path
    presents it. Refused (False) when the row is not open or another LIVE
    claimant holds it; a dead claimant's claim is taken over."""
    with _transaction(conn, commit_section):
        row = conn.execute(
            "SELECT status, claimed_by FROM pending_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None or row[0] not in OPEN_STATUSES:
            return False
        if row[0] == "claimed" and row[1] not in (None, claimant) and alive(row[1]):
            return False
        conn.execute(
            "UPDATE pending_items SET status='claimed', claimed_by=? WHERE id = ?",
            (claimant, item_id),
        )
    return True


def _select_items(
    conn: sqlite3.Connection,
    where: str,
    params: tuple[str, ...],
    alive: Callable[[str], bool],
) -> list[PendingItem]:
    sql = (
        "SELECT id, decision_key, kind, producer, payload, payload_digest, status,"
        " claimed_by, created_at, last_seen_at, resolution FROM pending_items"
    )
    items: list[PendingItem] = []
    for row in conn.execute(f"{sql} {where} ORDER BY id", params).fetchall():
        status, claimed_by = row[6], row[7]
        if status == "claimed" and (claimed_by is None or not alive(claimed_by)):
            status, claimed_by = "pending", None
        targets = tuple(
            r[0]
            for r in conn.execute(
                "SELECT concept_id FROM pending_item_targets"
                " WHERE item_id = ? ORDER BY ordinal",
                (row[0],),
            )
        )
        items.append(
            PendingItem(
                id=row[0],
                decision_key=row[1],
                kind=row[2],
                producer=row[3],
                payload=row[4],
                payload_digest=row[5],
                status=status,
                claimed_by=claimed_by,
                created_at=row[8],
                last_seen_at=row[9],
                targets=targets,
                resolution=row[10],
            )
        )
    return items


def open_items(
    conn: sqlite3.Connection,
    *,
    kind: str | None = None,
    alive: Callable[[str], bool] = claimant_alive,
) -> list[PendingItem]:
    """Every open row, oldest first. A `claimed` row whose claimant is dead
    reads as `pending` (no lease timer). Read-only; an absent queue reads as
    empty -- callers that must tell the two apart use `queue_exists`."""
    if not queue_exists(conn):
        return []
    where = "WHERE status IN ('pending','claimed')"
    params: tuple[str, ...] = ()
    if kind is not None:
        where += " AND kind = ?"
        params = (kind,)
    return _select_items(conn, where, params, alive)


def all_items(
    conn: sqlite3.Connection,
    *,
    alive: Callable[[str], bool] = claimant_alive,
) -> list[PendingItem]:
    """Every row of every status, oldest first (`openkos pending --all`).
    Read-only; an absent queue reads as empty (see `queue_exists`)."""
    if not queue_exists(conn):
        return []
    return _select_items(conn, "", (), alive)


def kind_stats(conn: sqlite3.Connection) -> list[KindStats]:
    """Per-kind lifetime counters, in `KINDS` order, for kinds that ever had a
    row. The counts cover only the queue's current lifetime: a rebuild or a
    purge resets applied history. Read-only."""
    if not queue_exists(conn):
        return []
    counts: dict[str, dict[str, int]] = {}
    for kind, status, resolution, n in conn.execute(
        "SELECT kind, status, resolution, COUNT(*) FROM pending_items"
        " GROUP BY kind, status, resolution"
    ):
        bucket = counts.setdefault(kind, dict.fromkeys(_STAT_FIELDS, 0))
        bucket["enqueued"] += n
        if status in OPEN_STATUSES:
            bucket["open"] += n
        elif status == "stale":
            bucket["stale"] += n
        elif status == "declined":
            bucket["declined"] += n
        elif resolution == "modified":
            bucket["modified"] += n
        else:
            bucket["as_proposed"] += n
    return [
        KindStats(
            kind=kind,
            enqueued=counts[kind]["enqueued"],
            as_proposed=counts[kind]["as_proposed"],
            modified=counts[kind]["modified"],
            declined=counts[kind]["declined"],
            stale=counts[kind]["stale"],
            open=counts[kind]["open"],
        )
        for kind in KINDS
        if kind in counts
    ]


_STAT_FIELDS: Final = (
    "enqueued",
    "as_proposed",
    "modified",
    "declined",
    "stale",
    "open",
)

_SOURCES_OF_PREFIX = "sources-of:"


def find_item_ids_referencing(
    conn: sqlite3.Connection,
    concept_ids: Iterable[str],
    *,
    digests: Iterable[str] = (),
) -> set[int]:
    """The ids of every row (any status) that names a concept in `concept_ids`
    as a target, as an input ref (exactly, or as a `sources-of:<id>` suffix), or
    that was computed from a digest in `digests`. Read-only: the erasing sweep
    is the privacy-purge unit's, this is the lookup it will use."""
    if not queue_exists(conn):
        return set()
    ids = list(concept_ids)
    found: set[int] = set()
    for concept_id in ids:
        for sql, arg in (
            (
                "SELECT item_id FROM pending_item_targets WHERE concept_id = ?",
                concept_id,
            ),
            (
                "SELECT item_id FROM pending_item_input_digests WHERE input_ref = ?",
                concept_id,
            ),
            (
                "SELECT item_id FROM pending_item_input_digests WHERE input_ref = ?",
                _SOURCES_OF_PREFIX + concept_id,
            ),
        ):
            found.update(r[0] for r in conn.execute(sql, (arg,)))
    for digest in digests:
        found.update(
            r[0]
            for r in conn.execute(
                "SELECT item_id FROM pending_item_input_digests WHERE digest = ?",
                (digest,),
            )
        )
        found.update(
            r[0]
            for r in conn.execute(
                "SELECT id FROM pending_items WHERE payload_digest = ?", (digest,)
            )
        )
    return found
