# Delta for LLM Client

## Non-Goals Update (for archive-time merge into main spec's `## Non-Goals`)

Remove "embeddings" from the excluded list — embeddings are now in scope via
`Embedder`/`OllamaClient.embed()`. The Non-Goals sentence becomes: "This spec
does not define: streaming (`stream:true`/NDJSON); tool/function calling;
retries or backoff; `/api/generate` or any non-Ollama provider; any CLI
command; changes to `ingest`, `forget`, or `config`'s schema beyond an
optional host key, `model`, and `embedding_model`." Persistence, vector
storage, and retrieval fusion remain explicitly out of scope for this client.

## ADDED Requirements

### Requirement: Embedder Protocol Produces Order-Preserving Vectors

An `Embedder` Protocol MUST expose `embed(texts: Sequence[str]) ->
list[list[float]]`, returning exactly one 1024-float vector per input
string, in the same order as the input. An empty input sequence MUST
return an empty list without any network call.

#### Scenario: One vector per input, order preserved

- GIVEN three distinct input strings
- WHEN `embed(texts)` is called
- THEN it returns three vectors, each of length 1024, in the same order
  as the inputs

#### Scenario: Empty input returns empty output

- GIVEN an empty sequence of texts
- WHEN `embed(texts)` is called
- THEN it returns an empty list and makes no network call

### Requirement: OllamaClient Embeds Text Via /api/embed

`OllamaClient.embed(texts)` MUST POST `{"model": <tag>, "input": [...]}` to
`POST {base_url}/api/embed`. It MUST parse the response defensively,
accepting either an `embeddings` key or a singular `embedding` key
(response shape varies across Ollama versions), and MUST validate that
each resulting row is a list of exactly 1024 floats. A response that is
not valid JSON, lacks a recognized vector key, or contains a row of the
wrong length or non-numeric values MUST raise `OllamaError`.

#### Scenario: Successful embed call returns validated vectors

- GIVEN a reachable server that returns 200 with an `embeddings` array of
  1024-float rows
- WHEN `embed(texts)` is called
- THEN it returns those rows unchanged, as `list[list[float]]`

#### Scenario: Singular embedding key is accepted

- GIVEN a server response using a singular `embedding` key instead of
  `embeddings`
- WHEN `embed(texts)` is called
- THEN the response is parsed successfully using the same validation rules

#### Scenario: Wrong-dimension or malformed row raises OllamaError

- GIVEN a 200 response whose vector rows are not all length-1024 floats,
  or whose body is not valid JSON, or lacks any recognized vector key
- WHEN `embed(texts)` is called
- THEN `OllamaError` is raised rather than returning malformed data

### Requirement: Ollama Unavailable During Embedding Raises A Typed Error

A connection refused or request timeout during `embed(texts)` MUST raise
`OllamaUnavailable`, following the same mapping `chat()` uses. No raw
transport exception MUST escape.

#### Scenario: Server not running raises OllamaUnavailable

- GIVEN no Ollama server is reachable at the configured base URL
- WHEN `embed(texts)` is called
- THEN `OllamaUnavailable` is raised and no low-level transport exception
  escapes

### Requirement: Embedder Is Testable Without A Live Ollama Server

The `Embedder` seam MUST be injectable/mockable, mirroring how `answer()`
already injects a fake `LLMBackend`, so callers can exercise embedding
consumers hermetically without a running Ollama process or a real model.

#### Scenario: Fake Embedder satisfies the Protocol structurally

- GIVEN a test double returning deterministic fixed-length vectors
- WHEN it is passed anywhere an `Embedder` is expected
- THEN it satisfies the Protocol with no changes to the consuming code

### Requirement: Embedding Client Remains Config-Free

`embed()` MUST take the model tag as a caller-supplied argument, identical
in spirit to `chat()`. The `llm` package MUST NOT import `openkos.config`.

#### Scenario: llm package has no config import

- GIVEN the `llm` package's source
- WHEN the no-config-import check runs
- THEN no module under `llm/` imports `openkos.config`

### Requirement: Embedding Model Defaults Independently From The Chat Model

Configuration MUST expose `embedding_model`, defaulting to
`qwen3-embedding:0.6b`, distinct from the chat `DEFAULT_MODEL`. For this
slice, `embedding_model` is a code-level default only — it is NOT added to
`openkos.yaml.template` and has no per-workspace override or CLI flag; that
remains a separate future slice.

#### Scenario: Default embedding model differs from the chat default

- GIVEN a freshly read configuration with no embedding-specific override
- WHEN `embedding_model` is inspected
- THEN it equals `qwen3-embedding:0.6b`, distinct from the chat `model`
  default `qwen3:8b`
