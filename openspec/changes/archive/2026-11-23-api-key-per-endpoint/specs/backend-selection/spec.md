# Backend Selection Specification (delta)

## ADDED Requirements

### Requirement: The API Key Is Sent Only To The Endpoint It Belongs To

The chat client MUST receive `OPENKOS_OPENAI_API_KEY` as today. The embedding
client, and `diagnostics_client` with `purpose="embed"`, MUST receive an API
key by this rule, first match wins:

1. `OPENKOS_OPENAI_EMBEDDING_API_KEY`, when set and non-empty after stripping,
   whatever origin the embedding endpoint has;
2. otherwise `OPENKOS_OPENAI_API_KEY`, when `embedding_base_url` is unset (the
   request goes to `base_url`) or has the same origin as `base_url`
   (scheme, host and port, compared after the existing host normalization);
3. otherwise no key, and no `Authorization` header.

`OPENKOS_OPENAI_EMBEDDING_API_KEY` MUST be read only from the environment,
never from `openkos.yaml`, and its value MUST NOT appear in any message or log.
Behaviour with `embedding_base_url` unset and the new variable unset MUST be
unchanged, and `backend: ollama` MUST be unaffected.

#### Scenario: embedding_base_url unset

- GIVEN `OPENKOS_OPENAI_API_KEY` is set and `embedding_base_url` is unset
- WHEN an embed client is constructed
- THEN it receives `OPENKOS_OPENAI_API_KEY`

#### Scenario: Same origin as base_url

- GIVEN `base_url` is `https://api.example.com/v1` and `embedding_base_url`
  is `https://api.example.com/embed`
- WHEN an embed client is constructed
- THEN it receives `OPENKOS_OPENAI_API_KEY`

#### Scenario: Different origin without the embedding variable

- GIVEN `base_url` is `https://api.example.com` and `embedding_base_url` is
  `http://127.0.0.1:11434`, and only `OPENKOS_OPENAI_API_KEY` is set
- WHEN an embed client is constructed
- THEN it receives no API key, and the chat client still receives the key

#### Scenario: Different origin with the embedding variable

- GIVEN the same endpoints and `OPENKOS_OPENAI_EMBEDDING_API_KEY` is set
- WHEN an embed client is constructed
- THEN it receives the embedding variable's value, never the chat key

#### Scenario: Same scheme and host, different port

- GIVEN `base_url` is `https://api.example.com` and `embedding_base_url` is
  `https://api.example.com:8443`
- WHEN an embed client is constructed
- THEN it receives no key from `OPENKOS_OPENAI_API_KEY`

#### Scenario: Diagnostics follow the same rule

- GIVEN a different-origin `embedding_base_url`
- WHEN `diagnostics_client` is built with `purpose="embed"`
- THEN it receives the same key, or none, that `embed_client` would

## MODIFIED Requirements

### Requirement: A Non-Local Key Destination Is Named Once Per Host

WHEN an API key is sent to an effective endpoint (chat or embedding) that
classifies as non-local, the engine MUST print one notice line to stderr
naming that endpoint's origin (`scheme://host[:port]`, never the path, query,
userinfo or the key) and the variable that supplies the key, before the first
request using that key is made, at most once per process for each distinct
origin. When a separate embedding endpoint receives no key, the notice MUST
say so. The notice MUST NOT block the request. It MUST NOT be printed for a
local endpoint, for an endpoint that receives no key, or for `backend: ollama`.
An endpoint already covered by the plain-`http://` warning is named by that
warning alone. In the MCP server the notice MUST reach stderr through logging
and never stdout.

#### Scenario: Distinct chat and embedding hosts with an embedding key

- GIVEN `OPENKOS_OPENAI_API_KEY` and `OPENKOS_OPENAI_EMBEDDING_API_KEY` are
  set, the chat endpoint is `https://api.example.com/v1` and
  `embedding_base_url` is `https://embed.example.org/v1`
- WHEN clients are constructed
- THEN one notice is printed for each origin, each naming its own variable

#### Scenario: A keyless embedding endpoint is stated

- GIVEN only `OPENKOS_OPENAI_API_KEY` is set, the chat endpoint is
  `https://api.example.com/v1` and `embedding_base_url` is
  `https://embed.example.org/v1`
- WHEN clients are constructed
- THEN one notice names `https://api.example.com` and says the embedding
  endpoint receives no key, and none is printed for `embed.example.org`
