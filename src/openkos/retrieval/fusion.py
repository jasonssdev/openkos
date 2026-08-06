"""Pure rank-fusion helper: no I/O, no config.

`fuse()` combines the two already-ordered RETRIEVER lists -- a lexical
`FtsHit` list and a dense `VecHit` list -- into one ordered `concept_id`
list via reciprocal rank fusion (RRF), ranked purely by combined RANK
POSITION (never by `score`/`distance` magnitude). It is the WHOLE of
retrieval ranking. See `openspec/specs/retrieval-fusion/spec.md` for the
full contract.

There used to be a third channel here (issue #434 removed it). A
`fuse_with_graph()` layered a seeded personalized-PageRank list on top of
this base, additively: the graph could only contribute concepts the two
retrievers never saw, into `GRAPH_RESERVED_SLOTS` reserved slots at the tail
of the final top-`limit`. That shape was itself a fix (#402/#433) for an
earlier design that folded the graph in as a third RRF list, where it
reshuffled what FTS and dense had already found without ever contributing
anything of its own.

Bounding the channel is what made it measurable, and measuring it is what
ended it. Two A/B runs, 10 questions each: on a 21-node graph the SAME
concept, `concepts/document-skills`, was the contribution on 6 of 10
questions -- about MCP origin, BigQuery, agent building and productionizing
alike. On a 27-node graph the concentration fell to 4 of 10 and spread over
7 distinct concepts, but per-question judgement was 7 harmful, 3 neutral, 0
beneficial. Asked "When did MCP originate?", the graph evicted
`sources/mcp-origin` -- the document containing the answer -- to insert
`concepts/document-skills`.

THE LESSON IS ABOUT THE RANKING FUNCTION, NOT THE TYPED GRAPH. Seeded
personalized PageRank ranks by GLOBAL CENTRALITY, which is a property of the
corpus, not of the question. A larger graph changes WHICH central node wins
the reserved slot; it does not stop the slot costing a base hit, and it does
not turn centrality into relevance. The typed graph still earns its keep
elsewhere -- `resolution/contradiction.py` derives its candidate pairs from
typed edges, and that path caught a planted contradictory pair at confidence
1.00. What would justify a graph channel returning is a DIFFERENT ranking
function -- traversal from the question's own matched concepts along typed
edges -- proposed and measured on its own terms, not a revert.
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

    This is the ENTIRE ranking. Nothing is layered on top of it and
    nothing permutes it (issue #434).
    """
    scores: dict[str, float] = {}
    _accumulate(scores, fts_hits)
    _accumulate(scores, vec_hits)
    return sorted(scores, key=lambda concept_id: (-scores[concept_id], concept_id))
