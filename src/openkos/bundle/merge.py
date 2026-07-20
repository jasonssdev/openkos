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
    invariant)."""

    merged_survivor: str
    ledger_entry: okf.MergeLedgerEntry


@dataclass(frozen=True)
class UnmergePlan:
    """Pure result of planning the reversal of the LIFO-tail `merged_from`
    entry: no bundle file has been written yet. `restored_*` are the EXACT
    pre-merge verbatim bytes a later unit writes back; `link_rewrites` are
    the recorded rewrites that same unit must reverse by bounded
    exact-substring substitution (never a blind replace-all)."""

    restored_survivor: str
    restored_absorbed: str
    restored_index: str
    restored_log: str
    link_rewrites: list[okf.LinkRewrite]
    entry: okf.MergeLedgerEntry


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
    link_rewrites: list[okf.LinkRewrite] | None = None,
) -> MergePlan:
    """Pure Phase-A planning: compute the merged survivor's full text and
    the new `merged_from` ledger entry, without writing anything.

    `survivor_text`/`absorbed_text` are each the FULL verbatim
    frontmatter+body of an existing bundle document; `index_text`/
    `log_text` are the current bundle's `index.md`/`log.md` verbatim
    contents, captured ONLY to be embedded in the ledger entry's
    `index_before`/`log_before` -- this layer never computes an updated
    catalog/log (that composition is a later unit's concern). `link_rewrites`
    defaults to `[]`; the actual bundle-wide link scan is U3.

    The new entry is appended to the survivor's EXISTING `merged_from` list
    (decoded from `survivor_text`'s own frontmatter), so the returned
    `merged_survivor` carries every prior entry plus this one, in LIFO
    order. Raises `ValueError` on a same/blank id (spec: Same-id or unknown
    id rejected) -- this layer has no notion of "existing on disk", so
    "unknown" here means a blank id; the CLI's `_resolve_concept_path` is
    what checks disk existence (a later unit).
    """
    _reject_same_or_blank(survivor_id, absorbed_id)

    survivor_metadata, survivor_body = okf.load_frontmatter(survivor_text)
    absorbed_metadata, absorbed_body = okf.load_frontmatter(absorbed_text)

    existing_entries = okf.decode_merged_from(survivor_metadata)
    _reject_already_merged(absorbed_id, existing_entries)

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
        schema=okf.MERGE_LEDGER_SCHEMA_V1,
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
    )

    merged_metadata[okf.MERGED_FROM_KEY] = okf.encode_merged_from(
        [*existing_entries, entry]
    )
    merged_survivor = okf.dump_frontmatter(merged_metadata, merged_body)

    return MergePlan(merged_survivor=merged_survivor, ledger_entry=entry)


def plan_unmerge(
    *,
    survivor_id: str,
    absorbed_id: str,
    survivor_text: str,
) -> UnmergePlan:
    """Pure planning: reverse ONLY the LIFO-tail `merged_from` entry on
    `survivor_text`, without writing anything (spec: Unmerge Achieves
    Round-Trip Parity).

    `absorbed_id` MUST equal the tail entry's `absorbed_id`, else this
    raises `ValueError` with no write -- reversing a non-tail entry is
    unsafe due to nested snapshots/overlapping rewrites (scenario:
    Absorbed-id is not the LIFO tail). A survivor with an empty
    `merged_from` ledger (nothing to unmerge for this pair) also raises
    `ValueError` (scenario: Unmerge of a non-merged pair).
    """
    _reject_same_or_blank(survivor_id, absorbed_id)

    metadata, _ = okf.load_frontmatter(survivor_text)
    entries = okf.decode_merged_from(metadata)
    if not entries:
        raise ValueError(f"{survivor_id!r} has no merged_from entries to unmerge")

    tail = entries[-1]
    if tail.absorbed_id != absorbed_id:
        raise ValueError(
            f"{absorbed_id!r} is not the LIFO tail of {survivor_id!r}'s merged_from "
            f"ledger (tail is {tail.absorbed_id!r}); unmerge refused"
        )

    return UnmergePlan(
        restored_survivor=tail.survivor_before,
        restored_absorbed=tail.absorbed_snapshot,
        restored_index=tail.index_before,
        restored_log=tail.log_before,
        link_rewrites=list(tail.link_rewrites),
        entry=tail,
    )
