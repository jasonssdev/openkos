# OpenAI-Compatible Client Specification (delta)

## MODIFIED Requirements

### Requirement: Generation Length Cap Is Detected From `finish_reason`

`chat(messages)` MUST forward the caller-supplied generation-token limit per
request as the top-level `max_completion_tokens` field, and MUST NOT send
`max_tokens`: OpenAI's reasoning models reject `max_tokens`, while
`max_completion_tokens` is accepted and honoured by OpenAI, Anthropic's
compatible endpoint, Ollama's `/v1`, llama.cpp, MLX and vLLM. WHEN the
response's `choices[0].finish_reason` equals `"length"`, the call MUST raise
`OpenAICompatibleGenerationCapped`, mirroring the meaning of Ollama's
`done_reason == "length"` mapping to `OllamaGenerationCapped`.

#### Scenario: max_completion_tokens is forwarded per request

- GIVEN a caller-supplied generation-token limit
- WHEN `chat(messages)` is called
- THEN the request body includes `max_completion_tokens` set to that limit
- AND the request body does not include `max_tokens`

#### Scenario: finish_reason length raises the generation-capped error

- GIVEN a response with `choices[0].finish_reason == "length"`
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleGenerationCapped` is raised

## ADDED Requirements

### Requirement: A Rejected Sampling Setting Names The Config Key

WHEN the server answers HTTP 400 and the response body names `temperature` or
`seed` as unsupported, deprecated or not allowed, `chat(messages)` MUST raise
`OpenAICompatibleError` whose message names the `openkos.yaml` key
(`temperature` or `seed`), says to unset it, and includes the server's own
text. The call MUST NOT be retried (400 is not transient) and the message MUST
NOT contain the API key. A 400 that names neither key is mapped exactly as
before.

#### Scenario: A refused temperature names the key

- GIVEN the server answers 400 "Unsupported value: 'temperature' does not
  support 0.2 with this model. Only the default (1) value is supported."
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleError` is raised naming `temperature` in
  `openkos.yaml` and saying to unset it, after exactly one attempt

#### Scenario: A refused seed names the key

- GIVEN the server answers 400 with a body naming `seed` as unsupported
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleError` is raised naming `seed` in `openkos.yaml`
