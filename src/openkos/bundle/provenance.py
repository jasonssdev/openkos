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


def provenance_reachable(
    provenance_by_id: Mapping[str, frozenset[str]], *, root_ids: Collection[str]
) -> list[str]:
    """Return the sorted fixpoint REACHABILITY set (roots + everything that
    cites into them, however partially) over an already-parsed `id ->
    frozenset(provenance ids)` map -- the reporting-scope sibling of
    `provenance_closure` (issue #232).

    Algorithm: seed `reachable` with the normalized `root_ids`, then iterate
    to a fixed point: a candidate `C` not yet in `reachable` joins iff its
    provenance set has a NON-EMPTY INTERSECTION with `reachable`.

    WHY THIS IS NOT `provenance_closure`'s SUBSET RULE: that rule is a
    conservative WRITE gate. `forget --scope source` deletes and
    `set-sensitivity` re-classifies every member it returns, so a concept
    still holding one surviving source must stay out -- admitting it would
    over-delete or over-classify. THIS relation is a REPORTING scope, where
    the fail-closed-excluded citer is the whole point: a concept citing
    `[sources/a, ghost]` can NEVER join `sources/a`'s closure (`ghost` never
    enters the set, so the subset test never passes), yet it is precisely
    the concept whose dangling `ghost` entry a human needs to see. Scoping a
    warning to closure members would therefore silence exactly the warning
    that matters. The intersection rule returns a strict superset of the
    closure: genuine closure members, PLUS every partial citer the subset
    rule excludes, PLUS their own descendants -- while still excluding an
    unrelated Source's entire tree, whose provenance (its raw `resource`) is
    disjoint from the invoked Source's reachable set.

    The non-empty requirement is inherent rather than an added guard here:
    an empty provenance set intersects nothing, so a provenance-less concept
    is never pulled in -- the same barrier `provenance_closure` needs its
    explicit non-empty test for.

    Termination: `reachable` only ever grows, and it is bounded by the
    finite universe of `root_ids | provenance_by_id.keys()`, so the fixpoint
    loop always halts -- including on a provenance cycle disjoint from any
    root (those concepts never intersect the set and are never added) and on
    self-referential provenance (a concept naming only itself cannot
    intersect `reachable` before it is already a member of it).

    Determinism: the returned list is `sorted()`; the fixpoint set itself is
    order-independent of both `provenance_by_id` iteration order and
    `root_ids` order.
    """
    reachable = {_normalize_id(root_id) for root_id in root_ids}

    changed = True
    while changed:
        changed = False
        for concept_id, entry_provenance in provenance_by_id.items():
            if concept_id in reachable:
                continue
            if entry_provenance & reachable:
                reachable.add(concept_id)
                changed = True

    return sorted(reachable)


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


def _reachable_ids(
    files: Mapping[str, str], *, root_ids: Collection[str]
) -> frozenset[str]:
    """Build `provenance_reachable`'s reporting scope straight from a
    whole-bundle `files` snapshot, as a membership set (issue #232).

    Mirrors `find_provenance_descendants`'s shape -- parse once via
    `_parse_provenance_by_id`, then delegate the fixpoint walk -- but returns
    a `frozenset` rather than a sorted list, because its only caller
    (`find_unresolvable_provenance`) tests membership per file and must keep
    its own `files` iteration order.

    This costs one extra frontmatter pass on the scoped path only.
    `_parse_provenance_by_id` collapses each `provenance:` list into a
    `frozenset`, which discards exactly the list order and duplicate entries
    that `find_unresolvable_provenance`'s pinned ordering contract reports,
    so its scan loop cannot be folded into this parse and must keep reading
    the raw list itself."""
    return frozenset(
        provenance_reachable(_parse_provenance_by_id(files), root_ids=root_ids)
    )


def provenance_source_ancestors(
    files: Mapping[str, str], *, object_id: str
) -> list[str]:
    """Every `sources/` id whose provenance chain REACHES `object_id` --
    the reverse lookup #628 tracks: 'protect this object' means raising
    every Source it was derived from, and #571's containment note only
    names the object's own DIRECT provenance parents.

    Walks the object's provenance UPWARD to a fixed point (the direction
    opposite `provenance_closure`'s descendant walk): the object's own
    `provenance` entries, then theirs, and so on -- so a Source reached
    only through an intermediate derived concept is still found. The
    answer keeps ONLY the `sources/`-prefixed ancestors, matching the
    prefix rule #571's note already applies; intermediate concepts are
    walked through, never reported. A `sources/` entry with no file behind
    it is still included -- a dangling Source is still one the caller
    asked to be told about, and hiding it would understate the
    containment set. A raw-resource entry (e.g. `notes.txt`, what a
    Source's own provenance holds) never matches the prefix and never
    joins the walk's answer.

    Termination mirrors `provenance_closure`: the ancestor set only grows
    and is bounded by the finite id universe, so a provenance cycle
    terminates and the object citing itself never re-enters the frontier.
    The returned list is `sorted()` -- deterministic regardless of `files`
    iteration order."""
    provenance_by_id = _parse_provenance_by_id(files)
    ancestors: set[str] = set()
    frontier = {_normalize_id(object_id)}
    while frontier:
        next_frontier: set[str] = set()
        for concept_id in frontier:
            for parent in provenance_by_id.get(concept_id, frozenset()):
                if parent not in ancestors:
                    ancestors.add(parent)
                    next_frontier.add(parent)
        frontier = next_frontier
    return sorted(ancestor for ancestor in ancestors if ancestor.startswith("sources/"))


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


def _source_levels(files: Mapping[str, str]) -> dict[str, str]:
    """Parse every file once, returning `id -> sensitivity` for every
    `type: Source` concept (design: "for source_id in sorted(ids where type
    == 'Source')", Phase A of the bundle-wide sweep).

    The `sensitivity` value is coerced to `str` only to satisfy
    `resolve_source_raises`'s `level: str` parameter -- the coercion
    preserves `okf._rank`'s exact fail-closed ranking (ADR-0003): a missing
    (`None`) value becomes `""`, which `_rank` floors at `private`,
    identically to `_rank(None)`; any other non-`str` value (e.g. a dirty
    `int`/`list` from hand-edited frontmatter) is stringified, which still
    fails closed to `confidential` unless the stringified form happens to
    already be a canonical member of `okf.SENSITIVITY_ORDER`. A file whose
    frontmatter fails to parse is SKIPPED, mirroring this module's other
    broad-except conventions (`provenance.py:194`, `:473`)."""
    source_levels: dict[str, str] = {}
    for path, text in files.items():
        metadata: dict[str, object] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: malformed frontmatter is skipped rather
            # than surfaced, mirroring `_parse_provenance_by_id`
            metadata = None
        if metadata is None:
            continue
        if metadata.get("type") != "Source":
            continue
        raw_level = metadata.get("sensitivity")
        if isinstance(raw_level, str):
            level = raw_level
        elif raw_level is None:
            level = ""
        else:
            level = str(raw_level)
        source_levels[_normalize_id(path)] = level
    return source_levels


def _levels_by_id(files: Mapping[str, str]) -> dict[str, object]:
    """Parse every file once into `id -> raw sensitivity value`, keeping the
    value RAW (`object`, not `str`).

    Ranking is unaffected by the choice: `okf.combine_sensitivity` ranks
    through the private fail-closed `okf._rank` (ADR-0003), where a missing
    value floors at `private` and a dirty non-string floors at `confidential`
    -- and an unrecognized STRING floors at `confidential` too, so `42` and
    `"42"` rank identically. This docstring used to claim the opposite (that
    coercing to `str`, as `_source_levels` must to satisfy
    `resolve_source_raises`'s `level: str` signature, "would turn a dirty
    `int` into a string that no longer fails closed the same way"); measured
    against every value YAML frontmatter can produce -- `int`, `bool`, `list`,
    `dict`, `float`, `date`, blank and unrecognized strings -- that claim is
    FALSE, and `_source_levels`'s own docstring already said so correctly
    (issue #736). Nothing in this module's behavior rested on it.

    WHAT RAW IS ACTUALLY LOAD-BEARING FOR is reporting, not ranking:
    `resolve_cited_high_water_raises` hands this value straight to
    `okf.DescendantRaise.current`, which a preview shows the operator as what
    the file HOLDS. Coercing would print `'42'` where the frontmatter holds
    `42` -- a preview claiming a string the operator never wrote. Of everything
    stated here, that reporting property is the ONLY one a `str` coercion
    breaks, and it is what
    `test_a_present_but_dirty_sensitivity_fails_closed_and_propagates` pins.

    A file whose frontmatter fails to parse is SKIPPED, mirroring
    `_parse_provenance_by_id`.
    """
    levels: dict[str, object] = {}
    for path, text in files.items():
        metadata: dict[str, object] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: malformed frontmatter is skipped rather
            # than surfaced, mirroring `_parse_provenance_by_id`
            metadata = None
        if metadata is None:
            continue
        levels[_normalize_id(path)] = metadata.get("sensitivity")
    return levels


def resolve_cited_high_water_raises(
    files: Mapping[str, str],
) -> list[okf.DescendantRaise]:
    """Pure fixpoint maintaining ADR-0003's high-water mark for a concept
    whose provenance spans MORE THAN ONE Source (issue #697).

    ADR-0012 deferred exactly this case, and named the deferral: a concept in
    no single Source's closure "stays reported, not resolved, until a human
    acts". `resolve_source_raises` cannot reach it because
    `provenance_closure`'s conservative NON-EMPTY SUBSET rule keeps out any
    concept still citing a Source outside the root's closure -- correct for
    propagating ONE Source's level down its exclusive descendants, and exactly
    wrong for a high-water mark, where citing a confidential source and a
    public one means confidential. The subset rule under-classifies here.

    THE RULE MIRRORS BIRTH, not the closure walk. `cli/main.py`'s
    `_stage_filed_answer` folds `okf.combine_sensitivity` over each CITED
    concept's own level to compute a filing's sensitivity; this recomputes the
    same fold over the same direct `provenance` entries, so an object's
    maintained level is the one its own birth rule would produce today. Reading
    Source ANCESTORS instead (`provenance_source_ancestors`, #628) would miss a
    cited intermediate concept raised by hand without its Source.

    WHY A FIXPOINT rather than one pass: raising `mid` changes the input for
    everything citing `mid`. A single pass would repair one level and leave the
    same gap one level up. Termination is the same argument
    `provenance_closure` makes -- `combine_sensitivity` is monotone and
    `okf.SENSITIVITY_ORDER` is finite, so levels only ever rise and the loop
    halts, including on a provenance cycle (two concepts citing each other
    converge on their shared maximum) and on self-referential provenance.

    THE RE-SCAN IS NOT WHERE THE TIME GOES, so do not "fix" it first. The walk
    order is arbitrary, and on a linear chain inserted in reverse the loop
    needs one pass per link -- genuinely quadratic. Measured once, on synthetic
    bundles, 2026-08-16 (issue #736); the ratios are what to trust, not the
    millisecond figures, which are one machine on one day. A real bundle shape,
    where every insight cites Sources DIRECTLY as `query --save` files them,
    converged in TWO passes and spent 2.3% of the call in this loop at 1k docs
    and at 10k alike; the other ~98% was the three whole-bundle frontmatter
    parses above. Even the adversarial 400-deep reverse chain -- a shape
    nothing in this product builds -- cost 36ms.
    Reordering the walk topologically was therefore REJECTED: it optimizes 2%
    of a maintenance verb an operator runs explicitly. If this call ever does
    need to be faster, collapse the three parses into one.

    A concept is staged only when EVERY cited id resolves to a file in `files`.
    A dangling citation leaves it unstaged, matching `lint`'s own
    `multi-source-uncovered` rule and ADR-0012 design D8: that signal belongs
    to `check_dangling_provenance`. The alternative -- folding an unresolvable
    citation to `confidential`, as `_stage_filed_answer` does at birth -- is
    deliberately NOT copied here. At birth it guards ONE document the operator
    is creating; in a bundle-wide sweep it would raise every descendant of one
    dangling ref, a blast radius no preview makes safe, and it would contradict
    the `lint` finding that sent the operator to this verb.

    A `type: Source` is NEVER staged: a Source's level is operator-set truth,
    never derived from what it cites. In practice a Source cites its raw
    `resource`, which never resolves to a bundle id and would leave it unstaged
    anyway; the explicit type guard states the invariant rather than resting on
    that coincidence.

    Raise-only by construction (`combine_sensitivity` never lowers), so a
    concept deliberately classified ABOVE everything it cites keeps its level.
    The returned list is `sorted()` by `concept_id`, each entry's `content` a
    full `okf.dump_frontmatter` re-render of the ORIGINAL metadata with only
    `sensitivity` replaced.
    """
    provenance_by_id = _parse_provenance_by_id(files)
    levels = _levels_by_id(files)
    source_ids = set(_source_levels(files))

    # Only concepts whose every citation resolves are eligible; the ineligible
    # are excluded once, before the loop, so the fixpoint cannot admit one
    # through a later iteration.
    eligible = {
        concept_id: parents
        for concept_id, parents in provenance_by_id.items()
        if parents
        and concept_id not in source_ids
        and all(parent in levels for parent in parents)
    }

    resolved = dict(levels)
    changed = True
    while changed:
        changed = False
        for concept_id, parents in eligible.items():
            current = resolved[concept_id]
            combined = current
            for parent in parents:
                combined = okf.combine_sensitivity(combined, resolved[parent])
            if combined != current:
                resolved[concept_id] = combined
                changed = True

    raises: list[okf.DescendantRaise] = []
    for concept_id in sorted(eligible):
        current = levels[concept_id]
        # Re-combined rather than read straight out of `resolved`, for a typed
        # `str`: the fold seeds from RAW frontmatter values (`object`), which
        # is what keeps `okf._rank`'s fail-closed ranking intact. The fold only
        # ever raises, so this is the canonical form of `resolved[concept_id]`.
        # The comparison stays against the RAW `current`, matching
        # `resolve_source_raises`: a document with no `sensitivity` key at all
        # differs from any canonical level and is written explicitly.
        new_level = okf.combine_sensitivity(current, resolved[concept_id])
        if new_level == current:
            continue
        metadata, body = okf.load_frontmatter(files[f"{concept_id}.md"])
        metadata["sensitivity"] = new_level
        raises.append(
            okf.DescendantRaise(
                concept_id=concept_id,
                current=current,
                new_level=new_level,
                content=okf.dump_frontmatter(metadata, body),
            )
        )
    return raises


def resolve_backfill_raises(files: Mapping[str, str]) -> list[okf.DescendantRaise]:
    """Pure, bundle-wide sweep core `backfill-sensitivity` needs (design
    D4/D5/D6) -- everything the verb requires that is NOT the Typer command:
    no `Path`, no I/O, no confirmation, no write.

    Treats every `type: Source` concept in `files` as an independent closure
    root (design D4): for each Source, sorted by concept id,
    `resolve_source_raises` computes that Source's own per-descendant raises
    exactly as `set-sensitivity` does. Results are merged by `concept_id`,
    keeping the record with the highest-ranked `new_level` -- ranked via the
    public `okf.SENSITIVITY_ORDER.index(...)` (design D5), NEVER the private
    `okf._rank` (ADR-0003, `okf.py:296-299`). Merge-by-max is exact, not an
    approximation: `combine_sensitivity(current, ...)` is monotone in level
    (`okf.py:323`), so the winning record's already-rendered `content` is
    the correct final bytes -- no re-render.

    Ties resolve to the FIRST Source in sorted order (design D5): Sources
    are iterated in sorted order and a later Source only replaces an
    earlier one's record for the same `concept_id` on a STRICTLY higher
    rank, never on an equal one.

    No `type` filter is applied to the descendant set (design D6, matching
    `main.py:3389-3404`): a Source is never written as its OWN closure
    root's output (`resolve_source_raises` already excludes `source_id`
    from its own descendant set), but a Source that is a genuine provenance
    descendant of ANOTHER Source IS raised like any other descendant. A
    concept that is a member of NO single Source's closure is never staged
    here -- `resolve_source_raises`'s own closure computation already
    excludes it from every per-Source call, so no extra filtering is
    needed at the merge step; that concept surfaces only through the `lint`/
    `status` detection findings (design D3), never silently through this
    sweep.

    Determinism (design D7): Sources are iterated `sorted()` by concept id,
    each per-Source `resolve_source_raises` call already returns its
    results `sorted()` by `concept_id`, and this function's own return
    value is `sorted()` by `concept_id` too.

    Deliberately does NOT call `find_unresolvable_provenance` (design D8):
    every Source cites its raw `resource`, which never resolves to a
    bundle id, so a bundle-wide run would emit one WARNING per Source on
    every invocation, including the no-op path. That signal belongs to
    `lint`'s existing `dangling` finding, never this sweep.
    """
    source_levels = _source_levels(files)

    best: dict[str, okf.DescendantRaise] = {}
    best_rank: dict[str, int] = {}

    def _offer(descendant_raise: okf.DescendantRaise) -> None:
        concept_id = descendant_raise.concept_id
        new_rank = okf.SENSITIVITY_ORDER.index(descendant_raise.new_level)
        if concept_id not in best or new_rank > best_rank[concept_id]:
            best[concept_id] = descendant_raise
            best_rank[concept_id] = new_rank

    for source_id in sorted(source_levels):
        for descendant_raise in resolve_source_raises(
            files, source_id=source_id, level=source_levels[source_id]
        ):
            _offer(descendant_raise)

    # Second producer (issue #697), merged by the SAME max rule and offered
    # LAST so a strictly higher cited high-water mark wins over a per-Source
    # raise, while an equal one leaves the closure sweep's record in place --
    # the existing "ties resolve to the first" contract, extended by one
    # producer rather than reinterpreted.
    for descendant_raise in resolve_cited_high_water_raises(files):
        _offer(descendant_raise)

    return [best[concept_id] for concept_id in sorted(best)]


def find_unresolvable_provenance(
    files: Mapping[str, str],
    *,
    known_extra_ids: Collection[str] = (),
    root_ids: Collection[str] | None = None,
) -> list[tuple[str, str]]:
    """Pure unresolvable-provenance scan, extracted from
    `set_sensitivity_cmd`'s Phase-A inline warning loop (design D1/D7;
    formerly `main.py:3369-3386`).

    Scans every file's parsed `provenance` list for an entry naming an id
    with no corresponding file in `files` and no membership in
    `known_extra_ids` -- returning `(citing_id, unresolved_entry_id)` for
    each. `known_extra_ids` exists SOLELY to let a caller reproduce
    `set_sensitivity_cmd`'s exact historical pairing: passing a
    target-EXCLUDING snapshot together with `known_extra_ids={target_id}`
    (design D7) -- it is not a general-purpose escape hatch.

    `root_ids` scopes WHICH CONCEPTS MAY CITE (issue #232). The default
    `None` scans bundle-wide, byte-identical to every prior release. When
    given, only concepts inside `provenance_reachable(..., root_ids=
    root_ids)` are reported -- see that function's docstring for why the
    scope is the non-empty-INTERSECTION reachability relation and not
    `provenance_closure`'s subset rule: the concept whose dangling entry
    needs reporting is precisely the one the subset rule fail-closed
    EXCLUDES, so scoping to closure members would silence it. `set-
    sensitivity` passes the invoked Source as the sole root, so a
    single-Source raise no longer warns about an unrelated Source's own
    unresolvable raw `resource` (which every Source carries -- see
    `resolve_backfill_raises`'s design-D8 note on the same trap).

    The id-EXISTENCE set is deliberately built from the FULL `files`
    mapping plus `known_extra_ids`, never from the scoped subset: an id that
    exists anywhere in the bundle IS resolvable, and narrowing the snapshot
    would invent false warnings for in-scope concepts citing out-of-scope
    siblings. Only the set of CITING concepts narrows.

    A scoped call therefore reports strictly fewer entries, and its blind
    spot is deliberate: a concept citing ONLY an unresolvable id is
    unreachable from any root and stays silent HERE. That case is owned by
    `lint.check_dangling_provenance`'s bundle-wide scan (issue #257) --
    never by this single-Source write verb; `lint.check_dangling_targets`
    still scans `relations:` and body markdown links only, never
    `provenance:`, and `lint.check_below_source_sensitivity` still skips a
    doc with an unresolvable cite.

    Ordering is pinned byte-identical to the original inline loop, scoped or
    not: `files` iteration order, then each file's `provenance:` list order,
    with NO dedupe -- one tuple per occurrence, including a duplicate entry.
    A raw resource-shaped entry (e.g. `raw/<resource>`) never normalizes to
    a bundle id and therefore always reports when in scope (design D6/D8) --
    this is reporting only, never a write gate;
    `find_provenance_descendants`'s own non-empty-subset rule already keeps
    a citing concept fail-closed excluded from any purge/raise set.

    **Deliberate behaviour change, NOT a byte-identical move**: the
    original inline loop caught only `except (OSError, ValueError)` around
    `okf.load_frontmatter`; this function catches broad `except Exception`
    (mirroring `_parse_provenance_by_id`'s identical broad catch just
    above). `frontmatter.loads` raises `yaml.YAMLError` on malformed YAML,
    which is neither an `OSError` nor a `ValueError`, so on `main` a
    sibling file with malformed frontmatter crashes `set-sensitivity` with
    an uncaught traceback; here it is silently skipped, same as any other
    file whose frontmatter fails to parse. See
    `tests/unit/bundle/test_provenance_source_raises.py::TestFindUnresolvableProvenance::test_malformed_frontmatter_sibling_is_skipped_not_raised`."""
    known_ids = {_normalize_id(extra_id) for extra_id in known_extra_ids} | {
        _normalize_id(path) for path in files
    }
    in_scope = None if root_ids is None else _reachable_ids(files, root_ids=root_ids)

    unresolvable: list[tuple[str, str]] = []
    for path, text in files.items():
        member_id = _normalize_id(path)
        if in_scope is not None and member_id not in in_scope:
            continue
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
