# LLM Client Specification

## Purpose

`llm/` is a pure library seam for chat completion against a locally running
Ollama server: a `LLMBackend` Protocol plus a concrete `OllamaClient` that
POSTs `/api/chat` via stdlib `urllib`, mapping every failure mode to a typed
exception. It has no CLI command and no workspace effect; its only consumer
is the future `query` command.

## Non-Goals

This spec does not define: streaming (`stream:true`/NDJSON); tool/function
calling or embeddings; retries or backoff; `/api/generate` or any
non-Ollama provider; any CLI command; changes to `ingest`, `forget`, or
`config`'s schema beyond an optional host key.

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
