# Vector Store Specification

## Purpose

`state/vectorstore.py` is the on-disk scaffolding for dense retrieval: a
guarded SQLite `sqlite-vec` extension loader, an injectable `VectorStore`
seam, and an idempotent `vectors.db` schema. It has no CLI command and
performs no embedding storage or query in this slice.

## Non-Goals

This spec does not define: vec0 upsert/query data flow; a `reindex`/backfill
verb; `content_hash` invalidation; RRF fusion; any change to
`retrieval/answer.py`; `numpy`; or `.openkos/` creation during `init` (the
directory is opened/created lazily by this module, not by `init`).

## Requirements

### Requirement: VectorStore Protocol Seam

The system MUST define a `VectorStore` Protocol so a fake implementation is
structurally injectable wherever a real store is expected, mirroring the
`Embedder`/`LLMBackend` seams.

#### Scenario: Fake store satisfies the Protocol

- GIVEN a fake object implementing the `VectorStore` Protocol's methods
- WHEN it is passed to a function typed against `VectorStore`
- THEN mypy accepts it and no runtime adapter is required

### Requirement: VecUnavailable Typed Error

The system MUST define `VecUnavailable(RuntimeError)`, raised whenever the
`sqlite-vec` extension cannot be loaded, mirroring `FtsUnavailable`.

#### Scenario: Unavailable extension raises a typed error, not a crash

- GIVEN a connection that cannot load extensions (`enable_load_extension`
  missing or raising)
- WHEN the loader attempts to enable `sqlite-vec`
- THEN it raises `VecUnavailable`, never an unhandled exception

### Requirement: Guarded Extension Loader

The loader MUST call `enable_load_extension(True)`, then `sqlite_vec.load(conn)`,
then `enable_load_extension(False)`, on a connection capable of extension
loading, leaving the connection ready to create the `vec0` virtual table.

#### Scenario: Extension loads and the vec0 table becomes creatable

- GIVEN a connection supporting `enable_load_extension`
- WHEN the guarded loader runs
- THEN `sqlite-vec` loads, extension loading is re-disabled, and
  `CREATE VIRTUAL TABLE ... USING vec0(...)` succeeds

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

### Requirement: On-Disk Location Via WorkspaceLayout

`WorkspaceLayout` MUST expose `openkos_dir` (`<root>/.openkos`) and
`vectors_db_path` (`<root>/.openkos/vectors.db`), and the store MUST open or
create its database at that resolved path.

#### Scenario: Paths resolve under the workspace root

- GIVEN a `WorkspaceLayout` with a given `root`
- WHEN `openkos_dir` and `vectors_db_path` are read
- THEN they resolve to `<root>/.openkos` and `<root>/.openkos/vectors.db`

### Requirement: Content Hash Helper

The system MUST provide a `content_hash` helper returning a stable sha256
hex digest for given bytes/text, for later use as an invalidation key.

#### Scenario: Identical content hashes identically

- GIVEN the same bytes hashed twice
- WHEN `content_hash` is called
- THEN both calls return the identical hex digest

#### Scenario: Different content hashes differently

- GIVEN two different byte strings
- WHEN each is passed to `content_hash`
- THEN the two digests differ

### Requirement: No CLI Surface, No Init-Time Side Effect

This module MUST NOT be invoked by `init` or any CLI command in this slice,
and `WorkspaceLayout` gaining `openkos_dir`/`vectors_db_path` MUST NOT change
`init`'s file-creation behavior.

#### Scenario: init behavior is unchanged

- GIVEN this module and the new `WorkspaceLayout` properties exist
- WHEN `openkos init` runs
- THEN it creates the same files as before, and no `.openkos/` directory or
  `vectors.db` is created

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

### Requirement: Protocol Extended Additively

`VectorStore` MUST gain `upsert`/`query` method signatures while the previous
`close()`-only lifecycle contract remains valid; existing call sites needing only
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

### Requirement: Generic Meta Table And Embedding-Model Tag

`open_vector_store` MUST create a generic `meta(key TEXT PRIMARY KEY, value
TEXT NOT NULL)` table in `vectors.db`, idempotently, mirroring the `meta`
table pattern in `state/derived.py`. The system MUST provide accessors to
read the stored `embedding_model` tag (returning `None` when absent) and to
write/replace it (`INSERT OR REPLACE`). This table is distinct from
`vector_meta` (the per-`concept_id` content-hash table) — it is a singleton
key/value store, not one row per document.

#### Scenario: Meta table is created idempotently

- GIVEN a `vectors.db` opened for the first time
- WHEN `open_vector_store` runs, then runs again on the same path
- THEN the `meta` table exists in both cases and re-opening is a no-op

#### Scenario: Absent tag reads as None

- GIVEN a freshly created `vectors.db` with no `embedding_model` row
- WHEN the tag accessor is read
- THEN it returns `None`, not an error

#### Scenario: Writing the tag persists across a reopen

- GIVEN a `vectors.db` whose tag accessor wrote `'qwen3-embedding:0.6b'`
- WHEN the store is closed and `open_vector_store` reopens the same path
- THEN reading the tag returns `'qwen3-embedding:0.6b'`

#### Scenario: Writing a new tag replaces the old one

- GIVEN a `vectors.db` with a stored tag `'model-a'`
- WHEN the tag accessor writes `'model-b'`
- THEN reading the tag returns `'model-b'` and exactly one `meta` row exists
  for the `embedding_model` key

### Requirement: Model Tag Is Independent Of The Bundle-Manifest Hash

The `embedding_model` tag stored in `vectors.db`'s `meta` table MUST NOT be
read, written, or otherwise referenced by `derived.bundle_manifest_hash` or
`derived.reindex_gate` — the FTS/graph rebuild gate stays computed purely
from `(concept_id, content_hash)` pairs, unaffected by which embedding model
is configured.

#### Scenario: Changing the embedding model does not alter the manifest hash

- GIVEN a bundle whose documents are unchanged
- WHEN the stored `embedding_model` tag differs from the current
  `cfg.embedding_model`
- THEN `derived.bundle_manifest_hash(bundle_dir)` returns the identical
  digest it would return with no model change

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
