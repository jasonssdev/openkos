# LLM Client Specification (delta)

## ADDED Requirements

### Requirement: Token Usage Is Reported To An Optional Sink Per Response

WHEN `OllamaClient` is constructed with a `usage_sink`, `chat(messages)` MUST
call it exactly once for every parsed response with a neutral `ChatUsage`:
`prompt_tokens` from `prompt_eval_count`, `completion_tokens` from
`eval_count`, and `total_tokens` as their sum when both are trustworthy
integers, else `None`. It MUST be called BEFORE a `done_reason == "length"`
response raises. A missing, boolean, negative or non-integer counter MUST NOT
raise: it is `None`, and the sink is still called so the call is counted. The
sink is constructor-injected, never per-call state on the instance, and WHEN it
is absent the request body MUST be byte-identical to one built without it.

#### Scenario: Counters are reported with their sum

- GIVEN a client with a `usage_sink` and a response with `prompt_eval_count`
  30 and `eval_count` 7
- WHEN `chat(messages)` is called
- THEN the sink receives `ChatUsage(30, 7, 37)`

#### Scenario: A length-capped response is still observed

- GIVEN a response with `done_reason` `length` and both counters
- WHEN `chat(messages)` raises the generation-capped error
- THEN the sink was called with those counters first

#### Scenario: Missing counters still count the call

- GIVEN a response with neither counter
- WHEN `chat(messages)` is called
- THEN the sink receives `ChatUsage(None, None, None)`
