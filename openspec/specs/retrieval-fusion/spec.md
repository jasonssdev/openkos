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
bundle state) MUST have its accumulated fused score scaled by
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

### Requirement: Source Documents Share The Displayed Top-N

The displayed top-`limit` of a fused ranking MUST hold at most
`max(1, limit // 2)` ids under `sources/` (whole raw-text documents, which
match both retrievers and would otherwise crowd out the compiled concepts that
name the answer). Sources beyond the cap MUST be deferred behind the remaining
ids and MUST backfill any slot those leave empty, in their original rank
order. It re-ranks and never excludes; a ranking with no `sources/` id MUST
equal a plain `ranked[:limit]`. The cited set stays exactly the set placed in
context.

#### Scenario: Compiled concepts outrank surplus Sources

- GIVEN a ranking of four `sources/` ids followed by three compiled concepts
  and `limit` 5
- WHEN the top-`limit` is selected
- THEN it is the first two Sources followed by the three concepts

#### Scenario: Deferred Sources backfill empty slots

- GIVEN three `sources/` ids, one compiled concept and one more Source, and
  `limit` 5
- WHEN the top-`limit` is selected
- THEN all five ids are returned, the concept ahead of the deferred Sources

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

The typed graph is retained elsewhere — contradiction-candidate derivation
reads typed edges — but it is not a retrieval channel: seeded personalized
PageRank ranks by global centrality, a property of the corpus rather than of
the question, so a reserved slot for it always costs a real FTS or dense hit.

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
