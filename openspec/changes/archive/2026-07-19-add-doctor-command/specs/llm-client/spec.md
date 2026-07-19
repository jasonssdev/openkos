# Delta for LLM Client

## ADDED Requirements

### Requirement: List Installed Models

`OllamaClient` MUST provide `list_models()` returning the installed model
tags via `GET {host}/api/tags`. A connection failure or timeout MUST raise
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
tag's `<name>:latest` form. It MUST read each installed entry defensively,
preferring a `model` field and falling back to a `name` field when `model`
is absent.

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
</content>
