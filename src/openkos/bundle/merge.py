"""Pure merge/unmerge planning: computes what a merge or unmerge would
produce, given in-memory doc content.

No bundle file is read or written here -- callers (a later unit) own
reading `survivor`/`absorbed`/`index.md`/`log.md` off disk, invoking the
confirm gate, and writing the plan's output. Inbound-link SCANNING is also
a later unit (U3) -- `link_rewrites` may be injected empty here. This
module composes `model.okf`'s `build_merged_document` and `merged_from`
ledger primitives into the full pre-merge snapshot set ADR-0002 requires,
and the LIFO-tail-enforced reversal `unmerge` needs.
"""

from dataclasses import dataclass

from openkos.model import okf


@dataclass(frozen=True)
class MergePlan:
    """Pure result of planning a merge of `absorbed_id` into a survivor: no
    bundle file has been written yet (Phase A). `merged_survivor` is the
    FULL frontmatter+body text a later unit writes verbatim (survivor
    before deleting the absorbed file, per design's Phase B ordering
    invariant) -- it carries NO `merged_from` key at all (durable-derived-
    state slice 1a): the ledger now lives in a sidecar
    (`bundle/ledger.py`), never the survivor's own frontmatter.
    `ledger_entries` is the FULL new sidecar content -- every entry the
    caller passed in via `existing_entries` plus this merge's own
    `ledger_entry`, in LIFO (append) order -- for a later unit to write to
    that sidecar."""

    merged_survivor: str
    ledger_entry: okf.MergeLedgerEntry
    ledger_entries: list[okf.MergeLedgerEntry]


@dataclass(frozen=True)
class UnmergePlan:
    """Pure result of planning the reversal of the LIFO-tail `merged_from`
    entry: no bundle file has been written yet. `restored_*` are the EXACT
    pre-merge verbatim bytes a later unit writes back; `link_rewrites` are
    the recorded rewrites that same unit must reverse by bounded
    exact-substring substitution (never a blind replace-all);
    `relation_rewrites` (design D1, v2) are the recorded third-party
    whole-file snapshots that same unit must reverse by ABSOLUTE overwrite
    (`bundle/relations.py::reverse_relation_rewrites`) -- `[]` for a v1
    (pre-slice-2a) ledger entry; `provenance_rewrites` (v3,
    rewrite-provenance-on-merge) are the analogous third-party whole-file
    snapshots for `provenance:` retargets, reversed by
    `bundle/provenance.py::reverse_provenance_rewrites` -- `[]` for a v1 or
    v2 ledger entry."""

    restored_survivor: str
    restored_absorbed: str
    restored_index: str
    restored_log: str
    link_rewrites: list[okf.LinkRewrite]
    relation_rewrites: list[okf.RelationRewrite]
    provenance_rewrites: list[okf.ProvenanceRewrite]
    entry: okf.MergeLedgerEntry
    remaining_entries: list[okf.MergeLedgerEntry]


def _reject_same_or_blank(survivor_id: str, absorbed_id: str) -> None:
    """Guard shared by `plan_merge`/`plan_unmerge`: both ids must be
    non-blank and distinct (spec: Same-id or unknown id rejected)."""
    if not survivor_id.strip() or not absorbed_id.strip():
        raise ValueError("survivor_id and absorbed_id must be non-empty")
    if survivor_id == absorbed_id:
        raise ValueError(
            f"survivor_id and absorbed_id must be distinct, both were {survivor_id!r}"
        )


def _reject_already_merged(
    absorbed_id: str, existing_entries: list[okf.MergeLedgerEntry]
) -> None:
    """Guard for `plan_merge`: `absorbed_id` must not already appear in the
    survivor's existing `merged_from` entries. Two same-id entries would be
    ambiguous for `plan_unmerge`'s LIFO-tail (id-keyed) targeting -- it could
    never tell which one a caller means."""
    if any(entry.absorbed_id == absorbed_id for entry in existing_entries):
        raise ValueError(
            f"absorbed_id {absorbed_id!r} is already merged into this survivor"
        )


def plan_merge(
    *,
    survivor_id: str,
    absorbed_id: str,
    survivor_text: str,
    absorbed_text: str,
    index_text: str,
    log_text: str,
    merged_at: str,
    existing_entries: list[okf.MergeLedgerEntry] | None = None,
    link_rewrites: list[okf.LinkRewrite] | None = None,
    relation_rewrites: list[okf.RelationRewrite] | None = None,
    provenance_rewrites: list[okf.ProvenanceRewrite] | None = None,
) -> MergePlan:
    """Pure Phase-A planning: compute the merged survivor's full text and
    the new `merged_from` ledger entry, without writing anything.

    `survivor_text`/`absorbed_text` are each the FULL verbatim
    frontmatter+body of an existing bundle document; `index_text`/
    `log_text` are the current bundle's `index.md`/`log.md` verbatim
    contents, captured ONLY to be embedded in the ledger entry's
    `index_before`/`log_before` -- this layer never computes an updated
    catalog/log (that composition is a later unit's concern). `link_rewrites`
    defaults to `[]`; the actual bundle-wide link scan is a later unit.
    `relation_rewrites` (design D1, v2) similarly defaults to `[]`; the
    actual third-party inbound scan (`bundle/relations.py`) and CLI wiring
    are a later unit's concern. `provenance_rewrites` (v3,
    rewrite-provenance-on-merge) likewise defaults to `[]`; the actual
    third-party inbound scan (`bundle/provenance.py`) and CLI wiring are
    PR2's concern -- this layer only carries whatever the caller injects
    into the new ledger entry, and ALWAYS writes `MERGE_LEDGER_SCHEMA_V3`
    (the reader still accepts v1 and v2 entries already on disk from before
    this merge).

    `existing_entries` (durable-derived-state slice 1a) is the survivor's
    CURRENT sidecar content, read by the caller via `bundle.ledger.
    read_entries` -- this layer no longer decodes it from `survivor_text`'s
    own frontmatter, since the ledger no longer lives there. Defaults to
    `[]` (a survivor merging for the first time). The new entry is appended
    to it, in LIFO order, and returned as `MergePlan.ledger_entries` for a
    later unit to write to the sidecar; `merged_survivor` itself carries no
    `merged_from` key at all. Raises `ValueError` on a same/blank id (spec:
    Same-id or unknown id rejected) -- this layer has no notion of
    "existing on disk", so "unknown" here means a blank id; the CLI's
    `_resolve_concept_path` is what checks disk existence (a later unit).
    """
    _reject_same_or_blank(survivor_id, absorbed_id)

    survivor_metadata, survivor_body = okf.load_frontmatter(survivor_text)
    absorbed_metadata, absorbed_body = okf.load_frontmatter(absorbed_text)

    existing = list(existing_entries) if existing_entries is not None else []
    _reject_already_merged(absorbed_id, existing)

    merged_metadata, merged_body = okf.build_merged_document(
        survivor_metadata,
        survivor_body,
        absorbed_metadata,
        absorbed_body,
        absorbed_id,
        survivor_id,
    )

    sensitivity_before = survivor_metadata.get("sensitivity")
    entry = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at=merged_at,
        absorbed_id=absorbed_id,
        absorbed_snapshot=absorbed_text,
        survivor_before=survivor_text,
        index_before=index_text,
        log_before=log_text,
        link_rewrites=list(link_rewrites) if link_rewrites is not None else [],
        sensitivity_before=""
        if sensitivity_before is None
        else str(sensitivity_before),
        sensitivity_after=str(merged_metadata.get("sensitivity")),
        relation_rewrites=list(relation_rewrites)
        if relation_rewrites is not None
        else [],
        provenance_rewrites=list(provenance_rewrites)
        if provenance_rewrites is not None
        else [],
    )

    merged_survivor = okf.dump_frontmatter(merged_metadata, merged_body)
    ledger_entries = [*existing, entry]

    return MergePlan(
        merged_survivor=merged_survivor,
        ledger_entry=entry,
        ledger_entries=ledger_entries,
    )


def plan_unmerge(
    *,
    survivor_id: str,
    absorbed_id: str,
    entries: list[okf.MergeLedgerEntry],
) -> UnmergePlan:
    """Pure planning: reverse ONLY the LIFO-tail entry of `entries`, without
    writing anything (spec: Unmerge Achieves Round-Trip Parity).

    `entries` (durable-derived-state slice 1a) is the survivor's CURRENT
    sidecar content, read by the caller via `bundle.ledger.read_entries` --
    this layer no longer decodes it from a `survivor_text` frontmatter key,
    since the ledger no longer lives there; restoring `restored_survivor`
    from `tail.survivor_before` needs nothing else off the survivor's
    current on-disk bytes at all.

    `absorbed_id` MUST equal the tail entry's `absorbed_id`, else this
    raises `ValueError` with no write -- reversing a non-tail entry is
    unsafe due to nested snapshots/overlapping rewrites (scenario:
    Absorbed-id is not the LIFO tail). That refusal is not a dead end
    (issue #562): when `absorbed_id` IS present deeper in `entries`, the
    error lists the FULL LIFO unwind sequence -- every id from the tail
    down to and including the requested one, in execution order (the exact
    slice `plan_unwind_sequence` computes) -- and names
    `openkos unmerge <survivor> --to <absorbed>` as the one-command
    alternative. An `absorbed_id` present nowhere in `entries` keeps a
    plain "was never merged into this survivor" refusal, with no unwind
    hint, since there is nothing to unwind to. An empty `entries` list
    (nothing to unmerge for this pair) also raises `ValueError` (scenario:
    Unmerge of a non-merged pair). `remaining_entries` is `entries` with
    the tail popped -- the sidecar's new content once a later unit writes
    it.
    """
    _reject_same_or_blank(survivor_id, absorbed_id)

    if not entries:
        raise ValueError(f"{survivor_id!r} has no merged_from entries to unmerge")

    tail = entries[-1]
    if tail.absorbed_id != absorbed_id:
        recorded_ids = [entry.absorbed_id for entry in entries]
        if absorbed_id not in recorded_ids:
            raise ValueError(
                f"{absorbed_id!r} was never merged into {survivor_id!r} -- no "
                f"matching entry in its merged_from ledger (tail is "
                f"{tail.absorbed_id!r}); unmerge refused"
            )
        # `_reject_already_merged` keeps absorbed ids unique while their
        # entries coexist, so the LAST (nearest-to-tail) occurrence is THE
        # occurrence; `rindex` stays correct even against a hand-authored
        # ledger carrying duplicates by choosing the shortest unwind.
        target_index = len(recorded_ids) - 1 - recorded_ids[::-1].index(absorbed_id)
        sequence = ", ".join(
            repr(entry.absorbed_id) for entry in reversed(entries[target_index:])
        )
        raise ValueError(
            f"{absorbed_id!r} is not the LIFO tail of {survivor_id!r}'s merged_from "
            f"ledger; unwinding to it requires reversing, in order: {sequence}. "
            f"Run `openkos unmerge {survivor_id} --to {absorbed_id}` to do this in "
            f"one command, or unmerge each pair in that order"
        )

    return UnmergePlan(
        restored_survivor=tail.survivor_before,
        restored_absorbed=tail.absorbed_snapshot,
        restored_index=tail.index_before,
        restored_log=tail.log_before,
        link_rewrites=list(tail.link_rewrites),
        relation_rewrites=list(tail.relation_rewrites),
        provenance_rewrites=list(tail.provenance_rewrites),
        entry=tail,
        remaining_entries=list(entries[:-1]),
    )


def plan_unwind_sequence(
    *,
    survivor_id: str,
    to_absorbed_id: str,
    entries: list[okf.MergeLedgerEntry],
) -> list[okf.MergeLedgerEntry]:
    """Pure planning for `unmerge <survivor> --to <absorbed-id>` (issue
    #562): the tail-to-target slice of `entries`, returned in EXECUTION
    order -- tail (newest merge) first, down to AND INCLUDING the entry
    whose `absorbed_id` equals `to_absorbed_id`.

    This computes only the ORDER of single-step unmerges; it deliberately
    composes nothing in memory. The CLI's `--to` loop re-runs the full
    single-step machinery per returned entry (Phase A recomputed from
    CURRENT disk state each time, fail-closed drift/collision checks
    included), because every intermediate state after a completed
    single-step unmerge is a consistent bundle -- so `plan_unmerge`'s
    LIFO-tail safety argument (nested snapshots/overlapping rewrites)
    holds unchanged at every step.

    Raises `ValueError`, with no write anywhere, on an empty `entries`
    list (nothing to unwind -- mirrors `plan_unmerge`'s "no merged_from
    entries" refusal) or a `to_absorbed_id` present nowhere in `entries`
    (same "was never merged into this survivor" shape `plan_unmerge`
    raises for an unknown id). Same/blank ids are rejected by the shared
    `_reject_same_or_blank` guard `plan_merge`/`plan_unmerge` use. Against
    a hand-authored ledger carrying duplicate absorbed ids (impossible via
    `plan_merge`, whose `_reject_already_merged` keeps coexisting ids
    unique), the LAST (nearest-to-tail) occurrence wins -- the shortest
    unwind, matching `plan_unmerge`'s non-tail error."""
    _reject_same_or_blank(survivor_id, to_absorbed_id)

    if not entries:
        raise ValueError(f"{survivor_id!r} has no merged_from entries to unmerge")

    for index in range(len(entries) - 1, -1, -1):
        if entries[index].absorbed_id == to_absorbed_id:
            return list(reversed(entries[index:]))

    raise ValueError(
        f"{to_absorbed_id!r} was never merged into {survivor_id!r} -- no matching "
        f"entry in its merged_from ledger; unmerge refused"
    )
