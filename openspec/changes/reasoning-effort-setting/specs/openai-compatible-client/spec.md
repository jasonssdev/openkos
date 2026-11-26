# OpenAI-Compatible Client Specification (delta)

## ADDED Requirements

### Requirement: A Configured Reasoning Effort Is Sent On Chat Requests Only

WHEN the caller supplies a `reasoning_effort`, every `chat(messages)` request
body MUST include it as the top-level field `reasoning_effort`. WHEN it is
`None`, the request body MUST be byte-identical to one built without the
parameter. `embed(texts)` MUST NEVER send it.

#### Scenario: A supplied effort is sent on every chat request

- GIVEN a client constructed with `reasoning_effort="low"`
- WHEN `chat(messages)` is called twice
- THEN each request body includes `"reasoning_effort": "low"` at the top level

#### Scenario: An unset effort leaves the request unchanged

- GIVEN a client constructed without `reasoning_effort`
- WHEN `chat(messages)` is called
- THEN the request body is byte-identical to one from a client built with
  `reasoning_effort=None` and contains no `reasoning_effort` field

#### Scenario: Embedding requests never carry it

- GIVEN a client constructed with `reasoning_effort="low"`
- WHEN `embed(texts)` is called
- THEN the request body contains no `reasoning_effort` field

### Requirement: A Rejected Reasoning Effort Names The Config Key

WHEN the server answers HTTP 400 and the response body mentions
`reasoning_effort`, `chat(messages)` MUST raise `OpenAICompatibleError` whose
message names `reasoning_effort` in `openkos.yaml`, says to unset it, and
includes the server's own text. The call MUST NOT be retried and the message
MUST NOT contain the API key. This follows the same mapping as `temperature`
and `seed`.

#### Scenario: A refused reasoning effort names the key

- GIVEN the server answers 400 naming `reasoning_effort` as unrecognized
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleError` is raised naming `reasoning_effort` in
  `openkos.yaml` and saying to unset it, after exactly one attempt
