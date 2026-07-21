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

### Requirement: Reindex Becomes Sole Writer Of FTS And Graph Derived Indexes

`reindex` MUST write, in addition to `vectors.db`, the on-disk FTS and graph
derived indexes under `.openkos/`, gated by the SAME bundle-manifest-hash
rebuild-on-change rule the vector store already uses via `content_hash`.
`query`/`answer()` MUST NEVER write to these on-disk stores; `reindex` is
their only writer.

#### Scenario: Reindex writes all three derived stores in one run

- GIVEN an initialized workspace with a bundle
- WHEN `openkos reindex` runs
- THEN `vectors.db`, the on-disk FTS index, and the on-disk graph index are
  all written or confirmed up-to-date by that single run

#### Scenario: Query never writes to a derived store

- GIVEN a workspace with persisted FTS/graph/vector indexes
- WHEN `openkos query "<question>"` runs
- THEN none of the three on-disk derived-index files are modified

### Requirement: WAL / Busy-Timeout PRAGMAs And Single-Commit-Per-Run

Every on-disk connection `reindex` opens (vectors, FTS, graph) MUST set
`PRAGMA journal_mode=WAL` and a `busy_timeout` at open, and `reindex` MUST
commit at most once per run across each store rather than once per
document, reducing write contention among the three on-disk writers.

#### Scenario: A single run performs one commit per store

- GIVEN a bundle with many changed documents
- WHEN `openkos reindex` runs
- THEN each on-disk store (vectors, FTS, graph) is committed exactly once
  for that run, not once per document

#### Scenario: WAL mode is active on every derived connection

- GIVEN `openkos reindex` has run at least once
- WHEN any of the three on-disk derived databases is inspected
- THEN its journal mode is WAL and a non-zero `busy_timeout` is configured

### Requirement: CLI Verb Is Thin Wiring

`openkos reindex` MUST: run `require_workspace`, read config, open the
on-disk `vectors.db`, FTS, and graph derived stores under `.openkos/`
(`open_vector_store` plus the FTS/graph store openers), invoke the
`state/reindex.py` orchestrator to write all three, then print a summary of
embedded/cache-hit/pruned/skipped counts — including whether the prune pass
was skipped due to a walk error — and exit 0.
(Previously: `reindex` opened and wrote only `vectors.db`; the summary line
carried no prune-skip indicator.)

#### Scenario: Successful run prints a summary and exits 0

- GIVEN an initialized workspace with a reachable Ollama server
- WHEN `openkos reindex` runs
- THEN it prints embedded/cache-hit/pruned/skipped counts and exits 0

#### Scenario: Run outside a workspace refuses

- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos reindex` runs
- THEN it exits non-zero with a clear stderr message and no raw traceback

#### Scenario: Summary reports when the prune pass was skipped

- GIVEN a bundle subtree that raises a directory-scan error during this run
- WHEN `openkos reindex` runs
- THEN the printed summary states that the prune pass was skipped for this
  run, distinct from a run where zero concepts qualified for pruning

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
exists on disk. WHEN `okf._walk_errors(bundle_dir)` reports one or more
directory-scan errors for this run, the orchestrator MUST skip the entire
prune pass for that run — no `concept_id` is removed — because an
unreadable subtree can make a still-existing document look absent from the
walk, and treating that absence as deletion would silently destroy a valid
vector. The embed and cache-hit passes MUST still run normally regardless
of walk errors. `ReindexReport` MUST additionally carry a `prune_skipped`
field distinguishing "prune ran and found nothing to prune" from "prune was
suppressed by a walk error", and the CLI summary MUST surface this
distinction to the user.
(Previously: any `concept_id` absent from the current walk was pruned
unconditionally, with no distinction between "genuinely deleted" and "walk
could not reach it"; `ReindexReport` had no field distinguishing a
skipped-by-walk-error prune pass from a prune pass that found nothing.)

#### Scenario: Deleted doc is pruned from the store

- GIVEN a `vector_meta` row for a concept whose file was deleted from the
  bundle, and the walk reports no directory-scan errors
- WHEN `reindex` runs
- THEN that concept's rows are removed from both `vectors` and `vector_meta`

#### Scenario: Walk error suppresses pruning for the whole run

- GIVEN a bundle subdirectory that raises a scandir `OSError` during the
  walk (e.g. permission denied), and that subdirectory holds a document
  whose `concept_id` already has a `vector_meta` row
- WHEN `reindex` runs
- THEN that `concept_id`'s row is NOT pruned from `vectors` or
  `vector_meta`, even though the walk did not see it this run
- AND the embed and cache-hit passes still complete normally for every
  document the walk did reach

#### Scenario: No walk errors preserves normal pruning behavior

- GIVEN a bundle whose walk completes with zero directory-scan errors
- WHEN `reindex` runs
- THEN pruning proceeds exactly as before this change, removing only
  `concept_id`s genuinely absent from the walk

#### Scenario: Walk-error prune-skip is observable in the report and CLI

- GIVEN a run whose walk reports a directory-scan error
- WHEN `reindex` completes
- THEN `ReindexReport.prune_skipped` reflects that suppression and the CLI
  summary states the prune pass was skipped for this run

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
