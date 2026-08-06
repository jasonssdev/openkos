# Retrieval Fusion Specification

## Purpose

`retrieval/fusion.py` is a pure, zero-I/O rank-fusion helper. `fuse()` takes
a `list[FtsHit]` and a `list[VecHit]` — each already ordered by its own
retriever (`FtsHit` ascending by score, `VecHit` ascending by distance) — and
returns one ordered list of `concept_id`s via reciprocal rank fusion (RRF),
ranked by combined position alone. Magnitudes (`score`, `distance`) are never
read; only rank position matters. That two-list ranking is the BASE, and
nothing else may permute it.

`fuse_with_graph()` layers the graph channel on top of that base ADDITIVELY:
the graph may only contribute concepts the two retrievers never saw, into a
bounded number of reserved slots at the tail of the final top-`limit`.

## Non-Goals

Weighted or normalized score fusion; distance-to-similarity conversion;
graph/link ranking as a third RRF input (see "The Graph Channel Is Additive,
Never A Reordering"); truncation of `fuse()`'s own output to a caller `limit`
(the caller truncates); any I/O, model call, or config access.

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

### Requirement: Pure Function, Deterministic, Zero I/O

`fuse` MUST perform no file, network, or database access, and MUST return
the identical ordered output for identical inputs across repeated calls.

#### Scenario: Same inputs yield the same output every call

- GIVEN a fixed `fts_hits` and `vec_hits` pair
- WHEN `fuse(...)` is called twice
- THEN both calls return byte-identical ordered output

### Requirement: The Graph Channel Is Additive, Never A Reordering

The graph list MUST NOT be folded into `fuse`'s RRF sum. `fuse` MUST take
exactly two lists; the graph channel MUST be applied by a separate
`fuse_with_graph(fts_hits, vec_hits, graph_hits, *, limit)`, which returns
the final top-`limit` `concept_id` list. A `GraphHit` dataclass MUST carry
`concept_id` plus a rank-determining field (score or rank), mirroring
`FtsHit`/`VecHit`'s shape; only its list POSITION is read, never its score
magnitude. `fuse_with_graph` MUST obey three rules, in order:

1. `fuse(fts_hits, vec_hits)` is the BASE ranking and MUST NOT be permuted —
   the base concepts that survive MUST appear in exactly that relative order.
2. `graph_hits` MUST contribute ONLY `concept_id`s absent from the FTS+dense
   pool, filling up to `min(GRAPH_RESERVED_SLOTS, limit // 2)` reserved slots
   at the TAIL of the returned list, in `graph_hits` order.
3. A `concept_id` already present in the FTS+dense pool MUST draw NOTHING
   from `graph_hits` — the graph can neither promote nor demote it.

(Previously: `fuse(fts_hits, vec_hits, graph_hits=None)` folded an optional
third graph list into the same `Σ 1/(K_RRF + rank)` sum. That was strictly
negative on a measured corpus (issue #402): a concept present only in the
graph list at graph rank 1 scores `1/61 ≈ 0.0164` and could never outscore
one present in both FTS and dense at rank 10 in each, `2/70 ≈ 0.0286` — so
the graph never contributed a concept of its own, 0 in 26 promotions over 10
questions, while its list still reshuffled the concepts the other two had
already found and evicted real hits. Tuning `K_RRF` does not fix that
asymmetry; a concept in two lists still beats one in a single list at
comparable ranks.)

#### Scenario: A concept the FTS+dense pool already contains gets no graph contribution

- GIVEN `cid` is in `fts_hits` at a rank below the final `limit`, and is
  rank 1 in `graph_hits`
- WHEN `fuse_with_graph(fts_hits, vec_hits, graph_hits, limit=5)` is called
- THEN `cid` is neither promoted into the top-`limit` nor allowed to displace
  any base concept ranked above it

#### Scenario: A graph-only concept claims a reserved tail slot

- GIVEN `cid` appears in `graph_hits` only — absent from both `fts_hits` and
  `vec_hits` — and the base ranking is at least `limit` long
- WHEN `fuse_with_graph(..., limit=5)` is called
- THEN the output is the first `limit - 1` base concepts, in base order,
  followed by `cid` in the reserved tail slot

#### Scenario: The base ranking is never permuted

- GIVEN a `graph_hits` list that ranks several base concepts in an order
  contradicting the base ranking
- WHEN `fuse_with_graph(...)` is called
- THEN the base concepts present in the output appear in exactly their
  `fuse(fts_hits, vec_hits)` relative order

### Requirement: The Graph's Reserved Slots Are Bounded And Named

The reserved-slot count MUST be a named, module-level `Final[int]`
(`GRAPH_RESERVED_SLOTS`), never a silent literal, and MUST additionally be
capped at `limit // 2` so the base ranking keeps at least half of any
`limit`. Reserved slots are a CAP, not a quota: a base ranking shorter than
`limit` MUST NOT be padded with more than the reserved number of graph
concepts.

#### Scenario: A large graph pool still claims only the reserved slots

- GIVEN a `graph_hits` list of 10 concepts, none of them in the FTS+dense
  pool
- WHEN `fuse_with_graph(..., limit=5)` is called
- THEN exactly `GRAPH_RESERVED_SLOTS` of them appear in the output

#### Scenario: A limit of 1 reserves nothing

- GIVEN a non-empty `graph_hits` of concepts absent from the pool
- WHEN `fuse_with_graph(..., limit=1)` is called
- THEN the output is the single top base concept — `limit // 2` is `0`, so
  the graph claims no slot

#### Scenario: A repeated graph-only concept occupies one slot

- GIVEN the same `concept_id` appears twice within `graph_hits` and is absent
  from the pool
- WHEN `fuse_with_graph(...)` is called
- THEN it appears exactly once in the output

### Requirement: A Graph That Adds Nothing Changes Nothing

WHEN `graph_hits` is empty, or every one of its entries is already inside the
FTS+dense pool, `fuse_with_graph` MUST return output byte-identical to
`fuse(fts_hits, vec_hits)[:limit]`.

#### Scenario: Empty graph list matches two-list fusion

- GIVEN `graph_hits = []`
- WHEN `fuse_with_graph(fts_hits, vec_hits, [], limit=5)` is called
- THEN the output equals `fuse(fts_hits, vec_hits)[:5]` exactly

#### Scenario: An all-pooled graph list matches two-list fusion

- GIVEN every `graph_hits` entry's `concept_id` is already in `fts_hits` or
  `vec_hits`
- WHEN `fuse_with_graph(..., limit=5)` is called
- THEN the output equals `fuse(fts_hits, vec_hits)[:5]` exactly

### Requirement: Graph-Augmented Fusion Stays Pure And Deterministic

`fuse_with_graph` MUST perform no file, network, or database access and MUST
invoke no model — it is ranking arithmetic only — and MUST return identical
ordered output for identical inputs across repeated calls.

#### Scenario: Same inputs yield the same output every call

- GIVEN a fixed `fts_hits`, `vec_hits`, `graph_hits`, and `limit`
- WHEN `fuse_with_graph(...)` is called twice
- THEN both calls return byte-identical ordered output
