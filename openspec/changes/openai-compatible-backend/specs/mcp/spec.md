# Delta for MCP

## MODIFIED Requirements

### Requirement: Chat-Client Construction Moves To `application/backends.py` Behind An Injected Factory, With CLI Delegators Preserved

The definitions of chat-client construction and local-exemption resolution
MUST live in `application/backends.py`, taking the concrete client class as
an injected factory argument rather than importing one directly — the
application layer MUST bind no concrete backend of its own. This chat-client
construction function MUST additionally dispatch between injected factories
by `cfg.backend` (`ollama` or `openai-compatible`), and
`application/backends.py` MUST expose a parallel `embed_client()` function
following the same injected-factory, backend-dispatch shape for embedding
construction. `cli/main.py` MUST keep its existing `_chat_client` and
`_resolve_local_exemption` names, each reduced to a one-line delegator that
calls the `application/backends.py` definition. This is a relocation of the
definitions with the CLI's call sites left in place as delegators — not call
sites repointed to `application` directly — so every existing test seam that
patches `_chat_client`/`_resolve_local_exemption` by name keeps working, and
the CLI's own observable behavior is unaffected. Both the CLI and the MCP
adapter build their chat AND embed clients through these same,
non-CLI-importable definitions, injecting both concrete client classes as
factories so either can be selected by `cfg.backend`.
(Previously: this requirement described chat-client construction and
local-exemption resolution only, with no backend dispatch and no
`embed_client()`, because only one backend — and no centralized embed
construction — existed.)

#### Scenario: The definition takes the client class as an injected factory

- GIVEN `application/backends.py`'s chat-client construction function
- WHEN its signature is inspected
- THEN it accepts the concrete client class(es) as injected factory
  arguments, and the module imports no concrete client class of its own

#### Scenario: The CLI keeps one-line delegators under their existing names

- GIVEN `cli/main.py` after this relocation
- WHEN `_chat_client` and `_resolve_local_exemption` are inspected
- THEN each still exists under its existing name as a single-line call into
  `application/backends.py`'s definition

#### Scenario: The CLI's observable behavior, and its test seam, are unaffected

- GIVEN the CLI's existing chat-client and local-exemption behavior, and
  the existing tests that patch `_chat_client`/`_resolve_local_exemption` by
  name
- WHEN the CLI runs any command that constructs a backend, after this
  relocation
- THEN its observable behavior is unchanged, and the same patches still take
  effect

#### Scenario: Chat construction dispatches to the factory matching cfg.backend

- GIVEN `cfg.backend == "openai-compatible"` and both concrete client
  classes injected as factories
- WHEN the chat-client resolver is called by either the CLI or the MCP
  adapter
- THEN it constructs and returns an instance of the `openai-compatible`
  factory, not the `ollama` one

#### Scenario: The MCP adapter builds its embed client through embed_client()

- GIVEN `mcp/server.py`'s embed-construction call site
- WHEN it needs an embedding client
- THEN it calls `application/backends.py`'s `embed_client()` rather than
  constructing `OllamaClient` or `OpenAICompatibleClient` directly

### Requirement: Layering Keeps The Core Free Of The Adapter, And The Adapter Free Of The CLI

`openkos.mcp` MUST import only: `application` modules, `openkos.config`,
`openkos.read_outcome`, the LLM backend's base interface, the Ollama and
OpenAI-compatible clients' concrete classes and their exception types
(needed to construct either backend and to map its exceptions to tool-error
codes), and the FTS module's unavailability exception; it MUST NOT import
`openkos.cli`, and it MUST NOT import `openkos.graph` — `navigate` reaches
the graph exclusively through an `application` service, not directly.
`openkos.sensitivity` MUST be imported by exactly one module under `mcp/`
(`gate.py`); no other `mcp` module MUST reference the disclosure predicate
or its set-producing sibling. `application` MUST NOT import `openkos.mcp`.
The CLI MUST import `openkos.mcp` lazily, inside the `mcp` verb's own
function body, so `asyncio` never loads on the CLI's ordinary startup path.
(Previously: the allowed concrete-client import was "the Ollama client's
concrete class and its exception types" only, because no second backend
existed.)

#### Scenario: openkos.mcp never imports openkos.cli

- GIVEN a static import check of every module under `src/openkos/mcp/`
- WHEN its imports are inspected
- THEN `openkos.cli` is absent from all of them

#### Scenario: openkos.mcp never imports openkos.graph

- GIVEN a static import check of every module under `src/openkos/mcp/`
- WHEN its imports are inspected
- THEN `openkos.graph` is absent from all of them

#### Scenario: Only gate.py imports the disclosure predicate module

- GIVEN a static import check of every module under `src/openkos/mcp/`
  other than `gate.py`
- WHEN its imports are inspected
- THEN none of them imports `openkos.sensitivity`

#### Scenario: application never imports openkos.mcp

- GIVEN a static import check of every module under
  `src/openkos/application/`
- WHEN its imports are inspected
- THEN `openkos.mcp` is absent from all of them

#### Scenario: The CLI imports mcp lazily, only inside the verb

- GIVEN a static import check of `cli/main.py`'s module-level imports
- WHEN they are inspected
- THEN `openkos.mcp` is absent from them, and appears only inside the `mcp`
  verb's function body

#### Scenario: mcp/server.py may import both concrete client classes

- GIVEN `mcp/server.py`'s imports, needed to inject both backends' concrete
  classes as factories into the resolver
- WHEN a static import check runs
- THEN both `OllamaClient` and `OpenAICompatibleClient` (and their exception
  types) are permitted imports for `mcp/server.py`, and the ban on importing
  `openkos.cli`/`openkos.graph` still holds
