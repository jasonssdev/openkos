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

from collections.abc import Mapping
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


@dataclass(frozen=True)
class RelationConflict:
    """One typed-relation edge a merge would orphan, since this slice has
    no edge-rewiring yet (design: MERGE GUARD -- REFUSE, fail-closed).
    `source_id` is the bundle-relative concept-id whose `relations:` entry
    is responsible: the absorbed object itself for an OUTBOUND conflict
    (its own edge would be stripped), or another bundle file (which may be
    the survivor) for an INBOUND conflict (its edge would dangle, pointing
    at a concept-id that no longer exists). `relation` is the specific
    `okf.Relation` entry causing the conflict."""

    source_id: str
    relation: okf.Relation


def find_relation_conflicts(
    absorbed_id: str, files: Mapping[str, str], absorbed_text: str
) -> list[RelationConflict]:
    """Pure Phase-A scan: detect every typed-relation edge a merge of
    `absorbed_id` would orphan (spec: Non-Silent Guard For Edge-Bearing
    Merge). Full reversible rewiring of typed edges through merge/unmerge
    is OUT OF SCOPE for this slice (Non-Goal -- deferred); this function
    only DETECTS the conflict so the caller can refuse before any write.

    Two directions are checked:

    OUTBOUND -- `absorbed_text`'s OWN `relations:` entries (decoded from
    its frontmatter): merging strips the absorbed object with no
    rewiring, so every entry there is a conflict.

    INBOUND -- every file in `files` (bundle-relative path, `.md` suffix
    included -- mirrors `merge`'s existing `other_files` scan shape --
    mapped to its full text already in memory) whose `relations:` contains
    an entry whose `target` equals `absorbed_id`: that edge would dangle,
    pointing at a concept-id that no longer exists after the merge. The
    caller is expected to include the SURVIVOR's own text in `files` too
    (not just files third to the merge) -- an edge FROM the survivor TO
    the absorbed object dangles exactly the same way as one from any other
    file.

    Returns `[]` when neither direction has a hit (spec: "Merge of an
    object with no typed relations proceeds unaffected"). A malformed
    `relations:` shape on `absorbed_text` itself is NOT this function's
    concern: `okf.decode_relations` raises `ValueError` on one, which
    propagates to the caller unhandled -- fail-closed, same as any other
    Phase-A precondition failure on the two concepts being merged directly.

    The INBOUND scan over `files`, however, parses frontmatter for every
    OTHER bundle file -- files this merge otherwise never touches. A
    malformed or unparseable frontmatter block there (e.g. a hand-edited,
    unrelated file with a broken YAML scalar) must not crash or block an
    otherwise-unrelated merge: mirrors `lint.py::collect_docs`'s identical
    broad `except Exception` around the same `okf.load_frontmatter` call,
    for the same "a concurrent/hand edit can corrupt frontmatter mid-scan"
    rationale. That file is simply skipped (contributes no inbound
    conflict) rather than surfaced as a fail-closed refusal -- a malformed
    unrelated file is a separate §9 concern for `check_conformance`/`lint`,
    not this guard's job.
    """
    conflicts: list[RelationConflict] = []

    absorbed_metadata, _ = okf.load_frontmatter(absorbed_text)
    for relation in okf.decode_relations(absorbed_metadata):
        conflicts.append(RelationConflict(source_id=absorbed_id, relation=relation))

    for file, text in files.items():
        relations: list[okf.Relation] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
            relations = okf.decode_relations(metadata)
        except Exception:  # broad: a concurrent/hand edit can corrupt
            # frontmatter mid-scan in an unrelated file; skip it rather
            # than crash or fail-close an otherwise-unrelated merge.
            relations = None
        if relations is None:
            continue
        source_id = file.removesuffix(".md")
        for relation in relations:
            if relation.target == absorbed_id:
                conflicts.append(
                    RelationConflict(source_id=source_id, relation=relation)
                )

    return conflicts


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
        survivor_metadata, survivor_body, absorbed_metadata, absorbed_body, absorbed_id
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
