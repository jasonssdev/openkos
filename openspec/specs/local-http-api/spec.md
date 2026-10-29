# Local HTTP API Specification

## Purpose

The loopback HTTP transport through which a local client, first the desktop
shell, reaches the engine. It exposes exactly the operations of the stable
Python API (`openkos.api`, ADR-0060) and nothing else: workspace status, the
pending report, the open decisions with preview and apply, judging quote and
run, the model-free queue refresh, guided setup's readiness read and model
pull, asking a question, and adding source files. It is a thin adapter: it holds one workspace
handle opened at launch, calls the v1 functions, and owns only transport,
serialization, error mapping and the in-memory job registries (judging, the
model pull, the ask and the ingest run). It adds
no engine capability, no stored state and no new operation.

The security shape follows ADR-0058: the server binds `127.0.0.1` on an
ephemeral port, requires a per-launch bearer token delivered over stdin, and
checks Host and Origin on every route. The transport is the FastAPI and uvicorn
adapter recorded in ADR-0061.

Responses are owner-local: they return stored content, including
confidential-derived text, to the token holder, as the CLI and `openkos.api`
do. The one exception is an ask job's answer, which `openkos.api.ask` gates
with the query disclosure rule before it reaches the adapter (ADR-0068).

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
| `GET /v1/merges?concept_id={id}` | `merge_history` |
| `GET /v1/pages?concept_id={id}` | `read_page` |
| `POST /v1/unmerge/preview` | `preview_unmerge` |
| `POST /v1/unmerge/apply` | `apply_unmerge` |
| `GET /v1/merged` | `merged_concepts` |
| `GET /v1/kept-distinct` | `kept_distinct` |
| `POST /v1/reopen/preview` | `preview_reopen` |
| `POST /v1/reopen/apply` | `apply_reopen` |
| `POST /v1/unrelate/preview` | `preview_unrelate` |
| `POST /v1/unrelate/apply` | `apply_unrelate` |
| `POST /v1/tiers/preview` | `preview_tier_change` |
| `POST /v1/tiers/apply` | `apply_tier_change` |
| `POST /v1/judging/quote` | `quote_judging` |
| `POST /v1/judging/runs` | `run_judging`, as a job (see the job requirement) |
| `GET /v1/judging/runs/{id}` | the job's state |
| `POST /v1/judging/runs/{id}/stop` | the job's cooperative stop |
| `GET /v1/setup/readiness` | `setup_readiness` |
| `POST /v1/setup/pulls` | `pull_model`, as a job (see the pull job requirement) |
| `GET /v1/setup/pulls/{id}` | the pull job's state |
| `POST /v1/setup/pulls/{id}/stop` | the pull job's cooperative stop |
| `POST /v1/ask` | `ask`, as a job (see the ask job requirement) |
| `GET /v1/ask/{id}` | the ask job's state |
| `POST /v1/ingest/quote` | `quote_ingest` |
| `POST /v1/decisions/{key}/ingest-quote` | `quote_refused_file` |
| `POST /v1/ingest/runs` | `run_ingest`, as a job (see the ingest job requirement) |
| `GET /v1/ingest/runs/{id}` | the ingest job's state |
| `POST /v1/ingest/runs/{id}/stop` | the ingest job's cooperative stop |
| `GET /v1/settings/model` | `model_settings` |
| `POST /v1/settings/model/preview` | `preview_model_settings` |
| `POST /v1/settings/model/apply` | `apply_model_settings` |
| `POST /v1/pages/sensitivity/preview` | `preview_sensitivity` |
| `POST /v1/pages/sensitivity/apply` | `apply_sensitivity` |
| `GET /v1/search` | `search_index` |
| `POST /v1/search/rebuilds` | `rebuild_search`, as a job (see the rebuild job requirement) |
| `GET /v1/search/rebuilds/{id}` | the rebuild job's state |
| `POST /v1/search/rebuilds/{id}/stop` | the rebuild job's cooperative stop |

The health route is authenticated like every other route. Every successful
response MUST be JSON. A request body that is not valid for its operation (an
unknown action, a kind outside the allowed values, a malformed echoed token,
a wrong type) MUST be rejected with 422 and the standard error body, before any
operation runs. The server MUST NOT add side effects: a `GET` MUST NOT write,
take the workspace lock or call a model, as the corresponding Python reads do
not. A Concept ID contains `/`, so the unmerge, history, page, reopen and
unrelate routes carry it in a query parameter or the body, never in a path
segment.
The ingest quote reads the chosen files and the workspace and builds the
extraction client to read its model and locality; it makes no model call and
writes nothing.
The setup readiness `GET` talks to the configured model runtime to read its
version and installed models; that is not a model call, and it writes nothing.

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
- THEN each maps to one, and none exists for doctor

#### Scenario: Merge history maps to the history read

- GIVEN a survivor that absorbed one concept
- WHEN `GET /v1/merges?concept_id=<survivor>` is requested
- THEN the response is 200 with one record naming the absorbed id, and nothing
  is written

#### Scenario: A page maps to the page read

- GIVEN a page with one source file
- WHEN `GET /v1/pages?concept_id=<id>` is requested
- THEN the response is 200 with the page's title, type, excerpt, sensitivity
  and source, and nothing is written

#### Scenario: Combined pages map to the combined-pages read

- GIVEN a page that absorbed another
- WHEN `GET /v1/merged` is requested
- THEN the response is 200 with one entry naming the page and its merge
  record, and nothing is written

#### Scenario: Kept-separate rulings map to their list

- GIVEN a pair kept separate
- WHEN `GET /v1/kept-distinct` is requested
- THEN the response is 200 with one group naming both members, and nothing is
  written

#### Scenario: Readiness maps to the readiness read

- GIVEN an initialised workspace and a valid token
- WHEN `GET /v1/setup/readiness` is requested
- THEN the response is 200 with the fields of the API's setup readiness and the
  workspace is unchanged

### Requirement: Responses Are The Python API's Result Types, Populated And Round-Tripping

The success response of each operation MUST carry the fields of the
corresponding `openkos.api` result type under the same field names, with
nothing omitted and nothing renamed; a field the HTTP layer cannot serve
natively (for example a mapping, a fixed-size tuple, a path or an exception
instance) MUST be represented by a documented JSON form that preserves its
content. Where a result is one of several variants (the preview's plan, the
decision's detail, the apply outcome, the run result), the response MUST let a
client distinguish the variants from the body. A preview's `plan` carries a
literal `kind` discriminator (`merge`, `relate`, `set_tier` or `keep_distinct`,
the action it plans; `relate_reversed` plans a `relate`, and `merge_group` plans a `merge` whose `steps` list every pairwise merge), and the OpenAPI
document declares `plan` through a `PreviewPlan` component that is a `oneOf`
with that discriminator, so a generated typed client narrows it with
`switch (preview.plan.kind)`. Every response type MUST be exercised with a populated value (not
only an empty list) in a test that serializes it over HTTP and parses it back
without loss.
(Previously: no action planned a merge of more than one pair, and `relate_reversed` was the only action whose plan kind differed from its name.)

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
- THEN each plan carries a `kind` that matches its action (`relate_reversed`
  yields `relate`, `merge_group` yields `merge`), and the plans can be told
  apart by `kind` alone

#### Scenario: A merge-group plan round-trips populated

- GIVEN a merge-group preview over a cross-type group of three with one
  unmarked member
- WHEN the response is parsed
- THEN `steps`, `kept_distinct_ids`, `survivor_type` and `cross_type_concern`
  equal the Python preview's values

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

### Requirement: Every Request Body Is Capped, After The Token Check

The server MUST refuse a request whose body exceeds 1 MiB (1,048,576 bytes) with
413 and code `request_too_large`, before any route runs. A declared
`Content-Length` above the cap MUST be refused without reading the body; a body
with no `Content-Length`, or one that exceeds its declared length, MUST be
counted as it streams and the read aborted once it crosses the cap. A body of
exactly the cap MUST be accepted. The check MUST run after the Host, Origin and
token checks, so an unauthenticated request is answered 403 or 401, never 413,
and learns nothing about the cap. The cap is above the largest legitimate body,
a judging run echoing its quote token.

#### Scenario: A declared oversized body is refused unread

- GIVEN a valid Host and token
- WHEN a `POST` declares a `Content-Length` of 1 MiB plus one byte
- THEN the response is 413 with code `request_too_large` and no route ran

#### Scenario: A streamed oversized body is aborted

- GIVEN a valid Host and token
- WHEN a `POST` with no `Content-Length` streams more than 1 MiB
- THEN the response is 413 with code `request_too_large` and the read stops at
  the chunk that crossed the cap

#### Scenario: A body of exactly the cap is accepted

- GIVEN a valid Host and token
- WHEN a `POST` body is exactly 1 MiB
- THEN the request reaches the route

#### Scenario: An unauthenticated oversized request is not told about the cap

- GIVEN a request with no token declaring a `Content-Length` above the cap
- WHEN it is sent
- THEN the response is 401, not 413

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
| Request body above the cap | 413 | `request_too_large` | false |
| Unknown path or run id | 404 | `not_found` | false |
| Known path, method not allowed | 405 | `method_not_allowed` | false |
| Invalid request body or parameter | 422 | `invalid_request` | false |
| Path key differs from `version.decision_key` | 422 | `key_mismatch` | false |
| `Stale` outcome (any reason, `unknown_key` included) | 409 | `stale` | false |
| `QuoteStale` or `IngestQuoteStale` outcome | 409 | `quote_stale` | false |
| `NotApplied("not_offered")` | 422 | `action_not_offered` | false |
| `NotApplied("skipped")` | 422 | `skip_not_applicable` | false |
| `NotApplied("invalid_selection")` | 422 | `invalid_selection` | false |
| `NotApplied("not_mergeable")` | 409 | `not_mergeable` | false |
| `NotApplied("member_missing")` | 409 | `member_missing` | false |
| `NotApplied("record_failed")` | 409 | `record_failed` | true |
| `UnmergeStale` outcome | 409 | `stale` | false |
| `NotUnmergeable("survivor_missing")` | 409 | `survivor_missing` | false |
| `NotUnmergeable("not_merged")` | 409 | `not_merged` | false |
| `NotUnmergeable("survivor_edited")` | 409 | `survivor_edited` | false |
| `UndoStale` outcome | 409 | `stale` | false |
| `NotUndoable("not_kept_distinct")` | 409 | `not_kept_distinct` | false |
| `NotUndoable("not_related")` | 409 | `not_related` | false |
| `NotUndoable("tier_unchanged")` | 409 | `tier_unchanged` | false |
| `NotUndoable("unknown_type")` | 422 | `unknown_type` | false |
| `RunRefused` outcome | 422 | `run_refused` | false |
| A judging run already active | 409 | `run_active` | true |
| A model pull already active | 409 | `pull_active` | true |
| An ingest run already active | 409 | `ingest_active` | true |
| `PullRefused("not_configured")` | 422 | `model_not_configured` | false |
| `PullRefused("backend_cannot_pull")` | 422 | `pull_not_supported` | false |
| `SensitivityStale` outcome | 409 | `stale` | false |
| `SensitivityRefused("not_found")` | 404 | `page_not_found` | false |
| `SensitivityRefused("unchanged")` | 409 | `sensitivity_unchanged` | false |
| A search rebuild already active | 409 | `rebuild_active` | true |
| `RebuildRefused("remote_not_allowed")` | 422 | `remote_not_allowed` | false |
| `DriftDetected` | 409 | `write_drift` | true |
| `MergeGroupInterrupted` | 500 | `merge_group_interrupted` | false |
| `UnmergeInterrupted` | 500 | `unmerge_interrupted` | false |
| `WriteRefused` (not drift, not failed) | 422 | `write_refused` | false |
| `WriteFailed` | 500 | `write_failed` | false |
| `WorkspaceBusyError` | 503 | `workspace_busy` | true |
| `WorkspaceLockUnavailableError` | 500 | `workspace_lock_unavailable` | false |
| Queue unavailable | 503 | `queue_unavailable` | false |
| Queue refresh refused | 503 | `queue_refresh_refused` | false |
| `JudgingRefused` | 503 | `judging_refused` | false |
| `IngestRefused` | 503 | `ingest_refused` | false |
| Workspace not found | 500 | `workspace_not_found` | false |
| Workspace unreadable | 500 | `workspace_unreadable` | false |
| `BackendUnavailable` | 503 | `backend_unavailable` | true |
| `BackendModelNotFound` | 424 | `backend_model_not_found` | false |
| `BackendGenerationCapped` | 502 | `generation_capped` | false |
| Any other `BackendError` (host locality, base) | 502 | `backend_error` | false |
| `SearchUnavailable` | 500 | `search_unavailable` | false |
| Any other exception, including any other `OpenKosError` and `OSError` | 500 | `internal_error` | false |

The `message` MUST be a fixed text chosen by the server for each code and MUST
NOT contain the text of the exception (`str(exc)`), a path, a stack trace or
stored content, with one exception: for `write_refused` the message MUST be the
`WriteRefused` exception's own user-facing `message`, which is documented
user-facing text. An unexpected exception MUST be logged to stderr (without the
token) and MUST yield `internal_error`; the process MUST keep serving. The
confirmation-declined and confirmation-unavailable refusals are unreachable
through this API and, if raised, MUST be reported as `internal_error`. The
`merge_group_interrupted` body carries no completed steps: a client learns the
group's state by listing the decisions again; the `unmerge_interrupted` body
carries no completed steps either: a client reads the survivor's merge history
again.

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

#### Scenario: An invalid group selection is a 422

- GIVEN a merge-group preview request whose survivor is not among the marked
  members
- WHEN it is posted
- THEN the response is 422 with code `invalid_selection` and `retryable` false

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

#### Scenario: An interrupted group is not a generic write failure

- GIVEN a merge-group apply whose second step fails
- WHEN the response is read
- THEN the status is 500, the code is `merge_group_interrupted`, not
  `write_failed`, and `retryable` is false

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

#### Scenario: An unmerge refusal is a 409

- GIVEN an unmerge preview whose survivor was itself absorbed, one naming an id
  never merged into the survivor, and one whose survivor was edited since the
  merge
- WHEN each is posted
- THEN each is 409 with code `survivor_missing`, `not_merged` or
  `survivor_edited` respectively, and none is retryable

#### Scenario: An interrupted unwind is not a generic write failure

- GIVEN an unmerge apply whose second step fails
- WHEN the response is read
- THEN the status is 500, the code is `unmerge_interrupted`, not `write_failed`,
  and `retryable` is false

#### Scenario: An undo refusal is answered by its reason

- GIVEN a reopen preview over a pair never kept separate, an unrelate preview
  for an absent relation, a tier preview for the entry the type already has and
  one for an unknown type
- WHEN each is posted
- THEN they are 409 `not_kept_distinct`, 409 `not_related`, 409
  `tier_unchanged` and 422 `unknown_type`, and none is retryable

#### Scenario: An unconfigured model is a 422

- GIVEN a model tag the workspace does not configure
- WHEN a pull is started for it
- THEN the response is 422 with code `model_not_configured` and no job exists

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

### Requirement: Responses Are Owner-Local And An Answer Is Gated Before It Leaves

Every response other than an ask job MUST return the full content the
corresponding `openkos.api` operation returns, including text derived from
confidential sources (for example decision rationales, pending-row subjects,
recent activity and the message of a write refusal), to the holder of the launch
token. This follows the CLI and `openkos.api`: the caller is the workspace
owner, and confidentiality is protected at the transport (loopback bind, the
per-launch token, the Host and Origin checks), not by withholding content. The
answer of an ask job MUST be the `Answer` `openkos.api.ask` returned, which
applies the query disclosure rule before the answer reaches the adapter
(ADR-0058, ADR-0068); the adapter MUST NOT add to it, and MUST NOT pass
`include_confidential` unless the request body asks for it. The adapter MUST
NOT import `openkos.mcp.gate`. The package documentation MUST state this scope.

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

#### Scenario: An ask answer is the gated one

- GIVEN a question whose prompt held a confidential concept
- WHEN the ask job is started without `include_confidential` and polled to the
  end
- THEN its answer has `outcome` `withheld` and names no confidential concept

#### Scenario: The scope is stated

- GIVEN the `openkos.localapi` package documentation
- WHEN it is read
- THEN it states that responses are owner-local and that an ask answer is
  gated by `openkos.api.ask`

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

### Requirement: The Group Selection Travels In The Preview Body And The Echoed Version

`POST /v1/decisions/{key}/preview` MUST accept an optional `selection` object
holding `survivor_id` and `member_ids` and MUST pass it to `preview_decision`
unchanged; a body without it previews as before. The preview response's
`version` MUST carry the selection as a field of `DecisionVersion` (null for
every action but `merge_group`), and the apply request MUST echo that version
unchanged, so the server keeps no selection between the two calls and the apply
request body gains no field. A selection of the wrong shape MUST be 422
`invalid_request` before any operation runs; a well-formed but invalid
selection is the operation's own `invalid_selection` outcome. The request model
MUST reject an unknown top-level key without splitting the selection's schema
into separate input and output components.

#### Scenario: Preview and apply a group over HTTP

- GIVEN an open identity decision judged SAME over three members
- WHEN a preview is posted with `action` `merge_group` and a selection, and the
  returned version is posted unchanged to apply with `action` `merge_group`
- THEN the decision is applied and the response's `applied.merged` lists both
  pairs

#### Scenario: An altered echoed selection is stale

- GIVEN a merge-group preview response
- WHEN its version is posted to apply with one marked member removed from
  `version.selection.member_ids`
- THEN the response is 409 with code `stale` and nothing is written

#### Scenario: A malformed selection is a validation error

- GIVEN a preview request whose `selection.member_ids` is a string
- WHEN it is posted
- THEN the response is 422 with code `invalid_request` and no operation ran

#### Scenario: One selection component in the contract

- GIVEN the generated OpenAPI document
- WHEN its components are listed
- THEN `GroupSelection` appears once, with no `-Input` or `-Output` variant

### Requirement: The Unmerge Version Travels Unchanged From Preview To Apply

`POST /v1/unmerge/preview` MUST accept `survivor_id`, `absorbed_id` and an
optional `discard_survivor_edits` (default false) and pass them to
`preview_unmerge` unchanged; its response MUST be the `UnmergePreview`, with the
version under `version`. `POST /v1/unmerge/apply` MUST accept that version echoed
unchanged and an optional `wait_seconds` (at most the write ceiling), so the server
keeps no state between the two calls; its success response MUST be
`{"unmerged": <Unmerged>, "warnings": [<string>...]}`. Both request models MUST
reject an unknown top-level key, and `UnmergeVersion` MUST appear once in the
OpenAPI document, with no `-Input` or `-Output` variant.

#### Scenario: Split a merge over HTTP

- GIVEN two concepts merged through a decision
- WHEN an unmerge preview is posted for them and its version is posted unchanged
  to apply
- THEN the response is 200, `unmerged.restored_ids` names the absorbed concept
  and the concept's document exists again

#### Scenario: An altered echoed version is stale

- GIVEN an unmerge preview response
- WHEN its version is posted to apply with `discard_survivor_edits` flipped
- THEN the response is 409 with code `stale` and nothing is written

#### Scenario: An unknown key is a validation error

- GIVEN an unmerge apply request with an extra top-level key
- WHEN it is posted
- THEN the response is 422 with code `invalid_request` and no operation ran

#### Scenario: One unmerge version component in the contract

- GIVEN the generated OpenAPI document
- WHEN its components are listed
- THEN `UnmergeVersion` appears once, with no `-Input` or `-Output` variant

### Requirement: An Unknown Page Answers Not Found

`GET /v1/pages` MUST answer 404 with the standard error body, code `not_found`
and `retryable` false, the same row as an unknown path or run id, when
`openkos.api.read_page` returns `None`: an id that is not a valid Concept ID, a
page that does not exist, or one that cannot be read. The message MUST be the
fixed text of that code and MUST NOT contain the id. A request without
`concept_id` MUST answer 422 `invalid_request`. No new error code is added.

#### Scenario: A missing page is not found

- GIVEN no page `people/nobody`
- WHEN `GET /v1/pages?concept_id=people/nobody` is requested with a valid token
- THEN the response is 404 with code `not_found` and the body does not contain
  `people/nobody`

#### Scenario: A traversal id is not found

- GIVEN an id with a `..` segment
- WHEN it is requested as `concept_id`
- THEN the response is 404 with code `not_found` and no file outside the
  bundle is read

### Requirement: The Undo Versions Travel Unchanged From Preview To Apply

`POST /v1/reopen/apply`, `POST /v1/unrelate/apply` and `POST /v1/tiers/apply`
MUST take the version their preview returned (`ReopenVersion`,
`UnrelateVersion`, `TierChangeVersion`) echoed unchanged in `version`, and an
optional `wait_seconds` capped as every write's is. A version whose digest no
longer matches what its key plans MUST be answered 409 `stale` and write
nothing; a key altered to one with nothing to undo is refused as its preview
would be; an unknown top-level key MUST be 422 `invalid_request`. Each version MUST be one component of the
published schema, the same in the preview's response and the apply's request.
The tier preview's `tier` MUST be one of `static`, `slow`, `volatile` or `null`;
any other value MUST be 422 `invalid_request` before the operation runs.

#### Scenario: A reopen is applied with its echoed version

- GIVEN a reopen preview over a kept-separate pair
- WHEN its version is posted unchanged to `POST /v1/reopen/apply`
- THEN the response is 200 with the reopened members and the warnings list

#### Scenario: An altered unrelate version is stale

- GIVEN an unrelate preview
- WHEN its version is posted with a different `content_digest`
- THEN the response is 409 `stale` and the page is unchanged

#### Scenario: An unknown tier is rejected before the operation

- GIVEN a tier preview request whose `tier` is `"sometimes"`
- WHEN it is posted
- THEN the response is 422 `invalid_request` and nothing ran

### Requirement: A Model Pull Runs As A Single Pollable Job

Starting a pull (`POST /v1/setup/pulls` with `{"model": <tag>}`) MUST NOT block
for the download: it MUST return 202 with the job once the pull is admitted
(the service's first stop check) or has already finished, and a `Location`
header naming the job. At most one pull MAY be active in the server process: a
start while one is active MUST return 409 `pull_active` and start nothing. A
`PullRefused` result, or an exception raised before admission (an unreachable
runtime), MUST answer the start request itself with its row of the error table
and leave no job. The job resource MUST be `{pull_id, model, state, phase,
completed_bytes, total_bytes, outcome, error}`, where `state` is `running`,
`stopping`, `finished` or `failed`; `phase`, `completed_bytes` and
`total_bytes` are the latest progress (null before the first event, bytes 0);
`outcome` is the pull outcome when the pull returned (state `finished`); and
`error` is an error body when the pull raised after admission (state `failed`,
no outcome), with the fixed message of its row, never the exception's text.
`GET /v1/setup/pulls/{id}` MUST return the job with 200 for any known pull.
`POST /v1/setup/pulls/{id}/stop` MUST set the cooperative stop and return the
job with `state` `stopping`; stopping a finished or failed pull MUST return it
unchanged. An unknown id MUST return 404. Neither the start nor a poll takes the
workspace lock. The registry is in memory; the newest 16 finished or failed
pulls stay retrievable, and server shutdown sets every stop and waits at most
the same grace as a judging run.

#### Scenario: Start returns before the download finishes

- GIVEN a configured, uninstalled model and a runtime that streams slowly
- WHEN the pull is started
- THEN the response is 202 with `state` `running` before the stream ends

#### Scenario: Poll a pull to completion

- GIVEN a started pull
- WHEN the runtime finishes the stream and the job is polled
- THEN the job is `finished` with outcome status `completed`

#### Scenario: Stop a pull

- GIVEN a running pull
- WHEN stop is posted and the job is polled after the next line
- THEN the job is `finished` with outcome status `stopped`

#### Scenario: A second pull is refused while one is active

- GIVEN a running pull
- WHEN another pull is started
- THEN the response is 409 with code `pull_active` and `retryable` true

#### Scenario: An unreachable runtime answers the start

- GIVEN a runtime that refuses connections
- WHEN a pull is started
- THEN the response is 503 with code `backend_unavailable` and no job exists

### Requirement: A Question Is Asked As A Single Pollable Job

`POST /v1/ask` with `{question, limit, include_deprecated,
include_confidential}` (only `question` required; an unknown key, a `limit`
below 1 or a wrong type is 422 `invalid_request`) MUST NOT block for the
answer: it MUST return 202 with the job and a `Location` header naming the job,
and the ask MUST proceed on its own thread. An omitted `limit` MUST mean the
API's default. At most one ask MAY be active in the server process: a start
while one is active MUST return 409 with code `ask_active`, `retryable` true,
and start nothing. An ask MAY run while a judging run is active. The job
resource MUST be `{ask_id, state, phase, completed, total, answer, error}`,
where `state` is `running`, `finished` or `failed`; `phase`, `completed` and
`total` are the latest progress event (null before the first); `answer` is the
`Answer` when `finished`; and `error` is the error body of the error table when
the ask raised, in which case the state is `failed` and there is no answer.
`GET /v1/ask/{id}` MUST return the job with 200 for any known id, and 404
`not_found` for an unknown one. The newest 16 finished or failed asks MUST stay
retrievable; an older one is evicted and unknown. An ask has no stop. The
registry is in memory: after a restart an ask id is unknown.

#### Scenario: An ask start returns at once

- GIVEN a model call that blocks until the test releases it
- WHEN an ask is started
- THEN the response is 202 with an ask id before the model call completes

#### Scenario: Poll to an answer

- GIVEN a started ask
- WHEN the job is polled until its state is not `running`
- THEN the state is `finished` and the answer is present

#### Scenario: A second ask while one runs is refused

- GIVEN an active ask
- WHEN another is started
- THEN the response is 409 with code `ask_active` and the first is unaffected

#### Scenario: An ask that raises ends failed

- GIVEN a chat backend that cannot be reached
- WHEN the ask is polled to the end
- THEN the poll is 200 with `state` `failed`, an `error` body with code
  `backend_unavailable` and the fixed message, and no answer

#### Scenario: An unknown ask id

- GIVEN no ask with the id
- WHEN it is polled
- THEN the response is 404 with code `not_found`

#### Scenario: An invalid limit is rejected before anything starts

- GIVEN a body with `limit` 0
- WHEN it is posted
- THEN the response is 422 `invalid_request` and no job exists

### Requirement: A Finding Preview Is Served Beside The Decision Preview

The server MUST serve `preview_finding` as `POST /v1/decisions/{key}/preview-finding`,
with a body naming the `action` and nothing else, answering 200 with the
`FindingPreview`, whose `plan` is declared as a union discriminated on `kind`
(`"reconcile"` or `"decline"`). The version it returns MUST be applied through
`POST /v1/decisions/{key}/apply`, unchanged. The route MUST NOT write, take the
lock or call a model. Its outcomes MUST map to the rows the decision preview
uses, plus two: `NotApplied` `wrong_preview` is 422 with the code
`wrong_preview` (also returned by the decision preview given a finding action),
and `NotApplied` `already_reconciled` is 409 with the code `already_reconciled`
(also returned by the apply). The `DecisionPreview` component and its plan union
MUST be unchanged.

#### Scenario: A contradiction is previewed and applied over HTTP

- GIVEN an open typed-edge contradiction
- WHEN `POST /v1/decisions/{key}/preview-finding` is sent with `supersede`, and
  its version is sent to `POST /v1/decisions/{key}/apply` with `supersede`
- THEN the first answers 200 with a plan of `kind` `"reconcile"` and
  `relation_type` `"supersedes"`, and the second 200 with the applied result

#### Scenario: The decision preview refuses a finding action

- GIVEN an open contradiction
- WHEN `POST /v1/decisions/{key}/preview` is sent with `decline`
- THEN the response is 422 with the code `wrong_preview` and nothing is written

#### Scenario: A pair reconciled differently is a conflict

- GIVEN an open contradiction whose pair was reconciled another way since it
  was found
- WHEN its finding preview is requested with reconcile
- THEN the response is 409 with the code `already_reconciled`

### Requirement: The Model Settings Are Served Under `/v1/settings/model`

The server MUST add these operations to the ones it serves under `/v1`, each
calling the matching `openkos.api` function with the launch's workspace:

| Method and path | `openkos.api` operation |
|---|---|
| `GET /v1/settings/model` | `model_settings` |
| `POST /v1/settings/model/preview` | `preview_model_settings` |
| `POST /v1/settings/model/apply` | `apply_model_settings` |

The preview body MUST be a `ModelSettingsChange` with unknown fields rejected.
The apply body MUST carry the preview's `version` unchanged and an optional
`wait_seconds`, capped as for every write. `ModelSettingsRefused` MUST answer
422 with the reason as its code (`invalid_base_url`,
`invalid_embedding_base_url`, `invalid_model`, `invalid_embedding_model`,
`base_url_required`), except `unchanged`, which MUST answer 409
`settings_unchanged`; `ModelSettingsStale` MUST answer 409 `stale`. No body,
response or error message MUST carry an API key.

#### Scenario: The settings read writes nothing

- GIVEN an initialised workspace and a valid token
- WHEN `GET /v1/settings/model` is requested
- THEN the response is 200 with the fields of `ModelSettings` and the workspace
  is unchanged

#### Scenario: A preview and its version apply the change

- GIVEN a preview answered by `POST /v1/settings/model/preview`
- WHEN its `version` is sent unchanged to `POST /v1/settings/model/apply`
- THEN the response is 200 with the changed settings and `openkos.yaml` holds
  them

#### Scenario: An invalid URL is a typed refusal

- GIVEN a preview body whose `base_url` is not an http(s) URL
- WHEN it is sent
- THEN the response is 422 with code `invalid_base_url`

### Requirement: Adding Files Runs As A Single Pollable Job

`POST /v1/ingest/quote` with `{paths, include_confidential}` (`paths` a list of
1 to 500 strings of at most 4096 characters; an unknown key, an empty list or a
wrong type is 422 `invalid_request` before the operation runs) MUST return 200
with the `IngestQuote` `openkos.api.quote_ingest` returned, token included.
`POST /v1/decisions/{key}/ingest-quote` with `{include_confidential}` MUST
return 200 with the quote `openkos.api.quote_refused_file` returned for that
refused-file decision, and 409 `stale` for a `Stale` result; a key of another
kind is 422 `invalid_request`. The adapter MUST NOT read, stat or resolve a path
itself: every path check is the API's, reported per file in the quote.

Starting a run (`POST /v1/ingest/runs` with the echoed token) MUST NOT block for
the run: it MUST return 202 with the job and a `Location` header naming it once
the run is admitted (the service's first stop check, after its stale and
refusal checks and before any model call) or has already finished. An
`IngestQuoteStale` result MUST answer the start with 409 `quote_stale`, its
message naming the moved fields; a missing or null token MUST answer 422
`run_refused`; neither leaves a job. At most one ingest run MAY be active in
the server process: a start while one is active MUST return 409 with code
`ingest_active` and `retryable` true and start nothing. The job resource MUST be
`{run_id, state, index, total, phase, calls, outcome, warnings, error}`, where
`state` is
`running`, `stopping`, `finished` or `failed`; `index`, `total` and `phase` are
the latest progress (null before the first event); `calls` is the chat calls
spent so far; `outcome` is the run's outcome when it returned (state
`finished`, whatever its status, `busy` and `stopped` included); and `error` is
an error body when the run raised after admission (state `failed`, no outcome),
with the fixed message of its row, never the exception's text.
`GET /v1/ingest/runs/{id}` MUST return the job with 200 for any known run.
`POST /v1/ingest/runs/{id}/stop` MUST set the cooperative stop and return the
job with `state` `stopping`; it takes effect before the next file. Stopping a
finished or failed run MUST return it unchanged. An unknown id MUST return 404.
`IngestRefused` MUST map to 503 `ingest_refused`, not retryable. A workspace
that stays busy MUST be reported by the outcome's status, never by a failed
poll. The registry is in memory; the newest 16 finished or failed runs stay
retrievable, and server shutdown sets every stop and waits at most the same
grace as a judging run.

#### Scenario: An ingest start returns at once

- GIVEN a quote's token and a model call that blocks
- WHEN the run is started
- THEN the response is 202 with `state` `running` and a `Location` header
  before the model call returns

#### Scenario: Poll an ingest to completion

- GIVEN a started run of one file
- WHEN the job is polled until it is `finished`
- THEN the outcome lists the file as `added` with its Source id

#### Scenario: A second ingest while one runs is refused

- GIVEN an active ingest run
- WHEN another is started
- THEN the response is 409 with code `ingest_active`, `retryable` true, and the
  first run is unaffected

#### Scenario: A stale ingest token answers the start

- GIVEN a quote and the file edited afterwards
- WHEN the run is started with its token
- THEN the response is 409 `quote_stale`, no job exists and no model call was
  made

#### Scenario: A quote is not read by the adapter

- GIVEN a quote request naming a path inside the workspace's `.openkos/`
- WHEN it is posted
- THEN the response is 200 and the file is `refused` with `inside_workspace`

#### Scenario: A refused file is quoted through its decision

- GIVEN an open `watch_refusal` decision whose file is in the inbox
- WHEN `POST /v1/decisions/{key}/ingest-quote` is posted
- THEN the response is 200 with a quote naming that file, and once its token
  has run the same request is 409 `stale`

#### Scenario: An empty path list is rejected before the operation

- GIVEN a quote request with `paths` `[]`
- WHEN it is posted
- THEN the response is 422 `invalid_request` and nothing ran

#### Scenario: A busy workspace is an outcome

- GIVEN the workspace lock held by another process
- WHEN a run is started and polled
- THEN the job is `finished` with outcome status `busy`

### Requirement: A Page's Sensitivity Is Served Under `/v1/pages/sensitivity`

The local API MUST serve `POST /v1/pages/sensitivity/preview` (body
`concept_id`, `level`) answering `SensitivityPreview`, and `POST
/v1/pages/sensitivity/apply` (body: the echoed `version`, `wait_seconds` capped
at the write wait) answering `{changed, warnings}`. A missing page MUST answer
404 `page_not_found`; a level already set, 409 `sensitivity_unchanged`; a stale
version, 409 `stale`; a busy workspace, 503 `workspace_busy` with
`Retry-After`; a level outside the three, 422 `invalid_request`.

#### Scenario: A round trip sets the level

- WHEN a client previews `concepts/garden` at `confidential` and applies the
  returned version
- THEN the apply answers 200 with `changed.level` `confidential` and the page
  reads `confidential`

### Requirement: Search Rebuilds Are A Pollable Job Under `/v1/search`

The local API MUST serve `GET /v1/search` answering `SearchIndex`; `POST
/v1/search/rebuilds` (body `allow_remote`, default false) answering 202 with a
`RebuildStatus` and a `Location`; `GET /v1/search/rebuilds/{rebuild_id}`; and
`POST /v1/search/rebuilds/{rebuild_id}/stop`. At most one rebuild MUST be
active: another start answers 409 `rebuild_active`. The rebuild MUST run on its
own daemon thread; the start MUST wait for admission (after the consent check,
before any page is sent), so a refusal (`remote_not_allowed`, 422) answers the
start itself, and a failure after admission (an unreachable AI model, a busy
workspace, an unusable store) is reported on the poll as `failed` with its
`error` row. `RebuildStatus` MUST carry
`rebuild_id`, `state` (`running`, `stopping`, `stopped`, `finished`,
`failed`), `phase`, `done`, `total`, `outcome` and `error`. An unknown id MUST
answer 404 `not_found`. `SearchUnavailable` MUST answer 500
`search_unavailable`. Shutting the server down MUST stop an active rebuild.

#### Scenario: A rebuild is started, polled and finished

- WHEN a client starts a rebuild against a local AI model for search and polls
  it
- THEN the poll reaches `finished` with an `outcome`

#### Scenario: A second rebuild is refused while one runs

- GIVEN a running rebuild
- WHEN another is started
- THEN it answers 409 `rebuild_active`
### Requirement: Undoing A Decline Or A Reconcile Is Served Under `/v1`

The server MUST serve the operations that undo a declined contradiction and a
reconcile, in addition to the operations table, and MUST call the matching
`openkos.api` function with the workspace handle opened at launch:

| Method and path | `openkos.api` operation |
|---|---|
| `GET /v1/declined-contradictions` | `declined_contradictions` |
| `POST /v1/contradictions/reopen/preview` | `preview_reopen_contradiction` |
| `POST /v1/contradictions/reopen/apply` | `apply_reopen_contradiction` |
| `GET /v1/reconciled` | `reconciled_pairs` |
| `POST /v1/unreconcile/preview` | `preview_unreconcile` |
| `POST /v1/unreconcile/apply` | `apply_unreconcile` |

The two `GET` routes MUST NOT write, take the workspace lock or call a model.
Concept IDs travel in the body, never in a path segment. The apply bodies MUST
echo the preview's version unchanged: an unknown field is 422
`invalid_request`, and an altered digest is 409 `stale`. The outcomes MUST map
to the uniform error body as follows:

| Source | Status | `code` | `retryable` |
|---|---|---|---|
| `NotUndoable("not_declined")` | 409 | `not_declined` | false |
| `NotUndoable("not_reconciled")` | 409 | `not_reconciled` | false |

#### Scenario: A declined contradiction is reopened over HTTP

- GIVEN a contradiction declined through the decision routes
- WHEN `GET /v1/declined-contradictions` is requested, then the reopen preview
  and apply routes are called with its pair and the preview's version
- THEN the list names the pair, the apply answers 200 with the reopened pair,
  and a second `GET` lists nothing

#### Scenario: A reconcile is undone over HTTP

- GIVEN a contradiction settled with `reconcile` through the decision routes
- WHEN `GET /v1/reconciled` is requested, then the unreconcile preview and
  apply routes are called with its pair and the preview's version
- THEN the list names the pair with `reconciled_with`, the apply answers 200,
  and a second `GET` lists nothing

#### Scenario: A pair with nothing to undo is a conflict

- GIVEN two pages never declined or reconciled
- WHEN the reopen or the unreconcile preview route is called with them
- THEN the response is 409 with `not_declined` or `not_reconciled`

#### Scenario: An altered unreconcile version is stale

- GIVEN an unreconcile preview whose `content_digest` is changed by the client
- WHEN the apply route is called with it
- THEN the response is 409 `stale` and nothing is written

### Requirement: The Judging Quote Names A Finding's Decision

`POST /v1/judging/quote` MUST accept an optional `decision_key`. For the kinds
`contradiction` and `revision` it MUST be present and MUST begin with the kind
and a colon, and the route MUST serve `quote_finding_recheck`; for every other
kind it MUST be absent and the route MUST serve `quote_judging` unchanged. Any
other combination MUST be rejected with 422 `invalid_request` before a client
is built. The quote's token MUST be started and followed through the existing
judging run routes, whose one-active-run rule and job resource are unchanged.

#### Scenario: A contradiction quote carries its key

- GIVEN a body `{"kind": "contradiction", "decision_key": "contradiction:abc"}`
- WHEN it is posted to `/v1/judging/quote`
- THEN the Python API's re-check quote is served for that decision

#### Scenario: A finding kind without a key is invalid

- GIVEN a body `{"kind": "revision"}`
- WHEN it is posted
- THEN the response is 422 `invalid_request` and no quote is made

### Requirement: The Watch Folder Is Served Under `/v1/settings/inbox`

The server MUST add these operations to the ones it serves under `/v1`, each
calling the matching `openkos.api` function with the launch's workspace:

| Method and path | `openkos.api` operation |
|---|---|
| `GET /v1/settings/inbox` | `inbox_settings` |
| `POST /v1/settings/inbox/preview` | `preview_inbox_settings` |
| `POST /v1/settings/inbox/apply` | `apply_inbox_settings` |

The preview body MUST be `{inbox}` with unknown fields rejected and `inbox`
required: a folder, or `null` to stop watching. The apply body MUST carry the
preview's `version` unchanged and an optional `wait_seconds`, capped as for every
write. `InboxSettingsRefused` MUST answer 422 with a code per reason
(`invalid_inbox`, `inbox_inside_workspace`, `inbox_not_a_directory`), except
`unchanged`, which MUST answer 409 `inbox_unchanged`; `InboxSettingsStale` MUST
answer 409 `stale`. Applying MUST write only the `unattended.inbox` line of
`openkos.yaml`, commit only it, leave the person's other uncommitted edits out of
the commit, and refuse (not overwrite) an uncommitted edit to that line. It MUST
NOT start or stop any process.

#### Scenario: A preview names the folder and writes nothing

- GIVEN an initialised workspace and an existing folder outside it
- WHEN `POST /v1/settings/inbox/preview` is sent with that folder
- THEN the response is 200, `proposed.inbox` is the folder's resolved path and
  the workspace is unchanged

#### Scenario: The engine's own trees are refused with their reason

- GIVEN a preview body naming the workspace's `raw/` folder
- WHEN it is sent
- THEN the response is 422 with code `inbox_inside_workspace`

#### Scenario: Turning the watch off removes the key

- GIVEN a workspace that watches a folder
- WHEN a preview with `inbox: null` is applied
- THEN `openkos.yaml` has no `unattended.inbox` and `inbox_settings` reads `null`

### Requirement: The Latest Unattended Outcome Is Served Under `/v1/unattended`

The server MUST serve `GET /v1/unattended`, calling `unattended_activity` with
the launch's workspace and answering `{last_job, budget_exhausted,
jobs_unreadable}`. It MUST be read-only: it MUST take no lock, make no model call,
write nothing and never create the job record. Because the daemon idles on a
spent budget rather than exiting, this is how a client learns that the watch
paused on the budget.

#### Scenario: A workspace that never ran the daemon has no history

- GIVEN a workspace with no job record
- WHEN `GET /v1/unattended` is sent
- THEN the response is 200 with `last_job` null and `budget_exhausted` false,
  and nothing was written

#### Scenario: A spent budget is served with its job

- GIVEN the newest watch job ended `budget_exhausted` and another writer holds
  the workspace lock
- WHEN `GET /v1/unattended` is sent
- THEN the response is 200 with `budget_exhausted` true and that job as
  `last_job`

### Requirement: The Engine's Activity Is Served Under `/v1/activity`

The server MUST serve `GET /v1/activity`, calling `engine_activity` with the
launch's workspace and answering `{current, recent, waiting_files,
uncommitted_paths, budget, jobs_unreadable}`. It MUST be read-only: it MUST take
no lock, make no model call, write nothing and never create the job record, and
it MUST require the bearer token like every other route.

#### Scenario: No history is an empty activity

- GIVEN a workspace with no job record
- WHEN `GET /v1/activity` is sent
- THEN the response is 200 with `current` null, an empty `recent` and a budget
  of 0 used, and nothing was written

#### Scenario: A finished task is served while another writer holds the lock

- GIVEN a finished watch job and another writer holding the workspace lock
- WHEN `GET /v1/activity` is sent
- THEN the response is 200 and `recent` names that job
