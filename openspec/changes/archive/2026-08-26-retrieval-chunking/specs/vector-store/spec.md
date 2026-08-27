# Delta for Vector Store

## MODIFIED Requirements

### Requirement: Idempotent Vector Schema

Opening the store MUST run `CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING
vec0(embedding float[1024], concept_id TEXT, chunk_index INTEGER,
content_hash TEXT)`, a second `vec0` table `doc_vectors(embedding
float[1024], concept_id TEXT)` holding one derived row per document, and the
companion `vector_meta` table keyed for `content_hash` lookups, now
additionally carrying `chunk_count INTEGER`. `concept_id` in `vectors` MUST
remain the document id and MUST NEVER be a composite key encoding chunk
position. Running schema creation twice MUST be a no-op.
(Previously: `vectors` had no `chunk_index` column and stored exactly one
row per `concept_id`; there was no `doc_vectors` table; `vector_meta` had no
`chunk_count` column.)

#### Scenario: Re-opening an existing (post-migration) store is a no-op migration

- GIVEN a `vectors.db` already containing the chunk-aware schema
- WHEN the store is opened again
- THEN no error occurs and the existing schema and data are unchanged

#### Scenario: Companion table supports hash-keyed lookup

- GIVEN an opened store
- WHEN the companion table is queried by `content_hash`
- THEN it returns matching rows without touching either `vec0` table

#### Scenario: vector_meta carries chunk_count per document

- GIVEN a document upserted with 5 chunks
- WHEN its `vector_meta` row is read
- THEN `chunk_count` equals `5`

### Requirement: k-NN Query Data Flow

The store MUST provide `query(embedding, k)` returning up to `k`
`(concept_id, distance)` pairs, ordered by ascending `(distance,
concept_id)`, with AT MOST ONE pair per `concept_id`. Internally it MUST
over-fetch `k × max(chunk_count)` rows from `vectors`, keep each
`concept_id`'s minimum distance, and return at most `k` documents. Ties at
the boundary between two different documents' rows MUST be broken
deterministically in Python by `(distance, concept_id)`, never left to
vec0's insertion-order fallback.
(Previously: `query` returned up to `k` `(concept_id, distance)` pairs
directly from a one-row-per-`concept_id` table, ordered solely by ascending
distance from vec0.)

#### Scenario: Query returns at most one hit per document

- GIVEN a store where one document has 4 chunk rows within the top-k window
- WHEN `query(embedding, k)` runs
- THEN at most one `(concept_id, distance)` pair for that document is
  returned, and its distance is the minimum among its chunk rows

#### Scenario: Query against an empty store returns no results

- GIVEN a store with no upserted vectors
- WHEN `query` runs
- THEN it returns an empty result, not an error

#### Scenario: A tie between two documents at the k-th boundary is resolved deterministically

- GIVEN two different documents' chunk rows tied at the exact same distance,
  straddling the k-th position after collapse
- WHEN `query(embedding, k)` is called twice against identical store state
- THEN both calls return the same document at that position, chosen by
  ascending `concept_id`, never by vec0's insertion order

#### Scenario: A k-th-boundary tie can still drop a whole document (documented residue)

- GIVEN more chunk rows tie exactly at vec0's internal k-th cut than the
  over-fetch admits
- WHEN `query(embedding, k)` runs
- THEN which document's rows survive vec0's cut is vec0's choice, not this
  store's — the residue is documented, not eliminated

## ADDED Requirements

### Requirement: Legacy-Shape Store Is Migrated, Not Silently Reused

`open_vector_store` MUST detect a pre-chunking `vectors` table (one lacking
`chunk_index`) by probing for the column, and on detection MUST drop and
recreate `vectors` under the chunk-aware schema and MUST CLEAR every row
from `vector_meta`. This runs inside the store's own schema-creation commit.

#### Scenario: A legacy 3-column store is migrated on open

- GIVEN a `vectors.db` created before this change (no `chunk_index` column)
- WHEN `open_vector_store` opens it
- THEN `vectors` is dropped and recreated with the chunk-aware schema, and
  `vector_meta` has zero rows afterward

#### Scenario: Clearing vector_meta prevents a permanently empty store

- GIVEN a legacy store were migrated WITHOUT clearing `vector_meta`
- WHEN the next `reindex` runs and reads the surviving hash cache
- THEN every document would read as a cache-hit against a table holding
  zero actual vectors, and the store would never recover — this requirement
  exists to prevent exactly that failure

### Requirement: Multi-Chunk Upsert Is Atomic And Orphan-Free

`upsert_many` MUST accept, per document, a sequence of chunk vectors. For
each document it MUST delete every existing row across `vectors`,
`doc_vectors`, and `vector_meta` for that `concept_id` (matched only on
`concept_id`, never on chunk count), then insert the new chunk rows, one
`doc_vectors` row, and one `vector_meta` row carrying the new `chunk_count`.
A single `DELETE ... WHERE concept_id = ?` MUST remove all of a document's
chunk rows.

#### Scenario: Re-embedding at a different chunk count leaves no orphans

- GIVEN a document currently stored with 12 chunk rows
- WHEN it is re-embedded and upserted with 5 chunks
- THEN exactly 5 `vectors` rows remain for that `concept_id`, and zero of
  the original 12 survive

#### Scenario: One DELETE removes an entire document's chunk rows

- GIVEN a document stored with N chunk rows
- WHEN `DELETE FROM vectors WHERE concept_id = ?` runs for it
- THEN all N rows are removed in that one statement

### Requirement: Neighbors Reads The Derived Document Vector And Preserves The Never-Raises Degrade

`neighbors()` MUST read from `doc_vectors` instead of `vectors`. A document
with no `doc_vectors` row (zero stored chunks) MUST cause `neighbors()` to
return `[]`, exactly as the existing "row is None" branch already does — no
new failure path is introduced.

#### Scenario: A zero-chunk document degrades to no neighbors

- GIVEN a document with zero chunk rows and therefore no `doc_vectors` row
- WHEN `neighbors(concept_id, k)` is called for it
- THEN it returns `[]` without raising

#### Scenario: Neighbors ranks by the derived document vector

- GIVEN two documents each with a `doc_vectors` row
- WHEN `neighbors()` is called
- THEN ranking reflects distance between derived document vectors, not any
  single chunk's vector
