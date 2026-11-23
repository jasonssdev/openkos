# Doctor Command Specification (delta)

## MODIFIED Requirements

### Requirement: Doctor States Where The API Key Will Be Sent

`doctor` MUST print an informational `API key destination` line that is
always `[PASS]`. For `backend: openai-compatible` it MUST state, per endpoint
(chat, then embedding), the origin (`scheme://host[:port]`, no path, query or
userinfo), whether that origin is this machine, and whether a key is sent
there; an embedding endpoint that receives no key MUST be stated as receiving
none. With no key set for either endpoint it MUST say no key is sent; for
`backend: ollama` it MUST say no key is sent. It MUST NOT print any key value.

#### Scenario: A remote chat host and a keyless local embedding host

- GIVEN `backend: openai-compatible`, `base_url: https://api.example.com/v1`,
  `embedding_base_url: http://127.0.0.1:11434` and only
  `OPENKOS_OPENAI_API_KEY` set
- WHEN `openkos doctor` runs
- THEN the line names `https://api.example.com` as the chat destination (not
  this machine), names `http://127.0.0.1:11434` as the embedding endpoint that
  receives no key, and no key value appears

#### Scenario: A separate embedding key

- GIVEN the same endpoints and `OPENKOS_OPENAI_EMBEDDING_API_KEY` also set
- WHEN `openkos doctor` runs
- THEN the line states that the embedding endpoint receives the embedding key
