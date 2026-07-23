"""Canonical-layer effective-status predicate (status-aware-retrieval,
MVP-3 gap #8 · S1).

`deprecated_concept_ids` is the ONE shared predicate every retrieval input
(FTS/vector/graph) and candidate-load surface (contradiction detection,
adjudication) filters against before fusion/candidate emission — see
`openspec/changes/status-aware-retrieval/design.md`. It imports only
`openkos.model.okf` + stdlib, a package-root leaf like `lint.py`/`config.py`:
both `retrieval/` and `resolution/` depend on it with no cycle and no
retrieval<->resolution coupling.

A concept is effective-deprecated iff its own `status` frontmatter field
equals `"deprecated"`, OR it is the TARGET of ANY other concept's outbound
`supersedes` edge. Self-`supersedes` edges (source == target) are dropped
before set-building, so they never mark a concept deprecated — that is the
only exemption this predicate makes.

**R2 (PINNED, fail-safe)**: there is no reciprocal-cancellation or
cycle-detection step of any kind — the predicate never inspects graph
structure beyond a single edge's own source/target. It simply deprecates
every non-self target it sees. A side effect of that simplicity is that ANY
`supersedes` cycle — a mutual 2-cycle (A -> B, B -> A), a 3-cycle, a longer
cycle, or a cycle with an extra chord edge — ends up with every one of its
members deprecated, since each member is the target of at least one
in-cycle edge. Contradictory or cyclic supersession is treated as
unresolved and hidden rather than guessed at.
"""

from pathlib import Path
from typing import Protocol

from openkos.model import okf


class _HasConceptId(Protocol):
    """Structural type for anything `filter_hits` can filter: `FtsHit`,
    `VecHit`, and `GraphHit` all expose `.concept_id`, but this module never
    imports them directly (that would pull `retrieval`/`state` into a
    canonical-layer leaf)."""

    concept_id: str


def deprecated_concept_ids(bundle_dir: Path) -> frozenset[str]:
    """Compute the set of effective-deprecated concept ids for `bundle_dir`
    in one `okf._iter_docs` walk.

    A concept id is included when its own `status` is `"deprecated"`, or
    when it is the target of another concept's `supersedes` edge (self-refs
    dropped, no other exemption — see module docstring for the R2 fail-safe
    rule). A document that fails to read/parse, or whose `relations:` field
    is malformed, contributes no status/edges for itself and is otherwise
    skipped (fail-safe: never raises)."""
    status_by_id: dict[str, str] = {}
    supersedes: set[tuple[str, str]] = set()  # (source, target), source != target
    for scan in okf._iter_docs(bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        cid = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        meta = scan.metadata or {}
        status_by_id[cid] = str(meta.get("status") or "")
        try:
            relations = okf.decode_relations(meta)
        except ValueError:
            relations = []  # malformed relations: no edges, no crash
        for relation in relations:
            if relation.type == "supersedes" and relation.target != cid:
                supersedes.add((cid, relation.target))

    # Fail-safe rule (R2): any non-self supersedes target is deprecated,
    # with no reciprocal-cancellation or cycle-length exemption — every
    # member of any supersedes cycle (mutual pair or longer) is hidden.
    superseded = {target for source, target in supersedes if target != source}
    own_deprecated = {
        cid for cid, status in status_by_id.items() if status == "deprecated"
    }
    return frozenset(own_deprecated | superseded)


def filter_hits[H: _HasConceptId](hits: list[H], deprecated: frozenset[str]) -> list[H]:
    """Drop every hit whose `.concept_id` is in `deprecated`, preserving the
    relative order of the remaining hits.

    Generic over any `.concept_id`-bearing hit type (`FtsHit`, `VecHit`,
    `GraphHit`) so every retrieval seam reuses this one filter (design:
    "one generic filter_hits ... applied at every seam")."""
    return [hit for hit in hits if hit.concept_id not in deprecated]
