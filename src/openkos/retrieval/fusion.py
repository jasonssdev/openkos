"""Pure rank-fusion helpers: no I/O, no config.

`fuse()` combines the two already-ordered RETRIEVER lists -- a lexical
`FtsHit` list and a dense `VecHit` list -- into one ordered `concept_id`
list via reciprocal rank fusion (RRF), ranked purely by combined RANK
POSITION (never by `score`/`distance` magnitude). That two-list ranking is
the BASE, and nothing else is allowed to permute it.

`fuse_with_graph()` layers the graph channel on top of that base
ADDITIVELY: the graph may only contribute concepts the two retrievers never
saw, into a bounded number of reserved slots at the tail of the final
top-`limit`. See `openspec/specs/retrieval-fusion/spec.md` for the full
contract.

Why the graph is NOT a third RRF list (issue #402). RRF scores by rank
position with `K_RRF = 60`, so a concept present only in the graph list at
graph rank 1 scores `1/61 ≈ 0.0164`, while a concept present in BOTH FTS and
dense at rank 10 in each scores `2/70 ≈ 0.0286`. A concept only the graph
can see therefore could never outscore one the two retrievers agree on, no
matter how central personalized PageRank found it -- yet the graph list still
reshuffled the concepts the other two lists already contained. Measured over
10 questions on an 8-source bundle, the graph promoted 26 concepts into the
top-5 and every one of them was already inside the FTS+dense pool (zero
graph-only contributions), while it demoted real hits -- dropping
`concepts/google-adk` out of the top-5 for "What should I know about ADK?",
its exact-title match. The graph got to move things down without ever
earning the right to move anything new up. Tuning `K_RRF` does not fix that
asymmetry: a concept in two lists still beats one in a single list at
comparable ranks.

The typed graph's entire justification is surfacing what lexical and
semantic retrieval MISS. A channel that only reshuffles what the other two
already found is, at best, noise. Reserving slots makes its contribution
measurable -- you can point at exactly which concepts the graph added and
judge whether they helped.
"""

from dataclasses import dataclass
from typing import Final

from openkos.state.fts import FtsHit
from openkos.state.vectorstore import VecHit

K_RRF = 60
"""RRF's `k` constant: dampens the contribution of low ranks (spec-pinned)."""

GRAPH_RESERVED_SLOTS: Final[int] = 1
"""How many slots of the final top-`limit` the graph channel may claim.

One, chosen by measurement rather than taste. The graph channel is unproven
-- #402 measured its net contribution on an 8-source bundle as strictly
negative -- so it buys its way in one slot at a time. Re-running that
measurement against this implementation decided the number: at TWO reserved
slots the graph does contribute genuinely new concepts (20 of 20 from
outside the FTS+dense pool, against 0 of 26 before), but the second slot
displaces base rank 4, which is exactly where `concepts/google-adk` sits for
"What should I know about ADK?" -- the eviction the issue opens with,
re-created by the cure. At one slot that concept survives and the graph
still contributes on every query.

`_reserved_slot_count` additionally caps the reservation at `limit // 2`, so
a `limit=1` query is never answered from a graph-only concept alone. The two
caps are independent on purpose: raising this constant must not silently let
the graph take a small `limit` outright."""


@dataclass(frozen=True)
class GraphHit:
    """One `graph_retrieve.graph_rank` result: a personalized-PageRank
    candidate concept.

    Deliberately defined here (the CONSUMPTION site), not alongside
    `graph_rank` (the producer) in `retrieval/graph_retrieve.py`, which
    imports it from this module -- an inversion of the `FtsHit`/`VecHit`
    precedent. Placing it with the producer would force this pure, zero-I/O
    leaf to transitively import `networkx`; `fusion.py` never imports
    `graph_retrieve`, so there is no import cycle.
    """

    concept_id: str
    """The OKF concept id (bundle-relative path, `.md` suffix removed)."""
    score: float
    """The raw personalized-PageRank score. `fuse_with_graph()` uses list
    POSITION, never this magnitude, mirroring `FtsHit.score`/
    `VecHit.distance`."""


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

    This is the BASE ranking. The graph channel is layered on top by
    `fuse_with_graph`, which never permutes what this returns.
    """
    scores: dict[str, float] = {}
    _accumulate(scores, fts_hits)
    _accumulate(scores, vec_hits)
    return sorted(scores, key=lambda concept_id: (-scores[concept_id], concept_id))


def _reserved_slot_count(limit: int) -> int:
    """How many of `limit`'s slots the graph channel may claim.

    `min(GRAPH_RESERVED_SLOTS, limit // 2)`: the flat cap keeps the graph's
    contribution small and countable, and the `limit // 2` term keeps the
    base ranking in the majority for a `limit` too small for the flat cap to
    do so (`limit=1` reserves nothing at all)."""
    return min(GRAPH_RESERVED_SLOTS, max(limit, 0) // 2)


def _graph_only_ids(
    graph_hits: list[GraphHit], pool: set[str], slots: int
) -> list[str]:
    """The best-ranked `concept_id`s of `graph_hits` that are absent from
    `pool`, capped at `slots`.

    Order is `graph_hits`' own order (personalized-PageRank rank), never
    re-sorted -- the graph channel's one job is to name what the retrievers
    missed, best candidate first. Each claimed id joins `claimed`, so a
    `concept_id` repeated within `graph_hits` costs a single slot; that is
    the same membership test the pool filter uses, not a second rule."""
    reserved: list[str] = []
    claimed = set(pool)
    for hit in graph_hits:
        if len(reserved) == slots:
            break
        if hit.concept_id in claimed:
            continue
        claimed.add(hit.concept_id)
        reserved.append(hit.concept_id)
    return reserved


def fuse_with_graph(
    fts_hits: list[FtsHit],
    vec_hits: list[VecHit],
    graph_hits: list[GraphHit],
    *,
    limit: int,
) -> list[str]:
    """Return the final top-`limit` `concept_id` list: the FTS+dense base
    ranking, plus a bounded, ADDITIVE graph contribution at the tail.

    Three rules, in order (issue #402):

    1. `fuse(fts_hits, vec_hits)` is the base ranking and is never permuted.
       The surviving base concepts appear in exactly that relative order.
    2. `graph_hits` may contribute ONLY `concept_id`s absent from the
       FTS+dense pool, filling up to `_reserved_slot_count(limit)` reserved
       slots at the TAIL of the returned list. The tail is the conservative
       placement: it disturbs no base position that survives, and it makes
       the graph's contribution trivially identifiable -- it is always the
       last `n` entries.
    3. A `concept_id` already in the FTS+dense pool draws NOTHING from
       `graph_hits`. The graph can neither promote nor demote it.

    WHEN the graph contributes nothing new -- an empty `graph_hits`, or one
    whose every entry is already in the pool -- the result is byte-identical
    to `fuse(fts_hits, vec_hits)[:limit]`.

    Reserved slots are a CAP, not a quota: a base ranking shorter than
    `limit` is not padded past `_reserved_slot_count(limit)` graph concepts.

    Performs no file, network, or database access and invokes no model; it
    is pure ranking arithmetic, returning identical output for identical
    inputs across repeated calls.
    """
    base = fuse(fts_hits, vec_hits)
    reserved = _graph_only_ids(graph_hits, set(base), _reserved_slot_count(limit))
    return base[: max(limit, 0) - len(reserved)] + reserved
