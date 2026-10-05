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
absent/unknown rather than fabricated. It MUST also surface the entry's
content digest, sourced from the entry's `digest` field when present; WHEN
that field is absent or not a string, the entry MUST still be returned with
its digest absent/unknown rather than fabricated. The digest MUST exist only in
memory on the returned entry: it MUST NOT be written to any file. A backend
that cannot report a digest MUST return its entries with the digest absent.
A connection failure or timeout
MUST raise `OllamaUnavailable`; any other non-200 response or a 200
response whose body is not valid JSON MUST raise `OllamaError` — following
the same error-mapping discipline as `chat()`. `list_models()` MUST remain
config-free: the `llm` package MUST NOT import `openkos.config`.
(Previously: the entry carried tag and family only, with no digest.)

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
- THEN the entry's tag is taken from `name`

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

#### Scenario: The digest is surfaced

- GIVEN a reachable server whose `/api/tags` entry carries a `digest` string
- WHEN `list_models()` is called
- THEN the returned entry carries that digest unchanged

#### Scenario: An entry without a digest is still returned

- GIVEN an entry with no `digest` field, or a non-string `digest`
- WHEN `list_models()` is called
- THEN the entry is returned with its digest absent/unknown and no other
  field is affected

#### Scenario: A backend that reports no digest returns entries without one

- GIVEN a backend that exposes no model digests (an `openai-compatible`
  backend)
- WHEN it lists models
- THEN each entry is returned with its digest absent
