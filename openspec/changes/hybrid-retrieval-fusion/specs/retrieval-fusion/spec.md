# Retrieval Fusion Specification

## Purpose

`retrieval/fusion.py` is a pure, zero-I/O reciprocal-rank-fusion (RRF) helper.
It takes a `list[FtsHit]` and a `list[VecHit]` — each already ordered by its
own retriever (`FtsHit` ascending by score, `VecHit` ascending by distance) —
and returns one ordered list of `concept_id`s, ranked by combined position
alone. Magnitudes (`score`, `distance`) are never read; only rank position
matters.

## Non-Goals

Weighted or normalized score fusion; distance-to-similarity conversion;
graph/link ranking as a third input; truncation to a caller `limit` (the
caller truncates the returned list); any I/O or config access.

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
