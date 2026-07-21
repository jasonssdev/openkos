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
vec0(embedding float[1024], concept_id TEXT, content_hash TEXT)` plus a
companion plain table keyed for `content_hash` lookups; running this twice
MUST be a no-op.

#### Scenario: Re-opening an existing store is a no-op migration

- GIVEN a `vectors.db` already containing the `vectors` schema
- WHEN the store is opened again
- THEN no error occurs and the existing schema and data are unchanged

#### Scenario: Companion table supports hash-keyed lookup

- GIVEN an opened store
- WHEN the companion table is queried by `content_hash`
- THEN it returns matching rows without touching the `vec0` table

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
