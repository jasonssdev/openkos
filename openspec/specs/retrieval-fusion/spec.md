# Retrieval Fusion Specification

## Purpose

`retrieval/fusion.py` is a pure, zero-I/O rank-fusion helper. `fuse()` takes
a `list[FtsHit]` and a `list[VecHit]` — each already ordered by its own
retriever (`FtsHit` ascending by score, `VecHit` ascending by distance) — and
returns one ordered list of `concept_id`s via reciprocal rank fusion (RRF),
ranked by combined position alone. Magnitudes (`score`, `distance`) are never
read; only rank position matters. That two-list ranking is the BASE, and
nothing else may permute it.

`fuse()` is the WHOLE of retrieval ranking: nothing is layered on top of it.

## Non-Goals

Weighted or normalized score fusion; distance-to-similarity conversion;
graph/link ranking in ANY position — as a third RRF input, or as an additive
reserved-slot channel on top of the base (see "Retrieval Fusion Has No Graph
Channel"); truncation of `fuse()`'s own output to a caller `limit` (the
caller truncates); any I/O, model call, or config access.

## Requirements

### Requirement: RRF Score And Ordering

For each `concept_id` appearing in either input list, the system MUST compute
`fused(cid) = Σ 1 / (k_rrf + rank_i(cid))` summed over every list containing
`cid`, where `rank_i(cid)` is `cid`'s 1-based position within list `i` as
given (no re-sorting by score/distance) and `k_rrf = 60`. The system MUST
return `concept_id`s ordered by descending `fused` score, ties broken by
`concept_id` ascending.

#### Scenario: Presence in both lists outranks presence in one

- GIVEN `cid_A` is rank 1 in both the FTS list and the dense list, and
  `cid_B` is rank 1 in the FTS list only
- WHEN `fuse(fts_hits, vec_hits)` is called
- THEN `cid_A` (`1/61 + 1/61 ≈ 0.0328`) is ordered before `cid_B`
  (`1/61 ≈ 0.0164`)

#### Scenario: k=60 formula matches a worked example

- GIVEN `cid` is rank 3 in the FTS list and absent from the dense list
- WHEN `fuse(...)` is called
- THEN `cid`'s fused score equals exactly `1 / (60 + 3)`

#### Scenario: Equal fused scores tie-break by concept_id ascending

- GIVEN two `concept_id`s produce numerically equal fused scores
- WHEN `fuse(...)` is called
- THEN the lexicographically smaller `concept_id` is ordered first

### Requirement: Each Retriever's Full Pool Contributes

`fuse` MUST consider every element of both input lists — it MUST NOT
truncate, filter, or re-rank either list before computing `fused`. The
caller, not `fuse`, is responsible for slicing the returned list to any
display `limit`.

#### Scenario: All elements of both pools are represented

- GIVEN an FTS list of 10 hits and a dense list of 10 hits with partial
  overlap
- WHEN `fuse(...)` is called
- THEN every distinct `concept_id` from both lists appears in the output

### Requirement: Single-List And Empty-List Edge Cases

WHEN one input list is empty, `fuse` MUST rank purely by the other list's
positions. WHEN both input lists are empty, `fuse` MUST return an empty
result without error.

#### Scenario: Empty FTS list, non-empty dense list

- GIVEN `fts_hits = []` and a non-empty `vec_hits`
- WHEN `fuse(fts_hits, vec_hits)` is called
- THEN the output equals the dense list's `concept_id` order

#### Scenario: Empty dense list, non-empty FTS list

- GIVEN `vec_hits = []` and a non-empty `fts_hits`
- WHEN `fuse(fts_hits, vec_hits)` is called
- THEN the output equals the FTS list's `concept_id` order

#### Scenario: Both lists empty

- GIVEN `fts_hits = []` and `vec_hits = []`
- WHEN `fuse(fts_hits, vec_hits)` is called
- THEN the output is an empty list and no exception is raised

### Requirement: Duplicate Concept IDs Within One List Do Not Double-Count

WHEN the same `concept_id` appears more than once within a single input
list, `fuse` MUST use only that `concept_id`'s first (best-ranked)
occurrence in that list's contribution to `fused`; later occurrences in the
same list MUST NOT add further score.

#### Scenario: Duplicate within one list is deduplicated by best rank

- GIVEN `cid` appears at rank 1 and again at rank 5 within `fts_hits`
- WHEN `fuse(fts_hits, vec_hits)` is called
- THEN `cid`'s FTS contribution to `fused` equals `1 / (60 + 1)`, not the
  sum of both occurrences

### Requirement: Filed Syntheses Are Down-Weighted In The Ranking

An id under `insights/` (a filed synthesis — model output over an earlier
bundle state, issue #649) MUST have its accumulated fused score scaled by
`0.5` before ordering. The scaling is part of the ranking function itself,
not a layer over it: purity, determinism, and the two-list contract are
unchanged, and a fuse whose inputs contain no `insights/` id MUST order
byte-identically to the unpenalized formula. The penalty re-ranks and never
excludes: a penalized insight still participates in the ordering with its
scaled score.

#### Scenario: An insight at equal rank orders below the source

- GIVEN `insights/earlier-answer` at rank 1 in the FTS list and
  `sources/notes` at rank 1 in the dense list
- WHEN `fuse(fts_hits, vec_hits)` is called
- THEN `sources/notes` (`1/61`) is ordered before `insights/earlier-answer`
  (`0.5/61`)

#### Scenario: A dual-channel insight does not outrank a dual-channel source

- GIVEN `insights/earlier-answer` at rank 1 in both lists and a source-backed
  concept at rank 2 in both lists
- WHEN `fuse(...)` is called
- THEN the source-backed concept (`2/62`) is ordered before the insight
  (`0.5 × 2/61`)

#### Scenario: A relevant insight still beats a barely-relevant source

- GIVEN `insights/earlier-answer` at rank 1 in the FTS list and a source at
  rank 63 in the same list
- WHEN `fuse(...)` is called
- THEN the insight (`0.5/61`) is ordered before the source (`1/123`)

### Requirement: Pure Function, Deterministic, Zero I/O

`fuse` MUST perform no file, network, or database access, and MUST return
the identical ordered output for identical inputs across repeated calls.

#### Scenario: Same inputs yield the same output every call

- GIVEN a fixed `fts_hits` and `vec_hits` pair
- WHEN `fuse(...)` is called twice
- THEN both calls return byte-identical ordered output

### Requirement: Retrieval Fusion Has No Graph Channel

`fuse` MUST take exactly two lists — the lexical `FtsHit` list and the dense
`VecHit` list — and its output, sliced by the caller to a display `limit`,
MUST be the final ranking. The module MUST NOT expose a `fuse_with_graph`
function, a `GRAPH_RESERVED_SLOTS` constant, or a `GraphHit` dataclass, and
MUST NOT reserve any slot of the final top-`limit` for a channel other than
those two lists.

(Previously: `fuse_with_graph(fts_hits, vec_hits, graph_hits, *, limit)`
layered a seeded personalized-PageRank list on top of the two-list base,
additively — it could contribute only `concept_id`s absent from the FTS+dense
pool, into `min(GRAPH_RESERVED_SLOTS, limit // 2)` reserved slots at the
tail, and could neither promote nor demote a concept the base already
contained. That shape was itself the fix for an even earlier design, where
the graph was folded into the same `Σ 1/(K_RRF + rank)` sum and reshuffled
what FTS and dense had already found without ever contributing a concept of
its own — 0 in 26 promotions over 10 questions.

Bounding the channel is what made it countable, and counting it is what
ended it. Two A/B measurements, 10 questions each, taken with the channel
already bounded: on a 21-node/23-edge graph every question's ranking changed
and the SAME concept, `concepts/document-skills`, was the contribution on 6
of 10 questions — MCP origin, BigQuery, agent building and productionizing
alike. On a 27-node/38-edge graph the concentration fell to 4 of 10 across 7
distinct concepts, but per-question judgement was 7 harmful, 3 neutral, 0
beneficial. Asked "When did MCP originate?", the graph evicted
`sources/mcp-origin` — the document containing the answer — to insert
`concepts/document-skills`; it did the same to `sources/10-mcp` on a question
about which protocol BigQuery belongs to.

THE DEFECT IS THE RANKING FUNCTION, NOT THE TYPED GRAPH. Seeded personalized
PageRank ranks by GLOBAL CENTRALITY, a property of the corpus rather than of
the question. A larger graph changes WHICH central node wins the reserved
slot; it does not stop the slot costing a base hit, and it does not turn
centrality into relevance. The typed graph is deliberately retained
elsewhere — contradiction-candidate derivation reads typed edges and caught a
planted contradictory pair at confidence 1.00. What would justify a graph
channel returning is a DIFFERENT ranking function — traversal from the
question's own matched concepts along typed edges — proposed and measured on
its own terms, never as a revert of this requirement.)

#### Scenario: Fusion exposes no graph surface

- GIVEN the `retrieval/fusion` module
- WHEN its public names are inspected
- THEN `fuse_with_graph`, `GRAPH_RESERVED_SLOTS`, and `GraphHit` are all
  absent, and `fuse` accepts exactly `fts_hits` and `vec_hits`

#### Scenario: The top-`limit` is entirely FTS+dense

- GIVEN a bundle whose typed graph would rank some concept highly by
  centrality, and that concept is absent from both the FTS and the dense hit
  list
- WHEN the caller fuses and slices to `limit`
- THEN the result is exactly the first `limit` entries of
  `fuse(fts_hits, vec_hits)`, and the graph-only concept does not appear
