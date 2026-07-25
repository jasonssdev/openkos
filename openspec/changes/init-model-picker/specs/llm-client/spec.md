# Delta for LLM Client

## MODIFIED Requirements

### Requirement: List Installed Models

`OllamaClient` MUST provide `list_models()` returning, per installed model,
at least the tag and the model family via `GET {host}/api/tags`. The
method MUST read each installed entry defensively, preferring a `model`
field and falling back to a `name` field when `model` is absent, for the
tag — this D2 field-variance handling is unchanged. It MUST additionally
surface the entry's family, sourced from the `details.family` field when
present. WHEN an entry's `details` object or `family` field is absent, the
entry MUST still be returned (never dropped), with its family
absent/unknown rather than fabricated. A connection failure or timeout
MUST raise `OllamaUnavailable`; any other non-200 response or a 200
response whose body is not valid JSON MUST raise `OllamaError` — following
the same error-mapping discipline as `chat()`. `list_models()` MUST remain
config-free: the `llm` package MUST NOT import `openkos.config`.
(Previously: returned installed model tags only, as `list[str]`,
discarding all other per-entry fields including `details`/`family`.)

#### Scenario: Reachable server returns installed tags with family

- GIVEN a reachable Ollama server whose `/api/tags` response includes a
  chat model entry with `details.family: "qwen"` and an embedding model
  entry with `details.family: "bert"`
- WHEN `list_models()` is called
- THEN both entries are returned, each carrying its tag and its family
  (`"qwen"` and `"bert"` respectively)

#### Scenario: Entry missing details/family is still returned

- GIVEN a reachable server whose `/api/tags` response includes an entry
  with no `details` object or no `family` field
- WHEN `list_models()` is called
- THEN that entry is still returned (not dropped), with family
  absent/unknown

#### Scenario: Tag extraction preserves model-or-name fallback

- GIVEN an installed entry with a `name` field but no `model` field
- WHEN `list_models()` is called
- THEN the entry's tag is taken from `name`, unchanged from prior behavior

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

## ADDED Requirements

### Requirement: Family-Based Embedding Model Classification

A pure classification helper MUST determine whether an installed model
entry is an embedding model, based on its family: family `"bert"` (and any
other embedding families explicitly documented at design time) MUST
classify as embedding. A missing or unrecognized family MUST classify as
NON-embedding — ambiguity MUST NEVER cause a model to be excluded from
chat candidates.

#### Scenario: Known embedding family classifies as embedding

- GIVEN an installed entry with family `"bert"`
- WHEN the classification helper is applied
- THEN the entry is classified as an embedding model

#### Scenario: Missing or unknown family classifies as non-embedding

- GIVEN an installed entry with no family, or a family not in the
  documented embedding-family set
- WHEN the classification helper is applied
- THEN the entry is classified as NON-embedding
