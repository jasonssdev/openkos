"""Pure orphan-closure helper for `forget --scope source` (design: "New
`src/openkos/bundle/provenance.py` (canonical layer, MUST NOT import
`openkos.graph` -- same rule `references.py`/`links.py` follow)"; spec:
"Provenance Descendant Resolution").

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

Like `bundle/references.py`, this module is canonical-layer: it MUST NOT
import `openkos.graph`, is pure (no I/O), and takes the whole-bundle
`files` snapshot the caller already has in memory.
"""

from collections.abc import Collection, Mapping

from openkos.model import okf


def _normalize_id(raw_id: str) -> str:
    """Strip a trailing `.md` suffix so a `provenance` entry, a `files` key,
    and a `root_ids` entry all compare on the same canonical id, regardless
    of whether the caller wrote it with or without the suffix."""
    return raw_id.removesuffix(".md")


def find_provenance_descendants(
    files: Mapping[str, str], *, root_ids: Collection[str]
) -> list[str]:
    """Return the sorted orphan-closure purge set (roots + descendants).

    Algorithm: seed `purge` with the normalized `root_ids`. Parse every
    file's `provenance` frontmatter list ONCE into `id -> frozenset(...)`
    (canonical, `.md`-stripped ids on both sides); a file whose frontmatter
    fails to parse, or whose `provenance` is not a list, is SKIPPED -- it
    can then never be added to `purge`, which is fail-safe against
    over-deletion (mirroring `bundle/references.py`'s "malformed file is
    skipped rather than surfaced" contract, here applied to preservation
    instead of detection). Then iterate to a fixed point: a candidate `C`
    not yet in `purge` joins iff its provenance set is NON-EMPTY and a
    subset of `purge`.

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
    order-independent of both `files` iteration order and `root_ids` order.
    """
    provenance_by_id: dict[str, frozenset[str]] = {}
    for path, text in files.items():
        concept_id = _normalize_id(path)
        metadata: dict[str, object] | None
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: malformed frontmatter is preserved
            # rather than swallowed into the purge set, see docstring's
            # "critical over-deletion barrier"
            metadata = None
        if metadata is None:
            continue
        raw_provenance = metadata.get("provenance")
        if not isinstance(raw_provenance, list):
            continue
        provenance_by_id[concept_id] = frozenset(
            _normalize_id(str(entry)) for entry in raw_provenance
        )

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
