# Delta for Retrieval Fusion

## ADDED Requirements

### Requirement: Optional Third Graph List Fuses Identically To The First Two

`fuse(fts_hits, vec_hits, graph_hits=None)` MUST accept an optional third
argument `graph_hits: list[GraphHit] | None`. A new `GraphHit` dataclass MUST
carry `concept_id` plus a rank-determining field (score or rank), mirroring
`FtsHit`/`VecHit`'s shape. WHEN `graph_hits` is provided (non-`None`), the
system MUST fold it into `fused(cid)` using the SAME `1 / (K_RRF + rank_i(cid))`
formula and rank-only purity as the other two lists — no re-sorting by
`score`, no distinct weighting for the graph list.

#### Scenario: A concept ranked well in all three lists outranks partial overlap

- GIVEN `cid_A` is rank 1 in the FTS list, the dense list, AND the graph list,
  while `cid_B` is rank 1 in the FTS list only
- WHEN `fuse(fts_hits, vec_hits, graph_hits)` is called
- THEN `cid_A`'s fused score (`3 × 1/61`) is strictly greater than `cid_B`'s
  (`1/61`), so `cid_A` is ordered first

#### Scenario: A graph-only concept surfaces in the fused output

- GIVEN `cid` appears in `graph_hits` only — absent from both `fts_hits` and
  `vec_hits`
- WHEN `fuse(fts_hits, vec_hits, graph_hits)` is called
- THEN `cid` appears in the output, contributing exactly `1 / (K_RRF +
  rank_graph(cid))` to its fused score

#### Scenario: No truncation of the graph pool

- GIVEN a `graph_hits` list of 10 entries
- WHEN `fuse(...)` is called
- THEN every distinct `concept_id` from `graph_hits` is represented in the
  fused output — the caller, not `fuse`, slices to `limit`

### Requirement: Omitted Graph List Is Byte-Identical To Prior Behavior

WHEN `graph_hits` is omitted or explicitly `None`, `fuse` MUST produce the
exact same ordered output as the current two-list `fuse(fts_hits, vec_hits)`
contract — no behavior change for existing callers.

#### Scenario: Default call matches pre-graph output

- GIVEN a fixed `fts_hits` and `vec_hits` pair
- WHEN `fuse(fts_hits, vec_hits)` is called (no third argument) and
  `fuse(fts_hits, vec_hits, graph_hits=None)` is called separately
- THEN both calls return byte-identical ordered output, matching what
  `fuse(fts_hits, vec_hits)` returned before `graph_hits` existed

### Requirement: Three-List Fusion Stays Deterministic

`fuse` with three lists MUST remain pure and deterministic: identical
`fts_hits`, `vec_hits`, and `graph_hits` inputs MUST produce identical ordered
output across repeated calls, with ties still broken by `concept_id` ascending.

#### Scenario: Same three-list inputs yield the same output every call

- GIVEN a fixed `fts_hits`, `vec_hits`, and `graph_hits` triple
- WHEN `fuse(fts_hits, vec_hits, graph_hits)` is called twice
- THEN both calls return byte-identical ordered output

#### Scenario: Ties across three lists still break by concept_id ascending

- GIVEN two `concept_id`s produce numerically equal fused scores after
  folding in `graph_hits`
- WHEN `fuse(...)` is called
- THEN the lexicographically smaller `concept_id` is ordered first
