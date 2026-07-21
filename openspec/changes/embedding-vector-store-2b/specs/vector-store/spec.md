# Delta for Vector Store

Scope note: this delta moves vec0 `upsert`/`query` data flow and
`content_hash` invalidation from the 2a spec's Non-Goals into scope, and adds
four hygiene/coverage requirements from Engram #1397. 2a's open-path
(`open_vector_store`, `VectorStoreDB.close`) signatures stay byte-stable —
the Protocol is extended additively only. Test hygiene note: the stale
`# --- Review Correction 2 ...` label in `test_vectorstore.py` (line ~476)
MUST be renamed to a durable name; this is a test-file edit with no runtime
scenario.

## ADDED Requirements

### Requirement: Vector Upsert Data Flow

The store MUST provide `upsert(concept_id, embedding, content_hash)` that
serializes `embedding` via `serialize_float32`, deletes any existing `vectors`
rows for `concept_id`, inserts the new row, and upserts the matching
`vector_meta` row.

#### Scenario: First upsert of a new concept inserts one row

- GIVEN a `concept_id` with no prior row
- WHEN `upsert` is called
- THEN exactly one `vectors` row and one `vector_meta` row exist for it

#### Scenario: Re-upsert replaces the prior vector

- GIVEN a `concept_id` already stored
- WHEN `upsert` is called again with a new embedding and hash
- THEN the old row is gone, exactly one row remains, and `vector_meta`
  reflects the new `content_hash`

### Requirement: k-NN Query Data Flow

The store MUST provide `query(embedding, k)` returning up to `k`
`(concept_id, distance)` pairs, ordered by ascending distance, via
`embedding MATCH ? AND k = ? ORDER BY distance` against `vectors`.

#### Scenario: Query returns nearest neighbors ordered by distance

- GIVEN a store containing multiple upserted vectors
- WHEN `query(embedding, k)` runs
- THEN it returns at most `k` `(concept_id, distance)` pairs in ascending
  distance order

#### Scenario: Query against an empty store returns no results

- GIVEN a store with no upserted vectors
- WHEN `query` runs
- THEN it returns an empty result, not an error

### Requirement: Protocol Extended Additively

`VectorStore` MUST gain `upsert`/`query` method signatures while the 2a
`close()`-only lifecycle contract remains valid; 2a call sites needing only
`close()` MUST continue to typecheck unchanged.

#### Scenario: Extended fake satisfies the Protocol

- GIVEN a fake implementing `close`, `upsert`, and `query`
- WHEN passed to a function typed against `VectorStore`
- THEN mypy accepts it with no runtime adapter

### Requirement: Single-Level Cleanup Invariant On Failed Open

A failed `open_vector_store` MUST remove only the artifacts that call itself
created (a newly-created `.openkos/` directory and/or `vectors.db` file) and
MUST NEVER remove or otherwise touch the enclosing workspace root or any
ancestor directory.

#### Scenario: Workspace root survives a failed open

- GIVEN an initialized workspace root containing other files, and no
  pre-existing `.openkos/`
- WHEN `open_vector_store` fails after creating `.openkos/`
- THEN `.openkos/` is removed but the workspace root and its other contents
  remain fully intact

### Requirement: Idempotent Double-Close

`VectorStoreDB.close()` MUST be safe to call more than once; a second call
MUST NOT raise.

#### Scenario: Second close is a no-op

- GIVEN an already-closed `VectorStoreDB`
- WHEN `close()` is called again
- THEN no exception is raised

### Requirement: Pre-Existing Database Survives A Failed Reopen

When `path` already exists before `open_vector_store` is called
(`db_preexisted=True`) and a later step in that call fails, the pre-existing
file MUST be left untouched — its bytes MUST be unchanged.

#### Scenario: Pre-existing vectors.db survives a failed reopen

- GIVEN a real, non-empty `vectors.db` file already on disk
- WHEN `open_vector_store` is called against it and a later step (extension
  load or schema DDL on the real connection) fails
- THEN the file still exists afterward with its original bytes unchanged
