# Delta for Reindex Command

## MODIFIED Requirements

### Requirement: Composed Embed Text Replaces Raw-Bytes Embedding

For each discovered document, `reindex` MUST compose embed text from title,
description, tags, and body — the same composition `fts.py` uses to build
its own index text — and MUST split it into a header (title + description +
tags) and a body, embedding the body as one or more chunks per the
`embedding-chunking` capability's windowing contract, with the header
repeated on every chunk's embed text. This closes #554 (a document's own
content no longer truncates out) and closes #888 (a document exceeding the
embedder's window is no longer represented solely by its first chunk).
(Previously: each document composed to exactly ONE embed-text string,
embedded via exactly one `embedder.embed([text])[0]` call; a document whose
composed text exceeded the embedder's window was silently truncated by the
embedder itself.)

#### Scenario: Embed text matches FTS's field composition

- GIVEN a document with distinct title, description, tags, and body content
- WHEN `reindex` embeds that document
- THEN every chunk's embed text is composed from those same four fields,
  matching `fts.build_index`'s field shape

#### Scenario: A short document produces exactly one chunk

- GIVEN a document whose composed body fits within one chunking window
- WHEN `reindex` embeds it
- THEN exactly one embed call is made for that document

#### Scenario: A long document is embedded via multiple chunk calls, none of them truncating

- GIVEN a document whose body exceeds the embedder's window
- WHEN `reindex` embeds it
- THEN it issues more than one embed call, one per chunk, and every
  embeddable byte of the body is inside exactly one chunk

#### Scenario: A document with no ledger history embeds identically to before, content-wise

- GIVEN a document that was never a merge survivor
- WHEN `reindex` embeds it
- THEN its chunks' composed text still carries its title/description/tags/
  body content — no field is dropped by chunking

#### Scenario: A large-history survivor's own content still fits within its own chunks

- GIVEN a merge survivor whose ledger entries live outside this document's
  own frontmatter
- WHEN `reindex` embeds that survivor
- THEN its chunks' composed text is bounded to its own title/description/
  tags/body — no relocated ledger history is embedded

### Requirement: Per-Doc Embed Failure Is Isolated, Not Fatal

`reindex` MUST embed each queued document as a set of per-chunk embed calls
(per-document grain: one document's chunk loop is a single unit) rather
than as one whole-batch call across documents. WHEN any chunk's embed call
within a document's chunk loop raises the generic transient `OllamaError`
(the HTTP-400 EOF class) and the retry budget (`llm-client`) is exhausted,
`reindex` MUST treat the WHOLE document as failed: it MUST NOT upsert any
chunk, any `doc_vectors` row, or any `vector_meta` update for that document,
MUST leave that document's prior stored rows (if any) untouched, and MUST
increment a dedicated `ReindexReport.embed_failed` tally — never embedded,
never pruned this run, and DISTINCT from `ReindexReport.skipped`. A partial
mean over the surviving chunks of a partially-failed document MUST NEVER be
stored: doing so would type-check and count as one document while silently
storing a wrong vector beside a `content_hash` that reads as current on the
next run, making the wrongness permanent. A transient embed failure MUST
NOT abort the run: `reindex` MUST still perform its single end-of-run
commit covering every successfully embedded document plus any pruning, MUST
exit `0`, and MUST continue processing every remaining queued document.

WHEN any chunk's embed call instead raises `OllamaUnavailable`,
`OllamaModelNotFound`, OR `OllamaEmbeddingDimensionMismatch`, `reindex` MUST
NOT treat it as a per-document failure. `reindex` MUST re-raise it from the
chunk loop BEFORE the generic transient-error handler, letting it propagate
to the existing "Error Ladder Mirrors `query`" requirement unchanged: a
clear stderr message and exit `1`, with no further queued documents
processed after the raise.

`ReindexReport.embedded` MUST equal the count of documents successfully
embedded (every chunk succeeded) this run.
(Previously: embed grain was one document = one `embedder.embed([text])`
call; a chunked document now issues N calls, and this requirement adds the
all-or-nothing rule across those N calls plus the no-partial-vector
invariant.)

#### Scenario: One poison document among many survives as a partial-progress run

- GIVEN a batch of 10 queued documents where document #4 has one chunk (of
  several) whose embed call raises the transient generic `OllamaError`
  after exhausting the retry budget, and every other document's every chunk
  succeeds
- WHEN `openkos reindex` runs
- THEN the 9 survivors are embedded, upserted, and committed;
  `ReindexReport.embedded` is `9`; `ReindexReport.embed_failed` is `1`;
  document #4 has zero new rows written; and the process exits `0`

#### Scenario: A partially-failed document stores no partial document vector

- GIVEN a document with 5 chunks where chunk 3 fails after retries and
  chunks 1, 2, 4, 5 succeed
- WHEN `openkos reindex` runs
- THEN no `vectors`, `doc_vectors`, or `vector_meta` row is written or
  updated for that document this run, and its prior stored rows (if any)
  are unchanged

#### Scenario: Survivors are committed and immediately queryable

- GIVEN the same partial-failure run as above
- WHEN `openkos query "<question>"` runs immediately afterward against one
  of the 9 successfully embedded documents
- THEN that document is retrievable via dense search — its chunk rows were
  part of the run's single end-of-run commit, not discarded

#### Scenario: Every queued document transiently fails leaves an empty embed pass, not a crash

- GIVEN every queued document has at least one chunk whose embed call
  raises the transient generic `OllamaError` after exhausting the retry
  budget
- WHEN `openkos reindex` runs
- THEN `ReindexReport.embedded` is `0`, `ReindexReport.embed_failed` equals
  the number of queued documents, no exception propagates, and the process
  exits `0`

#### Scenario: Unreachable Ollama mid-chunk-loop is fatal, not a per-document skip

- GIVEN Ollama becomes unreachable partway through a document's chunk loop
  (some chunks already embedded, `OllamaUnavailable` raised on the next
  chunk)
- WHEN `openkos reindex` runs
- THEN it does NOT count that document as `embed_failed`, does NOT proceed
  to remaining queued documents, prints the existing clear stderr message,
  and exits `1`

#### Scenario: Missing embedding model mid-chunk-loop is fatal, not a per-document skip

- GIVEN the configured embedding model is not installed and
  `OllamaModelNotFound` is raised while embedding one of a queued
  document's chunks
- WHEN `openkos reindex` runs
- THEN it does NOT count that document as `embed_failed`, prints the
  existing clear stderr message, and exits `1`, exactly as the existing
  "Error Ladder Mirrors `query`" requirement specifies for a missing model

#### Scenario: Dimension mismatch mid-chunk-loop is fatal, not a per-document skip

- GIVEN the configured embedding model returns a wrong-length vector row
  and `OllamaEmbeddingDimensionMismatch` is raised while embedding one of a
  queued document's chunks
- WHEN `openkos reindex` runs
- THEN it does NOT count that document as `embed_failed`, does NOT proceed
  to the remaining queued documents, prints a clear stderr message
  identifying the failure as a permanent dimension mismatch (not "will
  retry next run"), and exits `1`

## ADDED Requirements

### Requirement: Reindex Discloses The Real Re-Embed Trigger, Not A False Model-Change Claim

WHEN `reindex` forces a full re-embed via the Embedding-Model Tag Gate, its
CLI summary MUST compare the stored effective tag and the current effective
tag by their `{model}#{composition}` parts and print exactly one of three
true statements: (1) if the model parts differ,
`embedding model changed (<old-model> -> <new-model>)`; (2) if the model
parts are equal and only the composition part differs,
`embed text composition changed (<old-comp> -> <new-comp>); your embedding
model is unchanged (<model>)`; (3) if no previous tag was stored,
`no embedding-model tag stored (fresh or dropped store)`. It MUST NEVER
compare the stored effective tag against the bare configured model name —
that comparison falsely reports a model change when only the embed-text
composition (e.g. chunking) changed. Every branch MUST also report
`embed_calls` over `embedded` documents.

#### Scenario: A composition-only bump reports composition, not model, change

- GIVEN a stored tag `bge-m3#compose-v1` and a current tag `bge-m3#chunk-v1`
- WHEN `reindex` forces the re-embed and prints its summary
- THEN it prints the composition-changed wording naming
  `compose-v1 -> chunk-v1` and states the embedding model `bge-m3` is
  unchanged, never "embedding model changed"

#### Scenario: A genuine model bump still reports a model change

- GIVEN a stored tag `bge-m3#chunk-v1` and a current tag
  `qwen3-embedding:0.6b#chunk-v1`
- WHEN `reindex` forces the re-embed and prints its summary
- THEN it prints the model-changed wording naming
  `bge-m3 -> qwen3-embedding:0.6b`

#### Scenario: A fresh or dropped store reports the absent-tag wording

- GIVEN no previous tag is stored
- WHEN `reindex` forces the re-embed and prints its summary
- THEN it prints the fresh-or-dropped-store wording, naming neither a model
  nor a composition change

#### Scenario: Every disclosure branch reports the chunk-multiplied call count

- GIVEN any of the three branches above
- WHEN `reindex` completes
- THEN the summary reports `embed_calls`, which for a chunked run exceeds
  the count of `embedded` documents whenever any document produced more
  than one chunk

(`purge`'s own pre-emptive quoting of this wording is `privacy-purge`'s
requirement, not this one — see the `privacy-purge` delta's MODIFIED
"Deferred-Reembed Warning On Success" for that scenario, which asserts on
`purge`'s output, not `reindex`'s.)
