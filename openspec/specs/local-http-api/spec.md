# Local HTTP API Specification

## Purpose

The loopback HTTP transport through which a local client, first the desktop
shell, reaches the engine. It exposes exactly the operations of the stable
Python API (`openkos.api`, ADR-0060) and nothing else: workspace status, the
pending report, the open decisions with preview and apply, judging quote and
run, and the model-free queue refresh. It is a thin adapter: it holds one
workspace handle opened at launch, calls the v1 functions, and owns only
transport, serialization, error mapping and the judging job registry. It adds
no engine capability, no stored state and no new operation.

The security shape follows ADR-0058: the server binds `127.0.0.1` on an
ephemeral port, requires a per-launch bearer token delivered over stdin, and
checks Host and Origin on every route. The transport is the FastAPI and uvicorn
adapter recorded in ADR-0061.

Responses are owner-local in this stage: they return stored content, including
confidential-derived text, to the token holder, as the CLI and `openkos.api`
do. The sensitivity disclosure gate is not applied here and is deferred to the
first ask/query endpoint. This is a recorded decision, not an omission.

Route paths in this specification are relative to the `/v1` prefix unless they
start with `/`.

## Requirements

### Requirement: The Server Is Launched By `openkos serve` With A Stdin Token

`openkos serve --workspace PATH` MUST start the server for the workspace at
`PATH`, fixed for the life of the process. The workspace MUST be opened and
validated before anything is bound; an invalid or unreadable workspace MUST
make the command exit with status 1 and the reason on stderr, bind nothing and
write nothing to stdout. The command MUST read the bearer token as exactly one
line from stdin before binding any socket. The token MUST NOT be accepted from
argv, the environment, a file or a URL. A token line that is absent (stdin at
EOF before a line) or empty MUST make the command exit non-zero before
binding, with status 2, the reason on stderr and nothing on stdout. The command MUST
bind `127.0.0.1` on an operating-system-chosen port (port 0) and MUST NOT bind
any other address. Once the server accepts connections, the command MUST write
exactly one line to stdout: a single JSON object announcing readiness that
carries at least `event` equal to `"ready"`, `host` equal to `"127.0.0.1"` and
`port` as the integer bound port, and MAY carry further fields such as
`api_version` and `base_path`. Stdout MUST carry nothing else for the life
of the process; all logs MUST go to stderr, and a log line MUST NOT contain
the token. The exit status MUST be 0 for a clean shutdown, 1 for a runtime
failure (a refused workspace, a bind or start-up failure) and 2 for a usage
failure (an absent, empty or malformed token line, or a missing `api` extra).

#### Scenario: A token on stdin starts a ready server

- GIVEN a valid workspace and a token written as one line to the command's
  stdin
- WHEN `openkos serve --workspace PATH` runs
- THEN stdout carries exactly one line that parses as a JSON object with
  `event` `"ready"`, `host` `"127.0.0.1"` and an integer `port`, and any
  further field is limited to `api_version` and `base_path`
- AND a connection to that port on `127.0.0.1` is accepted

#### Scenario: The announced port is the bound port

- GIVEN a started server and its ready line
- WHEN a request with a valid token and `Host: 127.0.0.1:<port>` is sent to
  that port
- THEN it is served

#### Scenario: The token is read before binding

- GIVEN stdin is closed without a token line
- WHEN the command runs
- THEN it exits with status 2, stdout is empty and no socket was bound

#### Scenario: An empty token is refused

- GIVEN stdin carries an empty line
- WHEN the command runs
- THEN it exits with status 2, stdout is empty and no socket was bound

#### Scenario: The token is not accepted through argv or the environment

- GIVEN a token passed only as an argument or an environment variable and an
  empty stdin
- WHEN the command runs
- THEN it exits with status 2 as for a missing token, and that value is never used
  to authenticate

#### Scenario: A bad workspace fails before readiness

- GIVEN a path that is not a workspace root
- WHEN the command runs with a token on stdin
- THEN it exits with status 1, the reason is on stderr and stdout is empty

#### Scenario: Stdout is reserved

- GIVEN a running server that has served requests and logged
- WHEN stdout is read to its end
- THEN it contains only the ready line

### Requirement: The Server Shuts Down When Its Parent Goes Away

The command MUST treat end-of-file on stdin after readiness as the loss of its
parent and MUST shut down gracefully, exiting zero, so that no orphan engine
outlives a force-quit shell. On SIGTERM the command MUST set the cooperative
stop of any active judging run, stop accepting connections and exit zero. In
both cases it MUST NOT wait indefinitely for a run: a model call in flight
cannot be preempted, so shutdown MUST complete within a bounded time even if
the run's thread has not finished. Chunks already persisted by a run MUST
remain persisted.

#### Scenario: Stdin EOF ends the process

- GIVEN a running server
- WHEN the parent closes the command's stdin
- THEN the process exits with status zero

#### Scenario: SIGTERM ends the process

- GIVEN a running server with no active run
- WHEN it receives SIGTERM
- THEN the process exits with status zero

#### Scenario: SIGTERM stops an active run

- GIVEN a running server with an active judging run
- WHEN it receives SIGTERM
- THEN the run's stop is set and the process exits within a bounded time

#### Scenario: A stuck model call does not hold the process

- GIVEN an active run blocked in a model call that never returns
- WHEN stdin reaches EOF
- THEN the process still exits within a bounded time

### Requirement: A Missing `api` Extra Fails With An Install Remedy

The HTTP framework and server are an optional dependency, the extra
`openkos[api]`. When they cannot be imported, `openkos serve` MUST exit
with status 2, MUST write a message to stderr that names `openkos[api]` as the
remedy, and MUST write nothing to stdout. Importing the CLI, running
`openkos --help` and running every other verb MUST NOT import `fastapi`,
`starlette`, `uvicorn` or `pydantic`, so a build without the extra is fully
usable apart from `serve`. This MUST be proved twice: by a CI build-job step
that installs the built wheel without the extra and asserts that `openkos
--help` succeeds and that `openkos serve` exits with the install remedy on
stderr, and by an in-process unit test in which the framework is not
importable.

#### Scenario: Serve without the extra

- GIVEN an environment where the HTTP framework is not importable
- WHEN `openkos serve --workspace PATH` runs
- THEN it exits with status 2, stderr names `openkos[api]` and stdout is empty

#### Scenario: Help without the extra

- GIVEN the same environment
- WHEN `openkos --help` runs
- THEN it succeeds

#### Scenario: The built wheel without the extra

- GIVEN the CI build job's wheel installed without `[api]`
- WHEN `openkos --help` and then `openkos serve --workspace PATH` run
- THEN the first succeeds, and the second exits with status 2 with the install
  remedy on stderr

#### Scenario: CLI start-up never loads the framework

- GIVEN a fresh interpreter that imports the CLI application
- WHEN `sys.modules` is inspected
- THEN none of `fastapi`, `starlette`, `uvicorn` or `pydantic` is present

### Requirement: Every Route Passes The Host Check

Every request, to every registered route including any route the framework
provides and any path that matches no route, MUST be rejected with 403 when its
`Host` header is absent or is not a loopback host (`127.0.0.1` or `localhost`)
carrying exactly the port the server is bound to. The Host check MUST precede
every other check: a request with a foreign Host MUST receive 403 whatever its
Origin, token or method, including a CORS preflight.

#### Scenario: A forged Host is rejected

- GIVEN a server bound to port P and a valid token
- WHEN a request is sent with `Host: evil.example`
- THEN the response is 403

#### Scenario: The right host on the wrong port is rejected

- GIVEN a server bound to port P
- WHEN a request is sent with `Host: 127.0.0.1:<other port>`
- THEN the response is 403

#### Scenario: A loopback Host with the bound port passes the check

- GIVEN a server bound to port P and a valid token
- WHEN a request is sent with `Host: localhost:P`
- THEN the Host check passes and the request is served

#### Scenario: Host is checked before the token

- GIVEN a request with a foreign Host and no token
- WHEN it is sent
- THEN the response is 403, not 401

### Requirement: Every Route Passes The Origin Check

A request that carries an `Origin` header MUST be rejected with 403 unless the
origin is on a short fixed allowlist of the desktop shell's own origins, which
includes `tauri://localhost`. A request with no `Origin` header (a non-browser
client such as the shell's native layer) MUST NOT be rejected for that reason.
The literal origin `null` is not on the allowlist. The Origin check MUST follow
the Host check and MUST precede the token check: a request with a disallowed
Origin MUST receive 403 whatever its token.

#### Scenario: A foreign origin is rejected

- GIVEN a valid Host and a valid token
- WHEN a request is sent with `Origin: https://evil.example`
- THEN the response is 403

#### Scenario: The shell origin is accepted

- GIVEN a valid Host and a valid token
- WHEN a request is sent with `Origin: tauri://localhost`
- THEN the request is served

#### Scenario: No origin is accepted

- GIVEN a valid Host and a valid token
- WHEN a request is sent with no `Origin` header
- THEN the request is served

#### Scenario: A null origin is rejected

- GIVEN a valid Host and a valid token
- WHEN a request is sent with `Origin: null`
- THEN the response is 403

#### Scenario: Origin is checked before the token

- GIVEN a request with a disallowed Origin and no token
- WHEN it is sent
- THEN the response is 403, not 401

### Requirement: CORS Preflight Is Answered Without A Token But Never Without A Valid Host And Origin

A CORS preflight (an `OPTIONS` request carrying `Origin` and
`Access-Control-Request-Method`) carries no `Authorization` header. For a
preflight whose Host passes the Host check and whose Origin is on the allowlist,
the server MUST answer successfully without requiring the token, with
`Access-Control-Allow-Origin` equal to the request's origin, and with allowed
methods and headers sufficient for the `/v1` operations, including
`Authorization` and `Content-Type`. A preflight with a foreign Host or a
disallowed Origin MUST receive 403 and MUST NOT carry any
`Access-Control-Allow-*` header. A preflight that passes the Host check but
whose Origin is not on the allowlist, or that requests a method other than
`GET` or `POST` or a header other than `Authorization` and `Content-Type`, MUST
receive 403 with code `preflight_rejected`. A non-preflight request from an allowlisted
origin MUST still require the token, and its response MUST carry
`Access-Control-Allow-Origin` for that origin. The server MUST NOT allow any
origin by wildcard.

#### Scenario: An allowed preflight succeeds without a token

- GIVEN a valid Host
- WHEN an `OPTIONS` preflight with `Origin: tauri://localhost`,
  `Access-Control-Request-Method: POST` and no `Authorization` header is sent
  to an existing route
- THEN the response is successful with `Access-Control-Allow-Origin:
  tauri://localhost`
- AND the allowed headers include `Authorization`

#### Scenario: A preflight with a foreign Host is rejected

- GIVEN a preflight from an allowlisted origin
- WHEN it is sent with `Host: evil.example`
- THEN the response is 403 and carries no `Access-Control-Allow-*` header

#### Scenario: A preflight from a foreign origin is rejected

- GIVEN a valid Host
- WHEN a preflight with `Origin: https://evil.example` is sent
- THEN the response is 403 with code `preflight_rejected` and carries no
  `Access-Control-Allow-*` header

#### Scenario: A preflight asking for an unsupported method is rejected

- GIVEN a valid Host and an allowlisted origin
- WHEN a preflight with `Access-Control-Request-Method: DELETE` is sent
- THEN the response is 403 with code `preflight_rejected`

#### Scenario: A real request still needs the token

- GIVEN a valid Host and `Origin: tauri://localhost`
- WHEN a `GET` with no `Authorization` header is sent
- THEN the response is 401

#### Scenario: A served response carries the allow-origin header

- GIVEN a valid Host, `Origin: tauri://localhost` and a valid token
- WHEN a `GET` is sent
- THEN the response is 200 and carries `Access-Control-Allow-Origin:
  tauri://localhost`

### Requirement: Every Route Requires The Per-Launch Bearer Token

After the Host and Origin checks, every request that is not an allowed
preflight MUST present the launch token as `Authorization: Bearer <token>`. A
request with no `Authorization` header, or one that is not a bearer credential,
MUST receive 401. A request with a bearer credential that does not equal the
launch token MUST receive 403. A token carried in the URL, a query string, a
cookie or any header other than `Authorization` MUST NOT authenticate: such a
request without a valid `Authorization` header MUST receive 401. The checks
MUST apply uniformly: a test MUST enumerate every route registered on the
application, plus an unregistered path, and assert that each rejects a missing
token, a wrong token, a foreign Host and a disallowed Origin, so that a route
cannot be added without them. An unregistered path with no token MUST receive
401, not 404. The server MUST NOT serve interactive documentation pages.

#### Scenario: A missing token is 401

- GIVEN a valid Host
- WHEN any registered route is requested with no `Authorization` header
- THEN the response is 401

#### Scenario: A wrong token is 403

- GIVEN a valid Host
- WHEN any registered route is requested with `Authorization: Bearer wrong`
- THEN the response is 403

#### Scenario: A token in the URL does not authenticate

- GIVEN a valid Host
- WHEN a route is requested with the correct token only in the query string
- THEN the response is 401

#### Scenario: A non-bearer scheme is 401

- GIVEN a valid Host
- WHEN a route is requested with `Authorization: Basic <anything>`
- THEN the response is 401

#### Scenario: Every registered route is protected

- GIVEN the application's complete route table
- WHEN the enumeration test sends each route a missing token, a wrong token, a
  foreign Host and a foreign Origin
- THEN every request is rejected with the status of its check

#### Scenario: An unknown path is not an oracle

- GIVEN a valid Host and no token
- WHEN a path that matches no route is requested
- THEN the response is 401, not 404
- AND with a valid token the same path returns 404

#### Scenario: No documentation pages are served

- GIVEN a valid Host and a valid token
- WHEN the conventional interactive documentation paths are requested
- THEN each returns 404

### Requirement: The Operations Are Served Under `/v1`, One Per Python API Operation

The server MUST expose every v1 operation of `openkos.api`, and no operation
`openkos.api` does not have, under the path prefix `/v1`. The prefix MUST be
tied to `openkos.api.API_VERSION`: it is `/v` followed by that integer. The
operations MUST be reachable as follows, and the server MUST call the matching
`openkos.api` function with the workspace handle opened at launch:

| Method and path | `openkos.api` operation |
|---|---|
| `GET /v1/health` | none (liveness; returns `status` `"ok"` and `api_version`) |
| `GET /v1/status` | `status` |
| `GET /v1/pending` | `pending_report` |
| `GET /v1/decisions` | `list_decisions` |
| `POST /v1/decisions/{key}/preview` | `preview_decision` |
| `POST /v1/decisions/{key}/apply` | `apply_decision` |
| `POST /v1/queue/refresh` | `refresh_decision_queue` |
| `POST /v1/judging/quote` | `quote_judging` |
| `POST /v1/judging/runs` | `run_judging`, as a job (see the job requirement) |
| `GET /v1/judging/runs/{id}` | the job's state |
| `POST /v1/judging/runs/{id}/stop` | the job's cooperative stop |

The health route is authenticated like every other route. Every successful
response MUST be JSON. A request body that is not valid for its operation (an
unknown action, a kind outside the allowed values, a malformed echoed token,
a wrong type) MUST be rejected with 422 and the standard error body, before any
operation runs. The server MUST NOT add side effects: a `GET` MUST NOT write,
take the workspace lock or call a model, as the corresponding Python reads do
not.

#### Scenario: Status maps to the status read

- GIVEN an initialised workspace and a valid token
- WHEN `GET /v1/status` is requested
- THEN the response is 200 with the fields of the API's workspace status and
  the workspace is unchanged

#### Scenario: Pending distinguishes an absent queue

- GIVEN a workspace whose queue has not been computed
- WHEN `GET /v1/pending` is requested
- THEN the response is 200 and marks the queue as not computed, distinct from
  a report with zero open rows

#### Scenario: Decisions are listed

- GIVEN a queue with two open decisions
- WHEN `GET /v1/decisions` is requested
- THEN the response is 200 with both decisions and nothing is written

#### Scenario: The prefix follows the API version

- GIVEN `openkos.api.API_VERSION` equal to N
- WHEN the application's routes are listed
- THEN every operation path starts with `/v` followed by N

#### Scenario: Health is authenticated

- GIVEN a valid Host
- WHEN `GET /v1/health` is requested with no token
- THEN the response is 401
- AND with a valid token it is 200 with `status` `"ok"` and `api_version`
  equal to `openkos.api.API_VERSION`

#### Scenario: An invalid body is rejected before the operation

- GIVEN a preview request whose action is not a known action
- WHEN it is sent
- THEN the response is 422 with the standard error body and no operation ran

#### Scenario: No operation beyond the API

- GIVEN the application's route table
- WHEN every operation route is mapped to an `openkos.api` function
- THEN each maps to one, and none exists for ingest, query, doctor or setup

### Requirement: Responses Are The Python API's Result Types, Populated And Round-Tripping

The success response of each operation MUST carry the fields of the
corresponding `openkos.api` result type under the same field names, with
nothing omitted and nothing renamed; a field the HTTP layer cannot serve
natively (for example a mapping, a fixed-size tuple, a path or an exception
instance) MUST be represented by a documented JSON form that preserves its
content. Where a result is one of several variants (the preview's plan, the
decision's detail, the apply outcome, the run result), the response MUST let a
client distinguish the variants from the body. A preview's `plan` is told apart
by the `action` field of the enclosing preview, not by a discriminator on the
plan itself, so a generated typed client narrows it by property presence, not by
`switch (preview.action)`; a discriminated union on the plan is an open
follow-up (#1422). Every response type MUST be exercised with a populated value (not
only an empty list) in a test that serializes it over HTTP and parses it back
without loss.

#### Scenario: A populated status round-trips

- GIVEN a workspace with non-empty counts and an edge count
- WHEN `GET /v1/status` is requested and the body is parsed
- THEN every field equals the value the Python status operation returns, with
  the count mapping and the edge pair preserved

#### Scenario: A populated decision round-trips

- GIVEN an open decision of each kind (identity, relation, tier)
- WHEN `GET /v1/decisions` is requested
- THEN each decision's typed detail is present and identifies its variant

#### Scenario: A preview plan identifies its action

- GIVEN a preview for each action
- WHEN the responses are read
- THEN each plan can be told apart from the others by the `action` field of the
  enclosing preview

### Requirement: Echoed Tokens Make The Wire Form Stateless

The server MUST keep no registry of quotes or decision versions. A preview
response MUST carry the decision version as a JSON object holding the fields
of `DecisionVersion`, and an apply request MUST carry that same object
unchanged in its body. A quote response MUST carry the full `QuoteToken` as
JSON, and a judging-run request MUST carry that token unchanged in its body. The
server MUST reconstruct the Python value from the echoed JSON and pass it to
the operation; a restart of the server between the two calls MUST NOT affect
the outcome. A value the client altered MUST be handled by the operation's own
binding check (`Stale` for a decision version, `QuoteStale` for a quote token),
not by server-held state. An apply request whose path key differs from
`version.decision_key` MUST be rejected with 422 `key_mismatch` before the
operation runs. A body that omits the quote token or sets it to null MUST yield
the `RunRefused` outcome for a missing token, which is 422 `run_refused`; a
token that is present but not of the documented shape MUST yield 422
`invalid_request`.

#### Scenario: Apply with the previewed version

- GIVEN a preview response for an open decision
- WHEN the same version object is posted to apply that decision
- THEN the decision is applied

#### Scenario: A restart between preview and apply

- GIVEN a preview from one server process
- WHEN a new server process on the same workspace receives the apply with that
  version
- THEN the decision is applied, as the workspace has not changed

#### Scenario: An altered version is stale

- GIVEN a version object with one field changed
- WHEN it is posted to apply
- THEN the response is 409 with code `stale`

#### Scenario: A version for a different decision is refused

- GIVEN an apply request whose path key differs from `version.decision_key`
- WHEN it is sent
- THEN the response is 422 with code `key_mismatch` and no operation ran

#### Scenario: A run with the echoed quote

- GIVEN a quote response
- WHEN its token is posted unchanged to start a run
- THEN the run is accepted

#### Scenario: A run without a token is refused

- GIVEN a start request with the token absent
- WHEN it is sent
- THEN the response is 422 with code `run_refused` and no model call is made

#### Scenario: A malformed token is a validation error

- GIVEN a start request whose token is a string instead of the documented object
- WHEN it is sent
- THEN the response is 422 with code `invalid_request`

### Requirement: Outcomes And Errors Map To Status Codes With A Uniform Body

Every non-success response, from the security checks, request validation,
returned outcome values and raised errors alike, MUST be JSON of the form
`{"code": <string>, "retryable": <boolean>, "message": <string>}`. The `code`
is stable and machine-readable; `retryable` is true only where trying the same
request again later can succeed; the `message` is a fixed text per code. The
mapping MUST be evaluated subclass-first and MUST be:

| Source | Status | `code` | `retryable` |
|---|---|---|---|
| Missing or malformed credential | 401 | `unauthorized` | false |
| Wrong token | 403 | `forbidden` | false |
| Host not allowed | 403 | `host_not_allowed` | false |
| Origin not allowed | 403 | `origin_not_allowed` | false |
| Preflight rejected | 403 | `preflight_rejected` | false |
| Unknown path or run id | 404 | `not_found` | false |
| Known path, method not allowed | 405 | `method_not_allowed` | false |
| Invalid request body or parameter | 422 | `invalid_request` | false |
| Path key differs from `version.decision_key` | 422 | `key_mismatch` | false |
| `Stale` outcome (any reason, `unknown_key` included) | 409 | `stale` | false |
| `QuoteStale` outcome | 409 | `quote_stale` | false |
| `NotApplied("not_offered")` | 422 | `action_not_offered` | false |
| `NotApplied("skipped")` | 422 | `skip_not_applicable` | false |
| `NotApplied("not_mergeable")` | 409 | `not_mergeable` | false |
| `NotApplied("member_missing")` | 409 | `member_missing` | false |
| `NotApplied("record_failed")` | 409 | `record_failed` | true |
| `RunRefused` outcome | 422 | `run_refused` | false |
| A judging run already active | 409 | `run_active` | true |
| `DriftDetected` | 409 | `write_drift` | true |
| `WriteRefused` (not drift, not failed) | 422 | `write_refused` | false |
| `WriteFailed` | 500 | `write_failed` | false |
| `WorkspaceBusyError` | 503 | `workspace_busy` | true |
| `WorkspaceLockUnavailableError` | 500 | `workspace_lock_unavailable` | false |
| Queue unavailable | 503 | `queue_unavailable` | false |
| Queue refresh refused | 503 | `queue_refresh_refused` | false |
| `JudgingRefused` | 503 | `judging_refused` | false |
| Workspace not found | 500 | `workspace_not_found` | false |
| Workspace unreadable | 500 | `workspace_unreadable` | false |
| `BackendUnavailable` | 503 | `backend_unavailable` | true |
| `BackendModelNotFound` | 424 | `backend_model_not_found` | false |
| `BackendGenerationCapped` | 502 | `generation_capped` | false |
| Any other `BackendError` (host locality, base) | 502 | `backend_error` | false |
| Any other exception, including any other `OpenKosError` and `OSError` | 500 | `internal_error` | false |

The `message` MUST be a fixed text chosen by the server for each code and MUST
NOT contain the text of the exception (`str(exc)`), a path, a stack trace or
stored content, with one exception: for `write_refused` the message MUST be the
`WriteRefused` exception's own user-facing `message`, which is documented
user-facing text. An unexpected exception MUST be logged to stderr (without the
token) and MUST yield `internal_error`; the process MUST keep serving. The
confirmation-declined and confirmation-unavailable refusals are unreachable
through this API and, if raised, MUST be reported as `internal_error`.

#### Scenario: A stale decision is a 409

- GIVEN a decision edited after its preview
- WHEN apply is posted with the previewed version
- THEN the response is 409 with `{"code": "stale", "retryable": false,
  "message": <fixed text>}` and the bundle is unchanged

#### Scenario: A stale quote is a 409

- GIVEN a quote and a changed configured model
- WHEN a run is started with the quote's token
- THEN the response is 409 with code `quote_stale` and no model call is made

#### Scenario: A not-offered action is a 422

- GIVEN an action the decision does not offer
- WHEN apply or preview is posted for it
- THEN the response is 422 with code `action_not_offered` and `retryable` false

#### Scenario: Skip is not applicable

- GIVEN a skip action
- WHEN apply is posted for it
- THEN the response is 422 with code `skip_not_applicable`

#### Scenario: A state conflict is a 409

- GIVEN an apply that finds its merge not possible, a member missing, or the
  record not written
- WHEN the responses are read
- THEN each is 409 with code `not_mergeable`, `member_missing` or
  `record_failed` respectively, and only `record_failed` is retryable

#### Scenario: A wrong method is a 405

- GIVEN a valid Host and a valid token
- WHEN `DELETE /v1/status` is requested
- THEN the response is 405 with code `method_not_allowed`

#### Scenario: Drift is retryable

- GIVEN a write refused because the files drifted
- WHEN the response is read
- THEN the status is 409, the code is `write_drift` and `retryable` is true

#### Scenario: A write refusal passes its documented message

- GIVEN a `WriteRefused` carrying a user-facing message
- WHEN the response is read
- THEN the status is 422, the code is `write_refused` and the message equals
  that user-facing message

#### Scenario: An unreachable backend is retryable

- GIVEN a backend that is unreachable
- WHEN a quote or run needs it
- THEN the response is 503 with code `backend_unavailable` and `retryable`
  true

#### Scenario: A missing model is not retryable

- GIVEN a configured model that the backend does not have
- WHEN a quote or run needs it
- THEN the response is 424 with code `backend_model_not_found` and `retryable`
  false

#### Scenario: An unexpected exception is generic

- GIVEN an operation that raises an exception whose text contains a file path
  and a secret-looking string
- WHEN the response is read
- THEN the status is 500, the code is `internal_error`, the body contains
  neither the path nor the string, and the server still serves the next request

#### Scenario: A capped generation is not a generic backend error

- GIVEN a run whose generation was capped
- WHEN the response is read
- THEN the status is 502, the code is `generation_capped` and `retryable` is false

#### Scenario: Subclass-first mapping

- GIVEN a `DriftDetected`, which is also a `WriteRefused` and an
  `OpenKosError`
- WHEN it is mapped
- THEN it yields `write_drift`, not `write_refused`

#### Scenario: Security rejections share the body

- GIVEN a request rejected for a missing token
- WHEN the body is parsed
- THEN it has exactly the keys `code`, `retryable` and `message`

#### Scenario: Validation errors share the body

- GIVEN a request body of the wrong type
- WHEN the response is read
- THEN it is 422 with exactly the keys `code`, `retryable` and `message`, and
  the code is `invalid_request`

### Requirement: Writes Wait At Most Five Seconds For The Workspace Lock

Apply and queue refresh, the operations that take the workspace lock for a
request, MUST accept an optional `wait_seconds` in their request body (the
refresh body MAY be omitted); the per-chunk persists of a judging run use the
default wait. The default MUST be 5 and the ceiling MUST be 5: a request
that asks for a wait above 5, or below 0, MUST be rejected with 422
(`invalid_request`) before the operation runs, so a client cannot hold a
server worker for longer. When the lock stays held beyond the wait, the
response MUST be 503 with code `workspace_busy`, `retryable` true and a
`Retry-After` header holding a positive integer number of seconds, and nothing
MUST have been written. Reads MUST NOT take the lock and MUST succeed while
another process holds it.

#### Scenario: A busy workspace answers 503 with Retry-After

- GIVEN the workspace lock held by another process
- WHEN apply is posted with a wait of 0
- THEN the response is 503, code `workspace_busy`, `retryable` true, and a
  `Retry-After` header with a positive integer
- AND nothing was written

#### Scenario: The default wait is five seconds

- GIVEN the workspace lock held by another process for longer than five seconds
- WHEN a write is posted with no wait
- THEN the response is 503 after waiting about five seconds, and not before

#### Scenario: A longer wait is refused

- GIVEN a write request asking for a wait of 3600
- WHEN it is sent
- THEN the response is 422 with code `invalid_request` and the operation did
  not run

#### Scenario: Reads ignore the lock

- GIVEN the workspace lock held by another process
- WHEN status, pending and decisions are requested
- THEN all succeed

#### Scenario: A lock released within the wait lets the write through

- GIVEN a lock held by another process and released after one second
- WHEN a write is posted with the default wait
- THEN it succeeds

### Requirement: Judging Runs As A Single Pollable Job

Starting a judging run (`POST /v1/judging/runs` with the echoed quote token)
MUST NOT block for the run: it MUST return 202 with the job once the run is
accepted, and the run MUST proceed independently of the request. At most one
run MAY be active in the server process: a start while one is active MUST
return 409 with code `run_active` and start nothing. The job resource MUST be
`{run_id, kind, state, index, total, persisted, outcome, error}`, where `state`
is `running`, `stopping`, `finished` or `failed`; `index` and `total` are the
latest progress the run's observer reported (null before the first event);
`persisted` is the cumulative number of chunks persisted; `outcome` is the
judging outcome when the run returned one (state `finished`, whatever the
outcome's status); and `error` is an error body `{code, retryable, message}`
when the run raised, in which case the state is `failed` and there is no
outcome. A run that ends because of a backend failure is a completed run: the
failure is carried in its `outcome` as a failure object holding a stable code
from the error table and its fixed message, never the exception's text.
`GET /v1/judging/runs/{id}` MUST return the job with status 200 for any known
run, whatever the run's own result. `POST /v1/judging/runs/{id}/stop` MUST set
the run's cooperative stop and return the job with `state` `stopping`; it takes
effect between chunks only, and the run then finishes reporting that it was
stopped. Stopping a finished or failed run MUST NOT be an error: it returns the
job unchanged. An unknown run id MUST return 404. The outcome MUST be
self-describing: the judging outcome's status and counts as the Python API
reports them. A `QuoteStale` or `RunRefused` result MUST NOT start a job: it
MUST be returned at start as the 409 or 422 of the error table. A run that
cannot persist a chunk because the workspace stays busy MUST report that
through its outcome status and MUST NOT fail the poll. A model call MUST NOT
block other requests: while a run is active, reads and the other writes MUST
still be served. The job registry is in-memory: after a restart a previous run
id is unknown (404), and chunks the run persisted remain persisted. The newest
16 finished or failed runs MUST stay retrievable until the process exits; an
older run is evicted and unknown (404).

#### Scenario: Start returns at once

- GIVEN a valid quote token and a model call that takes ten seconds
- WHEN the run is started
- THEN the response is 202 with a run id before the model call completes

#### Scenario: Poll to completion

- GIVEN an accepted run
- WHEN the job is polled until its state is `finished`
- THEN the progress reached the total and the outcome is present

#### Scenario: A second start while active is refused

- GIVEN an active run
- WHEN another start is posted
- THEN the response is 409 with code `run_active` and the first run is
  unaffected

#### Scenario: A new run can start after the first finished

- GIVEN a finished run
- WHEN another start is posted with a valid token
- THEN the response is 202 with a different run id

#### Scenario: Stop takes effect between chunks

- GIVEN an active run in the middle of a chunk
- WHEN stop is posted
- THEN the response returns the job with `state` `stopping`, and after the
  chunk completes the job is `finished` with an outcome that reports it was
  stopped and no further chunk started

#### Scenario: Stopping a finished run is not an error

- GIVEN a finished run
- WHEN stop is posted for it
- THEN the response is 200 with the finished job

#### Scenario: An unknown run id

- GIVEN no run with the id
- WHEN it is polled or stopped
- THEN the response is 404 with code `not_found`

#### Scenario: A backend failure is a failure object

- GIVEN a run that ends because the backend became unreachable
- WHEN the job is polled
- THEN the outcome carries a failure object with code `backend_unavailable` and
  the fixed message, and no exception text

#### Scenario: A run that raises ends failed

- GIVEN an admitted run whose operation raises
- WHEN the job is polled
- THEN the poll is 200 with `state` `failed`, an `error` body with a code from
  the error table and no outcome

#### Scenario: Old finished runs are evicted

- GIVEN more than 16 finished runs
- WHEN the oldest run is polled
- THEN the response is 404 with code `not_found`, and the newest 16 are served

#### Scenario: The server stays responsive during a run

- GIVEN an active run blocked in a model call
- WHEN status and decisions are requested
- THEN both are served promptly

#### Scenario: A stale quote starts no job

- GIVEN a quote token whose binding moved
- WHEN a start is posted
- THEN the response is 409 with code `quote_stale` and no job exists

#### Scenario: Quote costs no model call

- GIVEN a backend whose chat fails the test if called
- WHEN `POST /v1/judging/quote` is requested
- THEN the response is 200 with a quote and its token

### Requirement: Responses Are Owner-Local And Apply No Disclosure Gate

Every response of this stage MUST return the full content the corresponding
`openkos.api` operation returns, including text derived from confidential
sources (for example decision rationales, pending-row subjects, recent
activity and the message of a write refusal), to the holder of the launch
token. This follows the CLI and `openkos.api`: the caller is the workspace
owner, and confidentiality is protected at the transport (loopback bind, the
per-launch token, the Host and Origin checks), not by withholding content. The
sensitivity disclosure gate is not applied in this stage and is deferred to the
first endpoint that answers questions (ask/query), where it MUST be applied
before that endpoint ships. The adapter MUST NOT import `openkos.mcp.gate`. The
package documentation and `docs/` description of the local API MUST state this
scope.

#### Scenario: Confidential-derived text is returned to the token holder

- GIVEN an open decision whose rationale derives from a confidential source
- WHEN `GET /v1/decisions` is requested with a valid token
- THEN the rationale is present in the response unredacted

#### Scenario: The same text is withheld from a caller without the token

- GIVEN the same decision
- WHEN the request carries no token or a wrong one
- THEN the response is 401 or 403 and no content is returned

#### Scenario: The adapter does not depend on the gate

- GIVEN the import guard
- WHEN it inspects `openkos.localapi`
- THEN no module imports `openkos.mcp.gate`

#### Scenario: The scope is stated

- GIVEN the `openkos.localapi` package documentation
- WHEN it is read
- THEN it states that responses are owner-local and that the disclosure gate is
  deferred to the first ask/query endpoint

### Requirement: The Framework Is Confined To The Adapter Package And The Adapter Imports Only The API

None of `fastapi`, `uvicorn`, `starlette`, `pydantic` and `anyio` MUST be
imported by any module outside `openkos.localapi`; a test MUST fail, naming the
module and the import, if a module under `src/openkos/` outside it imports one
of them. Inside `openkos.localapi`, the only third-party imports MAY be
`fastapi`, `uvicorn` and `pydantic`: a direct import of `starlette` or `anyio`
MUST fail the guard. Modules under `openkos.localapi` MAY import the standard
library, those three packages and `openkos.localapi.*`; the only `openkos`
package they MAY import besides themselves is `openkos.api`, imported exactly
as `openkos.api` and never as `openkos.api._*`, and they MUST reach the engine
only through names `openkos.api` exports, including its leaf types and neutral
errors. A test MUST fail if a module under `openkos.localapi` imports any other
`openkos` module, in particular `openkos.application`, `openkos.cli`,
`openkos.mcp`, `openkos.state` or `openkos.llm`. The adapter
MUST NOT make the core asynchronous: it MUST call the synchronous v1 functions
and keep the long-running judging call off the request-serving workers. The
dependency declarations MUST make the framework and server an optional extra
named `api` and leave the core dependency list free of them.

#### Scenario: A foreign module importing the framework is rejected

- GIVEN a module outside `openkos.localapi` that imports `fastapi`
- WHEN the confinement guard runs
- THEN it fails and names the module and the import

#### Scenario: The adapter importing an application service is rejected

- GIVEN a module under `openkos.localapi` that imports an `openkos.application`
  module
- WHEN the import guard runs
- THEN it fails and names the module and the import

#### Scenario: The adapter importing a private API module is rejected

- GIVEN a module under `openkos.localapi` that imports `openkos.api._reads`
- WHEN the import guard runs
- THEN it fails and names the module and the import

#### Scenario: The adapter importing the framework internals is rejected

- GIVEN a module under `openkos.localapi` that imports `starlette` or `anyio`
- WHEN the import guard runs
- THEN it fails and names the module and the import

#### Scenario: The adapter importing the API passes

- GIVEN a module under `openkos.localapi` that imports `openkos.api`
- WHEN the import guard runs
- THEN it passes

#### Scenario: The extra is optional

- GIVEN the project's dependency declarations
- WHEN the core dependency list and the `api` extra are read
- THEN `fastapi` and `uvicorn` appear in the extra and not in the core list

### Requirement: A Committed OpenAPI Snapshot Pins The HTTP Contract

A committed `tests/unit/localapi/openapi.json` MUST record the application's generated OpenAPI
document. A test MUST compare the live document with the snapshot and MUST fail
on any difference. The snapshot MUST be regenerated only by a deliberate,
documented step, as the Python surface snapshot is. The document MUST describe
every operation of this specification under its path and method, the request
and response bodies, the uniform error body, and the security scheme, and MUST
be generated without starting the server or reading a workspace. Generating a
client from the document with a code generator is verified once, measured and
recorded in the change's design and verify artifacts; running that generator in
CI is out of scope for this stage.

#### Scenario: An untouched contract matches

- GIVEN no route, model or response change
- WHEN the snapshot test runs
- THEN it passes

#### Scenario: A changed response fails

- GIVEN a field added to a response model
- WHEN the snapshot test runs
- THEN it fails until the snapshot is regenerated and committed

#### Scenario: The document is generated without a workspace

- GIVEN no workspace on disk
- WHEN the application's OpenAPI document is generated
- THEN it is produced without error and without a socket
