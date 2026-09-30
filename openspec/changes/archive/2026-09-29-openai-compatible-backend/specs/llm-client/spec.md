# Delta for LLM Client

## MODIFIED Requirements

### Requirement: Model And Base URL Are Configurable

The model tag and base URL MUST be caller-supplied arguments, not
hard-coded. The base URL MUST default to `http://localhost:11434` when no
override is given, and MUST resolve by this precedence, highest first: an
explicit constructor argument, then the `OLLAMA_HOST` environment variable,
then the workspace's configured `base_url` (see `backend-selection`), then
the default. A user who sets only `OLLAMA_HOST` MUST see the identical
effective endpoint as before this change, whether or not a `base_url` key is
also present in `openkos.yaml`.
(Previously: the override source was described only as "e.g. via
`OLLAMA_HOST` or an equivalent caller-supplied value," with no explicit
precedence against a separately configured `base_url`, because
`openkos.yaml` had no `base_url` key at all.)

#### Scenario: Default base URL used when no override given

- GIVEN an `OllamaClient` constructed with only a model tag
- WHEN `chat(messages)` is called
- THEN the request targets `http://localhost:11434/api/chat`

#### Scenario: Base URL override is honored

- GIVEN an `OllamaClient` constructed with an explicit base URL override
- WHEN `chat(messages)` is called
- THEN the request targets that overridden base URL, not the default

#### Scenario: OLLAMA_HOST takes precedence over a configured base_url

- GIVEN `OLLAMA_HOST` is set in the environment and the workspace's
  `openkos.yaml` also sets a different `base_url`
- WHEN an `OllamaClient` is constructed with no explicit constructor
  argument
- THEN the effective endpoint is the value of `OLLAMA_HOST`, not `base_url`

#### Scenario: A configured base_url is used when OLLAMA_HOST is unset

- GIVEN `OLLAMA_HOST` is unset and the workspace's `openkos.yaml` sets
  `base_url`
- WHEN an `OllamaClient` is constructed with no explicit constructor
  argument
- THEN the effective endpoint is the configured `base_url`

#### Scenario: A user who only ever set OLLAMA_HOST sees no behavior change

- GIVEN a workspace with `OLLAMA_HOST` set in the environment and no
  `base_url`/`backend` key ever added to `openkos.yaml`
- WHEN this change is applied and `chat(messages)` is called
- THEN the request targets the identical endpoint it targeted before this
  change
