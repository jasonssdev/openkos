# Backend Selection Specification

## Purpose

`backend-selection` is the config surface and the single construction seam
that let a workspace choose which LLM backend (`ollama` or
`openai-compatible`) serves its chat and embedding calls: the `backend`,
`base_url`, and `embedding_base_url` config keys; the endpoint-precedence
rule against the pre-existing `OLLAMA_HOST` environment variable; the
environment-only API key and its non-disclosure; and the resolver in
`application/backends.py` that every chat and every embed construction site
goes through, so no consumer imports a concrete client class directly.

## Non-Goals

This spec does not define: the OpenAI-compatible client's own request/response
contract (`openai-compatible-client`); `init`'s behavior (`init` stays
Ollama-only — see `workspace-init`; a user switches backend by editing
`openkos.yaml`); a backend picker or `init --backend` flag; eval-harness
backend selection; streaming; changing `OllamaClient`'s own defaults or
behavior when `backend: ollama` (the default) is in effect.

## Requirements

### Requirement: `backend` Config Key Selects The Client Family

`openkos.yaml` MUST accept a `backend` key with value `ollama` or
`openai-compatible`. WHEN absent, it MUST default to `ollama`. A value other
than these two MUST be rejected by `read_config` with a clear message
naming the invalid value and the two accepted values.

#### Scenario: Absent key defaults to ollama

- GIVEN an `openkos.yaml` with no `backend` key
- WHEN `read_config` parses it
- THEN the resulting `Config.backend` equals `"ollama"`

#### Scenario: Explicit openai-compatible is accepted

- GIVEN an `openkos.yaml` containing `backend: openai-compatible`
- WHEN `read_config` parses it
- THEN the resulting `Config.backend` equals `"openai-compatible"`

#### Scenario: An unrecognized value is rejected with a clear message

- GIVEN an `openkos.yaml` containing `backend: something-else`
- WHEN `read_config` parses it
- THEN it raises an error naming `backend` as the offending field and naming
  both accepted values

### Requirement: A Shared `base_url` Key Serves Whichever Backend Is Selected

`openkos.yaml` MUST accept an optional `base_url` key naming the chat
endpoint for the currently selected `backend`, used by both `ollama` and
`openai-compatible` alike — there is no per-backend-name key namespace. WHEN
absent, the `ollama` backend resolves its own default as before this change
(see Endpoint Resolution Precedence below); the `openai-compatible` backend
has no packaged default and MUST refuse to be selected without an explicit
`base_url` (see "`openai-compatible` Backend Requires An Explicit `base_url`"
below).

#### Scenario: A configured base_url is used by whichever backend is selected

- GIVEN `openkos.yaml` sets `backend: openai-compatible` and
  `base_url: http://127.0.0.1:8000`
- WHEN a chat client is constructed for this workspace
- THEN it targets `http://127.0.0.1:8000`

### Requirement: `openai-compatible` Backend Requires An Explicit `base_url`

Unlike `ollama`, which has a packaged default endpoint, there is no common
default port across OpenAI-compatible servers (llama.cpp `8080`, LM Studio
`1234`, vLLM `8000`). WHEN the configured `backend` is `openai-compatible`
and `base_url` is absent, `read_config` MUST refuse with a clear message
naming `base_url` as required for this backend, rather than guessing a
default endpoint. This requirement is independent of endpoint-resolution
precedence: `OLLAMA_HOST` is never consulted for the `openai-compatible`
backend and cannot substitute for a missing `base_url`.

#### Scenario: openai-compatible without base_url is refused

- GIVEN an `openkos.yaml` containing `backend: openai-compatible` and no
  `base_url` key
- WHEN `read_config` parses it
- THEN it raises an error naming `base_url` as required for the
  `openai-compatible` backend, with no default endpoint assumed

#### Scenario: openai-compatible with base_url is accepted

- GIVEN an `openkos.yaml` containing `backend: openai-compatible` and
  `base_url: http://127.0.0.1:8080`
- WHEN `read_config` parses it
- THEN it succeeds and `Config.base_url` equals `http://127.0.0.1:8080`

#### Scenario: The ollama backend still requires no base_url

- GIVEN an `openkos.yaml` containing `backend: ollama` (or no `backend` key)
  and no `base_url` key
- WHEN `read_config` parses it
- THEN it succeeds, unaffected by this requirement

### Requirement: An Optional `embedding_base_url` Serves A Separate Embedding Endpoint

`openkos.yaml` MUST accept an optional `embedding_base_url` key, used only
for embedding requests. WHEN absent, embedding requests MUST target the same
endpoint `base_url` (or its backend's own default) resolves for chat. This
key exists because an OpenAI-compatible embedding model is often served by a
separate process or port from the chat model.

#### Scenario: Embedding requests use the separate endpoint when configured

- GIVEN `openkos.yaml` sets `base_url: http://127.0.0.1:8000` and
  `embedding_base_url: http://127.0.0.1:8001`
- WHEN an embed client is constructed for this workspace
- THEN it targets `http://127.0.0.1:8001`, not `http://127.0.0.1:8000`

#### Scenario: Embedding falls back to the chat endpoint when unset

- GIVEN `openkos.yaml` sets `base_url: http://127.0.0.1:8000` and no
  `embedding_base_url`
- WHEN an embed client is constructed for this workspace
- THEN it targets `http://127.0.0.1:8000`

### Requirement: A `base_url` Or `embedding_base_url` Containing Userinfo Is Refused

`read_config` MUST reject a `base_url` or `embedding_base_url` value whose
authority component carries userinfo (a bare `user`, or `user:password`,
before the `@` separating it from the host), for either key, regardless of
which `backend` is selected. The refusal message MUST point the user at
`OPENKOS_OPENAI_API_KEY` as the correct place for a credential, since a URL
embedded in a committed `openkos.yaml` is not.

#### Scenario: A base_url with userinfo is refused

- GIVEN `openkos.yaml` sets `base_url: http://user:pw@127.0.0.1:8080`
- WHEN `read_config` parses it
- THEN it raises an error naming the userinfo as the problem and pointing at
  `OPENKOS_OPENAI_API_KEY` as the correct place for a credential

#### Scenario: An embedding_base_url with userinfo is refused

- GIVEN `openkos.yaml` sets `embedding_base_url: http://user@127.0.0.1:8081`
- WHEN `read_config` parses it
- THEN it raises the same userinfo refusal, naming `embedding_base_url`

#### Scenario: A userinfo-free base_url is accepted

- GIVEN `openkos.yaml` sets `base_url: http://127.0.0.1:8080` with no
  userinfo present
- WHEN `read_config` parses it
- THEN it succeeds

### Requirement: Endpoint Resolution Precedence

For the `ollama` backend, the effective endpoint MUST resolve by this
precedence, highest first: (1) an explicit constructor argument, (2) the
`OLLAMA_HOST` environment variable, (3) the workspace's configured
`base_url` (or `embedding_base_url` for the embedding endpoint), (4) the
packaged Ollama default. A user who sets only `OLLAMA_HOST` MUST see the
identical effective endpoint as before this change, whether or not
`base_url` is also present in `openkos.yaml`.

For the `openai-compatible` backend, `OLLAMA_HOST` MUST NOT be consulted:
the endpoint resolves as (1) an explicit constructor argument, (2) the
configured `base_url` (or `embedding_base_url`), with no packaged default
(`base_url` is required for this backend). An Ollama-specific environment
variable MUST never redirect an OpenAI-compatible request, or the optional
API key it carries, to another host.

#### Scenario: OLLAMA_HOST wins over a configured base_url

- GIVEN `backend: ollama`, `OLLAMA_HOST` set in the environment, and
  `openkos.yaml` also setting a different `base_url`
- WHEN a client is constructed with no explicit constructor argument
- THEN the effective endpoint is the value of `OLLAMA_HOST`, not `base_url`

#### Scenario: A configured base_url is used when OLLAMA_HOST is unset

- GIVEN `OLLAMA_HOST` is unset and `openkos.yaml` sets `base_url`
- WHEN a client is constructed with no explicit constructor argument
- THEN the effective endpoint is the configured `base_url`

#### Scenario: The packaged default applies when nothing overrides it

- GIVEN neither `OLLAMA_HOST` nor `base_url` is set
- WHEN a client is constructed with no explicit constructor argument
- THEN the effective endpoint is the backend's packaged default

#### Scenario: OLLAMA_HOST never redirects the openai-compatible backend

- GIVEN `backend: openai-compatible`, `base_url: http://127.0.0.1:8080`, and
  `OLLAMA_HOST` set to a different host
- WHEN a client is constructed with no explicit constructor argument
- THEN the effective endpoint is `http://127.0.0.1:8080`, and no request
  or API key is sent to the `OLLAMA_HOST` value

#### Scenario: A user who only ever set OLLAMA_HOST sees no behavior change

- GIVEN a workspace with `OLLAMA_HOST` set in the environment and no
  `base_url`/`backend` key ever added to `openkos.yaml`
- WHEN this change is applied and a client is constructed
- THEN the effective endpoint is identical to what it resolved to before
  this change

### Requirement: The API Key Is Read Only From An Environment Variable

An optional API key for the `openai-compatible` backend MUST be read only
from the `OPENKOS_OPENAI_API_KEY` environment variable, never from a
`openkos.yaml` key. Its absence MUST NEVER gate startup or any command: a
server that requires no key MUST work identically whether or not the
variable is set. The key MUST be read in the application-layer resolver and
passed to the client as an explicit constructor argument — `llm/` stays
config-free and reads no environment variable itself.

WHEN `openkos.yaml` contains a top-level `api_key`, `openai_api_key`, or
`OPENKOS_OPENAI_API_KEY` key, `read_config` MUST refuse it with a message
stating that the API key is read only from the environment, because `init`
autocommits `openkos.yaml` to the user's repository and a key written there
would be committed alongside it.

The `OPENAI_API_KEY` environment variable (without the `OPENKOS_` prefix)
MUST NEVER be read by any part of this resolver, under any backend or
condition: a user with that variable already exported for an unrelated
cloud tool MUST NOT have its value silently forwarded, as a credential, to
whatever `base_url` names.

#### Scenario: A present key is passed to the client

- GIVEN `OPENKOS_OPENAI_API_KEY` is set in the environment
- WHEN an `openai-compatible` chat or embed client is constructed
- THEN that value is passed as the client's API key argument

#### Scenario: An absent key never blocks startup

- GIVEN `OPENKOS_OPENAI_API_KEY` is unset and the configured server requires
  no key
- WHEN a chat or embed client is constructed and used
- THEN the call succeeds normally, with no `Authorization` header sent

#### Scenario: The key is never written to openkos.yaml

- GIVEN any resolved API key value
- WHEN `openkos.yaml` is written by any command
- THEN no key value appears in it

#### Scenario: A key placed in openkos.yaml is refused at config read

- GIVEN `openkos.yaml` contains a top-level `api_key` (or `openai_api_key`,
  or `OPENKOS_OPENAI_API_KEY`) value
- WHEN `read_config` parses it
- THEN it raises an error stating the API key is read only from the
  environment and naming `OPENKOS_OPENAI_API_KEY` as the correct variable

#### Scenario: OPENAI_API_KEY is never read

- GIVEN `OPENAI_API_KEY` (no `OPENKOS_` prefix) is set in the environment and
  `OPENKOS_OPENAI_API_KEY` is unset
- WHEN an `openai-compatible` chat or embed client is constructed
- THEN no API key is passed to the client, no `Authorization` header is
  sent, and `OPENAI_API_KEY` is never read by the resolver

### Requirement: A Non-Loopback Key Send Over Plain HTTP Is Warned

WHEN an API key is present AND the effective endpoint classifies as
non-local (per the shared locality classifier) AND the endpoint's scheme is
plain `http://` (not `https://`), the resolver MUST print a warning to
stderr stating that a credential is about to be sent unencrypted to a
non-local host, before the first request using that key is made. This
warning MUST NOT block the request; it is advisory only.

#### Scenario: A key sent to a remote host over http warns

- GIVEN an API key is configured and the effective endpoint is
  `http://203.0.113.5:8000` (non-loopback, plain HTTP)
- WHEN a client using that endpoint and key is constructed
- THEN a warning is printed to stderr before any request is sent, naming the
  unencrypted-credential risk

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

### Requirement: One Resolver Seam Constructs Every Chat And Embed Client

`application/backends.py`'s chat-client construction MUST dispatch between
injected concrete-client factories by `cfg.backend`, and MUST additionally
expose an `embed_client()` function following the same injected-factory,
backend-dispatch shape for embedding construction. The application layer
itself MUST bind no concrete backend class of its own — both functions take
the concrete classes as caller-injected factory arguments. Every chat
construction site and every embed construction site in `cli/main.py`,
`cli/curate.py`, and `mcp/server.py` MUST go through one of these two
functions rather than constructing `OllamaClient` or
`OpenAICompatibleClient` directly.

#### Scenario: Chat construction dispatches by cfg.backend

- GIVEN `cfg.backend == "openai-compatible"` and both concrete client
  classes injected as factories
- WHEN the chat-client resolver is called
- THEN it constructs and returns an instance of the `openai-compatible`
  factory, not the `ollama` one

#### Scenario: embed_client mirrors chat_client's dispatch shape

- GIVEN `cfg.backend == "openai-compatible"` and both concrete client
  classes injected as factories
- WHEN `embed_client()` is called
- THEN it constructs and returns an instance of the `openai-compatible`
  factory, using `embedding_base_url` per its own resolution rule

#### Scenario: No embed construction site bypasses the resolver

- GIVEN the source of `cli/main.py`, `cli/curate.py`, and `mcp/server.py`
- WHEN their embed-client construction call sites are inspected
- THEN every one of them calls `embed_client()` rather than constructing
  `OllamaClient` or `OpenAICompatibleClient` directly

#### Scenario: A single test seam patches both backends

- GIVEN a unit test that wants to exercise either backend selection with no
  network access
- WHEN it patches the resolver's injected factories
- THEN no test that selects either backend can reach the network, regardless
  of which backend `cfg.backend` names
