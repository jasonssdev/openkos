"""Third-party inbound-relation rewrite/reverse primitives for
`merge`/`unmerge` (design D3; spec: Reversible Typed-Relation Rewiring,
Reversibility Ledger).

`find_inbound_relation_rewrites` is the pure Phase-A SCAN: given every
bundle file's text already in memory, it finds every file whose
`relations:` targets the absorbed id and records a whole-file
`okf.RelationRewrite(file, snapshot)` -- the file's FULL verbatim bytes
BEFORE this merge -- for each. Unlike `bundle/links.py`'s `LinkRewrite`
(reversed by an exact character offset), a `relations:` retarget/drop/
dedupe has no stable disambiguating position analogous to a link
occurrence, so reversal here is always an ABSOLUTE whole-file overwrite
(design D1/D3; D4's overlapping-LIFO proof relies on this). No file is
read or written here, and no CLI wiring happens in this module -- that is
PR3's concern.

`apply_relation_rewrites` is pure text-in/text-out: retargets a single
file's OWN `relations:` (absorbed id -> survivor id) and dedupes a
resulting collision, then re-emits via `okf.encode_relations`. This is a
NARROWER, single-file variant of `okf.merge_relations` (which merges TWO
objects' outbound edges and treats "self-loop" as "this object's own edge
now points at itself, because this object IS becoming the survivor") --
here there is only ONE object's existing edges (a GENUINE third-party
`file`'s own `relations:`, `file != survivor_id` and `file != absorbed_id`
by construction -- `find_inbound_relation_rewrites` excludes both), and a
genuine third-party retarget can NEVER produce a self-loop: `file ->
absorbed_id` always becomes `file -> survivor_id`, which can never equal
`file`'s own concept-id. Reusing `okf.merge_relations` unmodified would be
semantically wrong here regardless, since its `was_retargeted`
self-loop-drop rule unconditionally drops every retargeted entry whose new
target is `survivor_id` -- correct only for the OUTBOUND
(owner-becomes-survivor) case. A PRE-EXISTING, merge-UNRELATED `file ->
file` self-loop already on the third-party file (reachable: the codec does
not reject self-loops; only the `relate` CLI does) is left COMPLETELY
untouched -- it has nothing to do with this merge, mirroring
`okf.merge_relations`'s identical preservation of a pre-existing
survivor-side self-loop (correction batch, finding 1). A file with no
recorded rewrite is returned unchanged (no-op).

`reverse_relation_rewrites` is the exact inverse `unmerge` needs: it
returns the recorded snapshot verbatim, ignoring the passed-in `text`
entirely -- no offset math, no partial patch, matching this module's
byte-exact whole-file restore contract.
"""

from collections.abc import Mapping

from openkos.model import okf


def find_inbound_relation_rewrites(
    files: Mapping[str, str], *, absorbed_id: str, survivor_id: str
) -> list[okf.RelationRewrite]:
    """Pure Phase-A scan: find every GENUINE third-party file in `files`
    (bundle-relative path -> full text already in memory) whose
    `relations:` contains an entry targeting `absorbed_id`, and record a
    whole-file `RelationRewrite` -- the file's ORIGINAL, pre-merge text --
    for each (spec: "Third-party inbound relations retarget to the
    survivor"; design D3).

    The survivor itself (`file == survivor_id`) and the absorbed file
    itself (`file == absorbed_id`) are EXCLUDED from the scan regardless of
    what their own `relations:` target (correction batch, finding 1): the
    survivor's own outbound relations are the exclusive concern of
    `build_merged_document`/`okf.merge_relations`, and the absorbed file is
    deleted by this merge -- this trio is for genuine third parties only,
    never either merge participant.

    A file whose frontmatter fails to parse, or whose `relations:` shape
    itself is malformed (fails `okf.decode_relations`) -- a hand-edited,
    unrelated file -- is SKIPPED rather than surfaced as a fail-closed
    refusal, mirroring the deleted `find_relation_conflicts`'s identical
    broad `except Exception` around the same `load_frontmatter`/
    `decode_relations` calls (PR1's `bundle/merge.py`; same
    "a concurrent/hand edit can corrupt frontmatter mid-scan" rationale).
    `files` iteration order determines result order.
    """
    rewrites: list[okf.RelationRewrite] = []
    for file, text in files.items():
        file_id = file.removesuffix(".md")
        if file_id in (survivor_id, absorbed_id):
            continue  # neither merge participant is this scan's concern
        file_relations: list[okf.Relation] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
            file_relations = okf.decode_relations(metadata)
        except Exception:  # broad: an unrelated file's corrupt frontmatter
            # or relations shape must never crash or block an otherwise-
            # unrelated merge scan (mirrors the deleted
            # `find_relation_conflicts`'s identical broad-except skip).
            file_relations = None
        if file_relations is None:
            continue
        if any(relation.target == absorbed_id for relation in file_relations):
            rewrites.append(okf.RelationRewrite(file=file, snapshot=text))
    return rewrites


def apply_relation_rewrites(
    text: str,
    *,
    file: str,
    survivor_id: str,
    absorbed_id: str,
    rewrites: list[okf.RelationRewrite],
) -> str:
    """Pure: retarget `file`'s own `relations:` (`absorbed_id` ->
    `survivor_id`) and dedupe a resulting collision, then re-emit via
    `okf.encode_relations` (design D3). A no-op (returns `text` unchanged)
    unless `file` appears in `rewrites` -- a file not recorded by
    `find_inbound_relation_rewrites` had nothing to retarget.

    A genuine third-party retarget can NEVER produce a self-loop (`file ->
    absorbed_id` always becomes `file -> survivor_id`, which can never
    equal `file`'s own concept-id), so every OTHER relation entry on `file`
    -- including any PRE-EXISTING, merge-unrelated `file -> file` self-loop
    -- is re-emitted untouched (correction batch, finding 1; mirrors
    `okf.merge_relations`'s identical preservation of a pre-existing
    survivor-side self-loop). A retargeted entry that duplicates one
    already accepted (by `(target, type)` equality) is a COLLISION --
    dropped, one entry remains. An empty result omits the `relations:` key
    entirely, preserving "absent relations key is valid".
    """
    if not any(rewrite.file == file for rewrite in rewrites):
        return text

    metadata, body = okf.load_frontmatter(text)

    merged: list[okf.Relation] = []
    for relation in okf.decode_relations(metadata):
        retargeted = (
            okf.Relation(target=survivor_id, type=relation.type)
            if relation.target == absorbed_id
            else relation
        )
        if retargeted in merged:
            continue  # resulting collision: dedupe, keep the first
        merged.append(retargeted)

    if merged:
        metadata[okf.RELATIONS_KEY] = okf.encode_relations(merged)
    else:
        metadata.pop(okf.RELATIONS_KEY, None)
    return okf.dump_frontmatter(metadata, body)


def reverse_relation_rewrites(
    text: str, *, file: str, rewrites: list[okf.RelationRewrite]
) -> str:
    """Pure inverse of `apply_relation_rewrites`: restore `file`'s recorded
    whole-file snapshot verbatim -- an ABSOLUTE overwrite, never offset
    math (design D1/D3), unlike `bundle/links.py::reverse_link_rewrites`.
    The passed-in `text` is otherwise ignored; only the matching recorded
    `rewrites` entry's `.snapshot` is returned. A `file` with no matching
    recorded rewrite returns `text` unchanged (no-op, mirrors `links.py`'s
    ignore-other-files behavior).

    Raises `ValueError` if MORE THAN ONE rewrite is recorded for the same
    `file`: `find_inbound_relation_rewrites` records at most one entry per
    file per scan, so more than one within a single ledger entry's
    `relation_rewrites` list is a construction bug, not a legitimate
    multi-snapshot case (unlike `link_rewrites`, which legitimately holds
    multiple entries per file ACROSS DIFFERENT merges -- but
    `relation_rewrites` is scoped to one ledger entry, i.e. one merge).
    """
    matches = [rewrite for rewrite in rewrites if rewrite.file == file]
    if not matches:
        return text
    if len(matches) > 1:
        raise ValueError(
            f"more than one relation_rewrites snapshot recorded for {file!r}"
        )
    return matches[0].snapshot
