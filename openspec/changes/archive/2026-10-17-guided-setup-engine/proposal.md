# Proposal: Setup Readiness And A Model Pull Through `openkos.api`

Tracking issue: #1486. Follow-up decision: #1487 (how the app creates the first
workspace). Governing decisions: ADR-0047 (the 32 GB memory floor and the judge
models), ADR-0057 (Proposed: a user-installed Ollama behind a guided setup),
ADR-0060 (the versioned Python API), ADR-0061 (the local API adapter), ADR-0065
(the generated TypeScript client). No new decision record: the choices below
are reversible within the additive rule and are recorded in `design.md`. Paths
are under `src/openkos` unless they start with `tests/`, `docs/`, `desktop/` or
`openspec/`.

## Intent

Guided setup is a core MVP 6 deliverable: the app detects a missing runtime or
model, explains it, and pulls the models with visible progress (roadmap, "Guided
setup"). Today only the terminal can. `openkos doctor` detects an unreachable
Ollama or a missing model and prints `ollama pull <model>`; `openkos.api` has no
setup operation, so the local API and the desktop client can neither ask whether
the machine is ready nor pull a model.

## Scope

### In scope

- **Readiness.** `setup_readiness(workspace)` reports, with no model call and no
  write: the configured backend; the runtime's host (redacted), whether it is
  local, whether it answers, and its version (Ollama only); each configured model
  (the chat model, the embedding model and every task model that differs from the
  chat model, as `doctor` enumerates them) with its roles, whether it is
  required, whether it is installed and whether it can be pulled here; and the
  machine's memory against the 32 GB floor ADR-0047 measured. It reuses
  `doctor`'s primitives: the same resolver, the same tag matching and one shared
  task-model enumeration that `doctor` itself now calls.
- **Pull.** `pull_model(workspace, model, *, stop, observer)` pulls one configured
  model through Ollama's streaming `/api/pull`, reporting aggregated byte progress
  and observing a cooperative stop between progress lines. A model the workspace
  does not configure, or a backend that cannot pull, is refused as a value. An
  installed model returns `already_installed` without a pull.
- **Ollama client.** `OllamaClient.version()` (`GET /api/version`) and
  `OllamaClient.pull()` (`POST /api/pull`, NDJSON), behind a capability Protocol
  in `llm/base.py`.
- **Local API.** `GET /v1/setup/readiness`, and the pull as a single pollable job
  with stop (`POST /v1/setup/pulls`, `GET /v1/setup/pulls/{id}`,
  `POST /v1/setup/pulls/{id}/stop`), following the judging-run pattern.
- **Client.** The TypeScript client gains `setupReadiness`, `startModelPull`,
  `modelPull` and `stopModelPull`, regenerated from the OpenAPI snapshot.

### Out of scope

- The setup screens (`desktop/ui/`).
- Installing Ollama: the app sends the user to the official download (ADR-0057);
  readiness carries that address.
- Creating the first workspace over HTTP: the server is bound to an existing
  workspace, so the conservative default is that the shell runs the engine's
  `init` (#1487).
- A pull for the `openai-compatible` backend, which has no pull protocol.
- Probing the embedding endpoint separately when it differs from the chat
  endpoint: readiness probes one host, as `doctor` does.

## Principles

- **Local-first.** Readiness talks only to the configured runtime host. The pull
  asks that runtime to fetch a model the workspace already names; no user
  content is sent anywhere, and the engine itself contacts no other host.
- **Human curates, engine maintains.** A pull is started only by an explicit
  request; readiness never pulls.
- Nothing under `raw/` or `bundle/` is read or written; no lock is taken.

## Approach

A narrow `application/setup.py` service (ADR-0018) with injected client
factories; the API wraps it in API-owned frozen dataclasses (ADR-0060); the
local adapter serves the read on the worker pool and the pull on its own daemon
thread with an in-memory registry, as judging does (ADR-0061).

## Rollback

Every addition is additive within `API_VERSION` 1. Reverting the slices removes
the routes, the API names and the client methods; nothing persisted depends on
them, and a partial download is Ollama's own state, resumed on the next pull.

## Risks

- Ollama's `/api/pull` stream shape is not under this project's control
  (ADR-0057 accepts this); the parser tolerates unknown fields and statuses.
- A pull of several GB holds one daemon thread for minutes; at most one pull is
  active, and shutdown abandons it (a stopped pull resumes later).
