# Derived Index Cache Specification

## Purpose

`derived-index-cache` persists the FTS and graph projections to on-disk
SQLite storage under `.openkos/`, mirroring the shipped `vectors.db`
writer/reader split so `query`/`answer()` never rebuild these indexes per
invocation. Each store records the bundle manifest hash it was built from,
which gates refresh: any bundle change makes the store stale, and the next
refresh updates exactly the documents that were added, changed or removed,
falling back to a whole rebuild whenever the per-document update cannot be
trusted.

## Non-Goals

Caching personalized PageRank results (PPR is per-query seed-dependent, not
cacheable -- only the query-independent graph BUILD is cached); an
`embedding_model` tag in `vector_meta` (out of scope here); any lazy
auto-refresh at query time (query never writes a derived index).

## Requirements

### Requirement: On-Disk Persistence Of Derived Indexes

The system MUST persist the FTS and graph projections to on-disk SQLite
storage under `.openkos/` (e.g. `fts.db`, `graph.db`, or one `derived.db`),
written only by the paths that refresh them -- `reindex`, the refresh that
closes every bundle-writing verb, the daemon's maintenance pass, and `purge`
-- and surviving across process exit, mirroring `vectors.db`'s lifecycle.

#### Scenario: Reindex writes indexes that survive process exit

- GIVEN an initialized workspace with a bundle
- WHEN `openkos reindex` completes
- THEN a subsequent, separate `openkos query` process reads FTS and graph
  results without rebuilding either index

#### Scenario: No derived index exists before the first reindex

- GIVEN a freshly initialized workspace that has never run `reindex`
- WHEN the workspace is inspected
- THEN no on-disk FTS or graph derived index exists under `.openkos/`

### Requirement: Engine State Is Created Owner-Only

The system MUST create `.openkos/` and `bundle/.state/` (and any directory
created beneath `bundle/.state/`) with mode `0700`, and every SQLite store under
`.openkos/` with mode `0600` before its first write, so the store's `-wal` and
`-shm` sidecars carry the same restriction. The mode MUST NOT depend on the
process umask being restrictive. `bundle/`, `raw/`, and concept documents are
the user's own files and MUST NOT have their modes changed. A directory or file
that already exists MUST be left as found.

#### Scenario: A new store is unreadable by other accounts

- GIVEN a process with a permissive umask and no `.openkos/` directory
- WHEN a derived store is first opened
- THEN `.openkos/` has mode `0700`, the store file and its WAL sidecars have
  mode `0600`, and the workspace root's mode is unchanged

#### Scenario: A sidecar directory is private but the bundle is not touched

- GIVEN a `bundle/` directory with mode `0755`
- WHEN the first merge ledger or decision sidecar is written
- THEN `bundle/.state/` has mode `0700` and `bundle/` still has mode `0755`

### Requirement: Bundle-Manifest-Hash Cache Key

The cache key MUST be a digest computed over the sorted set of
`(concept_id, content_hash)` pairs for every discovered document in the
bundle, stored in a meta table. Reusing the `content_hash`
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

### Requirement: Consumers Read Persisted Indexes Read-Only

Any consumer of the persisted FTS or graph index (namely `query`/`answer()`)
MUST open it read-only and MUST NEVER write to it; only the refresh paths named above write.
Every read-only open of a derived store MUST go through one opener that
percent-encodes the store path before building the `mode=ro` URI, so a
workspace path containing `#`, `?` or `%` opens the same file it names (and the
staleness probe reports a freshly rebuilt store as fresh there).

#### Scenario: A store under a URI-special path reads as fresh

- GIVEN a store rebuilt under a directory named `a#b`, `a?b` or `a%20b`
- WHEN the staleness probe or any read-only opener opens it
- THEN it reads the store's stored manifest hash and reports it fresh

#### Scenario: An edited document stays invisible to query until the next refresh

- GIVEN a document is edited after the last refresh, and no refresh has run
  since
- WHEN `openkos query "<question>"` runs
- THEN it answers from the persisted (pre-edit) index content -- `query`
  performs no manifest recomputation or comparison of its own -- and only a
  subsequent refresh picks up the edit, mirroring how the dense
  (`vectors.db`) index already stays stale until its next refresh

#### Scenario: Query process never writes to the derived index

- GIVEN a workspace with a persisted FTS and graph index
- WHEN `openkos query "<question>"` runs
- THEN neither the FTS nor the graph on-disk index file is modified by that
  run

### Requirement: Each Store Records A Per-Document Manifest

`fts.db` and `graph.db` MUST each store, alongside the existing
`manifest_hash`, the `(concept_id, content_hash)` pair of every document the
store was last built from, written in the same transaction as the store's
rows. The existing `manifest_hash` MUST remain the staleness gate and MUST
still be the digest of exactly that pair set.

#### Scenario: The recorded pairs reproduce the stored digest

- GIVEN a store written by a refresh
- WHEN the digest of its recorded pairs is computed
- THEN it equals the store's `manifest_hash`

### Requirement: Per-Document Update With Whole-Rebuild Fallback

WHEN the current bundle's manifest hash differs from a store's
`manifest_hash`, the refresh MUST diff the current pairs against the
recorded ones into added, changed, and removed documents, and MUST update
only those documents' rows. The result MUST be identical in content to a
whole rebuild of the same bundle. For the graph store that MUST include:
removing a changed or removed document's outgoing edges; re-resolving, for
every added document, the links and typed relations from other documents
that name it (a link to a document that does not exist is not stored, so
it must be recovered when the target appears); dropping edges into a
removed document; and recomputing the proximity-candidate pass, whose
ranking and ceiling are global. The refresh MUST fall back to a whole
rebuild when the store has no recorded pairs, when the recorded pairs do
not reproduce the stored `manifest_hash`, when the store's schema version
differs from the code's, when a forced rebuild is requested, or when any
part of the per-document update fails.

A store whose schema version differs from the code's MUST NOT be reported
`unchanged` by the refresh even when the bundle's manifest hash matches, since
that hash does not change with the code that indexed the bundle; the refresh
rebuilds it. The staleness advisory read by `query`, `status` and `next` MUST
report such an `fts.db` as stale.

#### Scenario: One edited document updates one document's rows

- GIVEN a store built from a bundle of many documents
- WHEN exactly one document's content changes and the store is refreshed
- THEN only that document's rows are rewritten, and the store's content
  equals a whole rebuild of the bundle

#### Scenario: A new document recovers inbound links

- GIVEN document A links to `/concepts/b.md`, which did not exist at the last
  refresh
- WHEN `concepts/b.md` is created and the graph store is refreshed
- THEN the edge from A to B exists, as it would after a whole rebuild

#### Scenario: A store without recorded pairs rebuilds once

- GIVEN a store with a `manifest_hash` but no recorded pairs
- WHEN it is refreshed after a bundle change
- THEN it is rebuilt whole and records its pairs, and the next refresh is
  per-document

#### Scenario: An untrustworthy manifest falls back

- GIVEN a store whose recorded pairs do not reproduce its `manifest_hash`
- WHEN it is refreshed
- THEN it is rebuilt whole

### Requirement: The Staleness Probe Stays Read-Only

The advisory staleness probe used by `query`, `status`, and `next` MUST
keep comparing only the stored `manifest_hash` with the current one, MUST
NOT read or compare the recorded pairs, and MUST NOT write.

#### Scenario: status does not diff documents

- GIVEN a stale store
- WHEN `openkos status` runs
- THEN it reports the store stale without computing a per-document diff
