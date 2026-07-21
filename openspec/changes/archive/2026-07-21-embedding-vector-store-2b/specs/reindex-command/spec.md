# Reindex Command Specification

## Purpose

`openkos reindex` is the first writer of `vectors.db`: a CLI verb backed by a
new `state/reindex.py` orchestrator that walks the bundle, embeds each
document through the `Embedder` seam, and upserts into the vector store — an
incremental backfill gated by `content_hash` so unchanged docs never re-embed.

## Non-Goals

This spec does not define: RRF/hybrid fusion; any change to
`retrieval/answer.py` or the `query` command; graph traversal; chunk-level
embedding; embedding-text composition beyond raw doc text; a `doctor`
subcommand wiring (doctor remains read-only and never calls `reindex`).

## Requirements

### Requirement: CLI Verb Is Thin Wiring

`openkos reindex` MUST: run `require_workspace`, read config, call
`open_vector_store(vectors_db_path)`, invoke the `state/reindex.py`
orchestrator, then print a summary of embedded/cache-hit/pruned counts and
exit 0.

#### Scenario: Successful run prints a summary and exits 0

- GIVEN an initialized workspace with a reachable Ollama server
- WHEN `openkos reindex` runs
- THEN it prints embedded/cache-hit/pruned counts and exits 0

#### Scenario: Run outside a workspace refuses

- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos reindex` runs
- THEN it exits non-zero with a clear stderr message and no raw traceback

### Requirement: Bundle Walk And Concept Identity

The orchestrator MUST discover documents via the existing `okf._iter_docs`
walk (no new walker) and key each by `concept_id` = bundle-relative path
minus `.md`, identical to the identity used by `FtsHit`/`Citation`/`forget`.
Reserved filenames (`index.md`, `log.md`) MUST be excluded, mirroring
`fts.build_index`.

#### Scenario: Discovered doc's identity matches forget's identity

- GIVEN a document at `bundle/concepts/stoicism.md`
- WHEN `reindex` discovers it
- THEN its `concept_id` is `concepts/stoicism`

#### Scenario: Reserved files are never embedded

- GIVEN a bundle containing `index.md` and `log.md`
- WHEN `reindex` runs
- THEN neither file is embedded or upserted

### Requirement: Content-Hash Cache Gate

For each discovered doc, the orchestrator MUST compare its current
`content_hash` against `vector_meta`. An unchanged hash MUST be a cache-hit
that makes zero Embedder calls; a changed or absent hash MUST trigger
re-embedding and upsert.

#### Scenario: Unchanged content_hash is a cache-hit with zero Ollama calls

- GIVEN a doc whose stored `vector_meta.content_hash` matches its current
  on-disk hash
- WHEN `reindex` runs (no `--force`)
- THEN the fake Embedder records zero calls for that doc, and its stored
  vector is unchanged

#### Scenario: Changed content re-embeds and upserts

- GIVEN a doc whose current hash differs from `vector_meta`
- WHEN `reindex` runs
- THEN the Embedder is called for that doc and its vector/hash are upserted

#### Scenario: New doc is embedded and upserted

- GIVEN a doc with no `vector_meta` row
- WHEN `reindex` runs
- THEN it is embedded, its vector is inserted, and `vector_meta` gains a row

### Requirement: Prune Removed Documents

The orchestrator MUST prune from `vectors` and `vector_meta` any
`concept_id` present in `vector_meta` whose source `.md` file no longer
exists on disk.

#### Scenario: Deleted doc is pruned from the store

- GIVEN a `vector_meta` row for a concept whose file was deleted from the
  bundle
- WHEN `reindex` runs
- THEN that concept's rows are removed from both `vectors` and `vector_meta`

### Requirement: `--force` Bypasses The Cache Gate

`reindex --force` MUST re-embed and upsert every discovered document
regardless of a matching `content_hash`.

#### Scenario: `--force` re-embeds unchanged docs

- GIVEN every doc's hash already matches `vector_meta`
- WHEN `openkos reindex --force` runs
- THEN the Embedder is called once per discovered doc

### Requirement: Error Ladder Mirrors `query`

`reindex` MUST catch `OllamaError`-family exceptions and `VecUnavailable`,
printing a clear message to stderr and exiting 1, never a raw traceback.

#### Scenario: Ollama unreachable exits 1 with a clear message

- GIVEN Ollama is not reachable
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

#### Scenario: Vector extension unavailable exits 1 with a clear message

- GIVEN `sqlite-vec` cannot be loaded
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

### Requirement: No Retrieval Consumer Introduced

`reindex` MUST NOT alter `query` command or `retrieval/answer.py` behavior;
it only populates `vectors.db`.

#### Scenario: query command behavior is unchanged

- GIVEN this change is applied
- WHEN the existing `query` command runs
- THEN its observable behavior is identical to before this change
