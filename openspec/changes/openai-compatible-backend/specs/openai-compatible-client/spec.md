# OpenAI-Compatible Client Specification

## Purpose

`llm/openai_compatible.py` is a second concrete implementation of the
backend-neutral `LLMBackend`, `Embedder`, and `BackendDiagnostics` Protocols
defined in `llm/base.py`, targeting any local server that speaks the
OpenAI-compatible HTTP API — llama.cpp's `llama-server`, LM Studio, vLLM,
LocalAI. It mirrors `OllamaClient`'s transport shape (stdlib
`urllib.request`, an injectable `urlopen`, the same error-ladder discipline)
so that every existing consumer catching the neutral `BackendError`/
`BackendUnavailable` bases needs no change to work against this backend.

## Non-Goals

This spec does not define: streaming responses; `response_format`, JSON
mode, or grammar-constrained output (structured replies continue through
`llm/parsing.py`'s fail-closed extraction, unchanged, for both backends);
tool/function calling; suppression or normalization of reasoning output
(servers expose it inconsistently — inline `<think>` tags, a
`reasoning_content` field, or nothing — this is a documented known
limitation, not a compatibility feature this client provides); retries or
backoff for the CHAT path (mirrors `OllamaClient.chat`'s no-retry contract;
only `embed()` retries, per the Non-Goals of `llm-client`); the config
surface, endpoint precedence, the API key mechanism, or backend selection
(`backend-selection`); any CLI command or workspace effect; changing
`OllamaClient`'s own behavior or output in any way.

## Requirements

### Requirement: Successful Chat Call Returns Assistant Text

`LLMBackend.chat(messages)` MUST POST the configured model and `messages` to
`POST {base_url}/v1/chat/completions` with `stream: false`, and MUST return
`choices[0].message.content` from the response as a plain string.

#### Scenario: Chat call returns clean assistant text

- GIVEN an `OpenAICompatibleClient` configured with a model tag and base URL
- WHEN `chat(messages)` is called and the server responds 200 with
  `{"choices": [{"message": {"role": "assistant", "content": "..."},
  "finish_reason": "stop"}]}`
- THEN the call returns that `content` string, with `stream: false` present
  in the request body sent

### Requirement: System And User Roles Supported

`messages` MUST support both `system` and `user` roles, each sent as
`{"role": ..., "content": ...}` entries in request order.

#### Scenario: System and user messages both forwarded

- GIVEN a message list containing one `system` and one `user` entry
- WHEN `chat(messages)` is called
- THEN the request body's `messages` array contains both entries, each with
  its original `role` and `content`, in the given order

### Requirement: Server Unavailable Raises A Typed Error

A connection refused or a request timeout MUST raise
`OpenAICompatibleUnavailable`. No raw `urllib.error.URLError`,
`socket.timeout`, or similar low-level exception MUST ever propagate to the
caller.

#### Scenario: Server not running raises OpenAICompatibleUnavailable

- GIVEN no server is reachable at the configured base URL
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleUnavailable` is raised and no `URLError` escapes

#### Scenario: Request timeout raises OpenAICompatibleUnavailable

- GIVEN a configured, bounded request timeout
- WHEN the server does not respond within that timeout
- THEN `chat(messages)` raises `OpenAICompatibleUnavailable` and does not
  hang indefinitely

### Requirement: Unknown Model Raises A Typed Not-Found Error

An HTTP error response whose status and body indicate the requested model is
not available on the configured server MUST raise
`OpenAICompatibleModelNotFound`. Because OpenAI-compatible servers do not
share one uniform not-found status code across implementations, this
classification MUST be based on the response's status and body content, not
solely on a hard-coded status code.

#### Scenario: Unavailable model raises OpenAICompatibleModelNotFound

- GIVEN a model tag the configured server does not have loaded or available
- WHEN `chat(messages)` is called and the server responds with an error
  status whose body identifies the model as the cause
- THEN `OpenAICompatibleModelNotFound` is raised

### Requirement: Other Failures Raise A Generic Typed Error

Any other non-200 HTTP response, or a 200 response whose body is not valid
JSON or lacks the expected `choices[0].message.content` shape, MUST raise
`OpenAICompatibleError`.

#### Scenario: Non-model-not-found server error raises OpenAICompatibleError

- GIVEN the server responds with a non-200 status not classified as a
  model-not-found response
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleError` is raised, carrying the server's error detail

#### Scenario: Malformed JSON response raises OpenAICompatibleError

- GIVEN the server responds 200 with a body that is not valid JSON or is
  missing `choices[0].message.content`
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleError` is raised rather than an unhandled parsing
  exception

### Requirement: Generation Length Cap Is Detected From `finish_reason`

`chat(messages)` MUST forward `max_tokens` per request when the caller
supplies a generation-token limit. WHEN the response's
`choices[0].finish_reason` equals `"length"`, the call MUST raise
`OpenAICompatibleGenerationCapped`, mirroring the meaning of Ollama's
`done_reason == "length"` mapping to `OllamaGenerationCapped`.

#### Scenario: max_tokens is forwarded per request

- GIVEN a caller-supplied generation-token limit
- WHEN `chat(messages)` is called
- THEN the request body includes `max_tokens` set to that limit

#### Scenario: finish_reason length raises the generation-capped error

- GIVEN a response with `choices[0].finish_reason == "length"`
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleGenerationCapped` is raised

### Requirement: Usage Counters Are Read With The Same Fail-Open Discipline As Ollama

WHEN the response carries a `usage` object, `chat(messages)` MUST read
`usage.prompt_tokens` and `usage.completion_tokens` defensively for the same
prompt/generation-boundary trustworthiness purpose Ollama's
`prompt_eval_count`/`eval_count` counters serve. WHEN `usage` is absent, or
either counter is absent or not numeric, this MUST NOT raise: the call MUST
degrade to treating that counter as unknown rather than fabricating a value
or failing the call, since not every OpenAI-compatible server populates
`usage` on every response.

#### Scenario: Present usage counters are read

- GIVEN a response carrying `usage.prompt_tokens` and
  `usage.completion_tokens`
- WHEN `chat(messages)` is called
- THEN both counters are read and used exactly as Ollama's equivalent
  counters are used

#### Scenario: Absent usage does not fail the call

- GIVEN a response with no `usage` object
- WHEN `chat(messages)` is called
- THEN the call still returns the assistant text, with the counters treated
  as unknown rather than raising

### Requirement: Temperature And Seed Are Top-Level Fields With The Same None-Means-Omit Rule

WHEN the caller supplies a `temperature` or `seed` value, `chat(messages)`
MUST include it as a top-level request field (not nested, unlike Ollama's
`options`). WHEN either is `None`, it MUST be omitted from the request
entirely rather than sent as `null` or a default value.

#### Scenario: Supplied temperature and seed are sent top-level

- GIVEN caller-supplied `temperature` and `seed` values
- WHEN `chat(messages)` is called
- THEN the request body carries both as top-level fields, not nested under
  any sub-object

#### Scenario: None values are omitted, not sent as null

- GIVEN `temperature` and `seed` are both `None`
- WHEN `chat(messages)` is called
- THEN neither key appears in the request body

### Requirement: `context_window` Is Advisory-Only And Never Sent

The client MUST expose a `context_window` property for OpenKOS's own prompt
budgeting, identical in purpose to `OllamaClient.context_window`. Unlike
Ollama (which sends `options.num_ctx` per request), this client MUST NEVER
send `context_window` to the server in any request: OpenAI-compatible
servers fix their context size at server start (e.g. `-c`,
`--max-model-len`) and accept no per-request override for it.

#### Scenario: context_window is never part of the request body

- GIVEN a client constructed with an explicit `context_window`
- WHEN `chat(messages)` is called
- THEN no request field carrying that value is sent to the server

#### Scenario: context_window is still readable for prompt budgeting

- GIVEN a client constructed with an explicit `context_window`
- WHEN a caller reads the `context_window` property
- THEN it returns the configured value, usable for local prompt-budget
  planning exactly as Ollama's property is used today

### Requirement: An Optional Bearer Key Is Applied Per Request And Never Logged

The client MUST accept an optional API key as a caller-supplied argument.
WHEN present, every request MUST carry it as an `Authorization: Bearer
<key>` header. WHEN absent, no `Authorization` header MUST be sent. The key
MUST NEVER appear in an exception message, a log line, or any other
diagnostic text the client produces. The client MUST NOT follow any HTTP
redirect (any 3xx status, any request method), because the standard
redirect-following behavior copies the `Authorization` header onto the
redirected request regardless of destination host; a redirect response
MUST instead raise a typed, non-retryable error naming the status, and
MUST NOT echo the redirect target's query string.

#### Scenario: Present key is sent as a bearer header

- GIVEN a client constructed with an API key
- WHEN any request is made
- THEN the request carries `Authorization: Bearer <key>`

#### Scenario: Absent key sends no Authorization header

- GIVEN a client constructed with no API key
- WHEN any request is made
- THEN no `Authorization` header is present

#### Scenario: The key never appears in a raised exception's message

- GIVEN a client constructed with an API key, and a request that fails
- WHEN the resulting exception's message is inspected
- THEN the key value does not appear anywhere in it

#### Scenario: A redirect response is refused, never forwarding the key

- GIVEN a client constructed with an API key, configured against a server
  that responds with an HTTP redirect (e.g. 303) to a different host
- WHEN a request is made
- THEN the client raises a typed error naming the redirect status, the
  second host never receives any request or the `Authorization` header,
  and the raised error's message contains neither the key nor the
  redirect target's query string

### Requirement: Testable Without A Live Server

The HTTP transport MUST be an injectable/mockable seam — mirroring
`OllamaClient`'s injected `urlopen` — so unit tests can exercise every
success and error path without a running OpenAI-compatible server.

#### Scenario: Full behavior covered with the HTTP layer mocked

- GIVEN a test double standing in for the HTTP transport
- WHEN it is configured to return each response shape (success, model
  not found, other non-200, malformed body, connection error, timeout)
- THEN `chat(messages)` and `embed(texts)` exhibit the corresponding
  documented behavior without any network call reaching a real server

### Requirement: Embedder Produces Order-Preserving, Dimension-Validated, L2-Normalized Vectors

`embed(texts)` MUST POST to `POST {base_url_for_embeddings}/v1/embeddings`
(the embedding endpoint, which MAY differ from the chat endpoint — see
`backend-selection`) and MUST parse the response's `data[].embedding` rows,
respecting each row's `index` field for ordering rather than assuming
response order. Each resulting row MUST be validated to contain exactly
`EMBED_DIM` numeric values, using the same validation the `Embedder`
Protocol already requires. Every returned vector MUST additionally be
L2-normalized client-side before being returned, regardless of whether the
server already returns a normalized vector: this is a defensive,
idempotent step (normalizing an already-unit vector is a no-op) that
protects `graph/proximity.py`'s cosine-to-Euclidean conversion, which is
pinned only for Ollama's `bge-m3` and is not guaranteed for an arbitrary
OpenAI-compatible embeddings server.

#### Scenario: Rows are ordered by their index field, not response order

- GIVEN a response whose `data` array lists embedding rows out of `index`
  order
- WHEN `embed(texts)` is called
- THEN the returned vectors are ordered by `index`, matching the order of
  the input `texts`

#### Scenario: Every returned vector is L2-normalized

- GIVEN a response containing a non-unit-norm vector row
- WHEN `embed(texts)` is called
- THEN the returned vector for that row has unit L2 norm

#### Scenario: An already-normalized vector is unchanged in effect

- GIVEN a response containing a vector row that is already unit L2 norm
- WHEN `embed(texts)` is called
- THEN the returned vector is numerically equivalent to the input row
  (normalization is a no-op)

### Requirement: Wrong-Dimension Row Raises A Distinct Permanent Error

A response whose vector row has a length other than `EMBED_DIM` MUST raise
`OpenAICompatibleEmbeddingDimensionMismatch`, distinct from the generic
`OpenAICompatibleError`, mirroring `OllamaEmbeddingDimensionMismatch`'s
contract: it MUST be a subclass of `OpenAICompatibleError`, MUST carry the
offending row's actual length and the expected `EMBED_DIM` in its message,
and MUST NOT be retried by the transient-failure retry path.

#### Scenario: Wrong-dimension row raises the distinct permanent error

- GIVEN a 200 response whose vector row has a length other than `EMBED_DIM`
- WHEN `embed(texts)` is called
- THEN `OpenAICompatibleEmbeddingDimensionMismatch` is raised, distinct from
  the generic `OpenAICompatibleError`, and is never retried

### Requirement: Transient Embed Failures Are Retried Before Propagating

`embed(texts)` MUST retry a transient failure a bounded number of times with
backoff before propagating any exception, mirroring
`OllamaClient.embed`'s retry contract exactly: a retried attempt that
succeeds MUST be transparent to the caller; `OpenAICompatibleModelNotFound`
and `OpenAICompatibleEmbeddingDimensionMismatch` MUST NOT be retried; an
exhausted retry budget MUST raise the retryable exception to the caller.

#### Scenario: Transient failure followed by success is transparent to the caller

- GIVEN the transport raises a transient error on the first attempt and
  succeeds on a later attempt within the retry budget
- WHEN `embed(texts)` is called
- THEN it returns the validated, normalized vectors with no exception raised

#### Scenario: Exhausted retry budget raises to the caller

- GIVEN the transport fails on every attempt within the retry budget with a
  retryable failure
- WHEN `embed(texts)` is called
- THEN it raises that exception after the final attempt

### Requirement: Server Unavailable During Embedding Raises A Typed Error

A connection refused or request timeout during `embed(texts)` MUST raise
`OpenAICompatibleUnavailable`, following the same mapping `chat()` uses.

#### Scenario: Server not running raises OpenAICompatibleUnavailable

- GIVEN no server is reachable at the configured embedding base URL
- WHEN `embed(texts)` is called
- THEN `OpenAICompatibleUnavailable` is raised

### Requirement: List Installed Models Via `/v1/models`

`OpenAICompatibleClient` MUST provide `list_models()` returning, per listed
model, at least its id via `GET {base_url}/v1/models`, satisfying the same
`BackendDiagnostics` contract `OllamaClient.list_models()` satisfies. Since
`/v1/models` carries no model-family equivalent, every returned entry's
`family` MUST be `None` — never fabricated. A connection failure or timeout
MUST raise `OpenAICompatibleUnavailable`; any other non-200 response or a
malformed body MUST raise `OpenAICompatibleError`.

#### Scenario: Reachable server returns listed models with no family

- GIVEN a reachable server whose `/v1/models` response lists two model ids
- WHEN `list_models()` is called
- THEN both entries are returned, each with `family` equal to `None`

#### Scenario: Unreachable server raises OpenAICompatibleUnavailable

- GIVEN no server is reachable at the configured base URL
- WHEN `list_models()` is called
- THEN `OpenAICompatibleUnavailable` is raised

### Requirement: Locality Uses The Shared Classifier

`OpenAICompatibleClient.locality` MUST be decided by the same
literal-loopback classifier `OllamaClient` uses (no DNS resolution, ever;
loopback literals only; fail-closed on anything unparseable or non-local),
applied independently to the chat endpoint and, when configured separately,
the embedding endpoint.

#### Scenario: A loopback base_url classifies local

- GIVEN a client configured with `http://127.0.0.1:8080` or
  `http://localhost:8080` as its base URL
- WHEN `locality` is read
- THEN it reports local, using the identical classification rule Ollama's
  client uses for an equivalent host

#### Scenario: An unparseable or remote host classifies non-local

- GIVEN a client configured with a non-loopback or unparseable host value
- WHEN `locality` is read
- THEN it reports non-local (fail-closed)

### Requirement: Error Hierarchy Mirrors Ollama's Under The Neutral Bases

`OpenAICompatibleError` MUST subclass `BackendError`.
`OpenAICompatibleUnavailable` MUST subclass both `OpenAICompatibleError` and
`BackendUnavailable`. `OpenAICompatibleModelNotFound`,
`OpenAICompatibleGenerationCapped`, and
`OpenAICompatibleEmbeddingDimensionMismatch` MUST each subclass
`OpenAICompatibleError`. This mirrors `OllamaError`'s hierarchy one-to-one,
so every existing `except BackendError`/`except BackendUnavailable` call
site continues to catch this backend's failures unchanged, with no new
concrete Ollama class introduced and no existing Ollama class renamed.

#### Scenario: A generic BackendError catch handles every concrete failure

- GIVEN a call site with an unmodified `except BackendError`
- WHEN any exception from this client's chat, embed, or diagnostics methods
  is raised
- THEN that `except BackendError` catches it

#### Scenario: A generic BackendUnavailable catch handles connectivity failures

- GIVEN a call site with an unmodified `except BackendUnavailable`
- WHEN `OpenAICompatibleUnavailable` is raised
- THEN that `except BackendUnavailable` catches it

### Requirement: Config-Free Client

`llm/openai_compatible.py` MUST take the model tag, base URL(s), and
optional API key as caller-supplied arguments. The `llm` package MUST NOT
import `openkos.config`.

#### Scenario: llm package has no config import

- GIVEN the `llm` package's source, including `openai_compatible.py`
- WHEN the no-config-import check runs
- THEN no module under `llm/` imports `openkos.config`
