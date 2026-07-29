"""Pure orphan-closure helper for `forget --scope source` (design: "New
`src/openkos/bundle/provenance.py` (canonical layer, MUST NOT import
`openkos.graph` -- same rule `references.py`/`links.py` follow)"; spec:
"Provenance Descendant Resolution"), AND the third-party inbound-provenance
rewrite/reverse primitives for `merge`/`unmerge` (design:
rewrite-provenance-on-merge; spec: "Reversible Inbound-Provenance
Rewiring").

`find_provenance_descendants` computes the orphan-after-delete purge SET
starting from a root set of concept-ids: a candidate concept `C` (not
already in the purge set) joins the purge set iff its `provenance`
frontmatter list is NON-EMPTY and every one of its entries is ALREADY in the
purge set (the orphan-after-delete subset invariant). This is a reverse-edge
closure -- `provenance` points from a derived concept back to the source(s)
it came from -- so resolution walks that reverse edge outward from the
root(s), pulling in any concept whose ENTIRE provenance is about to be
deleted, and leaving alone any concept that still has at least one surviving
source.

`find_inbound_provenance_rewrites` is the pure Phase-A SCAN: given every
bundle file's text already in memory, it finds every file whose
`provenance:` targets the absorbed id and records a whole-file
`okf.ProvenanceRewrite(file, snapshot)` -- the file's FULL verbatim bytes
BEFORE this merge -- for each. Shaped exactly like
`bundle/relations.py::find_inbound_relation_rewrites`: `provenance:` is a
YAML list field with no stable disambiguating position analogous to a link
occurrence, so reversal here is always an ABSOLUTE whole-file overwrite,
never offset math. This scan is deliberately NOT gated on the absorbed
concept's `type`: `query --save` can file ANY cited concept id, Source or
not, as another object's `provenance`, so a non-Source absorbed concept can
orphan third-party provenance too.

`apply_provenance_rewrites` is pure text-in/text-out: retargets a single
file's OWN `provenance:` (absorbed id -> survivor id) via retarget-then-
dedupe, first-occurrence-wins, keyed on the normalized id (mirrors
`build_merged_document`'s union rule and
`bundle/relations.py::apply_relation_rewrites`'s shape). A list already
naming BOTH the survivor and the absorbed id collapses to one survivor
entry at the EARLIER of the two positions; every other entry keeps its
relative order. The result is provably never empty (`find_*` only records
files holding >= 1 absorbed entry), so unlike `apply_relation_rewrites`
there is no pop-the-key branch -- `provenance:` always stays present.

`reverse_provenance_rewrites` is the exact inverse `unmerge` needs: it
returns the recorded snapshot verbatim, but ONLY after confirming the
passed-in `text` (the file's CURRENT on-disk bytes) still matches what THIS
merge actually wrote there. Unlike `reverse_relation_rewrites` (which only
needs `link_rewrites`), this recomputes the expected post-merge content by
applying, IN `merge_core`'s exact chain order, `bundle_links.
apply_link_rewrites` -> `relations.apply_relation_rewrites` ->
`apply_provenance_rewrites` forward from the recorded pre-merge `snapshot`
-- provenance is the THIRD pass in that chain, so its drift check must
account for both prior passes to avoid a false positive on a file touched
by all three.

Like `bundle/references.py`/`bundle/relations.py`, this module is
canonical-layer: it MUST NOT import `openkos.graph`, is pure (no I/O), and
takes the whole-bundle `files` snapshot the caller already has in memory.
"""

from collections.abc import Collection, Mapping

from openkos.bundle import links as bundle_links
from openkos.bundle import relations as bundle_relations
from openkos.model import okf


def _normalize_id(raw_id: str) -> str:
    """Strip a trailing `.md` suffix so a `provenance` entry, a `files` key,
    and a `root_ids` entry all compare on the same canonical id, regardless
    of whether the caller wrote it with or without the suffix."""
    return raw_id.removesuffix(".md")


def provenance_closure(
    provenance_by_id: Mapping[str, frozenset[str]], *, root_ids: Collection[str]
) -> list[str]:
    """Return the sorted fixpoint closure set (roots + descendants) over an
    already-parsed `id -> frozenset(provenance ids)` map -- the extracted
    core `find_provenance_descendants` and `resolve_source_raises` both
    build on (design D1; moved verbatim from the prior inline loop below).

    Algorithm: seed `purge` with the normalized `root_ids`, then iterate to
    a fixed point: a candidate `C` not yet in `purge` joins iff its
    provenance set is NON-EMPTY and a subset of `purge`.

    THE CRITICAL over-deletion barrier is that non-empty guard. An empty
    (or absent) `provenance` is vacuously a subset of ANY set, including
    `purge` -- without explicitly requiring `provenance` to be non-empty,
    every concept with no recorded provenance would satisfy the subset test
    on the very first iteration, and the "cascade" would swallow the entire
    bundle instead of just the concepts genuinely orphaned by the delete.

    Termination: `purge` only ever grows, and it is bounded by the finite
    universe of `root_ids | provenance_by_id.keys()`, so the fixpoint loop
    always halts -- including on a provenance cycle disjoint from any root
    (those concepts simply never satisfy the subset test and are never
    added) and on self-referential provenance (a concept naming itself can
    never be a subset of `purge` before it is already a member of it).

    Determinism: the returned list is `sorted()`; the fixpoint set itself is
    order-independent of both `provenance_by_id` iteration order and
    `root_ids` order.
    """
    purge = {_normalize_id(root_id) for root_id in root_ids}

    changed = True
    while changed:
        changed = False
        for concept_id, entry_provenance in provenance_by_id.items():
            if concept_id in purge:
                continue
            if entry_provenance and entry_provenance <= purge:
                purge.add(concept_id)
                changed = True

    return sorted(purge)


def _parse_provenance_by_id(files: Mapping[str, str]) -> dict[str, frozenset[str]]:
    """Parse every file's `provenance` frontmatter list ONCE into
    `id -> frozenset(...)` (canonical, `.md`-stripped ids on both sides); a
    file whose frontmatter fails to parse, or whose `provenance` is not a
    list, is SKIPPED -- it can then never join a `provenance_closure` purge
    set, which is fail-safe against over-deletion (mirroring
    `bundle/references.py`'s "malformed file is skipped rather than
    surfaced" contract, here applied to preservation instead of
    detection)."""
    provenance_by_id: dict[str, frozenset[str]] = {}
    for path, text in files.items():
        concept_id = _normalize_id(path)
        metadata: dict[str, object] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: malformed frontmatter is preserved
            # rather than swallowed into the purge set, see
            # `provenance_closure`'s "critical over-deletion barrier"
            metadata = None
        if metadata is None:
            continue
        raw_provenance = metadata.get("provenance")
        if not isinstance(raw_provenance, list):
            continue
        provenance_by_id[concept_id] = frozenset(
            _normalize_id(str(entry)) for entry in raw_provenance
        )
    return provenance_by_id


def find_provenance_descendants(
    files: Mapping[str, str], *, root_ids: Collection[str]
) -> list[str]:
    """Return the sorted orphan-closure purge set (roots + descendants).

    Parse every file's `provenance` frontmatter list once
    (`_parse_provenance_by_id`), then delegate the fixpoint walk to
    `provenance_closure` -- see its docstring for the algorithm, the
    over-deletion barrier, termination, and determinism guarantees, all
    unchanged by this extraction."""
    provenance_by_id = _parse_provenance_by_id(files)
    return provenance_closure(provenance_by_id, root_ids=root_ids)


def resolve_source_raises(
    files: Mapping[str, str], *, source_id: str, level: str
) -> list[okf.DescendantRaise]:
    """Pure per-Source raise resolver, extracted verbatim from
    `set_sensitivity_cmd`'s Phase-A inline scan (design D1; formerly
    `main.py:3339-3411`'s `_DescendantRaise` staging loop).

    `files` is a whole-bundle snapshot (bundle-relative path, INCLUDING the
    `.md` suffix, -> full file text); `source_id` is the canonical
    (`.md`-stripped) id of the Source concept whose provenance descendants
    are being raised; `level` is the Source's own new (already-validated)
    sensitivity level.

    Resolves `source_id`'s provenance closure via `find_provenance_descendants`
    (the conservative non-empty-subset rule, unchanged), excludes `source_id`
    itself (a root is never its own descendant -- design D6), then for each
    remaining closure member computes `okf.combine_sensitivity(current,
    level)`: a member whose combined result equals its current value is
    NOT staged (this is a raise-only propagator, never a lowering or a
    same-rank rewrite). The result is `sorted()` by `concept_id` (design
    D7), each entry's `content` a full `okf.dump_frontmatter` re-render of
    the member's ORIGINAL metadata with only `sensitivity` replaced.
    """
    descendant_ids = [
        member
        for member in find_provenance_descendants(files, root_ids={source_id})
        if member != _normalize_id(source_id)
    ]

    raises: list[okf.DescendantRaise] = []
    for member in descendant_ids:
        member_text = files[f"{member}.md"]
        member_metadata, member_body = okf.load_frontmatter(member_text)
        member_current = member_metadata.get("sensitivity")
        new_level = okf.combine_sensitivity(member_current, level)
        if new_level == member_current:
            continue
        member_metadata["sensitivity"] = new_level
        raises.append(
            okf.DescendantRaise(
                concept_id=member,
                current=member_current,
                new_level=new_level,
                content=okf.dump_frontmatter(member_metadata, member_body),
            )
        )
    return raises


def find_unresolvable_provenance(
    files: Mapping[str, str], *, known_extra_ids: Collection[str] = ()
) -> list[tuple[str, str]]:
    """Pure bundle-wide unresolvable-provenance scan, extracted verbatim
    from `set_sensitivity_cmd`'s Phase-A inline warning loop (design D1/D7;
    formerly `main.py:3369-3386`).

    Scans every file's parsed `provenance` list for an entry naming an id
    with no corresponding file in `files` and no membership in
    `known_extra_ids` -- returning `(citing_id, unresolved_entry_id)` for
    each. `known_extra_ids` exists SOLELY to let a caller reproduce
    `set_sensitivity_cmd`'s exact historical pairing: passing a
    target-EXCLUDING snapshot together with `known_extra_ids={target_id}`
    (design D7) -- it is not a general-purpose escape hatch.

    Ordering is pinned byte-identical to the original inline loop: `files`
    iteration order, then each file's `provenance:` list order, with NO
    dedupe -- one tuple per occurrence, including a duplicate entry. A raw
    resource-shaped entry (e.g. `raw/<resource>`) never normalizes to a
    bundle id and therefore always reports (design D6/D8) -- this is
    reporting only, never a write gate; `find_provenance_descendants`'s own
    non-empty-subset rule already keeps a citing concept fail-closed
    excluded from any purge/raise set."""
    known_ids = {_normalize_id(extra_id) for extra_id in known_extra_ids} | {
        _normalize_id(path) for path in files
    }

    unresolvable: list[tuple[str, str]] = []
    for path, text in files.items():
        member_id = _normalize_id(path)
        metadata: dict[str, object] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: malformed frontmatter is skipped rather
            # than surfaced, mirroring `_parse_provenance_by_id`
            metadata = None
        if metadata is None:
            continue
        raw_provenance = metadata.get("provenance")
        if not isinstance(raw_provenance, list):
            continue
        for entry in raw_provenance:
            entry_id = _normalize_id(str(entry))
            if entry_id not in known_ids:
                unresolvable.append((member_id, entry_id))
    return unresolvable


def find_inbound_provenance_rewrites(
    files: Mapping[str, str], *, absorbed_id: str, survivor_id: str
) -> list[okf.ProvenanceRewrite]:
    """Pure Phase-A scan: find every GENUINE third-party file in `files`
    (bundle-relative path -> full text already in memory) whose
    `provenance:` contains an entry targeting `absorbed_id`, and record a
    whole-file `okf.ProvenanceRewrite` -- the file's ORIGINAL, pre-merge
    text -- for each (spec: "Reversible Inbound-Provenance Rewiring").

    This scan is deliberately NOT gated on the absorbed concept's `type`:
    `query --save` writes arbitrary cited concept ids into `provenance`,
    so a merge absorbing a NON-Source concept can orphan third-party
    provenance too -- unlike `set-sensitivity`'s propagation, which IS
    Source-gated for a different, unrelated reason.

    The survivor itself (`file == survivor_id`) and the absorbed file
    itself (`file == absorbed_id`) are EXCLUDED from the scan regardless of
    what their own `provenance:` names: the survivor's own provenance is
    the exclusive concern of `build_merged_document`'s generic list-union,
    and the absorbed file is deleted by this merge -- this trio is for
    genuine third parties only, never either merge participant.

    A file whose frontmatter fails to parse, or whose `provenance` is not a
    list, is SKIPPED rather than surfaced as a fail-closed refusal --
    mirrors `find_inbound_relation_rewrites`'s identical broad-except
    behavior around the same `load_frontmatter` call. `files` iteration
    order determines result order.
    """
    rewrites: list[okf.ProvenanceRewrite] = []
    for file, text in files.items():
        file_id = _normalize_id(file)
        if file_id in (survivor_id, absorbed_id):
            continue  # neither merge participant is this scan's concern
        metadata: dict[str, object] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: an unrelated file's corrupt frontmatter
            # must never crash or block an otherwise-unrelated merge scan
            # (mirrors find_inbound_relation_rewrites' identical skip).
            metadata = None
        if metadata is None:
            continue
        raw_provenance = metadata.get("provenance")
        if not isinstance(raw_provenance, list):
            continue
        if any(
            _normalize_id(str(entry)) == _normalize_id(absorbed_id)
            for entry in raw_provenance
        ):
            rewrites.append(okf.ProvenanceRewrite(file=file, snapshot=text))
    return rewrites


def apply_provenance_rewrites(
    text: str,
    *,
    file: str,
    survivor_id: str,
    absorbed_id: str,
    rewrites: list[okf.ProvenanceRewrite],
) -> str:
    """Pure: retarget `file`'s own `provenance:` (`absorbed_id` ->
    `survivor_id`) via retarget-then-dedupe, first-occurrence-wins, keyed on
    the normalized id (design; mirrors `build_merged_document`'s union
    rule). A no-op (returns `text` unchanged) unless `file` appears in
    `rewrites` -- a file not recorded by `find_inbound_provenance_rewrites`
    had nothing to retarget.

    Every entry is considered in order: an entry naming the absorbed id is
    retargeted to `survivor_id` in place; the FIRST entry (by normalized
    key) to resolve to a given id is kept, every later duplicate is
    dropped. A list already naming BOTH survivor and absorbed collapses to
    one `survivor_id` entry at whichever position was EARLIER; every other
    entry keeps its relative order. Retained entries keep their ORIGINAL
    string form (only a retargeted entry's string changes). The result is
    provably never empty (`find_inbound_provenance_rewrites` only records
    files holding >= 1 absorbed entry), so unlike
    `bundle/relations.py::apply_relation_rewrites` there is no pop-the-key
    branch -- `provenance:` always stays present after a retarget.
    """
    if not any(rewrite.file == file for rewrite in rewrites):
        return text

    metadata, body = okf.load_frontmatter(text)
    raw_provenance = metadata.get("provenance")
    entries = raw_provenance if isinstance(raw_provenance, list) else []

    absorbed_key = _normalize_id(absorbed_id)
    merged: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        value = str(entry)
        retargeted = survivor_id if _normalize_id(value) == absorbed_key else value
        key = _normalize_id(retargeted)
        if key in seen:
            continue  # first occurrence wins
        seen.add(key)
        merged.append(retargeted)

    metadata["provenance"] = merged
    return okf.dump_frontmatter(metadata, body)


def reverse_provenance_rewrites(
    text: str,
    *,
    file: str,
    survivor_id: str,
    absorbed_id: str,
    rewrites: list[okf.ProvenanceRewrite],
    link_rewrites: list[okf.LinkRewrite],
    relation_rewrites: list[okf.RelationRewrite],
) -> str:
    """Pure inverse of `apply_provenance_rewrites`: restore `file`'s
    recorded whole-file snapshot verbatim -- an ABSOLUTE overwrite, never
    offset math -- but DRIFT-AWARE and FAIL-CLOSED, mirroring
    `bundle/relations.py::reverse_relation_rewrites`.

    `text` MUST be `file`'s CURRENT on-disk content. This recomputes the
    EXPECTED post-merge content for `file` and compares: if `text` does not
    match, the file drifted (a legitimate edit landed on it after the merge
    and before this `unmerge`), and this raises `ValueError` rather than
    overwriting that edit with the stale snapshot.

    Unlike `reverse_relation_rewrites` (which only needs `link_rewrites`),
    provenance is the THIRD rewrite pass in `merge_core`'s chain, so the
    expected content must be reconstructed FORWARD from the recorded
    pre-merge `snapshot` through ALL THREE passes in their exact chain
    order: `bundle_links.apply_link_rewrites`, then
    `bundle_relations.apply_relation_rewrites`, then
    `apply_provenance_rewrites` -- each a no-op for `file` when it has no
    entry in the corresponding rewrites list. This is why the signature
    requires BOTH `link_rewrites` and `relation_rewrites` in addition to
    its own `rewrites`.

    A `file` with no matching recorded rewrite returns `text` unchanged
    (no-op) -- no drift check applies.

    Raises `ValueError` if MORE THAN ONE rewrite is recorded for the same
    `file`: `find_inbound_provenance_rewrites` records at most one entry
    per file per scan, so more than one within a single ledger entry's
    `provenance_rewrites` list is a construction bug, not a legitimate
    multi-snapshot case.
    """
    matches = [rewrite for rewrite in rewrites if rewrite.file == file]
    if not matches:
        return text
    if len(matches) > 1:
        raise ValueError(
            f"more than one provenance_rewrites snapshot recorded for {file!r}"
        )
    snapshot = matches[0].snapshot
    pre_relation_text = bundle_links.apply_link_rewrites(
        snapshot, file=file, rewrites=link_rewrites
    )
    pre_provenance_text = bundle_relations.apply_relation_rewrites(
        pre_relation_text,
        file=file,
        survivor_id=survivor_id,
        absorbed_id=absorbed_id,
        rewrites=relation_rewrites,
    )
    expected_post_merge = apply_provenance_rewrites(
        pre_provenance_text,
        file=file,
        survivor_id=survivor_id,
        absorbed_id=absorbed_id,
        rewrites=matches,
    )
    if text != expected_post_merge:
        raise ValueError(
            f"cannot reverse provenance rewrite: bundle/{file} drifted since "
            "the merge -- current content does not match what this merge "
            "wrote there"
        )
    return snapshot
