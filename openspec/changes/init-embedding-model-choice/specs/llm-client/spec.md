# Delta for LLM Client

## MODIFIED Requirements

### Requirement: OllamaClient Embeds Text Via /api/embed

`OllamaClient.embed(texts)` MUST POST `{"model": <tag>, "input": [...]}` to
`POST {base_url}/api/embed`. It MUST parse the response defensively,
accepting either an `embeddings` key or a singular `embedding` key
(response shape varies across Ollama versions), and MUST validate that
each resulting row is a list of exactly 1024 floats. A response that is
not valid JSON, lacks a recognized vector key, or contains a row with
non-numeric values MUST raise `OllamaError`. A response whose row has the
WRONG LENGTH (not exactly 1024 entries) MUST instead raise the distinct
`OllamaEmbeddingDimensionMismatch` (see the new requirement below) — this
is NOT a generic `OllamaError` and MUST NOT be caught by a bare
`except OllamaError` at any call site that needs to distinguish the two.
(Previously: a wrong-length row and a malformed/non-numeric row were both
rewrapped identically into the generic `OllamaError`, giving callers no
way to distinguish a permanent dimension mismatch from a transient
malformed response.)

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

#### Scenario: Malformed or non-numeric row raises the generic OllamaError

- GIVEN a 200 response whose body is not valid JSON, lacks any recognized
  vector key, or whose row contains non-numeric values (but is the
  correct length)
- WHEN `embed(texts)` is called
- THEN `OllamaError` is raised rather than returning malformed data

#### Scenario: Wrong-dimension row raises the distinct permanent error

- GIVEN a 200 response whose vector row has a length other than 1024
- WHEN `embed(texts)` is called
- THEN `OllamaEmbeddingDimensionMismatch` is raised, distinct from the
  generic `OllamaError`

## ADDED Requirements

### Requirement: Dimension Mismatch Is A Distinct Permanent Error

`OllamaEmbeddingDimensionMismatch` MUST be a subclass of `OllamaError` (so
existing broad `except OllamaError` call sites that do not care about the
distinction keep compiling and catching it), but MUST be checked and
handled BEFORE any bare `except OllamaError` at call sites that need to
treat it as fatal rather than transient — mirroring the existing
`OllamaUnavailable`/`OllamaModelNotFound` subclass-ordering discipline in
this client. It MUST carry the offending row's actual length and the
expected `EMBED_DIM` in its message. It MUST NOT be retried by the
transient-failure retry path (Transient Embed Failures Are Retried Before
Propagating) — a wrong dimension cannot heal by retry, identical in spirit
to `OllamaModelNotFound`.

#### Scenario: Dimension mismatch is never retried

- GIVEN the transport returns a wrong-length row on every attempt
- WHEN `embed(texts)` is called
- THEN `OllamaEmbeddingDimensionMismatch` is raised immediately on first
  occurrence, without consuming any retry attempt

#### Scenario: Message names the actual and expected dimension

- GIVEN a response row of length 768 instead of the expected 1024
- WHEN `embed(texts)` raises `OllamaEmbeddingDimensionMismatch`
- THEN its message states both the actual length (768) and the expected
  `EMBED_DIM` (1024)

#### Scenario: Subclass relationship preserves existing broad catches

- GIVEN a call site with an unmodified bare `except OllamaError`
- WHEN `embed(texts)` raises `OllamaEmbeddingDimensionMismatch`
- THEN that bare `except OllamaError` still catches it, unchanged from
  before this requirement existed
