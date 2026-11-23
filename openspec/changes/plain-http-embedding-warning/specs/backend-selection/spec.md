# Backend Selection Specification (delta)

## MODIFIED Requirements

### Requirement: A Non-Loopback Key Send Over Plain HTTP Is Warned

WHEN an API key is sent to an effective endpoint (chat or embedding) AND that
endpoint classifies as non-local (per the shared locality classifier) AND its
scheme is plain `http://` (not `https://`), the engine MUST print a warning to
stderr stating that a credential is about to be sent unencrypted to a
non-local host, before the first request using that key is made. The warning
MUST name the endpoint (chat or embedding) and its host, and MUST NOT contain
the key. An endpoint that shares its origin with another endpoint already
warned about MUST NOT produce a second warning: one line per host. The warning
MUST be printed at most once per process, MUST NOT block the request, and is
advisory only.

#### Scenario: A key sent to a remote host over http warns

- GIVEN an API key is configured and the effective endpoint is
  `http://203.0.113.5:8000` (non-loopback, plain HTTP)
- WHEN a client using that endpoint and key is constructed
- THEN a warning is printed to stderr before any request is sent, naming the
  unencrypted-credential risk

#### Scenario: A key sent to a plain-http embedding host warns

- GIVEN `OPENKOS_OPENAI_EMBEDDING_API_KEY` is set and `embedding_base_url` is
  `http://203.0.113.5:9000` (non-loopback, plain HTTP)
- WHEN an embed client is constructed
- THEN a warning naming the embedding endpoint and `203.0.113.5:9000` is
  printed to stderr, and the key does not appear in it

#### Scenario: A same-origin embedding endpoint is warned about once

- GIVEN `OPENKOS_OPENAI_API_KEY` is set and `base_url` and `embedding_base_url`
  are on `http://203.0.113.5:8000`
- WHEN chat and embed clients are constructed
- THEN exactly one plain-HTTP warning naming that host is printed

#### Scenario: A local endpoint with a key prints no such warning

- GIVEN an API key is configured and the effective endpoint is
  `http://127.0.0.1:8000` (loopback)
- WHEN a client using that endpoint and key is constructed
- THEN no unencrypted-credential warning is printed

#### Scenario: An https endpoint with a key prints no such warning

- GIVEN an API key is configured and the effective endpoint uses `https://`
  to a non-local host
- WHEN a client using that endpoint and key is constructed
- THEN no unencrypted-credential warning is printed

#### Scenario: An endpoint that receives no key prints no such warning

- GIVEN `embedding_base_url` is a plain-http non-local host on a different
  origin than `base_url` and no embedding key variable is set
- WHEN an embed client is constructed
- THEN no unencrypted-credential warning names the embedding endpoint
