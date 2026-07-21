"""Pure reciprocal-rank-fusion (RRF) helper: no I/O, no config.

`fuse()` combines a lexical `FtsHit` list and a dense `VecHit` list -- each
already ordered by its own retriever -- into one ordered `concept_id` list,
ranked purely by combined RANK POSITION (never by `score`/`distance`
magnitude). See `openspec/changes/hybrid-retrieval-fusion/specs/
retrieval-fusion/spec.md` for the full contract.
"""

from openkos.state.fts import FtsHit
from openkos.state.vectorstore import VecHit

K_RRF = 60
"""RRF's `k` constant: dampens the contribution of low ranks (spec-pinned)."""


def _accumulate[Hit: (FtsHit, VecHit)](
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


def fuse(fts_hits: list[FtsHit], vec_hits: list[VecHit]) -> list[str]:
    """Fuse `fts_hits` and `vec_hits` into one ordered `concept_id` list.

    `fused(cid) = Σ 1/(K_RRF + rank_i(cid))` summed over every list
    containing `cid`, where `rank_i(cid)` is `cid`'s 1-based position within
    list `i` AS GIVEN (no re-sorting by `score`/`distance`). Returns
    `concept_id`s ordered by descending `fused` score, ties broken by
    `concept_id` ascending. Considers every element of both lists -- no
    truncation, filtering, or re-ranking; the caller slices to its display
    `limit`. Performs no file, network, or database access, and returns the
    identical output for identical inputs across repeated calls.
    """
    scores: dict[str, float] = {}
    _accumulate(scores, fts_hits)
    _accumulate(scores, vec_hits)
    return sorted(scores, key=lambda concept_id: (-scores[concept_id], concept_id))
