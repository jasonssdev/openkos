# Derived Index Cache Specification

## Purpose

`derived-index-cache` persists the FTS and graph projections that `reindex`
builds to on-disk SQLite storage under `.openkos/`, mirroring the shipped
`vectors.db` writer/reader split so `query`/`answer()` never rebuild these
indexes per invocation. A bundle-manifest-hash meta table gates whole-index
rebuild: any bundle change invalidates the cache and triggers a full rebuild
on the next `reindex` run.

## Non-Goals

Per-doc incremental FTS/graph updates (graph edges are cross-document,
making safe incremental maintenance unsafe); caching personalized PageRank
results (PPR is per-query seed-dependent, not cacheable — only the
query-independent graph BUILD is cached); an `embedding_model` tag in
`vector_meta` (deferred to its own slice); any lazy auto-refresh at query
time (query never writes).

## Requirements

### Requirement: On-Disk Persistence Of Derived Indexes

The system MUST persist the FTS and graph projections to on-disk SQLite
storage under `.openkos/` (e.g. `fts.db`, `graph.db`, or one `derived.db`),
written ONLY by `reindex`, surviving across process exit — mirroring
`vectors.db`'s lifecycle.

#### Scenario: Reindex writes indexes that survive process exit

- GIVEN an initialized workspace with a bundle
- WHEN `openkos reindex` completes
- THEN a subsequent, separate `openkos query` process reads FTS and graph
  results without rebuilding either index

#### Scenario: No derived index exists before the first reindex

- GIVEN a freshly initialized workspace that has never run `reindex`
- WHEN the workspace is inspected
- THEN no on-disk FTS or graph derived index exists under `.openkos/`

### Requirement: Bundle-Manifest-Hash Cache Key

The cache key MUST be a digest computed over the sorted set of
`(concept_id, content_hash)` pairs for every discovered document in the
bundle, stored in a meta table. Reusing the shipped Slice 2b `content_hash`
primitive, ANY added, edited, or removed document MUST change this digest.

#### Scenario: Unchanged bundle reuses the cached index

- GIVEN a bundle whose documents are unchanged since the last `reindex`
- WHEN `reindex` runs again
- THEN the computed manifest hash matches the stored one and neither the
  FTS nor the graph index is rebuilt

#### Scenario: Any document change invalidates the cache

- GIVEN a bundle where one document was added, edited, or removed since the
  last `reindex`
- WHEN `reindex` runs
- THEN the computed manifest hash differs from the stored one, triggering a
  rebuild

### Requirement: Manifest Hash Is Order-Stable

The digest MUST sort `concept_id`s before hashing so that document discovery
order (walk order) never affects the resulting hash.

#### Scenario: Walk order does not affect the manifest hash

- GIVEN the same set of documents discovered in two different orders across
  two runs
- WHEN the manifest hash is computed each time
- THEN both runs produce an identical digest

### Requirement: Whole-Index Rebuild On Manifest Change

WHEN the computed manifest hash differs from the one stored in the meta
table, the system MUST rebuild the ENTIRE FTS and graph index (no partial
or per-doc patch), matching the existing build logic's correctness
guarantees.

#### Scenario: Single-document edit triggers a full rebuild

- GIVEN a bundle with many documents where exactly one document's content
  changed
- WHEN `reindex` runs
- THEN the entire FTS and graph index is rebuilt, not incrementally patched

#### Scenario: Edited doc stays invisible to query until the next reindex

- GIVEN a document is edited after the last `reindex` run, and `reindex` has
  not run again since
- WHEN `openkos query "<question>"` runs
- THEN it answers from the persisted (pre-edit) index content — `query`
  performs no manifest recomputation or comparison of its own — and only a
  subsequent `reindex` run picks up the edit, mirroring how the shipped
  dense (`vectors.db`) index already stays stale until the next `reindex`

### Requirement: Consumers Read Persisted Indexes Read-Only

Any consumer of the persisted FTS or graph index (namely `query`/`answer()`)
MUST open it read-only and MUST NEVER write to it; only `reindex` writes.

#### Scenario: Query process never writes to the derived index

- GIVEN a workspace with a persisted FTS and graph index
- WHEN `openkos query "<question>"` runs
- THEN neither the FTS nor the graph on-disk index file is modified by that
  run
