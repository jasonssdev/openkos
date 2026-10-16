# Design: A Local HTTP API Over The Stable Python API

## Technical Approach

`openkos.localapi` is a thin FastAPI application over `openkos.api` v1 (approach A
of the exploration; binding decisions 1-11 of the proposal are taken as given). It
owns transport, the ADR-0058 checks, serialization, the error table and the judging
job registry, and nothing else. It imports `openkos.api` and no other engine module,
so the existing layering and owner-local boundary tests stay as they are; only the
"nothing imports the API" guard is amended to admit this one package.

Four mechanisms carry the change:

1. **One pure-ASGI guard** runs Host, CORS preflight, Origin and token checks in code
   order in front of every route (Decision 4). Routes cannot opt out.
2. **The `openkos.api` frozen dataclasses are the response and request models**
   wherever Pydantic serves them; HTTP DTOs exist only for the exception-typed
   `JudgingOutcome.failure`, for envelopes the API has no type for (errors, run
   status, lists, request bodies), and nowhere else (Decision 2).
3. **Short operations run as plain `def` endpoints** on the anyio worker pool (the
   "anyio equivalent" ADR-0021 Decision Two names); the judging run runs on a
   dedicated daemon thread with a synchronous admission step, so a stale quote is a
   409 on the start request (Decision 7).
4. **`openkos serve`** imports the adapter lazily, reads the token from stdin, binds
   `127.0.0.1:0` itself, prints one JSON ready line, and exits 0 on stdin EOF,
   SIGTERM or SIGINT without waiting for a model call in flight (Decision 9).

The decision is recorded as ADR-0061 (content in Decision 13), written in S1 with
status Proposed.

Paths are under `src/openkos/` unless they start with `tests/`, `docs/`,
`openspec/`, `.github/` or `pyproject.toml`.

### Corrections to the proposal, from reading the code

None of these reopens a binding scope decision.

1. **FastAPI flattens a returned dataclass with `dataclasses.asdict`** before
   validating it (FastAPI's `_prepare_response_content`). `asdict` deep-copies any
   leaf that is not a dataclass, list, tuple or dict, and `WorkspaceStatus` carries
   two `MappingProxyType` leaves (`api/_reads.py:165`, `:191`); `copy.deepcopy` of a
   `mappingproxy` raises `TypeError`. The status endpoint therefore hands FastAPI a
   `dataclasses.replace` copy with plain `dict` values (the field annotation is
   `Mapping[str, int]`, which a `dict` satisfies). The response type stays
   `WorkspaceStatus`; no DTO. The S2b populated test is the RED that proves the
   failure and the fix (Decision 2).
2. **A stale quote is decided on the run thread, after the 202 a naive job resource
   would already have sent.** `run_judging` returns `QuoteStale` only after reading
   the configuration and building a client (`application/judging.py:1147-1156`).
   Binding decision 7 maps `QuoteStale` to 409, so the start request waits for
   admission (Decision 7). Admission is observed through the `StopToken` the
   adapter passes: every run loop calls `stop.is_set()` before its first chunk
   (`judging.py:1211`, `:1462`, `:1690`), which is after the binding check. A test
   pins that ordering against the real service.
3. **The owner-local boundary scan matches module names.**
   `test_curate_decisions_boundary.py:46` flags any import whose last dotted segment
   is `judging`, `curate_decisions` or `identity_adjudication`. A module named
   `localapi/judging.py` would be flagged on its own package-internal import. The
   run module is `_runs.py` and the operation router `_routes.py`.
4. **`serve` is a self-locking command, not a read-only one.** It writes (apply,
   refresh, judging persists) but takes the workspace lock only inside each commit
   section. It joins `_SELF_LOCKING_COMMANDS` (`cli/main.py:387`), like `daemon`, so
   the CLI lock guard never wraps its lifetime and adds no `--wait` option.
5. **The unit suite's network guard refuses loopback `connect`**
   (`tests/unit/conftest.py:154-162`, `:281-301`). `TestClient` opens no socket and
   is unaffected. The one launch test that needs a real HTTP request makes it from a
   child Python process, so the guard is not weakened (Decision 11).
6. **The "wheel smoke without the extra" proof is a CI step, not a `tests/smoke/`
   test.** The cross-platform smoke job installs the wheel into an environment that
   already has the dev group (`ci.yml:286-342`), which includes FastAPI. Only the
   build job's isolated wheel (`ci.yml:208`) lacks the extra. The step is added
   there (Decision 9).
7. **`ValueError` is not mapped to 422.** The only `ValueError`s `openkos.api`
   documents are an unknown judging kind and an out-of-range `wait_seconds`
   (`api/_judging.py:63`, `api/_wiring.py:44-54`); request validation pre-empts the
   first and the adapter fixes the second. A `ValueError` reaching the adapter is a
   defect and is a 500 `internal_error`, not a client error.
8. **The boundary test's docstring states the opposite of binding decision 2.**
   `test_curate_decisions_boundary.py:3-5` says a future local HTTP adapter "must
   bring its own disclosure gate". S2a rewords it to name the owner-local decision
   and the deferral of the gate to ask/query; the scan itself is unchanged.

## Architecture Decisions

### Decision 1: Package layout

**Choice**:

```
src/openkos/localapi/
  __init__.py   docstring only: owner-local, loopback, gate deferred to ask/query; imports nothing
  _guard.py     pure-ASGI guard: Host, CORS preflight, Origin, bearer token; CORS response headers
  _errors.py    ErrorBody, ApiProblem, the outcome and exception tables, RETRY_AFTER_SECONDS
  _models.py    HTTP DTOs: requests, HealthBody, DecisionList, ApplyResponse, RunStatus, JudgingOutcomeBody
  _app.py       ServerSettings, create_app(settings), openapi_document(), exception handlers, lifespan
  _routes.py    APIRouter(prefix="/v1"): health, status, pending, decisions, preview, apply, refresh, quote
  _runs.py      RunRegistry, admission StopToken, run observer, the /v1/judging/runs router
  launch.py     serve(root) -> int: open the workspace, read the token, bind, run uvicorn, ready line, lifelines
```

- `launch` is the only module the CLI imports (`from openkos.localapi import
  launch`, inside the verb body). Every other module is private.
- `_routes.py` and `_runs.py` call the API through the module attribute
  (`from openkos import api`; `api.status(...)`), so a test that patches
  `openkos.api.status` intercepts the call.
- No module is named `judging`, `curate_decisions` or `identity_adjudication`
  (correction 3).
- Direct third-party imports are `fastapi`, `pydantic` and `uvicorn` only. Starlette
  is reached through FastAPI (`app.exception_handler(404)` takes a status code, so
  no `starlette` import is needed) and the guard is typed with stdlib callables.
  `asyncio` is used in `_app.py` (lifespan) and nowhere in the core.

**Alternatives considered**: one `app.py` module (the spike's shape). Rejected: the
guard, the error table and the run registry each have their own test matrix, and a
reviewer needs to see the guard alone. Public submodules. Rejected: the CLI needs one
entry point; the rest is not a Python surface.

**Rationale**: mirrors `openkos.api`'s private-module layout (ADR-0060 Decision 1)
and keeps the security-critical code in one file small enough to review whole.

### Decision 2: Response and request models

**Choice**: per type, from the populated round-trip requirement.

| Type | Served as | Why |
|---|---|---|
| `WorkspaceStatus`, `HealthCounts`, `ActivityEntry`, `JobSummary` | the API dataclasses | the status endpoint passes `dataclasses.replace(s, concepts_by_type=dict(...), open_decisions_by_kind=dict(...))` (correction 1) |
| `PendingReport`, `PendingRow`, `KindCounts` | the API dataclasses | plain tuples, strings, ints |
| `tuple[Decision, ...]` | `DecisionList {decisions: list[Decision]}` | an envelope object leaves room for additive metadata; a top-level array does not |
| `Decision`, `IdentityDetail`, `RelationDetail`, `TierDetail`, `WithheldAction`, `Action` | the API types | see the discriminator rule below |
| `DecisionPreview`, the four plans, `DecisionVersion` | the API types | `NotApplied` and `Stale` become error responses, so the 200 body is only `DecisionPreview` |
| `Applied` | `ApplyResponse {applied: Applied, warnings: list[str]}` | `on_warning` messages (a commit that could not be made, `api/_wiring.py:57-66`) must reach the client |
| `QueueRefresh`, `KindRefresh` | the API dataclasses | plain |
| `JudgingQuote`, `QuoteBinding`, `QuoteNotice`, `QuoteToken`, `WorkItem` | the API dataclasses | plain; `member_digests` is `prefixItems` pairs |
| `JudgingOutcome` | `JudgingOutcomeBody` (DTO) | `failure: BackendError \| None` is an exception instance (`judging.py:1036`); Pydantic cannot build a schema for it. The DTO copies every other field and carries `failure: ErrorBody \| None` from the exception table |
| `QuoteStale`, `RunRefused`, `NotApplied`, `Stale` | error responses (Decision 6) | binding decision 7 |

**Undiscriminated unions.** No additive `kind` field is added at the HTTP layer in
v1. Each union already has a sibling tag on its parent: `Decision.kind` determines
`Decision.detail` (identity, relation_type, volatility), and `DecisionPreview.action`
determines `plan` (merge, relate and relate_reversed, set_tier, keep_distinct). The
member types have disjoint required fields (`api/_decisions.py:38-68`, `:143-176`),
so Pydantic's smart-mode union selects the right member, and the populated tests
exercise every member. Adding `kind` at the HTTP layer would mean seven mirror DTOs
plus wrapper copies of `Decision` and `DecisionPreview`, a second copy of every
field that drifts silently. If the measured TypeScript client (Decision 11) shows
narrowing is unusable, the fix belongs in `openkos.api` itself: a dataclass field
with a default is additive under ADR-0060's classifier, so no HTTP DTO is needed.

**Request models** are Pydantic `BaseModel`s with
`model_config = ConfigDict(extra="forbid", frozen=True)`, so a client cannot send a
field the server ignores. `ApplyRequest` and `RefreshRequest` carry the one
optional lock-wait field, `wait_seconds` (Decision 8). Nested
echoed API dataclasses (`QuoteToken`, `DecisionVersion`) are validated by Pydantic's
stdlib-dataclass support, which ignores unknown nested keys; integrity of the
echoed values rests on the service's own re-derivation (Decision 3).

**DTOs** are frozen `BaseModel`s too, so all adapter-owned types share one style.
DTO class names never reuse an `openkos.api` name, so component names stay short and
unique; a snapshot test asserts no component name is module-qualified.

**`separate_input_output_schemas`** keeps FastAPI's default (`True`). Its `-Input`
and `-Output` split appears only for a type whose validation and serialization
schemas differ (fields with defaults); every type used in both directions
(`QuoteToken`, `QuoteBinding`, `WorkItem`, `DecisionVersion`, `Action`) has no
defaulted field. Keeping the default marks defaulted response fields (`QuoteNotice`,
`KindRefresh.ambiguous`) as required in the response schema, which is what the
server always sends. A snapshot test fails if any component name ends in `-Input`
or `-Output`; that is when this decision is revisited.

**Alternatives considered**:
- DTOs for every response. Rejected: duplicates the API's fields and its snapshot,
  and the spike showed the dataclasses serve directly.
- `separate_input_output_schemas=False`. Rejected: it marks every defaulted response
  field optional in TypeScript although the server always sends it, and it is not
  needed while the split test holds.
- Returning `Response(TypeAdapter(T).dump_json(v))` from every endpoint to bypass
  `asdict`. Rejected: it bypasses response validation everywhere to fix one field
  on one type, and its handling of a `mappingproxy` is itself unverified.

### Decision 3: The echoed token wire form

**Choice**: the client echoes the JSON it received, unchanged. Both shapes are the
Pydantic serialization of the API dataclasses:

```json
"version": {
  "decision_key": "identity:3f9c0a…",
  "payload_digest": "9b1e…",
  "content_digest": "c47d…"
}

"token": {
  "binding": {
    "kind": "identity",
    "model": "qwen3:8b",
    "backend": "ollama",
    "is_local": true,
    "effective_confidential": true,
    "rubric_digest": "a1b2…"
  },
  "include_confidential": false,
  "include_uncertain": false,
  "work": [
    {"work_key": "5e7a…", "member_digests": [["people/ada", "d00d…"], ["people/ada-l", ""]]}
  ],
  "calls": 1
}
```

- `payload_digest` and `rubric_digest` are `null` when absent; tuples are JSON arrays;
  `member_digests` is an array of two-string arrays (OpenAPI 3.1 `prefixItems`).
- **Reconstruction** is Pydantic validation into the same frozen dataclasses: arrays
  become tuples per annotation, so the reconstructed value is `==` to the one the
  server issued (dataclass equality), which is what `changed_fields`
  (`judging.py:147-164`) and the decision service compare. No digest is parsed; the
  one-way `token()`/`digest()` helpers (`curate_decisions.py:80-88`,
  `judging.py:203-218`) are not part of the wire.
- **Apply** carries the key twice: the path (`/v1/decisions/{decision_key}/apply`)
  and `version.decision_key`. They must be equal, else 422 `key_mismatch` before the
  API is called, so a client bug cannot apply a version to a different decision than
  the URL names.
- **Integrity.** An echoed token is the owner's consent, not a credential: the caller
  already holds the bearer token and is the workspace owner (binding decision 2). A
  tampered token can only express a different consent. The service still refuses a
  moved model, backend, locality, confidential bit or rubric (`QuoteStale`), and a
  moved decision (`Stale`).

**Alternatives considered**: a server-side registry keyed by digest (rejected by
binding decision 3). An HMAC over the echoed token with a per-launch key. Rejected:
it defends against the owner, adds state to the launch, and makes the token opaque
to the client, which then cannot show what it consented to.

### Decision 4: The ADR-0058 guard and CORS

**Choice**: one pure-ASGI middleware (`_guard.py`), the only middleware the app adds.
For an `http` scope it runs, in this order:

1. **Host.** Exactly one `Host` header, lowercased, equal to `127.0.0.1:{port}` or
   `localhost:{port}`, where `port` is the bound port. Anything else (absent,
   duplicated, another name, IPv6, another port) → 403 `host_not_allowed`. This runs
   before everything, so a DNS-rebinding request cannot even reach the preflight
   answer.
2. **CORS preflight.** A request is a preflight when its method is `OPTIONS` and it
   carries both `Origin` and `Access-Control-Request-Method`. If the Origin is on the
   allowlist, the requested method is `GET` or `POST`, and every requested header is
   `authorization` or `content-type`, answer 204 with
   `Access-Control-Allow-Origin: <origin>`, `Access-Control-Allow-Methods: GET, POST`,
   `Access-Control-Allow-Headers: Authorization, Content-Type`,
   `Access-Control-Max-Age: 600`, `Vary: Origin`. Otherwise 403 `preflight_rejected`.
   A preflight never reaches the token check, because browsers never send
   `Authorization` on one; it never reaches a route either.
3. **Origin.** An absent `Origin` passes (a non-browser client: the shell's native
   side, a test, `curl`). A present `Origin` must be on the allowlist, else 403
   `origin_not_allowed`. `Origin: null` is not on it.
4. **Token.** Exactly one `Authorization` header of the form
   `Bearer <token>` (scheme case-insensitive, one space). Absent, duplicated, another
   scheme, or empty → 401 `unauthorized` with `WWW-Authenticate: Bearer`. Present
   but wrong → 403 `forbidden`. A token in the query string or the body is never
   read, so it is a 401.

On every response to an allowed-Origin request the guard adds
`Access-Control-Allow-Origin: <origin>`, `Vary: Origin` and
`Access-Control-Expose-Headers: Location, Retry-After`, so the browser lets the
shell read success and error bodies alike. `Access-Control-Allow-Credentials` is
never sent: there are no cookies.

- **Allowlist**: `("tauri://localhost", "http://tauri.localhost",
  "https://tauri.localhost")`, the Tauri webview origins on macOS/Linux and Windows,
  exact string match. Fixed in this stage; a `--allow-origin` option for a dev
  server is additive and belongs to the desktop-shell stage.
- **Constant time**: the server keeps only `sha256(token)`
  (`ServerSettings.token_digest`, never the token itself, never in a `repr`). The
  guard compares `hmac.compare_digest(sha256(presented).digest(), token_digest)`:
  equal-length inputs, so neither the value nor the length of the real token leaks
  through timing.
- **Non-http scopes**: `lifespan` passes through; a `websocket` scope is closed with
  code 1008 (uvicorn runs with `ws="none"`, so this is defense in depth).
- **Guard responses** are hand-encoded `{code, retryable, message}` JSON
  (`retryable: false` for every guard row), built without FastAPI so they cannot
  depend on routing.
- **`/v1/health` is authenticated.** ADR-0058 requires the checks on every route,
  and no client needs an unauthenticated probe: the shell learns readiness from the
  ready line (Decision 9) and holds the token. An unknown path without a token is
  401, not 404, so the route table is not discoverable.
- **No docs routes.** `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`: the
  schema is the committed snapshot (Decision 11), not a runtime route.

**Alternatives considered**:
- Starlette's `CORSMiddleware` outside a token middleware. Rejected: the order then
  depends on `add_middleware`'s last-added-runs-first rule, the Host check would have
  to be a third middleware placed outside both, and `CORSMiddleware` answers a
  rejected preflight with 400 and passes non-preflight requests through, so the
  Origin rule would be split across two components.
- Per-route `Depends(...)` security. Rejected by the proposal: a route added without
  it is unprotected.
- Unauthenticated `/health`. Rejected: no consumer needs it, and an unauthenticated
  route is the exception ADR-0058's "same checks on every route" forbids.

**Rationale**: the four checks are one function read top to bottom; the order is the
code order, and the route-enumeration test (Decision 11) proves it covers every
route.

### Decision 5: Endpoints

**Choice**: all under `/v1` (binding decision 9; the prefix is
`f"/v{api.API_VERSION}"`, and a test pins that it equals `/v1`).

| Method and path | Request | 200/202 body | Outcome and error statuses |
|---|---|---|---|
| `GET /v1/health` | - | `HealthBody {status: "ok", api_version: int, engine_version: str}` | - |
| `GET /v1/status` | - | `WorkspaceStatus` | 500 |
| `GET /v1/pending` | - | `PendingReport` | 503 `queue_unavailable`, 500 |
| `GET /v1/decisions` | - | `DecisionList` | 500 |
| `POST /v1/decisions/{decision_key}/preview` | `PreviewRequest {action: Action}` | `DecisionPreview` | 409, 422 (Decision 6) |
| `POST /v1/decisions/{decision_key}/apply` | `ApplyRequest {action: Action, version: DecisionVersion, wait_seconds: float = 5.0}` | `ApplyResponse` | 409, 422, 503 |
| `POST /v1/queue/refresh` | optional `RefreshRequest {wait_seconds: float = 5.0}` | `QueueRefresh` | 422, 500, 503 |
| `POST /v1/judging/quote` | `QuoteRequest {kind: JudgingKind, include_confidential: bool = false, include_uncertain: bool = false}` | `JudgingQuote` | 500 |
| `POST /v1/judging/runs` | `RunRequest {token: QuoteToken \| None = None}` | 202 `RunStatus`, `Location: /v1/judging/runs/{run_id}` | 409 `quote_stale`, 409 `run_active`, 422 `run_refused`, 5xx |
| `GET /v1/judging/runs/{run_id}` | - | `RunStatus` | 404 `not_found` |
| `POST /v1/judging/runs/{run_id}/stop` | - | `RunStatus` | 404 `not_found` |

- Each endpoint calls exactly one `openkos.api` operation (ADR-0021 Decision Five:
  no composed lock-taking operations).
- `engine_version` is `importlib.metadata.version("openkos")`, `"unknown"` on
  failure (the `--version` fallback).
- Every route declares 401, 403 and 422 with `ErrorBody` in its OpenAPI `responses`
  (router-level), plus its own rows above; declaring 422 suppresses FastAPI's default
  `HTTPValidationError` schema, which the adapter never sends.
- Preview is `POST` (binding scope; it takes a body and must not be cached or
  prefetched), though it writes nothing.
- `decision_key` is a single path segment (`<kind>:<hex>`,
  `state/pending_queue.py:207-215`); no pattern is imposed, and an unknown key is the
  service's own `Stale` or `NotApplied`.
- `wait_seconds` is validated by the request model (`0 <= x <= 5`, default 5);
  a value outside the range is 422 `invalid_request`, before the API is called.

### Decision 6: Error table and outcome mapping

**Choice**: every non-2xx body is exactly `{code: str, retryable: bool, message: str}`
(`ErrorBody`). `retryable` means "the same request, unchanged, may succeed later".
Messages are fixed strings from these tables; nothing echoes `str(exc)` except the
`WriteRefused` family's documented user-facing `message`
(`application/write_gate.py:42-51`).

**Returned outcomes** (binding decision 7):

| Outcome | Status | `code` | `retryable` | Message |
|---|---|---|---|---|
| `Stale(reason)` | 409 | `stale` | false | fixed per reason (`content_changed`, `row_resolved`, `row_superseded`, `row_inputs_changed`), ending "preview it again" |
| `NotApplied("not_offered")` | 422 | `action_not_offered` | false | "this decision does not offer that action" |
| `NotApplied("skipped")` | 422 | `skip_not_applicable` | false | "skip writes nothing; the client moves on without a request" |
| `NotApplied("not_mergeable")` | 409 | `not_mergeable` | false | fixed |
| `NotApplied("member_missing")` | 409 | `member_missing` | false | fixed |
| `NotApplied("record_failed")` | 409 | `record_failed` | true | fixed (the stored `detail` is not echoed) |
| `QuoteStale(kind, changed)` | 409 | `quote_stale` | false | "the quote is stale (changed: model, locality); quote again", built from the `ChangedField` literals only |
| `RunRefused("no_quote_token")` | 422 | `run_refused` | false | fixed; reached when `RunRequest.token` is omitted or null |

422 is used when the request itself names something that can never succeed against
this decision; 409 when the workspace's state conflicts with the request.

**Raised exceptions**, matched subclass-first:

| Exception | Status | `code` | `retryable` | Message |
|---|---|---|---|---|
| `WorkspaceBusyError` | 503, `Retry-After: 2` | `workspace_busy` | true | "the workspace is busy; try again" |
| `WorkspaceLockUnavailableError` | 500 | `workspace_lock_unavailable` | false | fixed |
| `DriftDetected` | 409 | `write_drift` | true | `exc.message` |
| `WriteFailed` | 500 | `write_failed` | false | `exc.message` |
| `WriteRefused` (including `Refused`; not drift, not failed) | 422 | `write_refused` | false | `exc.message` |
| `QueueUnavailableError` | 503 | `queue_unavailable` | false | fixed |
| `QueueRefreshRefused` | 503 | `queue_refresh_refused` | false | fixed |
| `JudgingRefused` | 503 | `judging_refused` | false | fixed |
| `WorkspaceNotFound` | 500 | `workspace_not_found` | false | fixed |
| `WorkspaceUnreadable` | 500 | `workspace_unreadable` | false | fixed |
| `BackendUnavailable` | 503 | `backend_unavailable` | true | "the configured model backend is unreachable" |
| `BackendModelNotFound` | 424 | `backend_model_not_found` | false | "the configured model is not installed" |
| `BackendGenerationCapped` | 502 | `generation_capped` | false | fixed |
| `BackendError` (any other) | 502 | `backend_error` | false | "the model backend failed" |
| any other `Exception`, including any other `OpenKosError`, `OSError` and `ValueError` | 500 | `internal_error` | false | "internal error"; logged to stderr |

**Framework rows**: request validation → 422 `invalid_request` (the default handler
is replaced, because Pydantic's errors echo input values, which could include the
echoed token); path/version key mismatch → 422 `key_mismatch`; unknown route or
unknown run id → 404 `not_found`; wrong method → 405 `method_not_allowed`; a second
active run → 409 `run_active` (retryable: it succeeds once the active run ends).
Guard rows are in Decision 4: 401 `unauthorized`, 403 `forbidden`, 403
`host_not_allowed`, 403 `origin_not_allowed`, 403 `preflight_rejected`.

**Mechanism**: endpoints call the API through one helper, `_errors.invoke(fn)`, which
catches `Exception` (`# noqa: BLE001 -- mapped to a fixed-message envelope`) and
raises `ApiProblem(status, body, headers)`; an `ApiProblem` handler renders it. The
handler sits in FastAPI's `ExceptionMiddleware`, so a mapped error never reaches
Starlette's `ServerErrorMiddleware`, which re-raises. Only a defect outside an
endpoint body (for example a response-serialization error) takes the generic
`Exception` handler; it still answers the fixed 500 envelope, and its traceback goes
to stderr through the CLI's existing logging, which the logging policy already treats
as the user's own terminal, never a stored artifact (`logsetup.py:15-20`).

**`Retry-After: 2`** is an unmeasured placeholder, like `PERSIST_ATTEMPTS`: by the
time a 503 is sent the request has already waited the 5 s ceiling (Decision 8), and
commit sections are short. It is a named constant (`RETRY_AFTER_SECONDS`) so a later
measurement changes one line.

**Alternatives considered**: the MCP table's `-32603` style (one generic error).
Rejected: the client needs `retryable` to decide between "try again" and "re-read".
Mapping `ValueError` to 422 (exploration). Rejected (correction 7). Echoing
`str(exc)` for `OpenKosError`. Rejected: exception messages can quote paths and
stored text; only `WriteRefused.message` is a documented user-facing contract.

### Decision 7: Threads, the judging job and shutdown

**Choice**:

- **Short operations** (health, status, pending, decisions, preview, apply, refresh,
  quote, run poll and stop) are plain `def` endpoints, which Starlette runs with
  `anyio.to_thread.run_sync` on the default limiter (40 threads). This is ADR-0021
  Decision Two's "anyio equivalent": one worker thread per operation, from a pool.
  It deviates from the MCP server's concrete mechanism (a fresh daemon thread per
  call, `Semaphore(4)`, and a per-call deadline, `mcp/server.py:69-88`, `:273-342`)
  in two ways ADR-0061 records: no deadline, because no short operation makes a
  model call (the quote builds a client and never calls it, `api/_judging.py:10-11`),
  and writes are bounded by the 5 s lock wait (Decision 8); and no extra semaphore,
  because ADR-0021 Decision Four bounds write concurrency by the lock, not the pool.
  A client that disconnects does not stop the thread: the operation runs to
  completion (ADR-0021 Decision Three, abandoned, not stopped).
- **The judging run** runs on its own `threading.Thread(daemon=True,
  name="openkos-judging-run")`, never on the pool, so a run of minutes holds no pool
  thread. `api.run_judging(ws, token, stop=<admission stop>, observer=<run observer>,
  wait_seconds=5.0)`.
- **Admission** (correction 2): the start endpoint registers the run as active, starts
  the thread, and waits on a `threading.Event` that is set by the first
  `is_set()` call on the run's `_AdmissionStop` (a `StopToken` subclass) or by the run
  returning or raising, bounded by `ADMISSION_TIMEOUT_SECONDS = 10.0`:
  - the run returned `QuoteStale` / `RunRefused` → 409 / 422, the run is removed;
  - the run raised → the exception table status, the run is removed;
  - admitted, or returned a `JudgingOutcome` (nothing to judge) → 202 with
    `RunStatus` and `Location`;
  - the timeout elapsed (a re-partition walk longer than 10 s) → 202; a later stale
    result is then reported in the run's `error` (`state: "failed"`).
- **`RunStatus`**: `{run_id, kind, state, index, total, persisted, outcome, error}`
  where `state` is `running` | `stopping` | `finished` | `failed`; `index`/`total`
  are the latest `JudgingProgress` (null before the first event); `persisted` is the
  latest cumulative `chunk_persisted` count (`judging.py:1260-1262`); `outcome` is a
  `JudgingOutcomeBody` when the run returned one (`finished`, whatever its `status`);
  `error` is an `ErrorBody` when the run raised or went stale after admission
  (`failed`). A poll never maps the run's result onto the poll's own HTTP status:
  it is 200 for any known run, so a polling client never mistakes the run's
  `retryable` for the poll's.
- **Registry**: one `threading.Lock`; at most one run in `running`/`stopping`
  (including during admission) or 409 `run_active`; finished and failed runs are
  kept, newest 16, older evicted (then 404). In memory only, lost on restart by
  design (proposal non-negotiables); per-chunk persistence keeps the paid work.
- **Stop**: `POST …/stop` sets the run's stop token (lock-free, `runtime.py:66-68`)
  and returns `RunStatus` with `state: "stopping"`; on a finished run it is a no-op
  returning the final status. The stop takes effect at the next chunk boundary.
- **Shutdown**: the FastAPI lifespan's exit calls
  `await asyncio.to_thread(registry.shutdown, SHUTDOWN_GRACE_SECONDS)` with a 5 s
  grace: set every stop token, join the active thread for at most the grace, and
  return either way. A model call in flight is abandoned with the daemon thread when
  the process exits; no lock is held across a model call (`judging.py:28-30`), so
  nothing is mid-write, and at most the chunk in flight is lost.

```
client              _runs (pool thread)            run thread                 openkos.api / judging
------              -------------------            ----------                 ---------------------
POST /runs {token}
  ── registry.begin() ── active? ─▶ 409 run_active
  ── start thread ─────────────────▶ api.run_judging(ws, token, stop=adm, observer=obs, wait=5)
  ── wait(admitted|done, 10 s)                         ├─ read cfg, build client (no call)
                                                       ├─ binding moved? ── return QuoteStale ─▶ done
                                                       ├─ re-partition (LLM-free)
                                                       └─ for chunk: adm.is_set() ─▶ admitted
  ◀── 409 quote_stale (done: QuoteStale)                     model call (no lock)
  ◀── 202 RunStatus + Location (admitted)                    with section(5 s): persist ─▶ obs.chunk_persisted(n)
GET /runs/{id} ── registry.status(id) ─▶ 200 RunStatus{state, index, total, persisted}
POST /runs/{id}/stop ── adm.set() ─▶ 200 RunStatus{state: "stopping"}
                                                       next chunk: adm.is_set() → stop → JudgingOutcome(status="stopped")
GET /runs/{id} ─▶ 200 RunStatus{state: "finished", outcome}
```

**Alternatives considered**:
- Running the run on the anyio pool. Rejected: a 2,400 s relation chunk
  (`judging.py:32-44`) holds a pool thread for its whole duration.
- 202 always, with `QuoteStale` reported only in the poll. Rejected: contradicts
  binding decision 7 (`QuoteStale` → 409).
- A synchronous pre-check by re-quoting and comparing bindings. Rejected: it composes
  two operations, repeats the candidate scan, and still races the run's own check.
- Reporting the run's terminal status as the poll's HTTP status (a poll returning
  409 or 503). Rejected: a polling client would read the run's `retryable` as the
  poll's and loop.

### Decision 8: Lock-wait policy

**Choice**: `WRITE_WAIT_SECONDS: Final = 5.0` in `_errors.py` (default and ceiling,
binding decision 8). `ApplyRequest` and `RefreshRequest` carry an optional
`wait_seconds: float = Field(default=5.0, ge=0, le=5.0)`; the refresh body itself may be
omitted. A value outside `0 <= x <= 5` is 422 `invalid_request` before the API is
called, so a client can shorten the wait but never lengthen it. The run passes
`WRITE_WAIT_SECONDS` for each persist (a run that stays busy ends `unpersisted`, it
does not raise, `api/_judging.py:86-88`). The quote takes `wait_seconds=0` inside the
API (`api/_judging.py:69`) and writes nothing. Reads take no lock. A busy workspace
therefore answers within the requested wait (5 s by default) with 503 and
`Retry-After: 2`.

**Alternatives considered**: 0 s (fail fast). Rejected: the daemon's short commit
phases would turn ordinary contention into user-visible errors. A client-supplied
wait up to `MAX_WAIT_SECONDS` (3,600 s). Rejected by binding decision 8: it would let
one request hold a pool thread for an hour.

### Decision 9: Launch (`openkos serve`)

**Choice**:

```
openkos serve --workspace PATH        (default: the current directory)
```

In `cli/main.py`, panel "Explore" next to `mcp`, classified in
`_SELF_LOCKING_COMMANDS` (correction 4). The verb body only:

1. lazily imports `from openkos.localapi import launch`; a `ModuleNotFoundError`
   whose `name`'s top level is `fastapi`, `uvicorn` or `pydantic` prints
   `openkos serve: the local API needs the optional "api" extra; install it with:
   pip install "openkos[api]"` to stderr and exits 2 (any other import error
   propagates);
2. `raise typer.Exit(code=launch.serve(Path(workspace).absolute()))`.

`launch.serve(root, *, stdin=None, stdout=None, install_signals=True) -> int`:

1. **Open** `api.Workspace.open(root)`; `WorkspaceNotFound`/`WorkspaceUnreadable` →
   stderr `openkos serve: refusing to serve -- <reason>.` (the workspace reason is the
   CLI's own refusal text), exit 1. `absolute()`, never `resolve()`, so the
   symlink refusal sees the path as given (ADR-0060 Decision 4).
2. **Token**: read one line from `stdin.buffer` (at most 1,026 bytes). The line must
   end in `\n` (an optional `\r` before it is stripped); a line without a newline is
   EOF, which means the shell is already gone. The token must match
   `^[A-Za-z0-9\-._~+/]{32,1024}=*$` (RFC 6750 `b64token`, with a 32-character
   floor). Missing, EOF, too long or malformed → stderr message that never contains
   the token, exit 2. When stdin is a terminal, one stderr hint says the token is
   being read. Never argv, environment, file or URL: the verb has no option that
   could carry it.
3. **Bind**: `socket.socket(AF_INET, SOCK_STREAM)`, `bind(("127.0.0.1", 0))`, read the
   port; failure → exit 1. The socket is handed to uvicorn (`sockets=[sock]`), which
   listens on it. Never `0.0.0.0`, never IPv6.
4. **App**: `create_app(ServerSettings(workspace, port, token_digest))`.
5. **Server**: `uvicorn.Server(uvicorn.Config(app, lifespan="on", loop="asyncio",
   http="h11", ws="none", proxy_headers=False, server_header=False,
   access_log=False, log_config=None, timeout_graceful_shutdown=5))`. `log_config=None`
   leaves uvicorn's loggers to the root logger the CLI already configured
   (`logsetup.py:249-254`: stderr, WARNING), so nothing reaches stdout.
   `proxy_headers=False` so no `X-Forwarded-*` header can rewrite the client or host.
6. **Ready line**: a daemon thread polls `server.started` every 20 ms; once true it
   writes exactly one line to stdout and flushes:
   `{"event": "ready", "host": "127.0.0.1", "port": 51234, "api_version": 1, "base_path": "/v1"}`.
   `server.started` is set after uvicorn's listening servers exist, so a client that
   reads the line can connect immediately. If the server stops before starting, no
   line is written and the exit code is 1. Nothing else is ever written to stdout.
7. **Lifelines**:
   - **stdin EOF**: a daemon thread reads `stdin.buffer` in 4 KiB chunks after the
     token line, discarding data, and sets `server.should_exit = True` on EOF (the
     shell died or closed the pipe; no orphan, roadmap MVP 6 criterion).
   - **SIGTERM / SIGINT**: when `install_signals`, a handler is installed before
     `server.run` that sets `server.should_exit`. uvicorn replaces it during the run
     and, after its graceful shutdown, re-raises a captured signal into the handler it
     saved, which is ours, so the process is not killed by the default action and
     returns normally.
   - Both paths run the lifespan exit, which stops the active run with the 5 s grace
     (Decision 7), then `serve` returns 0.

**Exit codes**: 0 graceful shutdown; 1 runtime failure (workspace refused, bind or
startup failure); 2 usage failure (token missing or invalid, missing extra).

**CI**: alongside an in-process unit test (the framework made unimportable), the build job gains a step after the wheel smoke test that runs the isolated
wheel without the extra: `openkos serve --workspace "$workdir" </dev/null` must exit
2 and its stderr must contain `openkos[api]` (correction 6). `openkos --help` in the
existing step already proves the CLI loads without the extra.

**Alternatives considered**:
- `--port`. Rejected by ADR-0058 (a random port per launch); the ready line is how
  the shell learns it.
- Letting uvicorn bind (`host="127.0.0.1", port=0`). Rejected: the app must know the
  port to check `Host` before the server starts, and uvicorn reports the bound port
  only through its log line.
- Printing the ready line from the lifespan startup. Rejected: lifespan startup runs
  before uvicorn creates its listening server, so a fast client could be refused.
- A new `logsetup` mode for `serve`. Rejected: the CLI mode already sends uvicorn's
  records to stderr at WARNING, and a file log adds a stored artifact this stage
  does not need.

```
shell (Tauri)                 openkos serve                        uvicorn / app
-------------                 -------------                        -------------
spawn(stdin=pipe, stdout=pipe)
                              import localapi (or exit 1: extra)
                              Workspace.open (or exit 1)
write "<token>\n" ──────────▶ read one line (or exit 2)
                              bind 127.0.0.1:0 → port
                              create_app(port, sha256(token))
                              server.run(sockets=[sock]) ────────▶ lifespan startup; listen
                              ready thread: started? ◀──────────── started = True
read line ◀────────────────── {"event":"ready","port":P,...}
GET /v1/health (Bearer) ─────────────────────────────────────────▶ guard → 200
...
close stdin / SIGTERM ──────▶ should_exit = True ────────────────▶ graceful (5 s) → lifespan exit:
                                                                    stop run, join ≤ 5 s
                              exit 0 ◀───────────────────────────── run() returns
```

### Decision 10: Import guards

**Choice**:

- **API importers** (`tests/unit/api/test_api_guards.py:189`): the importers of
  `openkos.api` outside the API must all be files under `localapi/`, and there must
  be at least one once the package exists. The parametrized spelling test is kept.
- **Adapter allowlist** (new `tests/unit/localapi/test_localapi_guards.py`): every
  module under `localapi/` imports only the standard library (excluding `sqlite3`),
  `fastapi`, `pydantic`, `uvicorn`, exactly `openkos.api` (never `openkos.api._*`)
  and `openkos.localapi.*`. `openkos.mcp`, `openkos.mcp.gate`, `openkos.application`,
  `openkos.state`, `openkos.cli`, `typer`, `rich`, `starlette` and `anyio` are
  asserted by name, each with a direct failing example, as the API guard does.
- **Confinement**: no module under `src/openkos/` outside `localapi/` imports
  `fastapi`, `uvicorn`, `starlette`, `pydantic` or `anyio` (AST scan, with a
  non-vacuity floor). The CLI's lazy `from openkos.localapi import launch` is allowed
  because it names `openkos.localapi`, not a framework.
- **Fresh interpreter**: `python -I -c "import openkos.cli.main"` leaves `fastapi`,
  `uvicorn`, `starlette`, `pydantic` and `openkos.localapi` out of `sys.modules`
  (the `test_mcp_cmd.py:139` pattern).
- **Unchanged**: the owner-local boundary scan already covers `localapi` by
  construction (it scans every top-level package but `application`, `cli` and
  `api`); `localapi` is not added to its allowed importers, because it reaches the
  decision and judging surfaces only through `openkos.api`.

### Decision 11: Tests

**Choice**: `tests/unit/localapi/`, all under strict TDD (observed RED first).

| Layer | What | How |
|---|---|---|
| Guard (ASGI) | each of the four checks, each failure code, the CORS headers on allowed-Origin responses, `Vary`, `WWW-Authenticate`, websocket close, sha256 comparison | call the guard directly with a recording inner app and synthetic scopes; assert the inner app was or was not reached |
| Route enumeration | every `APIRoute` in `create_app(...).routes` × {no token → 401, wrong token → 403, `evil.com` Host → 403, right host wrong port → 403, foreign Origin with valid token → 403, token only in the query → 401}; preflight from the allowed Origin with no token → 204; preflight with a foreign Host → 403; unknown path without a token → 401 | `TestClient(app, base_url=f"http://127.0.0.1:{PORT}")` as a context manager (runs the lifespan); the expected route set is asserted exactly (11 routes) so the matrix is not vacuous |
| Populated models | every response type with every optional field set and every union member: `WorkspaceStatus` with `MappingProxyType` mappings and `edges=(3, 1)`, each `Decision.detail` member and `None`, each plan, `QuoteNotice` with each code, `QueueRefresh` with `ambiguous`, `JudgingOutcomeBody` with a `failure` | patch `openkos.api.<fn>` with a stub that returns the instance and records its call (an unread patch fails silently); assert the exact JSON, then `TypeAdapter(T).validate_python(json)` `==` the instance |
| Echo round-trip | `JudgingQuote.token` JSON posted back to `/runs` and `DecisionPreview.version` JSON posted to `/apply` reach the stubbed API `==` the issued values | stubs capture the received argument |
| Error table | each exception row (status, code, retryable, message, `Retry-After`), raised with a sentinel message that must not appear in the body (except `WriteRefused`, whose `message` must); each outcome row; unknown exception → 500; validation errors never echo input | parametrized over the tables |
| Lock wait | apply and refresh pass `wait_seconds == 5.0`; `wait_seconds` above 5 or below 0 → 422 `invalid_request`; a held workspace lock → 503 with `Retry-After: 2` | the last with `WRITE_WAIT_SECONDS` patched to 0.1 and the real lock held by the test |
| Runs | admission (stub that calls `stop.is_set()` then blocks on an event); 409 `quote_stale` and 409 `run_active`; raise before and after admission; poll states and `persisted`; stop; eviction; `shutdown(grace)` returns within the grace while a stub stays blocked | stubbed `api.run_judging`, `threading.Event` gates, no sleeps beyond bounded waits |
| Admission pin | the real `api.run_judging` calls `stop.is_set()` before its first chat call, for each kind | the existing judging fixtures with a recording chat-client factory (`chat_client=`) and a recording `StopToken` subclass |
| Reads, real workspace | status, pending and decisions on an initialized workspace | the existing workspace fixture |
| Launch units | token reader (valid, no newline, too long, bad charset, empty, never echoed); ready-line format; missing-extra remedy with `sys.modules["fastapi"] = None` | in-process |
| Launch subprocess (POSIX) | token on stdin → one ready line → a child Python probe gets 200 with the token and 401 without → closing stdin exits 0 with no further stdout; SIGTERM exits 0; EOF before a token exits 2 with no ready line | `subprocess.Popen` of the CLI, bounded reads; the HTTP probe runs in a second child process (correction 5) |
| Guards | Decision 10 | AST scans and one fresh interpreter |
| Contract | `openapi_document()` equals the committed `tests/unit/localapi/openapi.json`; `create_app(settings).openapi()` equals it; every path starts with `/v1/`; no component name ends in `-Input`/`-Output` or is module-qualified; `info.version == str(api.API_VERSION)` | regenerate with `uv run python -m tests.unit.localapi.openapi_snapshot --write`, in the same commit as the change, like `api_surface.json` |

- `openapi_document()` builds the app's routes and handlers without settings, so the
  snapshot needs no workspace; `info.version` is the API version, never the package
  version, so a release does not move the snapshot.
- None of these tests are `cross_platform_smoke`; the subprocess tests skip on
  Windows (SIGTERM semantics) and run on Linux in CI.

**The one-off TypeScript measurement** (binding decision 10), run once by the S6
implementer in a scratch directory outside the repository, and recorded in the S6
PR description and the verify report, never committed:

1. Copy `tests/unit/localapi/openapi.json`.
2. `npx openapi-typescript@7 openapi.json -o api.d.ts`.
3. A `check.ts` that: narrows `Decision.detail` with `"member_ids" in detail`;
   switches on `DecisionPreview.action` and reads the matching `plan` fields;
   destructures `WorkspaceStatus.edges` as `[number, number] | null`; assigns a
   `JudgingQuote["token"]` to `RunRequest["token"]` and a
   `DecisionPreview["version"]` to `ApplyRequest["version"]` with no cast; reads an
   `ErrorBody` from a 409.
4. `npx -p typescript@5 tsc --noEmit --strict check.ts`.
5. Record: Node, `openapi-typescript` and `tsc` versions; commands; exit codes;
   generated line count; whether `prefixItems` became a tuple type; whether each
   narrowing compiled without casts; any `-Input`/`-Output` names. If Node is not
   available, record "not measured" rather than a pass.

### Decision 12: Packaging

**Choice**: `pyproject.toml`:

```toml
[project.optional-dependencies]
watch = ["watchdog>=4.0.0"]
# The local HTTP API (`openkos serve`, ADR-0061) for the desktop app. Imported
# only inside `openkos.localapi`; the core never needs it. Each package listed is
# imported directly (pydantic by the request and response models).
api = ["fastapi>=0.143.2", "pydantic>=2.14.1", "uvicorn>=0.54.0"]

[dependency-groups]
dev = [..., "fastapi>=0.143.2", "httpx>=0.28.1", "pydantic>=2.14.1", "uvicorn>=0.54.0"]
```

The floors are the spike's resolved versions (`RESULTS.md:4`), the only ones measured.
`httpx` is dev only (`TestClient`). `uv.lock` is regenerated and excluded from the
authored-line count. FastAPI's hard dependency on `opentelemetry-api` (`RESULTS.md:86`)
comes with the extra and is recorded in ADR-0061.

### Decision 13: ADR-0061 and documentation

**ADR-0061**: `docs/adr/0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md`,
status Proposed, dated the S1 commit, indexed in `docs/adr/README.md`.

- **Context**: ADR-0058 fixed the security shape, not the stack; `openkos.api` v1
  is the operation set; the shell cannot call Python in-process; the spike's
  footprint and freeze evidence (13 distributions, about 9.8 MB installed, +8.2 MB
  frozen, about 0.32 s to the first response, zero PyInstaller warnings); the
  engine had declared no Pydantic and no web framework.
- **Decision**: (1) FastAPI on uvicorn, as a thin adapter over `openkos.api` only;
  (2) Pydantic becomes a dependency for the first time, confined to
  `openkos.localapi`; the "no pydantic" rule now reads "the core and LLM
  extraction use no validation library", and recovery of structured model replies
  stays `llm/parsing.py`; (3) shipped as the optional extra `openkos[api]`, with the
  dev group carrying it for CI; (4) short operations on the anyio worker pool as
  ADR-0021's "anyio equivalent", the judging run on its own daemon thread, no
  per-call deadline because no short operation calls a model, and the deviation
  from the MCP server's thread-per-call, `Semaphore(4)` and deadline stated; (5) one
  ASGI guard in the order Host, CORS preflight, Origin, token, with the preflight
  answered before the token check and never before the Host check, and the Tauri
  origin allowlist; (6) the stdin token (sha256-only in memory), the port-0 bind
  handed to uvicorn, the one-line JSON ready line, stdin EOF and SIGTERM as the
  shutdown lifelines; (7) `opentelemetry-api` arrives as a transitive hard dependency
  of FastAPI.
- **Consequences**: easier: typed OpenAPI for the shell's client, request validation,
  dataclasses served directly, one guard for every route. Harder: a second
  serialization path (Pydantic) beside the API's dataclasses; the import guard now has
  one sanctioned importer; the `openapi.json` snapshot must be regenerated
  deliberately; the frozen app grows by about 8 MB; a stuck model call is abandoned at
  shutdown, never preempted; the threadpool has no deadline, so a short operation that
  hangs holds its thread (accepted: none calls a model).
- **Alternatives considered**: Starlette alone (2.1 MB, no schema, hand-written
  serialization; rejected because the shell's TypeScript client wants a schema);
  the hand-rolled stdlib server pattern of ADR-0027 (rejected: HTTP/1.1 parsing,
  CORS and concurrency are where hand-rolled servers fail); FastAPI as a core
  dependency (rejected: every `pip install openkos` pays for a desktop-only adapter);
  MCP's thread-per-call with a semaphore and deadline (rejected for short operations:
  no model call to bound; kept in spirit for the run's dedicated thread);
  `CORSMiddleware` plus a separate token middleware (rejected, Decision 4).
- ADR-0058 is not edited and stays Proposed; ADR-0061 cites it for the security
  shape and records only the preflight ordering it did not cover.

**Docs edits** (shape only, per the repository's documentation policy):

| File | Slice | Edit |
|---|---|---|
| `AGENTS.md` | S1 | "LLM calls" bullet: `pydantic` is not a core dependency; it arrives only with the `[api]` extra, confined to `openkos.localapi` (ADR-0061). "The core is synchronous" bullet: async only at the adapter edges (`mcp/`, `localapi/`) |
| `openspec/config.yaml` | S1 | `context`: pydantic is not a core dependency (optional `api` extra: fastapi, pydantic, uvicorn, confined to `openkos.localapi`) |
| `docs/tech_stack.md` | S1 | the Pydantic rows (`:66`, `:107`) and the "no web framework is a dependency yet" sentence (`:83`) |
| `docs/adr/README.md` | S1 | index row for ADR-0061 |
| `docs/cli.md` | S5 | a `serve` section (stdin token, ready line, lifelines, exit codes, the extra); the "not part of the CLI: a local API (planned …)" sentence (`:1284`) |
| `docs/architecture.md` | S6 | the adapter package ships (`:222`, `:228` and the shipped tree) |
| `docs/roadmap.md` | S6 | MVP 6 local API stage status (`:232`, `:242`) |
| `tests/unit/application/test_curate_decisions_boundary.py` docstring | S2a | correction 8 |

No count ("eleven endpoints") is written into prose; `openapi.json` and `--help` are
the authorities.

### Decision 14: Slices

**Choice** (auto-chain; about 400 authored changed lines per PR is advisory; tests
and docs are not cut to fit; `uv.lock` and `openapi.json` are generated and excluded).
The OpenAPI snapshot machinery lands with the first route so every later slice
regenerates it in the same commit, as `api_surface.json` does.

| Slice | Content | Authored lines (forecast) |
|---|---|---|
| S1 | ADR-0061 and index row; `[api]` extra and dev-group deps; `AGENTS.md`, `openspec/config.yaml`, `docs/tech_stack.md` | ~180 |
| S2a | `localapi/__init__.py`, `_guard.py`, `_errors.py` (envelope, guard and framework rows, `invoke`, `ApiProblem`), `_app.py` (factory, handlers, lifespan stub), `/v1/health`; guard ASGI tests, route-enumeration and preflight tests, Decision 10 guards and the API-importer amendment, boundary docstring, OpenAPI snapshot generator and test | ~450 |
| S2b | status, pending, decisions endpoints; `DecisionList`; read-path exception rows; populated-model tests (including the `MappingProxyType` RED) and real-workspace reads | ~380 |
| S3 | preview, apply, refresh; request models; outcome tables; `WRITE_WAIT_SECONDS`, `Retry-After`; echo round-trip of `DecisionVersion`; lock-wait and 409/422/503 tests | ~420 |
| S4a | `_runs.py` registry, admission stop, observer, shutdown, with unit tests that need no HTTP | ~350 |
| S4b | quote endpoint; run start/poll/stop routes; `RunStatus`, `JudgingOutcomeBody`; backend rows; `QuoteToken` echo round-trip; admission pin against the real service | ~400 |
| S5 | `launch.py`; `serve` verb and its classification; missing-extra remedy; CI step; token/ready-line unit tests; subprocess launch tests; fresh-interpreter lazy-import test; `docs/cli.md` | ~480 |
| S6 | measured TypeScript generation (recorded, not committed); `docs/architecture.md`, `docs/roadmap.md` | ~60 |
| S7 | archive: `local-http-api` spec and `stable-python-api` delta merged with every heading name-matched; ADR-0061 to Accepted | docs |

S2a and S5 exceed the advisory 400 because their test matrices are the point of
the slice (the guard matrix; the subprocess lifecycle); splitting them would ship a
guard without its proof or a verb without its shutdown tests. Each slice is
independently revertible: S2b-S4b add routes nothing in the engine calls; S5 adds the
verb; S1 adds an optional extra and a Proposed ADR. The chain strategy
(stacked-to-main or feature-branch-chain) is the orchestrator's cached choice.

## Data Flow

### A read

```
client ──HTTP──▶ uvicorn ──▶ _guard (Host, CORS, Origin, token) ──▶ FastAPI router
                                                                     │ def endpoint (anyio pool thread)
                                                                     ▼
                                              _errors.invoke(lambda: api.status(settings.workspace))
                                                                     │ raises → ApiProblem → ErrorBody
                                                                     ▼
                                       WorkspaceStatus (mappings as dict) ──▶ Pydantic ──▶ JSON
```

### Preview and apply

```
client                         localapi                                  openkos.api
------                         --------                                  -----------
POST /v1/decisions/K/preview {action}
                               invoke(api.preview_decision(ws, K, action))
                                 DecisionPreview ──────────────────────▶ 200 {decision_key, action, plan, version}
                                 NotApplied / Stale ───────────────────▶ 409 / 422 ErrorBody (per reason)
POST /v1/decisions/K/apply {action, version}
                               K == version.decision_key? else 422 key_mismatch
                               warnings = []
                               invoke(api.apply_decision(ws, DecisionVersion(**), action,
                                                         wait_seconds=5.0, on_warning=warnings.append))
                                 Applied ──────────────────────────────▶ 200 {applied, warnings}
                                 Stale / NotApplied ───────────────────▶ 409 / 422
                                 WorkspaceBusyError (after 5 s) ───────▶ 503 + Retry-After: 2
                                 DriftDetected / WriteRefused / WriteFailed ▶ 409 / 422 / 500 (message passed through)
```

### Judging and launch

See the sequence diagrams in Decisions 7 and 9.

## File Changes

| File | Action | Slice | Description |
|---|---|---|---|
| `docs/adr/0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md` | Create | S1 | ADR-0061, Proposed |
| `docs/adr/README.md` | Modify | S1 | Index row |
| `pyproject.toml` | Modify | S1 | `api` extra; dev group gains `fastapi`, `httpx`, `pydantic`, `uvicorn` |
| `uv.lock` | Regenerate | S1 | Generated |
| `AGENTS.md`, `openspec/config.yaml`, `docs/tech_stack.md` | Modify | S1 | Decision 13 |
| `localapi/__init__.py`, `_guard.py`, `_errors.py`, `_app.py`, `_routes.py` (health) | Create | S2a | Package, guard, envelope, factory |
| `tests/unit/localapi/__init__.py`, `conftest.py`, `test_guard.py`, `test_routes_security.py`, `test_localapi_guards.py`, `openapi_snapshot.py`, `openapi.json`, `test_openapi_snapshot.py` | Create | S2a | Guard matrix, enumeration, import guards, contract |
| `tests/unit/api/test_api_guards.py` | Modify | S2a | Admit `localapi/` as the sole importer |
| `tests/unit/application/test_curate_decisions_boundary.py` | Modify | S2a | Docstring only |
| `localapi/_routes.py`, `_models.py` (`DecisionList`) | Modify / Create | S2b | Read endpoints |
| `tests/unit/localapi/test_reads.py` | Create | S2b | Populated and real-workspace reads |
| `localapi/_routes.py`, `_models.py`, `_errors.py` | Modify | S3 | Preview, apply, refresh; outcome tables; lock wait |
| `tests/unit/localapi/test_decisions_write.py`, `test_errors.py` | Create | S3 | Writes, echo, error table |
| `localapi/_runs.py` | Create | S4a | Registry, admission, observer, shutdown |
| `tests/unit/localapi/test_runs_registry.py` | Create | S4a | Thread and stop tests |
| `localapi/_routes.py`, `_runs.py`, `_models.py`, `_app.py` | Modify | S4b | Quote and run routes; DTOs; lifespan shutdown |
| `tests/unit/localapi/test_judging_routes.py`, `test_admission_pin.py` | Create | S4b | HTTP run tests; pin against the real service |
| `localapi/launch.py` | Create | S5 | `serve(root)` |
| `cli/main.py` | Modify | S5 | `serve` verb; `_SELF_LOCKING_COMMANDS` |
| `.github/workflows/ci.yml` | Modify | S5 | Wheel-without-extra `serve` step |
| `tests/unit/localapi/test_launch.py`, `tests/unit/cli/test_serve_cmd.py` | Create | S5 | Token, ready line, remedy, subprocess lifecycle, lazy import |
| `docs/cli.md` | Modify | S5 | `serve` section |
| `docs/architecture.md`, `docs/roadmap.md` | Modify | S6 | Shape |
| `openspec/specs/local-http-api/spec.md`, `openspec/specs/stable-python-api/spec.md` | Create / Merge | S7 | Archive |

## Interfaces / Contracts

```python
# src/openkos/localapi/_errors.py
WRITE_WAIT_SECONDS: Final = 5.0
RETRY_AFTER_SECONDS: Final = 2

class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    retryable: bool
    message: str

class ApiProblem(Exception):
    def __init__(self, status: int, body: ErrorBody,
                 headers: Mapping[str, str] | None = None) -> None: ...

def invoke[T](fn: Callable[[], T]) -> T: ...                 # raises ApiProblem for any mapped exception
def outcome_problem(value: api.Stale | api.NotApplied | api.QuoteStale | api.RunRefused) -> ApiProblem: ...
def error_body(exc: BaseException) -> tuple[int, ErrorBody, dict[str, str]]: ...

# src/openkos/localapi/_models.py
class PreviewRequest(BaseModel):  action: api.Action
class ApplyRequest(BaseModel):    action: api.Action; version: api.DecisionVersion; wait_seconds: float = Field(5.0, ge=0, le=5.0)
class RefreshRequest(BaseModel):  wait_seconds: float = Field(5.0, ge=0, le=5.0)
class QuoteRequest(BaseModel):    kind: api.JudgingKind; include_confidential: bool = False; include_uncertain: bool = False
class RunRequest(BaseModel):      token: api.QuoteToken | None = None   # None -> RunRefused -> 422 run_refused
class HealthBody(BaseModel):      status: Literal["ok"]; api_version: int; engine_version: str
class DecisionList(BaseModel):    decisions: list[api.Decision]
class ApplyResponse(BaseModel):   applied: api.Applied; warnings: list[str]
class JudgingOutcomeBody(BaseModel):
    kind: api.JudgingKind
    status: Literal["completed", "stopped", "failed", "unpersisted"]
    judged: int; calls: int; persisted: int; unpersisted: int
    unpersisted_cause: Literal["workspace_busy", "store_error"] | None
    dropped: int; deferred: int; not_in_quote: list[str]; settled_since_quote: int
    failure: ErrorBody | None; failed_at: int | None; observer_failed: bool
class RunStatus(BaseModel):
    run_id: str
    kind: api.JudgingKind
    state: Literal["running", "stopping", "finished", "failed"]
    index: int | None; total: int | None; persisted: int
    outcome: JudgingOutcomeBody | None
    error: ErrorBody | None
# every model: model_config = ConfigDict(frozen=True, extra="forbid")

# src/openkos/localapi/_app.py
@dataclass(frozen=True)
class ServerSettings:
    workspace: api.Workspace
    port: int
    token_digest: bytes = field(repr=False)          # sha256 of the token; the token is never kept
    origins: tuple[str, ...] = DEFAULT_ORIGINS

DEFAULT_ORIGINS: Final = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")

def create_app(settings: ServerSettings) -> FastAPI: ...      # guard + routes + handlers + lifespan
def openapi_document() -> dict[str, object]: ...              # the same routes, no settings

# src/openkos/localapi/_runs.py
ADMISSION_TIMEOUT_SECONDS: Final = 10.0
SHUTDOWN_GRACE_SECONDS: Final = 5.0
FINISHED_RUNS_KEPT: Final = 16

class RunRegistry:
    def start(self, workspace: api.Workspace, token: api.QuoteToken) -> RunStatus: ...   # raises ApiProblem
    def status(self, run_id: str) -> RunStatus: ...                                       # raises ApiProblem 404
    def stop(self, run_id: str) -> RunStatus: ...
    def shutdown(self, grace: float) -> None: ...

# src/openkos/localapi/launch.py
def serve(root: Path, *, stdin: BinaryIO | None = None, stdout: TextIO | None = None,
          install_signals: bool = True) -> int: ...
def read_token(stream: BinaryIO) -> str: ...                  # raises TokenError; never echoes the input
def ready_line(port: int) -> str: ...
```

Ready line (stdout, exactly one line):

```json
{"event": "ready", "host": "127.0.0.1", "port": 51234, "api_version": 1, "base_path": "/v1"}
```

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | guard, error tables, registry, token reader, ready line, import guards | direct calls and AST scans (Decision 11) |
| Unit (HTTP in-process) | every route's security, populated serialization, echo round-trips, outcome and error statuses, lock wait, run lifecycle | `TestClient` with a `127.0.0.1:<port>` base URL, stubs on `openkos.api` |
| E2E (process) | launch, ready line, health over a real socket, stdin EOF and SIGTERM exits, EOF before token, missing extra | subprocess tests (POSIX), the CI wheel step |
| Contract | `openapi.json` snapshot, naming invariants, one-off TypeScript compile | snapshot test; recorded measurement |

Coverage: the new package must keep the 90% branch gate; the subprocess tests do not
count toward coverage, so every branch of `launch.py` that matters also has an
in-process unit test.

## Threat Matrix

The skill's matrix rows:

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A: nothing is classified or executed by file name | - | - |
| Git repository selection | N/A: the adapter runs no git; apply's auto-commit is `openkos.api`'s, chosen by `Workspace.root` and pinned by its existing tests (`api/_wiring.py:10-12`) | - | - |
| Commit state | N/A: unchanged API commit path | - | - |
| Push state | N/A: nothing pushes | - | - |
| PR commands | N/A: no PR automation | - | - |

Because the change adds a network listener and a process lifecycle, its own
boundary matrix is a design requirement and carries into tasks unchanged:

| Boundary | Adversarial case | Expected safe behavior | Planned RED test |
|---|---|---|---|
| Bind | any non-loopback interface | binds `127.0.0.1` only | ready line `host`; socket name asserted in the launch unit test |
| Host | `evil.com`, `127.0.0.1:<other>`, missing, duplicated, `[::1]:<port>` | 403 `host_not_allowed` before CORS or token | guard matrix; route enumeration |
| CORS preflight | foreign Origin; disallowed method or header; foreign Host | 403; Host checked first | guard matrix |
| Origin | foreign, `null` | 403 `origin_not_allowed` | guard matrix; route enumeration |
| Token | missing, wrong scheme, duplicated, wrong value, query string only | 401 / 403; constant-time digest compare | guard matrix; route enumeration |
| Token delivery | argv, environment, file, URL | impossible: stdin only; no option carries it | `serve --help` lists no token option; stdin reader tests |
| Token handling | logging or echo | only `sha256(token)` kept; never in `repr`, messages or bodies | `ServerSettings` repr test; token reader error-message test |
| Proxy headers | `X-Forwarded-For`/`-Host` | ignored (`proxy_headers=False`) | a forwarded Host header does not bypass the Host check |
| Error disclosure | exception text with sentinel content | fixed messages; `WriteRefused.message` only | error-table sentinel tests; validation-error no-echo test |
| Lifecycle | shell dies (stdin EOF), SIGTERM, stuck model call | exit 0 within the grace; no orphan | subprocess EOF and SIGTERM tests; `shutdown(grace)` with a blocked run |
| Lock | client-chosen long wait | impossible; 5 s ceiling, 503 | `wait_seconds` above 5 → 422; busy → 503 |

## Migration / Rollout

No migration required. The extra is optional; nothing in the engine calls the
adapter; `/v1` is not published until the desktop shell consumes it. Rollback is per
slice, as in the proposal.

## Open Questions

- [x] **Spec reconciliation.** Resolved by the reconciliation pass over spec and
      design: one error table (codes, statuses, `retryable`) now appears identically
      in both; `NotApplied` is split per reason; `RunRequest.token` is optional with
      omission mapped to 422 `run_refused`; `wait_seconds` is an optional request
      field in `[0, 5]`; the job resource is `{run_id, kind, state, index, total,
      persisted, outcome, error}` with the newest 16 runs retained; confinement is
      the stricter rule of Decision 10; exit codes are 0/1/2; the missing extra is
      proved by a CI build-job step plus an in-process unit test.
- [ ] **Dev-server origin.** The Tauri dev server's origin (for example
      `http://localhost:1420`) is not on the allowlist. A `--allow-origin` option is
      additive and belongs to the desktop-shell stage; confirm then.
- [ ] **`Retry-After: 2`, `ADMISSION_TIMEOUT_SECONDS = 10` and
      `SHUTDOWN_GRACE_SECONDS = 5`** are unmeasured placeholders, named constants
      so a measurement changes one line.
- [ ] **Request body size.** No cap is imposed; a large identity quote's echoed token
      can be megabytes. Loopback and token-authenticated, so deferred unless S4b's
      populated test shows a problem.
