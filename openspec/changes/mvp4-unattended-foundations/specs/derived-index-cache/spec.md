# Delta for Derived Index Cache

## REMOVED Requirements

### Requirement: Whole-Index Rebuild On Manifest Change

**Reason**: A single edited document cost a full FTS and graph rebuild on
the next refresh, which a scheduled maintenance pass would pay
continuously, and which is also the step that keeps a commit phase long
(ADR-0036). Replaced by "Per-Document Update With Whole-Rebuild Fallback",
which keeps the whole rebuild as the fallback path.

## ADDED Requirements

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

#### Scenario: A store from before this change rebuilds once

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
