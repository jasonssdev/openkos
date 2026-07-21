"""Pure reciprocal-rank-fusion (RRF) helper: no I/O, no config.

`fuse()` combines up to three already-ordered lists -- a lexical `FtsHit`
list, a dense `VecHit` list, and an optional graph `GraphHit` list -- into
one ordered `concept_id` list, ranked purely by combined RANK POSITION
(never by `score`/`distance`/graph-score magnitude). See
`openspec/changes/hybrid-retrieval-fusion/specs/retrieval-fusion/spec.md`
and `openspec/changes/graph-augmented-retrieval/specs/retrieval-fusion/
spec.md` for the full contract.
"""

from dataclasses import dataclass

from openkos.state.fts import FtsHit
from openkos.state.vectorstore import VecHit

K_RRF = 60
"""RRF's `k` constant: dampens the contribution of low ranks (spec-pinned)."""


@dataclass(frozen=True)
class GraphHit:
    """One `graph_retrieve.graph_rank` result: a personalized-PageRank
    candidate concept.

    Deliberately defined here (the CONSUMPTION site), not alongside
    `graph_rank` (the producer) in `retrieval/graph_retrieve.py`, which
    imports it from this module -- an inversion of the `FtsHit`/`VecHit`
    precedent. Placing it with the producer would force this pure, zero-I/O
    RRF leaf to transitively import `networkx`; `fusion.py` never imports
    `graph_retrieve`, so there is no import cycle.
    """

    concept_id: str
    """The OKF concept id (bundle-relative path, `.md` suffix removed)."""
    score: float
    """The raw personalized-PageRank score. `fuse()` uses list POSITION,
    never this magnitude, mirroring `FtsHit.score`/`VecHit.distance`."""


def _accumulate[Hit: (FtsHit, VecHit, GraphHit)](
    scores: dict[str, float], hits: list[Hit]
) -> None:
    """Add one list's RRF contribution to `scores`, in place.

    Uses each `concept_id`'s FIRST (best-ranked) occurrence within `hits`;
    a later duplicate within the same list adds no further score."""
    seen: set[str] = set()
    for rank, hit in enumerate(hits, start=1):
        concept_id = hit.concept_id
        if concept_id in seen:
            continue
        seen.add(concept_id)
        scores[concept_id] = scores.get(concept_id, 0.0) + 1.0 / (K_RRF + rank)


def fuse(
    fts_hits: list[FtsHit],
    vec_hits: list[VecHit],
    graph_hits: list[GraphHit] | None = None,
) -> list[str]:
    """Fuse `fts_hits`, `vec_hits`, and an optional `graph_hits` into one
    ordered `concept_id` list.

    `fused(cid) = Σ 1/(K_RRF + rank_i(cid))` summed over every list
    containing `cid`, where `rank_i(cid)` is `cid`'s 1-based position within
    list `i` AS GIVEN (no re-sorting by `score`/`distance`/graph score).
    Returns `concept_id`s ordered by descending `fused` score, ties broken by
    `concept_id` ascending. Considers every element of up to three lists --
    no truncation, filtering, or re-ranking; the caller slices to its display
    `limit`. `graph_hits` omitted or explicitly `None` folds in nothing,
    producing the exact same output as the prior two-list contract
    (byte-identical, no behavior change for existing callers). Performs no
    file, network, or database access, and returns the identical output for
    identical inputs across repeated calls.
    """
    scores: dict[str, float] = {}
    _accumulate(scores, fts_hits)
    _accumulate(scores, vec_hits)
    _accumulate(scores, graph_hits or [])
    return sorted(scores, key=lambda concept_id: (-scores[concept_id], concept_id))
