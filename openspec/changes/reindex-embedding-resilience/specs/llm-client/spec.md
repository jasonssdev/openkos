# Delta for LLM Client

## ADDED Requirements

### Requirement: Transient Embed Failures Are Retried Before Propagating

`OllamaClient.embed(texts)` MUST retry a transient failure (e.g. an
EOF/connection-reset raised by the underlying transport) a bounded number
of times with backoff before propagating any exception. WHEN a retried
attempt succeeds, `embed(texts)` MUST return the result exactly as if no
failure had occurred first — the caller MUST observe no partial result, no
exception, and no distinguishable difference from a first-attempt success.
`OllamaModelNotFound` MUST NOT be retried — it cannot heal by retry — and
MUST raise immediately on its first occurrence. WHEN the retry budget is
exhausted without success for a retryable `OllamaError`-family exception,
`embed(texts)` MUST raise that exception to its caller; retries MUST NEVER
silently swallow a persistent failure at the client layer. This
exhausted-retry exception is what downstream callers catch and handle at
their own layer: `reindex`'s per-doc embed loop turns it into an
embed-failure `skipped` doc rather than a fatal abort (see the
reindex-command "Per-Doc Embed Failure Is Isolated, Not Fatal"
requirement), and `query`'s question-embed step turns it into a
`dense_degraded=True` FTS-only fallback (see the query-answer "Dense
Retrieval Degrades To FTS-Only" requirement).

#### Scenario: Transient failure followed by success is transparent to the caller

- GIVEN the transport raises a transient error on the first attempt and
  succeeds on a later attempt within the retry budget
- WHEN `embed(texts)` is called
- THEN it returns the validated vectors with no exception raised and no
  observable trace that a retry occurred

#### Scenario: Immediate success makes no retry attempt

- GIVEN the transport succeeds on the first attempt
- WHEN `embed(texts)` is called
- THEN exactly one request is made and the result is returned unchanged

#### Scenario: Exhausted retry budget raises to the caller

- GIVEN the transport fails on every attempt within the retry budget with a
  retryable `OllamaError`-family exception
- WHEN `embed(texts)` is called
- THEN it raises that exception after the final attempt, and no
  partial/fabricated result is returned

#### Scenario: OllamaModelNotFound is never retried

- GIVEN the transport raises `OllamaModelNotFound`
- WHEN `embed(texts)` is called
- THEN it raises immediately without consuming any retry attempt

## MODIFIED Requirements

### Requirement: Embedding Model Defaults Independently From The Chat Model

Configuration MUST expose `embedding_model`, defaulting to `bge-m3`,
distinct from the chat `DEFAULT_MODEL`. For this slice, `embedding_model`
is a code-level default only — it is NOT added to `openkos.yaml.template`
and has no per-workspace override or CLI flag; that remains a separate
future slice.
(Previously: default was `qwen3-embedding:0.6b`.)

#### Scenario: Default embedding model differs from the chat default

- GIVEN a freshly read configuration with no embedding-specific override
- WHEN `embedding_model` is inspected
- THEN it equals `bge-m3`, distinct from the chat `model` default `qwen3:8b`

#### Scenario: Default satisfies the fixed vector dimension contract

- GIVEN the default `embedding_model` is `bge-m3`
- WHEN `Embedder.embed(texts)` is called against it
- THEN each returned vector has length 1024, satisfying the existing
  `EMBED_DIM` contract unchanged

#### Scenario: Switching to the new default on an existing store triggers the re-embed gate

- GIVEN a `vectors.db` whose stored `embedding_model` tag is
  `qwen3-embedding:0.6b`
- WHEN `reindex` runs with the new default `model_tag='bge-m3'`
- THEN the existing Embedding-Model Tag Gate (`reindex-command` spec)
  forces one full re-embed and `ReindexReport.model_reembedded` is `True`
  for that run
