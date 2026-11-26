# OpenAI-Compatible Client Specification (delta)

## ADDED Requirements

### Requirement: Token Usage Is Reported To An Optional Sink Per Response

WHEN the client is constructed with a `usage_sink`, `chat(messages)` MUST call
it exactly once for every parsed chat response, with a neutral `ChatUsage`
(`prompt_tokens`, `completion_tokens`, `total_tokens`, each an `int` or `None`),
BEFORE a `finish_reason == "length"` response raises, so a length-capped reply
is observed. `total_tokens` MUST be `usage.total_tokens` when it is a
trustworthy integer, else `prompt_tokens + completion_tokens` when both are
trustworthy, else `None`. A missing `usage`, a `usage` that is not an object,
or a counter that is absent, negative, boolean or not an integer MUST NOT
raise: that counter is `None`, and the sink is still called so the call is
counted. A transient failure that is retried carries no parsed response and
MUST NOT call the sink; the attempt that does receive one calls it once.

The sink is a constructor-injected callable, never state written onto the
instance, so `chat` remains safe under concurrent calls. WHEN no sink is given
nothing changes, and the request body MUST be byte-identical with and without
a sink.

#### Scenario: A success reports prompt, completion and total

- GIVEN a client with a `usage_sink` and a response whose `usage` is
  `{"prompt_tokens": 305, "completion_tokens": 26, "total_tokens": 331}`
- WHEN `chat(messages)` is called
- THEN the sink receives `ChatUsage(305, 26, 331)`

#### Scenario: An absent total falls back to the sum

- GIVEN a response whose `usage` carries only prompt and completion counters
- WHEN `chat(messages)` is called
- THEN the sink receives their sum as `total_tokens`

#### Scenario: A length-capped response is still observed

- GIVEN a response with `finish_reason` `length` and a `usage` object
- WHEN `chat(messages)` raises the generation-capped error
- THEN the sink was called with that response's usage first

#### Scenario: A response without usage still counts

- GIVEN a response with no `usage`
- WHEN `chat(messages)` is called
- THEN the sink receives `ChatUsage(None, None, None)` and the call returns
  the assistant text

#### Scenario: A retried attempt is counted once

- GIVEN the first attempt fails with HTTP 503 and the second returns a
  response with usage
- WHEN `chat(messages)` is called
- THEN the sink is called exactly once, with the second response's usage

#### Scenario: The request is unchanged

- GIVEN two clients that differ only in having a `usage_sink`
- WHEN each sends `chat(messages)`
- THEN the request bodies and headers are identical
