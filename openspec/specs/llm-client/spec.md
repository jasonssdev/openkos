# LLM Client Specification

## Purpose

`llm/` is a pure library seam for chat completion against a locally running
Ollama server: a `LLMBackend` Protocol plus a concrete `OllamaClient` that
POSTs `/api/chat` via stdlib `urllib`, mapping every failure mode to a typed
exception. It has no CLI command and no workspace effect; its only consumer
is the future `query` command.

## Non-Goals

This spec does not define: streaming (`stream:true`/NDJSON); tool/function
calling; retries or backoff; `/api/generate` or any non-Ollama provider;
any CLI command; changes to `ingest`, `forget`, or `config`'s schema
beyond an optional host key, `model`, and `embedding_model`. Persistence,
vector storage, and retrieval fusion remain explicitly out of scope for
this client.

## Requirements

### Requirement: Successful Chat Call Returns Assistant Text

`LLMBackend.chat(messages)` MUST POST the configured model and `messages` to
`POST {base_url}/api/chat` with `stream: false` and `think: false`, and MUST
return `message.content` from the response as a plain string.

#### Scenario: Chat call returns clean assistant text

- GIVEN an `OllamaClient` configured with a model tag and base URL
- WHEN `chat(messages)` is called and the server responds 200 with
  `{"message": {"role": "assistant", "content": "..."} , "done": true}`
- THEN the call returns that `content` string, with `stream:false` and
  `think:false` present in the request body sent

### Requirement: System And User Roles Supported

`messages` MUST support both `system` and `user` roles, each sent as
`{"role": ..., "content": ...}` entries in request order.

#### Scenario: System and user messages both forwarded

- GIVEN a message list containing one `system` and one `user` entry
- WHEN `chat(messages)` is called
- THEN the request body's `messages` array contains both entries, each with
  its original `role` and `content`, in the given order

### Requirement: Ollama Unavailable Raises A Typed Error

A connection refused or a request timeout MUST raise `OllamaUnavailable`.
No raw `urllib.error.URLError`, `socket.timeout`, or similar low-level
exception MUST ever propagate to the caller.

#### Scenario: Server not running raises OllamaUnavailable

- GIVEN no Ollama server is reachable at the configured base URL
- WHEN `chat(messages)` is called
- THEN `OllamaUnavailable` is raised and no `URLError` escapes

#### Scenario: Request timeout raises OllamaUnavailable

- GIVEN a configured, bounded request timeout
- WHEN the server does not respond within that timeout
- THEN `chat(messages)` raises `OllamaUnavailable` and does not hang
  indefinitely

### Requirement: Unknown Model Raises A Typed Not-Found Error

An HTTP 404 response whose body indicates the model tag was not found MUST
raise `OllamaModelNotFound`.

#### Scenario: Not-pulled model raises OllamaModelNotFound

- GIVEN a model tag that is not pulled on the Ollama server
- WHEN `chat(messages)` is called and the server responds 404 with
  `{"error": "model '<tag>' not found"}`
- THEN `OllamaModelNotFound` is raised

### Requirement: Other Failures Raise A Generic Typed Error

Any other non-200 HTTP response, or a 200 response whose body is not valid
JSON or lacks the expected `message.content` shape, MUST raise
`OllamaError`.

#### Scenario: Non-404 server error raises OllamaError

- GIVEN the server responds with a non-200 status other than 404
- WHEN `chat(messages)` is called
- THEN `OllamaError` is raised, carrying the server's error detail

#### Scenario: Malformed JSON response raises OllamaError

- GIVEN the server responds 200 with a body that is not valid JSON or is
  missing `message.content`
- WHEN `chat(messages)` is called
- THEN `OllamaError` is raised rather than an unhandled parsing exception

### Requirement: Model And Base URL Are Configurable

The model tag and base URL MUST be caller-supplied arguments, not
hard-coded. The base URL MUST default to `http://localhost:11434` when no
override is given, and MUST honor an explicit override (e.g. via
`OLLAMA_HOST` or an equivalent caller-supplied value).

#### Scenario: Default base URL used when no override given

- GIVEN an `OllamaClient` constructed with only a model tag
- WHEN `chat(messages)` is called
- THEN the request targets `http://localhost:11434/api/chat`

#### Scenario: Base URL override is honored

- GIVEN an `OllamaClient` constructed with an explicit base URL override
- WHEN `chat(messages)` is called
- THEN the request targets that overridden base URL, not the default

### Requirement: Testable Without A Live Ollama Server

The HTTP transport MUST be an injectable/mockable seam so unit tests can
exercise every success and error path without a running Ollama server.

#### Scenario: Full behavior covered with the HTTP layer mocked

- GIVEN a test double standing in for the HTTP transport
- WHEN it is configured to return each response shape (success, 404,
  other non-200, malformed body, connection error, timeout)
- THEN `chat(messages)` exhibits the corresponding documented behavior
  without any network call reaching a real Ollama process

### Requirement: List Installed Models

`OllamaClient` MUST provide `list_models()` returning the installed model
tags via `GET {host}/api/tags`. The method MUST read each installed entry
defensively, preferring a `model` field and falling back to a `name` field
when `model` is absent. A connection failure or timeout MUST raise
`OllamaUnavailable`; any other non-200 response or a 200 response whose
body is not valid JSON MUST raise `OllamaError` — following the same
error-mapping discipline as `chat()`. `list_models()` MUST remain
config-free: the `llm` package MUST NOT import `openkos.config`.

#### Scenario: Reachable server returns installed tags

- GIVEN an `OllamaClient` and a reachable Ollama server
- WHEN `list_models()` is called and the server responds 200 with a list
  of installed model entries
- THEN it returns the installed model tags as a list of strings

#### Scenario: Unreachable server raises OllamaUnavailable

- GIVEN no Ollama server is reachable at the configured base URL
- WHEN `list_models()` is called
- THEN `OllamaUnavailable` is raised and no low-level transport exception
  escapes

#### Scenario: Non-200 or malformed response raises OllamaError

- GIVEN the server responds with a non-200 status, or 200 with a body
  that is not valid JSON
- WHEN `list_models()` is called
- THEN `OllamaError` is raised rather than an unhandled exception

### Requirement: Model Tag Matching Tolerates Bare And Latest-Qualified Tags

A pure `model_tag_matches(configured, installed)` function MUST return
`True` when `configured` equals an installed tag exactly, or equals that
tag's `<name>:latest` form.

#### Scenario: Bare configured tag matches a :latest installed entry

- GIVEN a configured tag with no version suffix and an installed entry
  equal to `<name>:latest`
- WHEN `model_tag_matches` is called
- THEN it returns `True`

#### Scenario: Exact tag match

- GIVEN a configured tag equal to an installed entry's tag
- WHEN `model_tag_matches` is called
- THEN it returns `True`

#### Scenario: Installed entry exposes its tag only under name

- GIVEN an installed entry with a `name` field but no `model` field
- WHEN `model_tag_matches` is called against that entry
- THEN the `name` field is used for comparison

#### Scenario: No matching entry returns False

- GIVEN no installed entry equals the configured tag under either
  normalization
- WHEN `model_tag_matches` is called
- THEN it returns `False`

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

Configuration MUST expose `embedding_model`, defaulting to `bge-m3`,
distinct from the chat `DEFAULT_MODEL`. For this slice, `embedding_model`
is a code-level default only — it is NOT added to `openkos.yaml.template`
and has no per-workspace override or CLI flag; that remains a separate
future slice.

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
