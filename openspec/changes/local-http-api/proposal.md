# Proposal: A Local HTTP API Over The Stable Python API

MVP 6 stage "The local API" ([roadmap](../../../docs/roadmap.md), MVP 6;
[ADR-0058](../../../docs/adr/0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md),
[ADR-0060](../../../docs/adr/0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md)).
The full analysis is in `exploration.md` in this folder; this proposal does not
repeat it. Paths are under `src/openkos` unless they start with `tests/`, `docs/`,
`openspec/` or `pyproject.toml`.

## Intent

The desktop app's shell (Tauri) cannot call Python in-process. It needs a local,
authenticated transport to the engine that exposes exactly what `openkos.api` v1
offers: workspace status, the pending report, the open decisions with preview and
apply, judging quote and run, and the model-free queue refresh. ADR-0058 fixed the
security shape of that transport (loopback bind, a per-launch bearer token delivered
over stdin, Host and Origin checks on every route) but nothing implements it, and
`openkos.api` is today guarded so that nothing outside it may import it.

Why now: `openkos.api` v1 has landed (ADR-0060, PRs #1398-#1401), so the operation set
is frozen and versioned. The local API is the next MVP 6 stage and the last engine
piece the desktop shell depends on.

## Binding scope decisions

Settled by the user (Engram `decision/local-http-api-framework`,
`decision/local-http-api-scope`, `decision/local-http-api-disclosure`). Spec and
design must not reopen them.

1. **Framework**: FastAPI on uvicorn.
2. **Disclosure**: owner-local pass-through, as in the CLI and `openkos.api`. Every
   response in this stage returns full content, including confidential-derived text,
   to the token holder. The sensitivity disclosure gate is deferred to the first
   ask/query endpoint. The spec states this explicitly so that it reads as a decision,
   not an omission. The adapter does not import `openkos.mcp.gate`.
3. **Token wire form is stateless**: the client echoes the full `QuoteToken` and
   `DecisionVersion` as JSON; the server keeps no quote or version registry.
4. **Naming**: package `src/openkos/localapi/`; CLI verb `openkos serve`.
5. **Packaging**: `fastapi` and `uvicorn` ship as the optional extra `openkos[api]`;
   `fastapi`, `uvicorn` and `httpx` are also in the dev group so CI tests, MyPy and
   coverage run. `openkos serve` without the extra exits with an install remedy, and
   the wheel smoke test without the extra still passes.
6. **Judging progress**: a polled job resource (start, poll, stop), at most one active
   run. Server-Sent Events are out of scope.
7. **Outcomes as status codes**: the returned outcome values `Stale`, `QuoteStale`,
   `NotApplied` and `RunRefused` map to HTTP 409 or 422 with the body
   `{code, retryable, message}`; the full table follows exploration section 2.
8. **Write lock wait**: default and ceiling 5 s; a busy workspace returns 503 with
   `Retry-After`.
9. **Versioning**: URL prefix `/v1`, tied to `API_VERSION`.
10. **TypeScript client**: a committed `openapi.json` snapshot test in this stage; a
    one-off measured TypeScript generation recorded in design and verify; a Node CI
    job is deferred to the desktop-shell stage.
11. **CORS preflight** handling is recorded in a new ADR-0061; ADR-0058 is not edited.

## Scope

### In scope

- **`openkos.localapi` package**: an app factory, routers under `/v1`, and response
  models that reuse the `openkos.api` dataclasses directly, with HTTP DTOs only where
  a type is not servable (such as `JudgingOutcome.failure`, which is an exception
  instance).
- **ADR-0058 checks as ASGI middleware**, applied to every route, in the order Host
  (loopback and the bound port), then CORS preflight handling, then Origin
  allowlist, then bearer token (401 missing, 403 wrong). Preflight carries no
  `Authorization` header, so it is answered before the token check but never before
  the Host check.
- **Endpoints**, one per v1 operation: `GET status`, `GET pending`,
  `GET decisions`, `POST decisions/{key}/preview`, `POST decisions/{key}/apply`
  (with the echoed `DecisionVersion`), `POST queue/refresh`, `POST judging/quote`,
  and the judging job resource (`POST judging/runs` with the echoed `QuoteToken`
  returning 202 and a run id; `GET judging/runs/{id}`; `POST judging/runs/{id}/stop`),
  plus a health probe. ADR-0058 requires the checks on every route, so the probe is
  authenticated unless design records a reason otherwise. Exact paths are design's
  call.
- **Error table**: subclass-first mapping from the `OpenKosError` hierarchy and the
  neutral `Backend*` errors to status codes and fixed messages, with
  `{code, retryable, message}` bodies; `WorkspaceBusyError` to 503 with
  `Retry-After`; unknown exceptions to a generic 500 that never echoes `str(exc)`.
  `WriteRefused.message` is the one documented user-facing text passed through.
- **Threading**: short operations run as plain `def` endpoints on the anyio
  threadpool; the judging run executes on a dedicated daemon thread with a
  cooperative `StopToken`, so a minutes-long model call never starves the pool. The
  core stays synchronous (ADR-0021).
- **`openkos serve --workspace PATH`**: reads the token as one line from stdin before
  binding, never from argv, environment, file or URL; binds `127.0.0.1:0` itself and
  hands the socket to uvicorn; announces readiness and the port as one JSON line on
  stdout; logs to stderr; exits gracefully on stdin EOF (no orphan when the shell
  dies) and on SIGTERM (sets the active run's `StopToken`, does not wait
  indefinitely). FastAPI and uvicorn are imported lazily inside the verb, pinned by a
  subprocess test as `mcp` is.
- **Contract snapshot**: `app.openapi()` compared to a committed `openapi.json`,
  regenerated deliberately; populated-model round-trip tests over HTTP for every
  response type.
- **Import guard amendment**: `openkos.localapi` becomes the only package outside
  `openkos.api` permitted to import `openkos.api`, and it imports nothing else from
  the engine except leaf types the API already exports. A new guard keeps
  `fastapi`, `uvicorn`, `starlette` and `pydantic` confined to `openkos.localapi`.
- **ADR-0061** (Proposed): FastAPI + uvicorn ASGI adapter; Pydantic as a dependency
  for the first time and the narrowed scope of the old "no pydantic" rule; optional
  extra versus core; threadpool versus the MCP thread-per-call model; middleware
  order including CORS preflight; the stdin-token, port-0, ready-line launch;
  `opentelemetry-api` as a transitive hard dependency of FastAPI.
- **Docs where the general shape changes**: `AGENTS.md` (scope the pydantic sentence
  to LLM extraction and the core), `openspec/config.yaml` context line,
  `docs/tech_stack.md`, `docs/architecture.md` (the adapter package ships),
  `docs/cli.md` (the `serve` verb; the "planned" sentence), `docs/roadmap.md` (MVP 6
  stage status).

### Out of scope

- The sensitivity disclosure gate, and any ask/query, ingest, doctor or setup
  endpoint. They arrive with a later API slice.
- Server-Sent Events or any streaming progress transport.
- A server-side quote or decision-version registry.
- The desktop shell, its packaging and freezing, and a Node or TypeScript CI job.
- Any change to `openkos.api` signatures, `API_VERSION`, the application services,
  on-disk formats or store schemas.
- Edits to ADR-0058, ADR-0021, ADR-0027 or any accepted ADR. ADR-0058 stays Proposed.
- Windows named pipes and real DNS-rebinding measurement (ADR-0058's "not measured"
  items).
- Multi-workspace serving; the workspace is fixed at launch, as with `mcp`.

## Capabilities

### New Capabilities

- `local-http-api`: the loopback transport and its promises. It covers launch (stdin
  token, port 0, ready line, stdin-EOF and SIGTERM shutdown, missing-extra remedy);
  the Host, CORS, Origin and token checks on every route; the `/v1` endpoints and
  their 1:1 mapping to `openkos.api` v1; the stateless token wire form; the error
  table and `{code, retryable, message}` body; the write lock-wait policy; the
  judging job resource (single active run, poll, stop); the owner-local disclosure
  statement with the gate deferred to ask/query; and the committed OpenAPI snapshot.

### Modified Capabilities

- `stable-python-api`: MODIFIED requirement "An Import Guard Bars Interactive And
  Disclosing Layers From The API" — its third check ("no module outside
  `openkos.api` imports it") admits `openkos.localapi` as the sole importer, and the
  scenario "Nothing outside the API imports it" is amended accordingly. The
  owner-local statement ("The Surface Is Owner-Local And Applies No Disclosure
  Gate") is expected unchanged; the spec phase confirms.
- `mcp`: **expected unchanged.** The local API is a distinct adapter; the MCP
  layering test keeps barring `openkos.mcp` from owner-local services. The spec
  phase confirms and writes no delta if none is needed.

## Approach

1. **Thin adapter, approach A from exploration.** `openkos.localapi` imports only
   `openkos.api`. It holds one `Workspace` handle opened at launch and calls the v1
   functions; it owns transport, serialization, error mapping and the judging job
   registry, nothing else. This keeps the existing layering test unchanged: no new
   package reaches `application/` directly.
2. **Serialization from the dataclasses first.** FastAPI response models are the
   `openkos.api` frozen dataclasses wherever Pydantic can serve them; HTTP DTOs are
   introduced only where it cannot (the exception-typed `failure` field) or where
   the TypeScript contract needs a discriminator on an otherwise structural union.
   Design decides per type, guided by the populated round-trip tests, the
   `MappingProxyType` fields, `prefixItems` tuples and the `-Input`/`-Output` schema
   naming.
3. **Security before routing.** The ADR-0058 checks live in ASGI middleware, not in
   per-route dependencies, so no route can be added without them. A test enumerates
   every registered route and asserts each rejects a missing token, a wrong token, a
   foreign Host and a foreign Origin.
4. **Launch mirrors `mcp`.** Lazy import inside the verb body, stdout reserved for
   the one ready line, logs to stderr, stdin as the lifeline.
5. **Contract freezing mirrors `api_surface.json`.** A committed `openapi.json` and a
   regeneration path; changing it is deliberate, like the Python surface snapshot.

**Slice forecast** (auto-chain; about 400 authored lines per PR is advisory, and
tests and docs are never cut to fit it; `uv.lock` is generated and excluded):

| Slice | Content | Size |
|---|---|---|
| S1 | ADR-0061; `[api]` extra and dev-group deps in `pyproject.toml`; `AGENTS.md`, `openspec/config.yaml` and `docs/tech_stack.md` text | ~200 |
| S2 | Package, app factory, Host/CORS/Origin/token middleware, error table, health and the read endpoints (status, pending, decisions) with populated-model tests, import-guard amendment | ~350-400 (split middleware vs endpoints if over) |
| S3 | Preview, apply and refresh endpoints; `DecisionVersion` wire form; lock-wait policy; 409/422/503 mapping tests | ~350 |
| S4 | Judging quote; run job resource (start, poll, stop), dedicated thread, outcome DTO; thread and stop tests | ~400 |
| S5 | `openkos serve`: stdin token, port 0, ready line, stdin-EOF and SIGTERM shutdown, lazy-import test, subprocess integration test, missing-extra remedy, wheel-smoke-without-extra check, `docs/cli.md` | ~400 |
| S6 | Committed `openapi.json` snapshot test; measured one-off TypeScript generation recorded; `docs/architecture.md` and `docs/roadmap.md`; delta specs and archive | ~250 |

About 2,000 authored changed lines across six PRs. Chain strategy for auto-chain is
not yet chosen; the previous MVP 6 stage used stacked-to-main.

**TDD:** strict mode is active, runner `uv run pytest`. Each code slice needs an
observed RED before implementation.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/localapi/` | New | App factory, middleware, routers, DTOs, error table, judging job registry, launch helpers |
| `src/openkos/cli/main.py` | Modified | `serve` verb with lazy import and the missing-extra remedy; read-only/lock classification |
| `pyproject.toml` | Modified | `[project.optional-dependencies] api`; dev group gains `fastapi`, `uvicorn`, `httpx` |
| `tests/unit/api/test_api_guards.py` | Modified | Admit `openkos.localapi` as the sole importer of `openkos.api` |
| `tests/unit/localapi/` | New | Middleware matrix, endpoints, error table, job, OpenAPI snapshot, confinement guard |
| `tests/unit/cli/` | New/Modified | Lazy-import subprocess test, `serve` launch and shutdown tests |
| `.github/workflows/ci.yml` (build job), `tests/unit/cli/` | Modified / New | CI step: the wheel installed without `[api]` runs `openkos --help` (succeeds) and `openkos serve` (exits with the install remedy); plus an in-process unit test |
| `openspec/specs/local-http-api/spec.md`, `stable-python-api/spec.md` | New / Delta | At archive |
| `docs/adr/0061-*.md`, `docs/adr/README.md` | New / Modified | Written in S1 |
| `AGENTS.md`, `openspec/config.yaml`, `docs/tech_stack.md`, `docs/architecture.md`, `docs/cli.md`, `docs/roadmap.md` | Modified | Shape changes only |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| The echoed `QuoteToken`/`DecisionVersion` JSON shape becomes a contract once `/v1` is consumed | High | The OpenAPI snapshot pins it; design fixes the shape before S3/S4 merge |
| `JudgingOutcome.failure` (an exception instance) breaks app build as a response model | High | Outcome DTO with a failure code and fixed message, as in the MCP error table |
| Populated-model serialization (`MappingProxyType`, `tuple[int, int]`, undiscriminated unions) was never exercised over HTTP | Medium | Populated round-trip tests per response type in S2-S4 |
| CORS preflight ordering against the token check is untested by ADR-0058 or the spike | Medium | Middleware matrix test covers preflight with and without a valid Host; ADR-0061 records the order |
| Owner-local responses carry confidential-derived text over HTTP | Known, decided | Loopback only, per-launch token held only by the shell; the spec states it; the gate arrives with ask/query |
| A stuck model call cannot be preempted; shutdown could hang | Medium | Judging runs on a daemon thread; SIGTERM sets the `StopToken` and exits after a bounded wait |
| Threadpool model deviates from MCP's thread-per-call plus `Semaphore(4)` | Low | Recorded in ADR-0061; the core stays synchronous |
| FastAPI pulls Pydantic and `opentelemetry-api` into an engine that declared no Pydantic | Medium | Optional extra; confinement guard keeps them inside `openkos.localapi`; docs scoped |
| The wheel without the extra breaks `openkos --help` | Low | Lazy import plus a smoke test without the extra |
| TypeScript generation from the schema is unmeasured | Medium | One-off measured generation recorded in design/verify; Node CI deferred |
| The spec overstates shipped code | Medium | At verify and archive, check requirements against the shipped behavior and this Intent |

## Rollback Plan

The change adds an optional package, a CLI verb and an optional extra. It changes no
`openkos.api` signature, no application service, no on-disk format and no store
schema, so no workspace data depends on it. Each slice is a self-contained PR:
reverting S2-S4 removes endpoints that nothing in the engine calls; reverting S5
removes the `serve` verb and its docs; reverting S1 removes the extra and the ADR
(which is still Proposed). The import-guard amendment reverts with the package, so
the guard returns to "nothing outside `openkos.api` imports it". `/v1` is not
considered published until the desktop shell consumes it, so reverting before then
breaks no external client.

## Dependencies

- `openkos.api` v1 and ADR-0060 (merged).
- ADR-0058 (security shape, Proposed), ADR-0018 and ADR-0021 (synchronous core, async
  only at adapter edges), ADR-0059 (judging, `StopToken`, observer).
- `fastapi`, `uvicorn` (new optional extra), `httpx` (dev only, for `TestClient`).

## Non-negotiables preserved

- **Local-first and private.** Loopback only, no account, no cloud, no new key; the
  token never touches argv, environment, a file or a URL.
- **Sensitivity.** Confidential content never leaves the device: the transport is
  loopback-bound and token-authenticated, and the owner-local scope is an explicit,
  recorded decision. The disclosure gate arrives with the first ask/query endpoint.
- `raw/` is untouched and `bundle/` stays pure OKF; the adapter writes only through
  existing `openkos.api` operations.
- **Reconstructible.** The adapter holds no canonical state; the judging job registry
  is in-memory and lost on restart by design, with per-chunk persistence below it.
- **Human curates, engine maintains.** Apply stays an explicit per-decision request
  carrying the version the user saw; judging still requires an echoed quote.
- The core stays synchronous, there is no `engine.py`, and no agent framework is
  added.

## Success Criteria

- [ ] `openkos serve --workspace PATH`, given a token on stdin, binds `127.0.0.1` on
      an ephemeral port, prints exactly one JSON ready line with the port, and exits
      cleanly on stdin EOF and on SIGTERM (subprocess test).
- [ ] Every registered route rejects a missing token (401), a wrong token (403), a
      non-loopback or wrong-port Host, and a disallowed Origin; a CORS preflight from
      an allowed origin succeeds without a token (route-enumerating test).
- [ ] Each v1 operation is reachable under `/v1` and returns a populated response
      that round-trips; apply with a stale `DecisionVersion` and run with a stale
      `QuoteToken` return 409 with `{code, retryable, message}`.
- [ ] A busy workspace returns 503 with `Retry-After` within the 5 s ceiling; a client
      cannot request a longer wait.
- [ ] A judging run is started, polled to completion and stopped through the job
      resource; a second concurrent start returns 409.
- [ ] No response body echoes `str(exc)` except `WriteRefused.message`.
- [ ] `app.openapi()` equals the committed `openapi.json`; a one-off TypeScript
      generation from it is measured and recorded.
- [ ] `openkos --help` and the wheel smoke test pass without the `[api]` extra;
      `openkos serve` without it exits non-zero with an install remedy; importing the
      CLI does not import `fastapi` (subprocess test).
- [ ] The import guard admits only `openkos.localapi` as an importer of
      `openkos.api`; `fastapi`, `uvicorn`, `starlette` and `pydantic` are confined to
      `openkos.localapi`; `openkos.localapi` does not import `openkos.mcp.gate`.
- [ ] ADR-0061 is written with status Proposed and indexed; the `local-http-api` spec
      and the `stable-python-api` delta are merged at archive with every heading
      name-matched.
- [ ] `uv run pytest --cov` passes at or above 90% branch coverage, along with
      `ruff check .`, `ruff format --check .`, `mypy .` and the eval self-tests.
