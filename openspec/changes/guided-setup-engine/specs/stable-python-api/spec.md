# Delta for stable-python-api

## ADDED Requirements

### Requirement: Setup Readiness Is A Model-Free Read

`openkos.api.setup_readiness(workspace)` MUST return a `SetupReadiness` with:

- `runtime`: the configured `backend` (`ollama` or `openai-compatible`), the
  runtime's `host` as the userinfo-redacted display host, `is_local`,
  `reachable`, `version` (the runtime's version string on `ollama` when it
  answers, otherwise null) and `download_url` (the official Ollama download page
  on `ollama`, otherwise null).
- `models`: one entry per distinct configured tag, in the order chat model,
  embedding model, then task models by task name, where the task models are the
  per-task models that differ from the chat model, enumerated by the same
  function `doctor` uses. Each entry carries `tag`, `roles` (`chat`,
  `embedding` or the task names), `required` (true for the chat and embedding
  models only), `installed` (null when the runtime did not answer) and
  `pullable` (true on `ollama` only).
- `memory`: `total_bytes` (null where it cannot be read), `floor_bytes`
  (32 GiB, ADR-0047) and `meets_floor` (null when `total_bytes` is null).
- `ready`: true exactly when the runtime answered and every required model is
  installed.

It MUST NOT call a model, write anything or take the workspace lock. On
`ollama` it MUST probe `GET /api/version` and, only when that answers, list the
installed models; on `openai-compatible` reachability MUST be the model listing.
An unreachable runtime MUST be reported as `reachable: false`, never raised. A
configuration that cannot be read MUST raise `WorkspaceUnreadable`.

#### Scenario: A ready machine

- GIVEN a workspace on `ollama` whose runtime answers version `0.32.9` and lists
  the chat and embedding models
- WHEN `setup_readiness` is called
- THEN `runtime.reachable` is true, `runtime.version` is `0.32.9` and `ready` is
  true

#### Scenario: No runtime is running

- GIVEN a workspace on `ollama` whose host refuses connections
- WHEN `setup_readiness` is called
- THEN `runtime.reachable` is false, every model's `installed` is null, `ready`
  is false, `download_url` names the Ollama download, and nothing was raised

#### Scenario: A missing embedding model blocks readiness

- GIVEN a reachable runtime that lists the chat model only
- WHEN `setup_readiness` is called
- THEN the embedding model's entry is `installed: false`, `required: true`, and
  `ready` is false

#### Scenario: A missing judge model does not block readiness

- GIVEN a reachable runtime with the chat and embedding models but not the judge
  model
- WHEN `setup_readiness` is called
- THEN the judge model is listed with roles `adjudication` and `contradiction`,
  `required: false`, `installed: false`, and `ready` is true

#### Scenario: Memory below the floor is reported, not enforced

- GIVEN a machine reporting 16 GiB of memory
- WHEN `setup_readiness` is called
- THEN `memory.meets_floor` is false and the call still succeeds

### Requirement: A Configured Model Is Pulled With Progress And A Cooperative Stop

`openkos.api.pull_model(workspace, model, *, stop=None, observer=None)` MUST
pull `model` through the configured runtime and return a `PullOutcome` with
`model`, `status` (`completed`, `already_installed` or `stopped`),
`completed_bytes` and `total_bytes`, or a returned `PullRefused` with `model`
and `reason`:

- `not_configured` when `model` is not one of the tags `setup_readiness` lists,
  in which case nothing is sent to the runtime;
- `backend_cannot_pull` when the backend is not `ollama`.

A model the runtime already lists MUST return `already_installed` without a
pull. Otherwise the service MUST check `stop` once before the stream starts and
after every progress line, and on a stop MUST close the stream and return
`stopped`. The observer's `progress` MUST receive `PullProgress` events with
`model`, `phase` (`preparing`, `downloading`, `verifying` or `finishing`),
`completed_bytes` and `total_bytes` summed over the layers reported so far; the
runtime's own status text MUST NOT be forwarded. An unreachable runtime MUST
raise `BackendUnavailable`; a runtime that reports the model does not exist
MUST raise `BackendModelNotFound`; any other error line MUST raise
`BackendError`. The pull MUST NOT write under the workspace or take its lock.

#### Scenario: A pull reports progress and completes

- GIVEN a configured, uninstalled model and a runtime that streams two layers
  then `success`
- WHEN `pull_model` is called with an observer
- THEN the observer saw non-decreasing `completed_bytes` and the outcome is
  `completed`

#### Scenario: A stop between lines ends the pull

- GIVEN a pull in progress
- WHEN the stop token is set
- THEN the stream is closed after the current line and the outcome is `stopped`

#### Scenario: An unconfigured model is refused before any request

- GIVEN a model tag the workspace does not configure
- WHEN `pull_model` is called
- THEN it returns `PullRefused(reason="not_configured")` and the runtime
  received no request

#### Scenario: An installed model is not pulled again

- GIVEN the runtime already lists the model
- WHEN `pull_model` is called
- THEN it returns `already_installed` and no pull request was made

### Requirement: Setup Extends v1 Additively

`setup_readiness`, `pull_model`, `SetupReadiness`, `RuntimeReadiness`,
`ModelReadiness`, `MemoryReadiness`, `PullProgress`, `PullObserver`,
`PullOutcome` and `PullRefused` MUST be added to `__all__` and to the golden
snapshot without raising `API_VERSION`, and the surface classifier MUST classify
the change as additive.

#### Scenario: The classifier accepts the additions

- GIVEN the surface on the base branch
- WHEN the setup names are added
- THEN the classifier reports an additive change and `API_VERSION` is still 1
