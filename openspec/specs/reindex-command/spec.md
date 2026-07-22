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
embedded/cache-hit/pruned/skipped/embed-failed counts — including whether the
prune pass was skipped due to a walk error — and exit 0.
(Previously: `reindex` opened and wrote only `vectors.db`; the summary line
carried no prune-skip indicator, and `embed_failed` was surfaced solely via
the stderr re-run notice, not the primary stdout tally.)

#### Scenario: Successful run prints a summary and exits 0

- GIVEN an initialized workspace with a reachable Ollama server
- WHEN `openkos reindex` runs
- THEN it prints embedded/cache-hit/pruned/skipped/embed-failed counts and
  exits 0

#### Scenario: Run outside a workspace refuses

- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos reindex` runs
- THEN it exits non-zero with a clear stderr message and no raw traceback

#### Scenario: Summary reports when the prune pass was skipped

- GIVEN a bundle subtree that raises a directory-scan error during this run
- WHEN `openkos reindex` runs
- THEN the printed summary states that the prune pass was skipped for this
  run, distinct from a run where zero concepts qualified for pruning

#### Scenario: Zero embed failures still show the counter

- GIVEN a run where every document embeds successfully (embed_failed == 0)
- WHEN `openkos reindex` prints its stdout summary
- THEN the summary includes `0 embed-failed`, matching the always-shown
  convention already used for `0 skipped`

#### Scenario: Nonzero embed failures surface in both the stdout tally and the stderr notice

- GIVEN a run where one or more documents fail to embed (embed_failed > 0)
- WHEN `openkos reindex` completes
- THEN the stdout summary reports the nonzero `embed-failed` count as part of
  the complete tally
- AND the stderr re-run call-to-action notice is printed separately and
  unchanged, so the two signals remain distinct — a factual stdout count vs.
  an actionable stderr prompt

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
re-embedding and upsert. WHEN this run's stored `embedding_model` tag is
absent or differs from the current `model_tag` (Embedding-Model Tag Gate),
this per-doc comparison MUST be bypassed entirely for the vector pass —
every discovered, readable doc is treated as changed and re-embedded,
regardless of its content_hash.
(Previously: the content_hash comparison was the only gate; no model-tag
condition could override it.)

#### Scenario: Unchanged content_hash is a cache-hit with zero Ollama calls

- GIVEN a doc whose stored `vector_meta.content_hash` matches its current
  on-disk hash, and the stored model tag matches `model_tag`
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

#### Scenario: Model-tag mismatch overrides an otherwise-matching content_hash

- GIVEN a doc whose `content_hash` already matches `vector_meta`, but the
  stored `embedding_model` tag differs from `model_tag`
- WHEN `reindex` runs
- THEN that doc is re-embedded and upserted despite the matching hash

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
Additionally, `reindex` MUST catch lock-contention `sqlite3.OperationalError`
raised at ANY write surface of the three on-disk stores (vectors, FTS,
graph) — store open, `upsert_many`/prune commit, or `BEGIN IMMEDIATE` —
discriminated by `exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY,
sqlite3.SQLITE_LOCKED)`, NOT by message substring, and exit 1 with the SAME
uniform "another process holds the workspace lock; wait and retry" message
for all three stores. A non-lock `OperationalError` MUST NOT be swallowed by
this catch; it keeps its existing (generic operational-error) handling.
(Previously: only the graph ladder caught `sqlite3.Error` for a locked
`graph.db`; the vectors/FTS ladder had no lock-contention catch and a locked
`vectors.db`/`fts.db` produced a raw traceback instead of a clean exit 1.)

#### Scenario: Ollama unreachable exits 1 with a clear message

- GIVEN Ollama is not reachable
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

#### Scenario: Vector extension unavailable exits 1 with a clear message

- GIVEN `sqlite-vec` cannot be loaded
- WHEN `openkos reindex` runs
- THEN it prints a clear stderr message and exits 1

#### Scenario: Locked vectors.db exits 1 with the retry message, no traceback

- GIVEN a concurrent process holds a write lock on `vectors.db` past
  `busy_timeout`
- WHEN `openkos reindex` runs and hits `sqlite3.OperationalError` with
  errorcode `SQLITE_BUSY`/`SQLITE_LOCKED` at store open, upsert, or commit
- THEN it prints the uniform lock-contention message to stderr and exits 1,
  with no raw traceback

#### Scenario: Locked fts.db, including at BEGIN IMMEDIATE, exits 1 with the retry message

- GIVEN a concurrent process holds a write lock on `fts.db` past
  `busy_timeout`, including at the `BEGIN IMMEDIATE` step of
  `write_fts_index`
- WHEN `openkos reindex` runs and hits the same lock-contention
  `OperationalError`
- THEN it prints the uniform lock-contention message to stderr and exits 1,
  with no raw traceback

#### Scenario: Locked graph.db exits 1 with the SAME uniform message

- GIVEN a concurrent process holds a write lock on `graph.db` past
  `busy_timeout`
- WHEN `openkos reindex` runs and hits the lock-contention
  `OperationalError`
- THEN it prints the SAME uniform lock-contention message used for
  vectors/FTS, and exits 1 with no raw traceback

#### Scenario: A non-lock operational error is not mislabeled as lock contention

- GIVEN a store write raises `sqlite3.OperationalError` whose errorcode is
  NOT `SQLITE_BUSY`/`SQLITE_LOCKED`
- WHEN `openkos reindex` runs
- THEN it exits 1 via the existing generic operational-error handling, not
  the lock-contention message

#### Scenario: query command behavior is unaffected

- GIVEN this change is applied
- WHEN `openkos query "<question>"` runs, including against a locked store
- THEN its observable behavior (degrade-and-continue via
  `_open_*_or_degrade`) is identical to before this change

### Requirement: Per-Doc Embed Failure Is Isolated, Not Fatal

`reindex` MUST embed each queued document individually (per-doc grain,
`embedder.embed([text])` per doc) rather than as one whole-batch call. WHEN
an individual doc's embed call raises the generic transient `OllamaError`
(the HTTP-400 EOF class) and the retry budget (`llm-client`) is exhausted,
`reindex` MUST catch that failure and increment a dedicated
`ReindexReport.embed_failed` tally — never embedded, never pruned this run,
and DISTINCT from the existing `ReindexReport.skipped` count (which stays
reserved for permanent read/parse/decode failures; the two counts MUST NOT
be conflated) — then continue processing every remaining queued doc. A
transient embed failure MUST NOT abort the run: `reindex` MUST still
perform its single end-of-run commit (covering every successfully embedded
doc plus any pruning, unchanged atomic-commit contract) and MUST exit 0.

WHEN an individual doc's embed call instead raises `OllamaUnavailable`
(server unreachable) or `OllamaModelNotFound` (configured model not
installed), `reindex` MUST NOT treat it as a per-doc failure — an
environment that cannot serve any embed is not a transient per-input
failure. `reindex` MUST re-raise it and let it propagate to the existing
"Error Ladder Mirrors `query`" requirement unchanged: a clear stderr
message and exit 1, with no further queued docs processed after the raise.
Per-doc isolation applies ONLY to the generic transient `OllamaError`,
never to these two fatal subclasses.

`ReindexReport.embedded` MUST equal the count of docs successfully embedded
this run.

#### Scenario: One poison doc among many survives as a partial-progress run

- GIVEN a batch of 10 queued docs where doc #4's embed call raises the
  transient generic `OllamaError` after exhausting the retry budget and the
  other 9 succeed
- WHEN `openkos reindex` runs
- THEN the 9 survivors are embedded, upserted, and committed;
  `ReindexReport.embedded` is `9`; `ReindexReport.embed_failed` is `1`; and
  the process exits `0`

#### Scenario: Survivors are committed and immediately queryable

- GIVEN the same partial-failure run as above
- WHEN `openkos query "<question>"` runs immediately afterward against one
  of the 9 successfully embedded concepts
- THEN that concept is retrievable via dense search — its vector was part
  of the run's single end-of-run commit, not discarded

#### Scenario: Every queued doc transiently fails leaves an empty embed pass, not a crash

- GIVEN every queued doc's embed call raises the transient generic
  `OllamaError` after exhausting the retry budget
- WHEN `openkos reindex` runs
- THEN `ReindexReport.embedded` is `0`, `ReindexReport.embed_failed` equals
  the number of queued docs, no exception propagates, and the process
  exits `0`

#### Scenario: Unreachable Ollama mid-embed-loop is fatal, not a per-doc skip

- GIVEN Ollama becomes unreachable partway through the per-doc embed loop
  (some docs already embedded successfully, `OllamaUnavailable` raised on
  the next queued doc)
- WHEN `openkos reindex` runs
- THEN it does NOT count that doc as `embed_failed`, does NOT proceed to
  the remaining queued docs, prints the existing clear stderr message, and
  exits `1` — exactly the "Error Ladder Mirrors `query`" behavior for an
  unreachable server, unaffected by per-doc isolation

#### Scenario: Missing embedding model mid-embed-loop is fatal, not a per-doc skip

- GIVEN the configured embedding model is not installed and
  `OllamaModelNotFound` is raised while embedding a queued doc
- WHEN `openkos reindex` runs
- THEN it does NOT count that doc as `embed_failed`, prints the existing
  clear stderr message, and exits `1`, exactly as the existing "Error
  Ladder Mirrors `query`" requirement specifies for a missing model

### Requirement: Reindex Surfaces An Actionable Re-Run Notice On Embed-Failure Skips

WHEN `ReindexReport.embed_failed > 0` — one or more docs were skipped
specifically because their embed call transiently failed (the generic
`OllamaError` EOF class) after the retry budget was exhausted — `reindex`
MUST print a distinct, actionable stderr notice stating that this run is
INCOMPLETE and advising the user to run `openkos reindex` again to
complete it. This notice keys ONLY on `embed_failed`, NEVER on the existing
`skipped` count (permanent unreadable/parse/decode failures): an
`embed_failed` doc is transient and self-healing (a re-run gives it another
chance once Ollama recovers), whereas a `skipped` doc is not (re-running
without fixing the source file does not help), and the two MUST NOT be
conflated in this notice. The same notice MUST also fire when a partial
embed failure occurs during a model-change run (see the Embedding-Model Tag
Gate requirement below): the store then transiently holds a mix of
new-model (survivor) and old-model (failed) vectors until a later run
reaches `skipped == 0 AND embed_failed == 0`; the user MUST be told the
reindex is incomplete rather than left to discover the mixed state
silently. A run whose embed loop instead hits a FATAL error
(`OllamaUnavailable`/`OllamaModelNotFound`, see the Per-Doc Embed Failure Is
Isolated, Not Fatal requirement) exits 1 before reaching this notice — the
notice applies only to a run that completes with exit 0.

#### Scenario: Embed-failure skip prints the actionable re-run notice

- GIVEN a run where `ReindexReport.embed_failed >= 1` after exhausting
  retries on at least one doc, and every other queued doc succeeded
- WHEN `openkos reindex` completes
- THEN stderr contains a notice stating the run is incomplete and advising
  the user to run `openkos reindex` again

#### Scenario: An ordinary unreadable-file skip does not print the embed-failure notice

- GIVEN a run where `ReindexReport.skipped >= 1` (a doc could not be read
  or its frontmatter could not be parsed) and `ReindexReport.embed_failed`
  is `0`
- WHEN `openkos reindex` completes
- THEN stderr does NOT contain the embed-failure re-run notice (the
  existing unreadable-file diagnostics remain unchanged)

#### Scenario: Model-switch run with a partial embed failure prints the same notice

- GIVEN a model-change run (`model_tag` differs from the stored tag) where
  one doc's embed transiently fails after retries and the rest succeed
- WHEN `openkos reindex` completes
- THEN the new `model_tag` is NOT persisted this run (per the
  Embedding-Model Tag Gate requirement), survivors are committed on the new
  model, the failed doc's vector remains on the old model, and stderr
  contains the same actionable incomplete-run notice

### Requirement: Embedding-Model Tag Gate Forces Full Re-Embed On Mismatch

At the start of the vector reindex pass, `reindex()` MUST read the stored
`embedding_model` tag from `vectors.db`'s `meta` table and compare it against
the explicit `model_tag` param passed in for this run. If the stored tag is
absent OR differs from `model_tag`, the vector-store pass for this run MUST
behave as if `force=True` (bypass the content_hash cache gate; every
discovered, readable doc is queued for re-embedding via the existing
`upsert_many` DELETE+INSERT path — no vec0 DROP), and after the embed pass
completes, the new `model_tag` MUST be persisted as the stored tag ONLY WHEN
this run's `skipped` count is `0` AND its `embed_failed` count is `0`. WHEN
one or more queued docs were left un-(re)embedded this run — via a
permanent `skipped` (unreadable/parse/decode) OR a transient `embed_failed`
(embed EOF exhausted retries; see the Per-Doc Embed Failure Is Isolated,
Not Fatal requirement) — the tag MUST NOT be persisted, so the NEXT run
with the same `model_tag` still sees the mismatch and re-forces the full
re-embed, giving the previously-unhealed doc(s) another chance; this
repeats until one run finally reaches `skipped == 0 AND embed_failed == 0`,
at which point the tag is persisted and the store is no longer transiently
mixed-model. This gate is independent of the `--force` CLI flag (either can
trigger the same force-mode behavior) and MUST NOT affect the
`_reindex_fts`/graph pass, which stays gated solely by the bundle-manifest
hash.

#### Scenario: Model mismatch forces full re-embed regardless of content_hash

- GIVEN a `vectors.db` with a stored tag `'model-a'` and every doc's
  content_hash already matching `vector_meta`
- WHEN `reindex()` runs with `model_tag='model-b'`
- THEN every discovered doc is re-embedded and upserted, and the stored tag
  becomes `'model-b'`

#### Scenario: Absent tag (pre-slice vectors.db) forces one re-embed then self-heals

- GIVEN a `vectors.db` created before this change, with no `meta` table row
  for `embedding_model`
- WHEN `reindex()` runs once with `model_tag='model-a'`
- THEN every discovered doc is re-embedded this run, the stored tag becomes
  `'model-a'`, and the NEXT `reindex()` run with the same `model_tag` is
  purely incremental (content_hash gate governs normally)

#### Scenario: Matching tag leaves the content_hash gate unchanged

- GIVEN a stored tag equal to the current `model_tag`
- WHEN `reindex()` runs
- THEN cache-hit/changed/new classification for each doc follows the
  existing content_hash comparison exactly as before this change

#### Scenario: Model-tag mismatch does not trigger an FTS/graph rebuild

- GIVEN a stored tag that differs from `model_tag`, and a bundle whose
  documents are otherwise unchanged
- WHEN `reindex()` runs
- THEN the FTS and graph derived indexes are NOT rebuilt by this gate (only
  the bundle-manifest hash, unaffected by the model tag, governs their
  rebuild)

#### Scenario: Any left-behind doc during a model-change run withholds the tag and self-heals

- GIVEN a model-change run where one doc is left un-embedded this run —
  either a permanent `skipped` (unreadable/parse/decode) or a transient
  `embed_failed` (embed EOF exhausted retries) — and the rest succeed
- WHEN `reindex()` completes this run
- THEN the stored tag remains the OLD (or absent) value, NOT `model_tag`,
  and the NEXT `reindex()` call with the same `model_tag` re-forces a full
  re-embed of every doc (`model_changed` stays `True`) until a run finally
  reaches `skipped == 0 AND embed_failed == 0`, at which point the tag is
  persisted

#### Scenario: Partial embed failure during a model change leaves a transient mixed-model store

- GIVEN a model-change run where some docs succeed on the new model and one
  transiently fails (`embed_failed`) and keeps its old-model vector
- WHEN that run completes
- THEN the store transiently contains both new-model (survivor) and
  old-model (failed) vectors simultaneously, `query` retrieval is
  unaffected (dense search does not depend on the stored tag), and the
  mixed state is surfaced to the user via the actionable re-run notice
  rather than left silent

### Requirement: `reindex()` Accepts An Explicit Model Tag Parameter

`state.reindex.reindex()` MUST accept an explicit string parameter (the
current `cfg.embedding_model` value) used solely to compare against and
update the stored `embedding_model` tag. The `Embedder` Protocol MUST NOT
gain a model-identity accessor — the tag flows only through this explicit
param, never through the embed-only seam.

#### Scenario: CLI wires the configured model into reindex

- GIVEN `cfg.embedding_model` resolved from `openkos.yaml`
- WHEN `openkos reindex` invokes the orchestrator
- THEN that exact string is passed as the model-tag param, and the
  `Embedder` Protocol's method surface is unchanged

### Requirement: No Retrieval Consumer Introduced

`reindex` MUST NOT alter `query` command or `retrieval/answer.py` behavior;
it only populates `vectors.db`.

#### Scenario: query command behavior is unchanged

- GIVEN this change is applied
- WHEN the existing `query` command runs
- THEN its observable behavior is identical to before this change
