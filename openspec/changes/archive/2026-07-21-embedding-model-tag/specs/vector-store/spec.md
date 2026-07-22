# Delta for Vector Store

## ADDED Requirements

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
