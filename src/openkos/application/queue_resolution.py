"""Resolution of pending-work rows by the human write paths (#1141, ADR-0037,
`mvp4-unattended-foundations` unit 4.3).

A row leaves the open states only when a human-facing path performs the write it
proposes (or declines it). Those paths share a handful of write cores, so the
resolution lives in the cores and no verb, `curate` stage or walk can drift from
the others: `lifecycle.merge_core`, `relate_core`, `set_volatility_core`, the
reconcile write in `reconcile_service.reconcile_pair`, the decline/keep-distinct
writers `apply_identity_decision` / `apply_contradiction_decision`, and
`ingest_source` for a `watch_refusal`. The producer-side upsert and the runner
never call anything here (pending-work: "Only Human-Facing Write Paths Resolve A
Row").

**Same commit phase.** Every caller of a core already holds the workspace lock
around the write (a whole-verb lock or a commit section), so the resolution runs
under it too and takes no second section: a `forget` cannot interleave between a
write and its resolution, and the lock is not re-entered.

**`as_proposed` versus `modified`.** `as_proposed` when the applied change
equals the row's proposal -- the same merge direction, relation type and
direction, or volatility tier; `modified` otherwise. A proposal that carries no
concrete value (a contradiction: the row says "reconcile this pair", and the
direction is the human's) is `as_proposed`; a payload this module cannot read is
`modified`, never a claim of mechanical agreement.

**Derived and best-effort.** The queue is a derived cache: a write that already
landed must never fail because the cache is absent, locked or corrupt. An absent
`findings.db` (or a queue never computed) is a no-op and is never created; any
other failure leaves the row open, and the producer's next complete pass retires
it as `stale` once its inputs have changed.

**Keys are the producers'.** Every key is built with the same `pending_queue`
constructors the producers use, never re-derived here.

**The registry.** `WRITE_CORE_RESOLUTIONS` names each core that resolves rows and
the function it calls; `NON_RESOLVING_CORES` names each `*_core` that
deliberately resolves nothing, with the reason. A structural test enumerates
every `*_core` in the package, so a new core fails it until it is in one of the
two (`tests/unit/application/test_queue_resolution_registry.py`).
"""

import contextlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

from openkos import config
from openkos.state import derived
from openkos.state import pending_queue as pq

WRITE_CORE_RESOLUTIONS: Final = {
    "openkos.application.lifecycle.merge_core": "resolve_merged",
    "openkos.application.lifecycle.relate_core": "resolve_related",
    "openkos.application.lifecycle.set_volatility_core": "resolve_volatility_set",
    "openkos.application.reconcile_service.reconcile_pair": "resolve_reconciled",
    "openkos.application.duplicates_service.apply_identity_decision": (
        "resolve_declined_identity"
    ),
    "openkos.application.contradictions_service.apply_contradiction_decision": (
        "resolve_declined_contradiction"
    ),
    "openkos.application.ingest_service.ingest_source": "resolve_watch_refusals",
}
"""Fully qualified write core -> the `queue_resolution` function it must call."""

NON_RESOLVING_CORES: Final = {
    "openkos.application.lifecycle.unmerge_core": (
        "undoes a merge; no advisor proposes an unmerge"
    ),
    "openkos.application.lifecycle.forget_core": (
        "erases; the forget sweep deletes every row that names the concept"
    ),
    "openkos.application.lifecycle.sync_tags_core": (
        "no queue kind proposes a tag sync"
    ),
}
"""Fully qualified `*_core` that resolves no row, with the reason."""

_NO_SECTION: Final = contextlib.nullcontext
"""The caller of every core holds the workspace lock; see the module docstring."""


def _decode(payload: str) -> dict[str, object]:
    try:
        decoded = json.loads(payload)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


@contextlib.contextmanager
def _queue(root: Path) -> Iterator[sqlite3.Connection | None]:
    """The workspace's queue connection, or `None` when there is nothing to
    resolve against (no `findings.db`, no queue table) or it cannot be read."""
    path = config.WorkspaceLayout(root).findings_db_path
    try:
        present = path.is_file()
    except OSError:
        present = False
    if not present:
        yield None
        return
    try:
        conn = derived.open_derived_connection(path)
    except (sqlite3.Error, OSError):
        yield None
        return
    try:
        try:
            exists = pq.queue_exists(conn)
        except sqlite3.Error:
            exists = False
        yield conn if exists else None
    finally:
        conn.close()


def _resolve_by_key(
    root: Path,
    kind: str,
    key_body: str,
    resolution: Callable[[pq.PendingItem], pq.Resolution],
    resolved_by: str,
) -> None:
    with _queue(root) as conn:
        if conn is None:
            return
        with contextlib.suppress(sqlite3.Error, OSError):
            pq.resolve_open_by_key(
                conn,
                pq.stored_decision_key(kind, key_body),
                resolution=resolution,
                resolved_by=resolved_by,
                commit_section=_NO_SECTION,
            )


def _as_proposed(matches: bool) -> pq.Resolution:
    return "as_proposed" if matches else "modified"


def resolve_merged(
    root: Path, *, survivor_id: str, absorbed_id: str, proposed_survivor: str
) -> None:
    """`merge`: the identity row over exactly the merged pair. `proposed_survivor`
    is the member the engine would keep (`lifecycle.ordered_merge_pair`), read
    BEFORE the absorbed file is removed. A larger group's row is left open: a
    pairwise merge does not perform what a many-member proposal asks."""
    _resolve_by_key(
        root,
        "identity",
        pq.identity_key((survivor_id, absorbed_id)),
        lambda _item: _as_proposed(proposed_survivor == survivor_id),
        "merge",
    )


def resolve_related(
    root: Path, *, source_id: str, target_id: str, rel_type: str
) -> None:
    """`relate`: the relation_type row over the unordered edge; `as_proposed`
    when the payload's effective direction and suggested type are what was
    written."""

    def resolution(item: pq.PendingItem) -> pq.Resolution:
        payload = _decode(item.payload)
        return _as_proposed(
            payload.get("effective_source_id") == source_id
            and payload.get("effective_target_id") == target_id
            and payload.get("suggested_type") == rel_type
        )

    _resolve_by_key(
        root,
        "relation_type",
        pq.relation_type_key(source_id, target_id),
        resolution,
        "relate",
    )


def resolve_volatility_set(root: Path, *, type_name: str, tier: str) -> None:
    """`set-volatility`: the volatility row for the type; `as_proposed` when the
    tier written is the suggested one."""
    _resolve_by_key(
        root,
        "volatility",
        pq.volatility_key(type_name),
        lambda item: _as_proposed(_decode(item.payload).get("suggested_tier") == tier),
        "set-volatility",
    )


def resolve_reconciled(root: Path, *, pair_ids: Sequence[str]) -> None:
    """`reconcile`: the typed-edge contradiction row over the pair. A merged-body
    row is a different proposal (its key carries the absorbed id) and stays
    open. The row proposes "reconcile this pair" with no direction, so any
    direction the human chose is `as_proposed`."""
    first, second = sorted(pair_ids)
    _resolve_by_key(
        root,
        "contradiction",
        pq.contradiction_key((first, second), None),
        lambda _item: "as_proposed",
        "reconcile",
    )


def resolve_declined_identity(root: Path, *, member_ids: Sequence[str]) -> None:
    """A keep-distinct ruling: the identity row over exactly these members."""
    _resolve_by_key(
        root,
        "identity",
        pq.identity_key(member_ids),
        lambda _item: "declined",
        "keep-distinct",
    )


def resolve_declined_contradiction(
    root: Path, *, pair_ids: tuple[str, str], merged_absorbed_id: str | None
) -> None:
    """A contradiction decline: the row its sidecar key addresses, so a
    typed-edge decline never closes a merged-body row over the same pair."""
    _resolve_by_key(
        root,
        "contradiction",
        pq.contradiction_key(pair_ids, merged_absorbed_id),
        lambda _item: "declined",
        "decline",
    )


def resolve_watch_refusals(root: Path, *, raw_digest: str) -> None:
    """`ingest`: every open `watch_refusal` row whose refused bytes are the
    bytes of the raw copy that just landed. Matched on the digest, not the
    name: a rename in the inbox lands the same bytes under a new Source."""
    with _queue(root) as conn:
        if conn is None:
            return
        with contextlib.suppress(sqlite3.Error, OSError):
            pq.resolve_open_by_input_digest(
                conn,
                "watch_refusal",
                raw_digest,
                resolution="as_proposed",
                resolved_by="ingest",
                commit_section=_NO_SECTION,
            )
