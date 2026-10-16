# Archive Report: local-http-api

**Archived**: 2026-10-16 (S7, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-16-local-http-api/`
**Store mode**: hybrid. Filesystem merge and `git mv`, plus this report saved to Engram as `sdd/local-http-api/archive-report`.

## Final State At Close

- **Implementation**: all slices are merged to `main`: S1 (#1406), S2a (#1407), S2b (#1411), S2c (#1413), S4a (#1415), S3 (#1417), S4b (#1418), S5a (#1419), S5b (#1420), S6 (#1421). Side PRs: #1409, #1410, #1412, #1414, #1416. Tracking issue: #1408.
- **Shipped surface**: `openkos serve` over the `openkos.localapi` adapter (FastAPI and uvicorn in the optional `openkos[api]` extra), 11 operations under `/v1`, a committed OpenAPI snapshot at `tests/unit/localapi/openapi.json`.
- **ADR-0061**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row). The status test is `tests/unit/test_adr_index.py`.
- **ADR-0058**: left Proposed. See "ADR Decisions".

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| local-http-api | Created | `openspec/specs/local-http-api/spec.md`, copied from the delta (a new domain: Purpose and Requirements already present). 16 requirements. |
| stable-python-api | Updated | The MODIFIED requirement "An Import Guard Bars Interactive And Disclosing Layers From The API" replaced the same-named requirement in place (exact heading match). The delta's "(Previously: ...)" note was dropped from the living spec; it describes a diff, not behaviour. The scenarios went from 6 to 8 (two added for the local adapter). |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of both deltas was grepped as an exact line in its main spec. Result: 0 missing (116 unique headings for local-http-api, 9 for stable-python-api).

| Delta | Requirement heading | Main spec |
|-------|---------------------|-----------|
| local-http-api (ADDED) | The Server Is Launched By `openkos serve` With A Stdin Token | found |
| local-http-api (ADDED) | The Server Shuts Down When Its Parent Goes Away | found |
| local-http-api (ADDED) | A Missing `api` Extra Fails With An Install Remedy | found |
| local-http-api (ADDED) | Every Route Passes The Host Check | found |
| local-http-api (ADDED) | Every Route Passes The Origin Check | found |
| local-http-api (ADDED) | CORS Preflight Is Answered Without A Token But Never Without A Valid Host And Origin | found |
| local-http-api (ADDED) | Every Route Requires The Per-Launch Bearer Token | found |
| local-http-api (ADDED) | The Operations Are Served Under `/v1`, One Per Python API Operation | found |
| local-http-api (ADDED) | Responses Are The Python API's Result Types, Populated And Round-Tripping | found |
| local-http-api (ADDED) | Echoed Tokens Make The Wire Form Stateless | found |
| local-http-api (ADDED) | Outcomes And Errors Map To Status Codes With A Uniform Body | found |
| local-http-api (ADDED) | Writes Wait At Most Five Seconds For The Workspace Lock | found |
| local-http-api (ADDED) | Judging Runs As A Single Pollable Job | found |
| local-http-api (ADDED) | Responses Are Owner-Local And Apply No Disclosure Gate | found |
| local-http-api (ADDED) | The Framework Is Confined To The Adapter Package And The Adapter Imports Only The API | found |
| local-http-api (ADDED) | A Committed OpenAPI Snapshot Pins The HTTP Contract | found |
| stable-python-api (MODIFIED) | An Import Guard Bars Interactive And Disclosing Layers From The API | found, once |

No REMOVED or RENAMED headings exist in either delta.

## Requirement-vs-Code Check (S7.3)

Each requirement was read against `src/openkos/localapi/`, `src/openkos/cli/main.py` (`serve_cmd`), `.github/workflows/ci.yml`, `pyproject.toml` and the tests, not against the task list.

| Requirement | Result |
|-------------|--------|
| Launch with a stdin token | Met. `launch.serve`: workspace opened first (exit 1), token read (exit 2), `127.0.0.1` port 0, one `ready` line with `api_version` and `base_path`. |
| Shutdown when the parent goes away | Met, with a precision. EOF is detected with a raw `os.read` on the fd (S5b fixed a SIGTERM abort that a buffered read caused). Bounded by 5 s uvicorn grace plus the 5 s registry grace. The bound for a stuck model call is pinned at registry level (`test_runs_registry.py::test_shutdown_returns_within_the_grace_while_a_stub_stays_blocked`); the subprocess SIGTERM test runs with no active run. |
| Missing `api` extra | Met. `serve_cmd` imports `launch` lazily, exit 2 with `openkos[api]` on stderr; the CI wheel step asserts it; `test_serve_cmd.py` pins start-up isolation. |
| Host check | Met. First check in `LocalApiGuard`, before preflight, Origin and token. |
| Origin check | Met. Allowlist `tauri://localhost`, `http://tauri.localhost`, `https://tauri.localhost`; `null` rejected; a repeated Origin header is also 403. |
| CORS preflight | Met. 204 with exact-origin allow header; `preflight_rejected` for a bad origin, method or header. |
| Bearer token | Met. 401 for absent or non-bearer, 403 for wrong, digest compare. Docs paths (`/docs`, `/redoc`, `/openapi.json`) are 404 with a valid token (`test_routes_security.py`), 401 without. |
| Operations under `/v1` | Met. 11 routes; prefix from `API_VERSION`. |
| Result types round-trip | Met for content. **Overstated for the plan variant**: the spec said a client could distinguish variants "from the published schema alone", but `DecisionPreview.plan` is a plain `anyOf` whose discriminator (`action`) sits on the parent. Spec wording corrected in the living spec (the variant sentence and the "A preview plan identifies its action" scenario); the archived delta keeps the original text. Follow-up: #1422. |
| Echoed tokens | Met. `_EchoRequest` rejects unknown top-level keys only; digests are re-derived by the service. |
| Status mapping and uniform body | Met. Every row of the spec table matches `_errors._ROWS` and `_NOT_APPLIED`, with subclass-first order. |
| Five-second write wait | Met. `wait_seconds` is `Field(ge=0, le=5)`, so above 5 is 422 `invalid_request`; busy is 503 with `Retry-After: 2` (pinned in `test_decisions_write.py`). |
| Single pollable job | Met. 202 with `Location`, `run_active` is 409 and retryable (`test_judging_routes.py`), 16 finished runs kept (`test_eviction_keeps_the_newest_sixteen_finished_runs`). |
| Owner-local, no gate | Met. No `openkos.mcp.gate` import; the package docstring states the scope. |
| Framework confined | Met. `test_localapi_guards.py` covers fastapi, uvicorn, starlette, pydantic and anyio; the `api` extra is separate in `pyproject.toml` (`test_api_extra.py`). |
| OpenAPI snapshot | Met. Now also declares the error responses of each route (S6). |
| stable-python-api import guard | Met by the existing guard tests. |

The exit codes 0/1/2 are pinned by `test_launch.py`, `test_launch_process.py` and `test_serve_cmd.py`.

## ADR Decisions

- ADR-0061: Proposed to Accepted. The change implementing it has merged.
- ADR-0058: left Proposed. Its security shape (loopback, per-launch token, Host and Origin checks) is implemented, but its text says the sensitivity disclosure gate applies to every response, while this stage deliberately applies none (owner-local, gate deferred to the first ask/query endpoint). Accepting it now would freeze a sentence the shipped code contradicts, and an accepted ADR is append-only. It needs a human decision: accept as is, or supersede with an ADR for the owner-local scope. Recorded as #1423.
- ADR-0055 to ADR-0057: unchanged, nothing implements them.

## Open Follow-Ups (S7.4)

- **#1422**: make `DecisionPreview.plan` a discriminated union (needs a decision on the wire shape and on `API_VERSION`, before the desktop shell consumes it).
- **Dev-server origin**: a development web view origin (a `--allow-origin` option) is not supported; only the three shell origins are allowlisted.
- **Unmeasured constants**: `Retry-After: 2`, `ADMISSION_TIMEOUT_SECONDS` (10), `SHUTDOWN_GRACE_SECONDS` (5).
- **Request body size** is uncapped.
- **Disclosure gate** for ask/query: deferred to the first endpoint that answers questions.
- **TypeScript client Node CI**: deferred to the desktop-shell stage.
- **SSE for judging**: deferred; progress is by polling.
- **ADR-0058 status**: see above.

### TypeScript Measurement (S6, PR #1421)

Node v24.18.0, openapi-typescript 7.13.0, tsc 5.9.3 with `--strict`: exit 0. Errors are typed as `ErrorBody`. The plan union does not narrow on `switch (action)` (#1422); property-presence checks compile.

## Archive Move Readback

`git mv openspec/changes/local-http-api openspec/changes/archive/2026-10-16-local-http-api`. The original folder is gone. The archive holds `proposal.md`, `exploration.md`, `design.md`, `tasks.md`, `specs/local-http-api/spec.md`, `specs/stable-python-api/spec.md` and this report. S7 boxes are ticked in the archived `tasks.md`. No `apply-progress.md` or `verify-report.md` was kept in the folder: apply progress lives in Engram `sdd/local-http-api/apply-progress`.
