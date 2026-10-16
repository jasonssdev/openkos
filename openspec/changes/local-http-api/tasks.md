# Tasks: A Local HTTP API Over The Stable Python API

MVP 6 stage "The local API". Inputs: `proposal.md`, `design.md` (authoritative),
`specs/local-http-api/spec.md`, `specs/stable-python-api/spec.md` (delta) and ADR-0061
(content in `design.md` Decision 13, status Proposed, written in S1). No tracking
issue is assumed. No commit or PR text may use `Closes`, `Fixes` or `Resolves`
unless the issue number is repeated with the keyword per issue; a PR names the change
in prose ("the `local-http-api` change") and uses `Refs #N` where an issue applies.

Spec shorthand. `L1`..`L16` are the requirements of `local-http-api`, in file order:
L1 launched by `openkos serve` with a stdin token; L2 shuts down when the parent goes
away; L3 missing `api` extra fails with an install remedy; L4 every route passes the
Host check; L5 every route passes the Origin check; L6 CORS preflight answered
without a token but never without a valid Host and Origin; L7 every route requires
the bearer token; L8 operations served under `/v1`, one per Python API operation;
L9 responses are the Python API's result types, populated and round-tripping; L10
echoed tokens make the wire form stateless; L11 outcomes and errors map to status
codes with a uniform body; L12 writes wait at most five seconds for the lock; L13
judging runs as a single pollable job; L14 responses are owner-local, no disclosure
gate; L15 framework confined to the adapter package, adapter imports only the API;
L16 committed OpenAPI snapshot. `P1` is the single MODIFIED requirement of the
`stable-python-api` delta ("An Import Guard Bars Interactive And Disclosing Layers
From The API"). `D1`..`D14` are the design decisions.

Slice naming. The design's S2a/S2b are split further here (see the forecast), so the
design's "S2b" (read endpoints) is **S2c** below. Every other design slice keeps its
name; S5 is split into S5a and S5b. Mapping: design S2a = S2a + S2b; design S2b =
S2c; design S5 = S5a + S5b.

## Review Workload Forecast

Authored changed lines are additions plus deletions. `uv.lock` and the generated
`tests/unit/localapi/openapi.json` are excluded from estimates (the snapshot is still
part of its PR). The ~400-line figure is an advisory heuristic: no test or doc is cut
and no slice is split artificially to fit it.

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,800 authored across 11 slices (uv.lock and openapi.json excluded) |
| 400-line budget risk | High in total; per slice below (S3 and S4b at the advisory line, none clearly over) |
| Chained PRs recommended | Yes |
| Suggested split | S1 -> S2a -> S2b -> S2c -> S3 -> S4a -> S4b -> S5a -> S5b -> S6 -> S7 (11 PRs, merged in this order) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Per-slice forecast

| Slice | Title | Forecast (authored) | Depends on | Over ~400? |
|-------|-------|---------------------|------------|------------|
| S1 | ADR-0061, `[api]` extra, docs text | ~180 | none | No |
| S2a | ASGI guard and error envelope | ~260 | S1 | No |
| S2b | App factory, health, route enumeration, import guards, OpenAPI snapshot | ~220 | S2a | No |
| S2c | Read endpoints: status, pending, decisions | ~380 | S2b | No |
| S3 | Preview, apply, refresh; write error rows; lock wait | ~420 | S2c | At the line (accepted) |
| S4a | Run registry, admission stop, observer, shutdown (no HTTP) | ~350 | S2b | No |
| S4b | Quote and run routes; DTOs; backend rows; admission pin | ~400 | S3, S4a | At the line (accepted) |
| S5a | `launch.py`: token reader, bind, ready line, lifelines (in-process) | ~270 | S4b | No |
| S5b | `serve` verb, missing-extra remedy, CI wheel step, subprocess tests, `docs/cli.md` | ~230 | S5a | No |
| S6 | TypeScript measurement (recorded, not committed), architecture and roadmap docs | ~60 | S5b | No |
| S7 | Docs check, spec merge, ADR-0061 Accepted, archive | ~100 | S6 | No |

**Why S2a (~450 in the design) and S5 (~480) were split.** Both have a coherent
seam, so under auto-chain they are split rather than excepted:

- S2a/S2b: the guard (`_guard.py`, the error envelope, the synthetic-scope ASGI
  matrix) is a standalone, fully testable deliverable that needs no FastAPI app.
  The app factory, the route-enumeration matrix (which needs a route table), the
  Decision 10 import guards (which need an importer of `openkos.api`), the OpenAPI
  snapshot machinery and the API-importer amendment follow. Neither half ships a
  guard without its proof.
- S5a/S5b: `launch.serve` is exercised in-process (injected `stdin`, `stdout`,
  `install_signals=False`) and carries the coverage; the verb, the missing-extra
  remedy, the real-process lifecycle tests and the CI step need the verb to exist.
  S5a ships a Python function with no CLI entry point (nothing calls it); S5b adds
  the entry point and the end-to-end proof.

S3 and S4b sit at the advisory line because their error-table and run-lifecycle
matrices are the point of the slice; no coherent split remains (a write endpoint
without its 409/422/503 rows, or a run route without its admission pin, would ship
without its proof). Neither needs a decision before apply.

**Serialization.** Every slice from S2b onward regenerates
`tests/unit/localapi/openapi.json` (the snapshot lands in S2b), so slices are
serialized: slice N+1 branches from `main` after slice N is squash-merged, then
regenerates the snapshot rather than merging the JSON. S4a is the only slice with no
snapshot change and could be opened beside S3, but the chain order above is the plan
so each PR opens against a settled `main`.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| S1 | Record ADR-0061, declare the `api` extra, scope the docs text | PR 1 (base `main`) | `uv run pytest tests/unit/test_api_extra.py tests/unit/docs` | `uv lock --check`; `uv run python -c "import fastapi, uvicorn, pydantic, httpx"` | Revert removes the extra, dev deps and a Proposed ADR |
| S2a | Pure-ASGI guard with the shared error envelope | PR 2 (base `main`) | `uv run pytest tests/unit/localapi/test_guard.py` | The guard called directly with synthetic scopes and a recording inner app | Revert removes `localapi/_guard.py` and `_errors.py`; nothing imports them |
| S2b | App factory, authenticated health, route enumeration, import guards, OpenAPI snapshot | PR 3 (base `main`) | `uv run pytest tests/unit/localapi tests/unit/api/test_api_guards.py tests/unit/application/test_curate_decisions_boundary.py` | `TestClient(create_app(...))` `GET /v1/health` with and without the token | Revert removes the package skeleton; the API-importer amendment reverts with it |
| S2c | Status, pending and decisions endpoints, populated | PR 4 (base `main`) | `uv run pytest tests/unit/localapi/test_reads.py` | Real initialized workspace served through `TestClient`; held workspace lock | Revert removes three routes and `DecisionList` |
| S3 | Preview, apply, refresh; echo; write error rows; lock wait | PR 5 (base `main`) | `uv run pytest tests/unit/localapi/test_decisions_write.py tests/unit/localapi/test_errors.py` | Preview then apply on a real two-decision workspace; real held lock with `WRITE_WAIT_SECONDS` patched to 0.1 | Revert removes three routes, request models and write rows |
| S4a | Run registry with admission, observer, stop, shutdown | PR 6 (base `main`) | `uv run pytest tests/unit/localapi/test_runs_registry.py` | Stubbed `api.run_judging` gated on `threading.Event`; `shutdown(grace)` with a blocked stub | Revert removes `_runs.py`; nothing routes to it |
| S4b | Quote and run routes, DTOs, backend rows, admission pin | PR 7 (base `main`) | `uv run pytest tests/unit/localapi/test_judging_routes.py tests/unit/localapi/test_admission_pin.py` | Real `api.run_judging` with a recording chat factory for each kind | Revert removes five routes, `RunStatus`, `JudgingOutcomeBody`, lifespan shutdown |
| S5a | `launch.serve` in-process: token, bind, ready line, lifelines | PR 8 (base `main`) | `uv run pytest tests/unit/localapi/test_launch.py` | In-process `serve` with injected `stdin`/`stdout`, `install_signals=False` | Revert removes `launch.py`; no entry point existed |
| S5b | `openkos serve`, missing-extra remedy, CI wheel step, subprocess lifecycle, `docs/cli.md` | PR 9 (base `main`) | `uv run pytest tests/unit/cli/test_serve_cmd.py tests/unit/localapi/test_launch_process.py` | Spawn the CLI, token on stdin, one ready line, probe from a child process, close stdin | Revert removes the verb, the CI step and the cli.md section |
| S6 | Measured TypeScript generation (not committed); shape docs | PR 10 (base `main`) | `uv run pytest tests/unit/docs` | The recorded `openapi-typescript` + `tsc --strict` run in a scratch directory | Docs only |
| S7 | Spec merge, ADR-0061 Accepted, archive | PR 11 (base `main`) | `uv run pytest tests/unit/docs` (ADR-status and index tests) | Name-match every spec heading against the main specs | Docs only; revert restores the unmerged deltas |

## Conventions For Every Slice

- **Branch and PR naming.** Branch off `origin/main`. PR title is a Conventional
  Commit using the scopes from `AGENTS.md`:

  | Slice | Branch | PR title |
  |-------|--------|----------|
  | S1 | `feat/local-api-adr-and-extra` | `docs(docs): record the local API as a FastAPI adapter shipped as an optional extra` |
  | S2a | `feat/local-api-guard` | `feat(api): add the ASGI guard for the local HTTP API` |
  | S2b | `feat/local-api-app-skeleton` | `feat(api): add the local API app factory with an authenticated health route` |
  | S2c | `feat/local-api-reads` | `feat(api): serve status, pending and decisions over the local API` |
  | S3 | `feat/local-api-writes` | `feat(api): preview, apply and refresh over the local API` |
  | S4a | `feat/local-api-run-registry` | `feat(api): add the judging run registry for the local API` |
  | S4b | `feat/local-api-judging` | `feat(api): quote and run judging through the local API` |
  | S5a | `feat/local-api-launch` | `feat(api): launch the local API server with a stdin token` |
  | S5b | `feat/local-api-serve-verb` | `feat(cli): add openkos serve for the local HTTP API` |
  | S6 | `docs/local-api-shape` | `docs(docs): describe the shipped local API in architecture and roadmap` |
  | S7 | `docs/local-api-archive` | `docs(sdd): archive the local-http-api change and accept ADR-0061` |

- **Commits are work units**: each task closes with at least one Conventional Commit
  carrying its tests and docs. No `Co-Authored-By`, no AI attribution.
- **Locate every target by content**, never by a line number copied from `design.md`
  (`cli/main.py:387`, `ci.yml:208`, `judging.py:1147` and so on). They drift.
  Re-derive each against the working tree before editing.
- **Strict TDD** (`uv run pytest`): RED observed failing for the stated reason (an
  `ImportError` counts only for a test of a module that does not exist yet; any
  other RED must fail on an assertion), then GREEN, then REFACTOR. Record the
  command and the observed result. A test that passes first try is mutated before it
  is trusted. Patches of `openkos.api.<fn>` are read through the module attribute
  (`api.status(...)`); an unread monkeypatch fails silently, so every stub also
  records that it was called.
- **No network in unit tests.** The unit suite's guard refuses loopback `connect`
  (D11, correction 5). `TestClient` opens no socket and is fine; a test that needs a
  real HTTP request makes it from a child Python process.
- **Gates per slice**, each run directly and never piped (a pipe hides failures from
  `pytest` and `ruff format --check`), after the last change:
  1. `uv run ruff format --check .`
  2. `uv run ruff check .`
  3. `uv run mypy .` (the whole repo, not `src/`)
  4. `uv run pytest --cov > <scratchpad>/pytest-<slice>.log 2>&1` (90% branch
     coverage; read the log, do not pipe)
  5. `uv run python evals/run_self_tests.py`
  Do not rely on `pre-commit` alone; it misses what CI lints.
- **Mutation protocol** ("independent mutation" notes): make the mutation with an
  exact `Edit` on the one line the test pins, preferring a change in byte length; run
  only the test under proof and observe it FAIL for the expected assertion; revert
  with the inverse `Edit` (never `git checkout --`); purge bytecode with
  `find src tests -name __pycache__ -type d -prune -exec rm -rf {} +`; re-run to
  GREEN.
- **New files** are `git add`-ed before any review or diff-based check.
- **OpenAPI snapshot regeneration** (S2b onward): `uv run python -m
  tests.unit.localapi.openapi_snapshot --write`, in the same commit as the change
  that moves the contract; the diff summary is pasted in the PR body. Never
  hand-merge the JSON; rebase onto `main` and regenerate.
- **Delivery** is `auto-chain` with `stacked-to-main`: each slice is its own PR
  targeting `main`. Prefer opening slice N+1 only after slice N is squash-merged and
  green; if opened early on the previous branch, rebase `--onto main` past the old
  base after the squash (a stacked PR on a squashed base goes DIRTY and gets no CI)
  and confirm check-runs on the head SHA. Delete merged branches locally and
  remotely. Skills: `/Users/jasonssdev/.agents/skills/chained-pr/SKILL.md` and
  `/Users/jasonssdev/.agents/skills/work-unit-commits/SKILL.md`.
- **Documentation policy.** `docs/` changes only for shape; no counts ("eleven
  endpoints"), no "since #NNN". `openapi.json` and `--help` are the authorities.

---

## S1: ADR-0061, the `api` extra, docs text (~180 authored lines)

Goal: record the decision and make the optional dependency installable, with no
runtime code. Satisfies: L15 (the extra is optional), D12, D13. Depends on: none.

- [x] S1.1 RED: create `tests/unit/test_api_extra.py`: read `pyproject.toml` with
  `tomllib`; assert `fastapi`, `uvicorn` and `pydantic` appear under
  `[project.optional-dependencies].api` and in none of `[project].dependencies`;
  assert the dev group lists `fastapi`, `httpx`, `pydantic` and `uvicorn`; assert
  `watch` is untouched. Observe RED (extra missing). Satisfies: L15.
- [x] S1.2 GREEN: edit `pyproject.toml` per D12 (`api = ["fastapi>=0.143.2",
  "pydantic>=2.14.1", "uvicorn>=0.54.0"]` with the explanatory comment; dev group
  gains `fastapi>=0.143.2`, `httpx>=0.28.1`, `pydantic>=2.14.1`,
  `uvicorn>=0.54.0`); regenerate `uv.lock` with `uv lock` and confirm `uv lock
  --check` passes. `uv.lock` is excluded from the estimate. Satisfies: L15.
- [x] S1.3 RED then GREEN, ADR gate: copy `docs/adr/template.md` to
  `docs/adr/0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md`;
  transcribe Context, Decision (seven points), Consequences and Alternatives from D13
  (read-only source: `design.md`); status **Proposed**, with `description`, date and
  timestamp; add the row to `docs/adr/README.md`. Run the existing ADR-status test
  (`tests/unit/docs`) first and observe it RED if the README row is missing, then
  GREEN. ADR-0058 is not edited. Satisfies: D13.
- [x] S1.4 Docs text, shape only: `AGENTS.md` ("LLM calls" bullet: `pydantic` is not
  a core dependency, it arrives only with the `[api]` extra confined to
  `openkos.localapi`, ADR-0061; "The core is synchronous" bullet: async only at the
  adapter edges `mcp/` and `localapi/`); `openspec/config.yaml` `context` line (same
  statement); `docs/tech_stack.md` (the Pydantic rows and the "no web framework is a
  dependency yet" sentence, located by content). Do not touch any other ADR.
  Satisfies: D13.
- [x] S1.5 Survival: `uv run python -c "import fastapi, uvicorn, pydantic, httpx"`
  succeeds; `openkos --help` still works. Mutation: move `fastapi` into the core
  dependency list and observe S1.1 fail; revert, purge, GREEN. Satisfies: L15.
- [x] S1.6 Run the five gates. Commits: `docs(docs):` for the ADR and text;
  `feat(api): declare the api optional extra` for the `pyproject.toml` and lock
  change with its test (`build` and `chore` are not project scopes). Satisfies: L15,
  D12, D13.

Branch/PR: `feat/local-api-adr-and-extra`. Independent mutation: list `uvicorn` in the
core dependencies; the S1.1 test must fail.

---

## S2a: The ASGI guard and the error envelope (~260 authored lines)

Goal: the security-critical code, reviewable whole, with its full synthetic-scope
matrix, before any route exists. Satisfies: L4, L5, L6, L7, D4, and the guard rows of
D6. Depends on: S1.

Threat-matrix cases carried here (each is a RED test before its production task):
Host (`evil.com`, `127.0.0.1:<other>`, missing, duplicated, `[::1]:<port>`); CORS
preflight (foreign Origin, disallowed method or header, foreign Host); Origin
(foreign, `null`); Token (missing, wrong scheme, duplicated, wrong value, query
string only); Proxy headers (a forwarded Host does not bypass the Host check);
Token handling (digest only, never in `repr`).

- [x] S2a.1 RED: create `tests/unit/localapi/__init__.py` and
  `tests/unit/localapi/conftest.py` (a recording inner ASGI app, a scope builder, a
  fixed port and token digest). Create `tests/unit/localapi/test_guard.py` Host
  cases: exact `127.0.0.1:P` and `localhost:P` pass (case-insensitive); `evil.com`,
  `127.0.0.1:Q`, absent, duplicated and `[::1]:P` give 403 `host_not_allowed` and the
  inner app is not reached; a forwarded `X-Forwarded-Host` does not change the
  verdict. Observe RED (`ModuleNotFoundError`). Satisfies: L4.
- [x] S2a.2 RED: preflight cases in the same file: an allowlisted Origin with
  `Access-Control-Request-Method: POST` and headers `authorization, content-type` and
  no `Authorization` gets 204 with the exact `Allow-Origin`, `Allow-Methods: GET,
  POST`, `Allow-Headers: Authorization, Content-Type`, `Max-Age: 600`, `Vary:
  Origin`; foreign Origin, `DELETE`, an unlisted request header and a foreign Host
  each get 403 (`preflight_rejected`, or `host_not_allowed` for a foreign Host) with
  no `Access-Control-Allow-*` header; the Host check runs before the preflight
  answer; a preflight never reaches the inner app. Satisfies: L4, L6.
- [x] S2a.3 RED: Origin cases: absent Origin passes; each allowlisted origin
  (`tauri://localhost`, `http://tauri.localhost`, `https://tauri.localhost`) passes;
  `https://evil.example` and `null` give 403 `origin_not_allowed`; a disallowed
  Origin with no token is 403, not 401. Satisfies: L5.
- [x] S2a.4 RED: token cases: no header, `Basic x`, an empty bearer value and two
  `Authorization` headers give 401 `unauthorized` with `WWW-Authenticate: Bearer`; a
  wrong bearer value gives 403 `forbidden`; the right token passes; the token only in
  the query string gives 401; the comparison uses `hmac.compare_digest` over
  `sha256` (assert via a monkeypatch that records the call, plus equal-length digest
  inputs). Satisfies: L7.
- [x] S2a.5 RED: response-header and scope cases: an allowed-Origin response carries
  `Access-Control-Allow-Origin`, `Vary: Origin` and `Access-Control-Expose-Headers:
  Location, Retry-After`, on success and on a guard rejection; never
  `Access-Control-Allow-Credentials`; a `lifespan` scope passes through; a
  `websocket` scope is closed with code 1008; guard bodies are exactly
  `{code, retryable, message}` with `retryable` false. Satisfies: L6, L11 (guard rows).
- [x] S2a.6 RED: token-handling case: `ServerSettings`-style holder `repr` contains
  neither the token nor its digest bytes (the settings class itself lands in S2b;
  here assert on the guard's own config object, `_GuardConfig`, with
  `field(repr=False)` on the digest). Satisfies: D4.
- [x] S2a.7 GREEN: create `src/openkos/localapi/__init__.py` (docstring only,
  imports nothing: states that responses are owner-local, loopback, token-gated, and
  that the disclosure gate is deferred to the first ask/query endpoint; L14),
  `src/openkos/localapi/_errors.py` (only `ErrorBody` as a frozen
  `BaseModel(extra="forbid")`, the guard `code` constants, `WRITE_WAIT_SECONDS`,
  `RETRY_AFTER_SECONDS`), and `src/openkos/localapi/_guard.py` implementing D4 in
  the stated code order with hand-encoded JSON responses and stdlib-only typing. No
  `starlette` or `anyio` import. Satisfies: L4, L5, L6, L7, L14.
- [x] S2a.8 REFACTOR and survival: the guard file reads top to bottom as the four
  checks. Independent mutations (one at a time, reverted byte-exactly, bytecode
  purged): swap the Host and Origin checks; answer the preflight before the Host
  check; replace `compare_digest` with `==`; add `Allow-Credentials`. Each must
  redden a named test. Satisfies: L4..L7.
- [x] S2a.9 Run the five gates; commits `test(api): ...` is folded into
  `feat(api): add the ASGI guard ...` with its tests. Satisfies: L4..L7.

Branch/PR: `feat/local-api-guard`. Independent mutation: answer a foreign-Host
preflight with 204; the S2a.2 test must fail.

---

## S2b: App factory, authenticated health, route enumeration, import guards, snapshot (~220 authored lines)

Goal: a servable, fully guarded app with one route, the proof that no route can opt
out, the confinement guards and the contract snapshot machinery that every later slice
regenerates. Satisfies: L7, L8 (health, prefix), L11 (framework rows), L14, L15, L16,
P1. Depends on: S2a.

- [ ] S2b.1 RED: `tests/unit/localapi/test_routes_security.py` against
  `TestClient(create_app(settings), base_url=f"http://127.0.0.1:{PORT}")` used as a
  context manager (runs the lifespan): the route set is asserted exactly (health
  only, so far); each route x {no token 401, wrong token 403, `Host: evil.com` 403,
  right host wrong port 403, foreign Origin with a valid token 403, token only in
  the query 401}; preflight from the allowed Origin with no token 204; preflight with
  a foreign Host 403; an unknown path without a token is 401 and with a token is 404
  `not_found`; the conventional documentation paths (`/docs`, `/redoc`,
  `/openapi.json`) are 404 with a token; `DELETE /v1/health` with a token is 405
  `method_not_allowed`; health with a token returns `{status: "ok", api_version:
  API_VERSION, engine_version}`. Observe RED (no `create_app`). Satisfies: L4..L8,
  L11.
- [ ] S2b.2 RED: `tests/unit/localapi/test_errors.py` (framework rows only, the
  table grows in later slices): request-validation error is 422 `invalid_request`
  and never echoes the submitted value (post a body containing a sentinel); an
  unknown exception raised by a stubbed route is 500 `internal_error` with a fixed
  message, logged to stderr (captured) without the token, and the next request is
  still served; `ApiProblem` renders its status, body and headers. Satisfies: L11.
- [ ] S2b.3 GREEN: extend `src/openkos/localapi/_errors.py` with `ApiProblem`,
  `invoke`, `error_body` (generic `internal_error` row only; later slices add rows)
  and the framework codes; create `_models.py` (`HealthBody`), `_routes.py`
  (`APIRouter(prefix=f"/v{api.API_VERSION}")`, router-level `responses` declaring
  401, 403 and 422 with `ErrorBody`, `GET /health` as a plain `def`), and `_app.py`
  (`ServerSettings` with `token_digest` `field(repr=False)`, `DEFAULT_ORIGINS`,
  `create_app`, `openapi_document`, the `ApiProblem`, validation, 404 and 405 and
  generic handlers, a lifespan with no run registry yet, `docs_url=None,
  redoc_url=None, openapi_url=None`, the guard as the only middleware). `_routes.py`
  calls the API through `from openkos import api`. Satisfies: L7, L8, L11, L14.
- [ ] S2b.4 RED then GREEN: add a test that `ServerSettings` `repr` contains neither
  the token nor the digest, and that the prefix equals `/v1` and follows
  `API_VERSION` (monkeypatched value gives `/v<N>`). Satisfies: L8.
- [ ] S2b.5 RED: `tests/unit/localapi/test_localapi_guards.py` (D10): every module
  under `src/openkos/localapi/` imports only the standard library (excluding
  `sqlite3`), `fastapi`, `pydantic`, `uvicorn`, exactly `openkos.api` and
  `openkos.localapi.*`; direct failing examples, written to a temporary package, for
  `openkos.mcp`, `openkos.mcp.gate`, `openkos.application`, `openkos.state`,
  `openkos.cli`, `typer`, `rich`, `starlette`, `anyio` and `openkos.api._reads`;
  confinement: no module under `src/openkos/` outside `localapi/` imports `fastapi`,
  `uvicorn`, `starlette`, `pydantic` or `anyio` (AST scan with a non-vacuity
  floor); fresh interpreter: `python -I -c "import openkos.cli.main"` leaves
  `fastapi`, `uvicorn`, `starlette`, `pydantic` and `openkos.localapi` out of
  `sys.modules`. Observe RED where the package or example is missing. Satisfies:
  L3 (CLI start-up never loads the framework), L14, L15.
- [ ] S2b.6 RED then GREEN, API-importer amendment (P1): in
  `tests/unit/api/test_api_guards.py`, change the "nothing outside `api/` imports
  `openkos.api`" check so the importers of `openkos.api` outside the API must all be
  files under `localapi/` and there must be at least one now that `_routes.py`
  imports it; keep the parametrized spelling matrix and add the two scenarios
  "a module outside both imports it fails, naming the module" and "a `localapi`
  module importing it passes". Satisfies: P1.
- [ ] S2b.7 Boundary docstring: reword `tests/unit/application/test_curate_decisions_boundary.py`
  module docstring (correction 8) to name the owner-local decision and the deferral
  of the gate to ask/query; the scan itself is unchanged. Add one assertion (or run
  the existing scan) that `localapi` is covered by construction and is not an
  allowed importer of `application/`. Satisfies: P1.
- [ ] S2b.8 RED then GREEN, contract: create `tests/unit/localapi/openapi_snapshot.py`
  (generator with `--write`, canonical JSON) and
  `tests/unit/localapi/test_openapi_snapshot.py`: `openapi_document()` equals the
  committed `tests/unit/localapi/openapi.json`; `create_app(settings).openapi()`
  equals it; the document is produced with no workspace on disk and no socket; every
  path starts with `/v1/`; no component name ends in `-Input`/`-Output` or is
  module-qualified; `info.version == str(api.API_VERSION)`; a bearer security scheme
  is present. Generate and `git add` the first `openapi.json`. Satisfies: L16.
- [ ] S2b.9 Test-layout check: `tests/unit/conftest.py` network guard is left
  untouched; assert nothing here is marked `cross_platform_smoke`. Mutation: add a
  second route to `_routes.py` without a security-matrix entry and observe the
  enumeration test's exact-set assertion fail; add `import starlette` to a module and
  observe S2b.5 fail; revert, purge, GREEN. Satisfies: L7, L15.
- [ ] S2b.10 Run the five gates; commits `feat(api):` (app) and `test(api):` (guards,
  amendment). Satisfies: L4..L8, L11, L14..L16, P1.

Branch/PR: `feat/local-api-app-skeleton`. Independent mutation: register the router
with a `dependencies` bypass that skips the guard for `/v1/health`; the enumeration
test must fail.

---

## S2c: Read endpoints (~380 authored lines)

Goal: status, pending and decisions served populated and round-tripping, owner-local.
Satisfies: L8, L9, L11 (read rows), L12 (reads ignore the lock), L14. Depends on: S2b.

Includes correction 1 (the `MappingProxyType` flattening failure) as the RED that
proves the failure and the fix.

- [ ] S2c.1 RED: `tests/unit/localapi/test_reads.py` populated models, patching
  `openkos.api.status`, `api.pending_report` and `api.list_decisions` with stubs that
  return the instance and record the call: `WorkspaceStatus` with `MappingProxyType`
  mappings for `concepts_by_type` and `open_decisions_by_kind`, `edges=(3, 1)`,
  non-empty `recent_activity` and `jobs`; `PendingReport` computed and not computed,
  distinct from zero open rows; decisions of each kind with every `Decision.detail`
  member (`IdentityDetail`, `RelationDetail`, `TierDetail`) and `None`, and a
  `WithheldAction`. Assert the exact JSON, then `TypeAdapter(T).validate_python(json)
  == instance`. The status test fails first with `TypeError: cannot pickle
  'mappingproxy'` from FastAPI's `asdict`; record that RED. Satisfies: L9.
- [ ] S2c.2 GREEN: add `DecisionList` to `_models.py`; add `GET /status`,
  `GET /pending`, `GET /decisions` as plain `def` endpoints in `_routes.py`, each
  calling `_errors.invoke(lambda: api.<fn>(settings.workspace))`; the status endpoint
  passes `dataclasses.replace(s, concepts_by_type=dict(...),
  open_decisions_by_kind=dict(...))`; response types are the API dataclasses (no DTO,
  D2). Satisfies: L8, L9.
- [ ] S2c.3 RED then GREEN, read-path error rows in `test_errors.py`:
  `QueueUnavailableError` is 503 `queue_unavailable` (retryable false);
  `WorkspaceNotFound` 500 `workspace_not_found`; `WorkspaceUnreadable` 500
  `workspace_unreadable`; each raised with a sentinel message that must not appear in
  the body; `OSError` and `ValueError` are 500 `internal_error`. Add the rows to
  `_errors.py` subclass-first. Satisfies: L11.
- [ ] S2c.4 RED then GREEN, real workspace: use the existing workspace fixture.
  Status, pending and decisions on an initialized workspace return 200 and the
  workspace is byte-identical afterwards; an absent queue reports the not-computed
  pending report; two open decisions are both listed; with the workspace lock held
  by another process (`lock.workspace_lock`), all three still succeed and none takes
  the lock or calls a model (client factories patched to fail). Satisfies: L8, L12.
- [ ] S2c.5 RED then GREEN, owner-local: a decision whose rationale derives from a
  confidential source is returned unredacted with a valid token, and the same request
  without a token or with a wrong one returns 401/403 with no content; assert
  `openkos.mcp.gate` is not in `sys.modules` after importing `openkos.localapi`
  in a fresh interpreter. Satisfies: L14.
- [ ] S2c.6 Extend the route-enumeration expected set in
  `test_routes_security.py` with the three routes and confirm the whole matrix runs
  over them; regenerate `tests/unit/localapi/openapi.json` and confirm
  `DecisionList` and the API types appear with short component names. Satisfies: L7,
  L16.
- [ ] S2c.7 Mutation: drop the `dict(...)` conversion in the status endpoint and
  observe S2c.1 redden with the pickling error; return the lock-taking path from a
  read and observe S2c.4 fail; revert, purge, GREEN. Satisfies: L9, L12.
- [ ] S2c.8 Run the five gates; commit `feat(api):`. Satisfies: L8, L9, L11, L12, L14.

Branch/PR: `feat/local-api-reads`. Independent mutation: serve `list_decisions` as a
bare top-level array; the exact-JSON test must fail.

---

## S3: Preview, apply, refresh; write error rows; lock wait (~420 authored lines)

Goal: the three state-changing and previewing operations with the stateless echoed
version, the outcome and exception tables for writes, and the five-second wait
policy. Satisfies: L8, L9, L10, L11, L12. Depends on: S2c.

Threat-matrix case carried here: Lock (a client-chosen long wait is impossible; 5 s
ceiling, 503); Error disclosure (sentinel tests for every new row).

- [ ] S3.1 RED: `tests/unit/localapi/test_decisions_write.py` request validation:
  `PreviewRequest`, `ApplyRequest` and `RefreshRequest` reject an unknown field
  (`extra="forbid"`), an unknown action, a malformed version, and a `wait_seconds`
  above 5 or below 0 (422 `invalid_request`, API not called); the refresh body may be
  omitted. Satisfies: L8, L12.
- [ ] S3.2 RED: echo and mapping, with `openkos.api` stubs that capture the received
  argument: `DecisionPreview.version` posted unchanged to apply reaches
  `api.apply_decision` `==` the issued value (dataclass equality); the path key
  differing from `version.decision_key` is 422 `key_mismatch` and the stub was not
  called; apply passes `wait_seconds == 5.0` by default and `on_warning` messages
  reach `ApplyResponse.warnings`; refresh passes `wait_seconds == 5.0`. Satisfies:
  L10, L12.
- [ ] S3.3 RED: outcome rows, parametrized over the table: `Stale` (each reason) 409
  `stale`; `NotApplied` `not_offered` 422 `action_not_offered`, `skipped` 422
  `skip_not_applicable`, `not_mergeable` 409, `member_missing` 409, `record_failed`
  409 with `retryable` true; messages are fixed (the stored `detail` of a
  `record_failed` is not echoed). Satisfies: L11.
- [ ] S3.4 RED: exception rows: `WorkspaceBusyError` 503 `workspace_busy` with
  `Retry-After: 2`; `WorkspaceLockUnavailableError` 500; `DriftDetected` 409
  `write_drift` retryable (subclass-first: not `write_refused`); `WriteFailed` 500;
  `WriteRefused` 422 `write_refused` whose body `message` equals the exception's own
  user-facing `message` (the one passthrough); `QueueRefreshRefused` 503; and each of
  these raised with a sentinel message that must not appear in the body (except the
  `WriteRefused` one). Satisfies: L11.
- [ ] S3.5 GREEN: add `PreviewRequest`, `ApplyRequest`, `RefreshRequest`,
  `ApplyResponse` to `_models.py`; add the outcome and exception rows plus
  `outcome_problem` to `_errors.py`; add `POST /decisions/{decision_key}/preview`,
  `POST /decisions/{decision_key}/apply`, `POST /queue/refresh` to `_routes.py`, each
  one `openkos.api` call through `invoke` (no composed operations, ADR-0021
  Decision Five). Satisfies: L8..L12.
- [ ] S3.6 RED then GREEN, real workspace: preview then apply one of two open
  decisions on a real two-decision workspace in a temp git repo; the other stays open;
  an altered version field is 409 `stale` and the bundle is unchanged; a version for
  a decision edited after preview is 409 `stale`; a new `create_app` instance on the
  same workspace applies a version from another instance (restart between preview
  and apply); a skip action posted to apply is 422 `skip_not_applicable`; preview of
  an action the decision does not offer is 422 `action_not_offered`; an unknown
  action in the body is 422 `invalid_request` before any operation ran. Satisfies:
  L10, L11.
- [ ] S3.7 RED then GREEN, lock wait: with `WRITE_WAIT_SECONDS` patched to 0.1 and
  the real workspace lock held by the test, apply and refresh answer 503
  `workspace_busy` with a positive integer `Retry-After` and write nothing; a lock
  released within the wait lets the write through (released from a timer thread,
  bounded waits only); `wait_seconds: 0` answers 503 at once; `wait_seconds: 3600` is
  422 `invalid_request` and the API stub was not called; reads still succeed while
  the lock is held. Satisfies: L12.
- [ ] S3.8 Extend the enumeration set and regenerate `openapi.json`; confirm
  `ApplyRequest`, `ApplyResponse`, `DecisionVersion`, `Action` appear, no `-Input` or
  `-Output` names, and `wait_seconds` carries `minimum 0` and `maximum 5`. Mutation:
  change `le=5.0` to `le=50.0` and observe S3.1 fail; swap the `DriftDetected` and
  `WriteRefused` row order and observe S3.4 fail; revert, purge, GREEN. Satisfies:
  L11, L12, L16.
- [ ] S3.9 Run the five gates; commits `feat(api):`. Satisfies: L8..L12.

Branch/PR: `feat/local-api-writes`. Independent mutation: skip the `key_mismatch`
check; the S3.2 test must fail. This slice is at the advisory ~400 lines because the
outcome and error matrices are the point; no split keeps each endpoint with its rows.

---

## S4a: Run registry, admission stop, observer, shutdown (~350 authored lines)

Goal: the judging job machinery with unit tests that need no HTTP. Satisfies: L2
(bounded stop), L13. Depends on: S2b.

- [ ] S4a.1 RED: `tests/unit/localapi/test_runs_registry.py` with a stubbed
  `api.run_judging` gated on `threading.Event`s (bounded waits only, no sleeps):
  `start` registers one active run and returns `RunStatus` once the stub calls
  `stop.is_set()` (admission); the stub returning `QuoteStale` before admission makes
  `start` raise `ApiProblem` 409 `quote_stale` and removes the run; `RunRefused`
  becomes 422 `run_refused`; a stub that raises before admission maps through the
  exception table and removes the run; a stub that raises after admission leaves the
  run `failed` with an `error` body; the stub returning a `JudgingOutcome` makes the
  run `finished` with a `JudgingOutcomeBody`; a second `start` while one is
  `running` or `stopping` (including during admission) is 409 `run_active`
  (retryable); after finishing, another start is accepted with a different run id;
  admission timeout (`ADMISSION_TIMEOUT_SECONDS` patched small) returns `running`
  and a later `QuoteStale` lands in `error` with state `failed`. Observe RED (module
  missing). Satisfies: L13.
- [ ] S4a.2 RED: poll and stop: `status` shows `index`, `total` from the latest
  `JudgingProgress` (null before the first event) and `persisted` from the latest
  cumulative `chunk_persisted`; `stop` sets the token, returns `state: "stopping"`,
  and after the stub observes it the run ends `finished` with a `stopped` outcome;
  stopping a finished or failed run returns the job unchanged; unknown run id is
  `ApiProblem` 404 `not_found`; eviction keeps the newest 16 finished or failed runs
  (older ones 404); a poll never reflects the run's own result in a status. Satisfies:
  L13.
- [ ] S4a.3 RED: `shutdown(grace)` returns within the grace while a stub stays
  blocked (the stub never returns), sets every stop token, and leaves a daemon
  thread; `shutdown` with no active run returns at once; chunks the stub reported as
  persisted are unaffected. Satisfies: L2, L13.
- [ ] S4a.4 GREEN: create `src/openkos/localapi/_runs.py`: `ADMISSION_TIMEOUT_SECONDS`,
  `SHUTDOWN_GRACE_SECONDS`, `FINISHED_RUNS_KEPT`, `_AdmissionStop` (a `StopToken`
  subclass whose first `is_set()` sets an admission event), the run observer,
  `RunRegistry` with one `threading.Lock`, `start`/`status`/`stop`/`shutdown`, and a
  `threading.Thread(daemon=True, name="openkos-judging-run")` per run. The module is
  named `_runs.py` (correction 3) and calls the API through `api.run_judging`. It
  needs `JudgingOutcomeBody` and `RunStatus`; add both to `_models.py` here with
  `failure: ErrorBody | None` (the DTO shape in Interfaces / Contracts) and the
  exception-to-`ErrorBody` mapping reused from `_errors.error_body`. Satisfies: L13.
- [ ] S4a.5 Run the guard tests: `test_localapi_guards.py` still passes (stdlib
  `threading` only) and the owner-local boundary scan does not flag `_runs.py`.
  Mutation: set the admission event on `start` instead of on the first `is_set()`
  and observe the stale-quote test fail; drop the single-active check and observe
  `run_active` fail; revert, purge, GREEN. Satisfies: L13, L15.
- [ ] S4a.6 Run the five gates; commit `feat(api):`. Satisfies: L2, L13.

Branch/PR: `feat/local-api-run-registry`. Independent mutation: have `shutdown` join
without a timeout; the blocked-stub test must hang-fail within its bounded wait.

---

## S4b: Quote and run routes, DTOs, backend rows, admission pin (~400 authored lines)

Goal: the five judging routes over the registry, the backend rows of the error table,
and the pin that the real service reaches admission before its first model call.
Satisfies: L8, L9, L10, L11, L13. Depends on: S3, S4a.

Threat-matrix case carried here: Lifecycle (stuck model call; SIGTERM sets the run
stop, wired in the lifespan).

- [ ] S4b.1 RED: `tests/unit/localapi/test_judging_routes.py` quote: `POST
  /judging/quote` with a chat client whose `chat` fails the test returns 200 with a
  `JudgingQuote` whose `token` is the full `QuoteToken` JSON (binding, `work` with
  `member_digests` as two-string arrays, `calls`), `QuoteRequest` validates `kind`
  and defaults the two booleans to false, an unknown `kind` is 422
  `invalid_request`. Populated round-trip with `TypeAdapter(JudgingQuote)`.
  Satisfies: L8, L9, L13 (quote costs no model call).
- [ ] S4b.2 RED: start/poll/stop over HTTP with a stubbed `api.run_judging`: start
  returns 202 with `Location: /v1/judging/runs/{id}` and a `RunStatus` before a
  blocked "model call" completes; the echoed `QuoteToken` reaches the stub `==` the
  issued value; poll is 200 for running, stopping, finished and failed runs;
  progress reaches the total and `outcome` is present when finished; stop returns
  `state: "stopping"` then `finished` with a stopped outcome; a second start while
  active is 409 `run_active`; unknown id on poll and stop is 404 `not_found`; the 17th
  finished run evicts the first; status and decisions are served promptly while a
  run is blocked. Satisfies: L10, L13.
- [ ] S4b.3 RED: token shapes: a body that omits the token or sets it null is 422
  `run_refused` and the stub was not called; a token that is a string is 422
  `invalid_request`; `QuoteStale` from the stub is 409 `quote_stale` with a message
  built only from the `ChangedField` literals; no job exists after either. Satisfies:
  L10, L11.
- [ ] S4b.4 RED: backend rows, parametrized: `BackendUnavailable` 503
  `backend_unavailable` retryable; `BackendModelNotFound` 424
  `backend_model_not_found`; `BackendGenerationCapped` 502 `generation_capped`; any
  other `BackendError` 502 `backend_error`; `JudgingRefused` 503 `judging_refused`;
  subclass-first (a `BackendGenerationCapped` is not `backend_error`); sentinel
  message never in the body. A completed run whose outcome carries `failure` shows
  `ErrorBody` with a code from the table and the fixed message, never exception
  text; a run that raises after admission is `failed` with `error`. Satisfies: L11,
  L13.
- [ ] S4b.5 RED: table completeness: a test builds the set of `code` values in the
  spec's error table (listed in the test as literals) and asserts each is produced by
  exactly one row of `_errors.py` or the guard, and that no extra code exists, so a
  row cannot be forgotten. Satisfies: L11.
- [ ] S4b.6 RED: `tests/unit/localapi/test_admission_pin.py` against the real
  `api.run_judging` with the existing judging fixtures, a recording chat-client
  factory (`chat_client=`) and a recording `StopToken` subclass, for each of the three
  kinds: `stop.is_set()` is called before the first chat call and after the binding
  check, so a moved binding returns `QuoteStale` with no chat call. Satisfies: L13.
- [ ] S4b.7 GREEN: add `QuoteRequest`, `RunRequest` to `_models.py`; add the backend
  and judging rows to `_errors.py`; add the five routes to `_routes.py` (plain `def`
  for quote, poll and stop; the start route calls `registry.start` and sets
  `Location`); build the `RunRegistry` in `_app.py` and add the lifespan exit
  `await asyncio.to_thread(registry.shutdown, SHUTDOWN_GRACE_SECONDS)`. `asyncio` is
  used in `_app.py` only. Satisfies: L8, L11, L13, L2.
- [ ] S4b.8 RED then GREEN, shutdown in the app: closing the `TestClient` context
  with a blocked run returns within the grace and the stub's stop token is set.
  Satisfies: L2.
- [ ] S4b.9 Extend the enumeration set to the full route set and assert it exactly;
  regenerate `openapi.json`; confirm `RunStatus`, `JudgingOutcomeBody`, `QuoteToken`
  and `prefixItems` for `member_digests`, and no `-Input`/`-Output` or
  module-qualified names. Mutation: map `RunRefused` to 409 and observe S4b.3 fail;
  start the thread before registering the run and observe the `run_active` race test
  fail; revert, purge, GREEN. Satisfies: L7, L16.
- [ ] S4b.10 Run the five gates; commits `feat(api):`. Satisfies: L8..L13.

Branch/PR: `feat/local-api-judging`. Independent mutation: report `QuoteStale` as 202
(drop the admission wait); the S4b.3 stale test must fail. At the advisory ~400 lines
because the matrix is the slice.

---

## S5a: `launch.py`, in-process (~270 authored lines)

Goal: the launch sequence as a function with injected streams, covering the coverage
branches in-process. Satisfies: L1, L2 (in-process parts). Depends on: S4b.

Threat-matrix cases carried here: Bind (`127.0.0.1` only); Token delivery; Token
handling (never echoed); Lifecycle.

- [ ] S5a.1 RED: `tests/unit/localapi/test_launch.py` token reader: `read_token`
  accepts a valid `\n`-terminated line (and strips an optional `\r`); a line without
  a newline is EOF (`TokenError`, exit-2 class); empty line, over 1,026 bytes and a
  character outside `^[A-Za-z0-9\-._~+/]{32,1024}=*$` each raise `TokenError`;
  no error message contains any part of the input. Observe RED (module missing).
  Satisfies: L1.
- [ ] S5a.2 RED: ready line and bind: `ready_line(port)` is exactly one JSON object
  with `event`, `host` `127.0.0.1`, integer `port`, `api_version`, `base_path`
  `/v1` and no other field; the bound socket's `getsockname()` host is `127.0.0.1`
  and never `0.0.0.0` or IPv6; the port in the line equals the socket's. Satisfies:
  L1.
- [ ] S5a.3 RED: `serve` ordering and exits, injecting `stdin`, `stdout` and
  `install_signals=False`, with uvicorn's `Server.run` replaced by a recording stub
  where a real listener is not needed: an invalid workspace exits 1, the reason is
  on stderr and nothing was bound or written; a missing, empty or malformed token
  exits 2 before any socket is bound (assert via a patched `socket.socket` that
  records no `bind`), stdout empty, stderr without the token; a terminal stdin
  prints the one stderr hint; the workspace is opened with `absolute()`, never
  `resolve()` (a symlinked path is refused). Satisfies: L1.
- [ ] S5a.4 RED: running server, in-process on a background thread: with a real
  uvicorn server on the bound socket, the ready line is written only after
  `server.started`, then a `TestClient`-free probe is avoided (the unit network guard
  refuses loopback): assert instead that `serve` wrote exactly one line to the fake
  stdout, and that the config passed to `uvicorn.Config` has `proxy_headers=False`,
  `ws="none"`, `access_log=False`, `log_config=None`, `lifespan="on"`,
  `server_header=False`, `timeout_graceful_shutdown=5`. A server that stops before
  starting writes no ready line and returns 1. Satisfies: L1.
- [ ] S5a.5 RED: lifelines: after the token line, EOF on the injected stdin sets
  `server.should_exit` and `serve` returns 0; data after the token line is discarded
  without effect; the SIGTERM handler (installed with `install_signals=True` in a
  test that restores the previous handler) sets `should_exit`; a blocked run does not
  hold shutdown past the grace (stubbed registry); stdout contains only the ready
  line after requests were served through the app object. Satisfies: L1, L2.
- [ ] S5a.6 GREEN: create `src/openkos/localapi/launch.py` per D9 (`serve`,
  `read_token`, `ready_line`, `TokenError`, the ready thread polling `server.started`
  every 20 ms, the stdin-EOF daemon thread, the signal handler). Imports only
  stdlib, `uvicorn`, `openkos.api` and `openkos.localapi.*`; it prints nothing but the
  ready line to stdout. Satisfies: L1, L2.
- [ ] S5a.7 Guards and mutation: `test_localapi_guards.py` still passes. Mutations:
  bind `0.0.0.0` and observe the bind test fail; write the ready line from the
  lifespan and observe the ordering test fail; put the token in a stderr message and
  observe the no-echo test fail; revert, purge, GREEN. Satisfies: L1.
- [ ] S5a.8 Run the five gates; commit `feat(api):`. Satisfies: L1, L2.

Branch/PR: `feat/local-api-launch`. Independent mutation: bind `("0.0.0.0", 0)`; the
loopback-only test must fail.

---

## S5b: `openkos serve`, CI wheel step, subprocess lifecycle, `docs/cli.md` (~230 authored lines)

Goal: the verb, the lazy import with its remedy, the real-process proof and the wheel
proof without the extra. Satisfies: L1, L2, L3. Depends on: S5a.

- [ ] S5b.1 RED: `tests/unit/cli/test_serve_cmd.py` in-process: `openkos serve --help`
  exposes `--workspace` and no option that could carry a token (assert the option
  set); with `sys.modules["fastapi"] = None` the verb exits 2, stderr names
  `openkos[api]`, stdout is empty; another `ImportError` propagates; `serve` is in
  `_SELF_LOCKING_COMMANDS` and the CLI lock guard does not wrap it; the verb sits in
  the "Explore" panel; the verb forwards `Path(workspace).absolute()` and exits with
  `launch.serve`'s return code (stub). Observe RED (no verb). Satisfies: L1, L3.
- [ ] S5b.2 RED: fresh-interpreter test (the `test_mcp_cmd.py` pattern): `python -I -c
  "import openkos.cli.main; ..."` and `openkos --help` leave `fastapi`, `uvicorn`,
  `starlette`, `pydantic` and `openkos.localapi` out of `sys.modules`; running another
  verb (`--version`) does too. Satisfies: L3.
- [ ] S5b.3 GREEN: edit `src/openkos/cli/main.py`: add the `serve` verb (panel
  "Explore", next to `mcp`), the lazy `from openkos.localapi import launch` inside the
  body, the `ModuleNotFoundError` check on the top-level `name` in `{fastapi, uvicorn,
  pydantic}` printing the install remedy to stderr and exiting 2, and add `serve` to
  `_SELF_LOCKING_COMMANDS`. Locate by content (`_SELF_LOCKING_COMMANDS`, the `mcp`
  verb), not by line. Satisfies: L1, L3.
- [ ] S5b.4 RED: `tests/unit/localapi/test_launch_process.py`, POSIX only (skip on
  Windows), `subprocess.Popen` of the CLI with bounded reads: token on stdin gives
  one ready line; the HTTP probe runs in a **second child Python process** (the unit
  network guard blocks the test process) and gets 200 with the token and 401 without;
  closing stdin exits 0 with no further stdout; SIGTERM exits 0; EOF before a token
  exits 2 with no ready line and empty stdout; a bad workspace exits 1; a token in
  argv or the environment with an empty stdin exits 2 and is never accepted. None is
  marked `cross_platform_smoke`. Satisfies: L1, L2.
- [ ] S5b.5 CI step (RED is the local command, then the workflow): edit
  `.github/workflows/ci.yml` build job (the isolated wheel install, located by
  content) with a step after the wheel smoke test that runs `openkos serve
  --workspace "$workdir" </dev/null`, asserts exit status 2 and that stderr contains
  `openkos[api]`, and asserts stdout is empty. Reproduce the step locally against a
  built wheel in a scratch virtual environment without the extra (`uv build`, install
  the wheel only) and record the result in the PR body; confirm `openkos --help`
  already runs in the existing step. Satisfies: L3.
- [ ] S5b.6 Docs: `docs/cli.md` gains a `serve` section (stdin token, the ready line
  as documented fields, lifelines, exit codes 0/1/2, the `openkos[api]` extra, owner-
  local scope and the deferral of the gate) and the "not part of the CLI: a local API
  (planned ...)" sentence is rewritten (located by content). No counts. Satisfies:
  L1, L14.
- [ ] S5b.7 Mutation: remove `serve` from `_SELF_LOCKING_COMMANDS` and observe S5b.1
  fail; import `fastapi` at module top of `cli/main.py` and observe S5b.2 fail;
  revert, purge, GREEN. Satisfies: L3.
- [ ] S5b.8 Run the five gates, then `uv build` and the new CI commands locally;
  commits `feat(cli):`, `ci(ci):`, `docs(docs):`. Satisfies: L1..L3.

Branch/PR: `feat/local-api-serve-verb`. Independent mutation: have the verb read the
token from an environment variable; the argv/environment test must fail.

---

## S6: TypeScript measurement and shape docs (~60 authored lines)

Goal: measure, once, that the published schema yields a usable TypeScript client, and
update the docs whose general shape changed. Satisfies: L16 (measured, not in CI),
D11. Depends on: S5b.

- [ ] S6.1 One-off measurement in a scratch directory outside the repository (never
  committed): copy `tests/unit/localapi/openapi.json` (read-only); `npx
  openapi-typescript@7 openapi.json -o api.d.ts`; write `check.ts` that narrows
  `Decision.detail` with `"member_ids" in detail`, switches on
  `DecisionPreview.action` reading the matching `plan` fields, destructures
  `WorkspaceStatus.edges` as `[number, number] | null`, assigns a
  `JudgingQuote["token"]` to `RunRequest["token"]` and a
  `DecisionPreview["version"]` to `ApplyRequest["version"]` with no cast, and reads an
  `ErrorBody` from a 409; `npx -p typescript@5 tsc --noEmit --strict check.ts`.
  Record Node, `openapi-typescript` and `tsc` versions, commands, exit codes,
  generated line count, whether `prefixItems` became a tuple type, whether each
  narrowing compiled without casts, and any `-Input`/`-Output` names. If Node is not
  available, record "not measured", never a pass. Satisfies: L16.
- [ ] S6.2 Record the result in the S6 PR description and, after merge, in the verify
  report. If narrowing is unusable, the fix belongs in `openkos.api` (an additive
  defaulted field), not an HTTP DTO (D2): open a follow-up rather than editing
  `openkos.api` here. Satisfies: L16.
- [ ] S6.3 `docs/architecture.md` (the adapter package ships; the stale statements of
  the planned adapter and the shipped tree, located by content) and `docs/roadmap.md`
  (the MVP 6 local API stage status). No counts, no "since #NNN". Satisfies: L15.
- [ ] S6.4 Run the five gates and the docs tests; commit `docs(docs):`.

Branch/PR: `docs/local-api-shape`. Independent mutation: n/a (docs). Verify by
reading `docs/architecture.md` against `ls src/openkos/localapi`.

---

## S7: Spec merge, ADR-0061 Accepted, archive (~100 authored lines)

Goal: close the change. Docs only; no source or snapshot edit. Satisfies: L1..L16,
P1. Depends on: S6.

- [ ] S7.1 Merge delta specs: create `openspec/specs/local-http-api/spec.md` from
  `specs/local-http-api/spec.md`; MODIFY the one requirement in
  `openspec/specs/stable-python-api/spec.md` ("An Import Guard Bars Interactive And
  Disclosing Layers From The API") and its scenarios; name-match every heading of the
  16 requirements and the scenarios against the main specs after the merge (four had
  drifted in a previous change). Satisfies: all.
- [ ] S7.2 ADR-0061 status Proposed -> Accepted in all three places: the ADR file,
  the `docs/adr/README.md` row, and any status-keeping test input. Do not edit any
  other accepted ADR; ADR-0058 stays Proposed. Satisfies: D13.
- [ ] S7.3 Check each requirement against shipped code, not the task list (a passing
  spec is not a met goal): read the spec Purposes, the proposal's Intent and Success
  Criteria; list any requirement the code does not meet. In particular confirm the
  measured behaviors the spec states (404 on docs paths, the `Retry-After` header,
  `run_active` retryable, exit codes 0/1/2) against the tests that pin them.
  Satisfies: all.
- [ ] S7.4 Record open follow-ups in the archive report: the dev-server origin
  (`--allow-origin`), the unmeasured constants (`Retry-After: 2`,
  `ADMISSION_TIMEOUT_SECONDS`, `SHUTDOWN_GRACE_SECONDS`), the uncapped request body
  size, the TypeScript measurement result, and the deferred disclosure gate for
  ask/query.
- [ ] S7.5 Archive: move (not copy) `openspec/changes/local-http-api/` to
  `openspec/changes/archive/<date>-local-http-api/`, including `exploration.md` if
  present, and write `archive-report.md`. Diff against the sibling
  `openspec/changes/archive/2026-10-15-stable-python-api/` and check the disk: the
  archive agent has overclaimed completion before.
- [ ] S7.6 Run the five gates plus the ADR-status test; commit
  `docs(sdd): archive ...`. Satisfies: all.

Branch/PR: `docs/local-api-archive`. Independent mutation: set the ADR README row to
`Proposed`; the ADR-status test must fail. Rename one merged requirement heading; the
name-match check must fail.

---

## Coverage Matrix

| ID | Requirement | Tasks |
|----|-------------|-------|
| L1 | Launched by `openkos serve` with a stdin token | S5a.1..S5a.6, S5b.1, S5b.3, S5b.4, S5b.6 |
| L2 | Shuts down when its parent goes away | S4a.1, S4a.3, S4b.7, S4b.8, S5a.5, S5b.4 |
| L3 | Missing `api` extra fails with an install remedy | S2b.5, S5b.1, S5b.2, S5b.3, S5b.5 |
| L4 | Every route passes the Host check | S2a.1, S2a.2, S2b.1 |
| L5 | Every route passes the Origin check | S2a.3, S2b.1 |
| L6 | CORS preflight without a token, never without Host and Origin | S2a.2, S2a.5, S2b.1 |
| L7 | Every route requires the bearer token | S2a.4, S2b.1, S2c.6, S3.8, S4b.9 |
| L8 | Operations under `/v1`, one per API operation | S2b.1, S2b.4, S2c.2, S3.5, S4b.1, S4b.7 |
| L9 | Populated, round-tripping responses | S2c.1, S2c.2, S4b.1 |
| L10 | Echoed tokens, stateless | S3.2, S3.6, S4b.2, S4b.3 |
| L11 | Uniform body and status mapping | S2a.5, S2b.2, S2c.3, S3.3, S3.4, S4b.3..S4b.5 |
| L12 | Writes wait at most five seconds | S3.1, S3.2, S3.7, S2c.4 |
| L13 | Single pollable judging job | S4a.1..S4a.4, S4b.1..S4b.4, S4b.6 |
| L14 | Owner-local, no disclosure gate | S2a.7, S2b.5, S2c.5, S5b.6 |
| L15 | Framework confined; adapter imports only the API | S1.1, S1.2, S2b.5, S2b.9, S4a.5 |
| L16 | Committed OpenAPI snapshot | S2b.8, S2c.6, S3.8, S4b.9, S6.1 |
| P1 | Import guard admits `openkos.localapi` as sole importer | S2b.6, S2b.7 |

Threat matrix (design, applicable rows): Bind (S5a.2, S5a.7); Host (S2a.1, S2b.1);
CORS preflight (S2a.2, S2b.1); Origin (S2a.3, S2b.1); Token (S2a.4, S2b.1); Token
delivery (S5b.1, S5b.4); Token handling (S2a.6, S2b.4, S5a.1); Proxy headers
(S2a.1, S5a.4); Error disclosure (S2b.2, S2c.3, S3.4, S4b.4); Lifecycle (S4a.3,
S4b.8, S5a.5, S5b.4); Lock (S3.1, S3.7). Rows marked N/A in the design (documentation-
like paths, git repository selection, commit state, push state, PR commands) are
omitted.

Requirements left uncovered: none.

## Notes carried into apply

1. **Serialization.** Every slice from S2b regenerates `openapi.json`; no two slices
   are opened in parallel.
2. **S1 commit scopes.** `build` and `chore` are not project scopes; the extra's
   `pyproject.toml` change is committed as `feat(api):` with its test.
3. **Unit network guard.** No unit test opens a loopback socket; only child
   processes do (S5b.4).
4. **Correction 1 is a RED, not an assumption.** S2c.1 must be observed failing with
   the `mappingproxy` error before the `dataclasses.replace` fix is written.
5. **Unmeasured constants** (`Retry-After: 2`, `ADMISSION_TIMEOUT_SECONDS = 10`,
   `SHUTDOWN_GRACE_SECONDS = 5`) stay named constants; tests patch them rather than
   waiting real time.
6. **Design open questions** (dev-server origin, request body size) are deferred and
   recorded at archive (S7.4).
