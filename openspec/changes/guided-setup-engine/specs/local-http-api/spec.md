# Delta for local-http-api

## MODIFIED Requirements

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

The health route is authenticated like every other route. Every successful
response MUST be JSON. A request body that is not valid for its operation (an
unknown action, a kind outside the allowed values, a malformed echoed token,
a wrong type) MUST be rejected with 422 and the standard error body, before any
operation runs. The server MUST NOT add side effects: a `GET` MUST NOT write,
take the workspace lock or call a model, as the corresponding Python reads do
not. A Concept ID contains `/`, so the unmerge, history, page, reopen and
unrelate routes carry it in a query parameter or the body, never in a path
segment.
The setup readiness `GET` talks to the configured model runtime to read its
version and installed models; that is not a model call, and it writes nothing.
(Previously: the table had no setup routes.)

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
| `QuoteStale` outcome | 409 | `quote_stale` | false |
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
| `PullRefused("not_configured")` | 422 | `model_not_configured` | false |
| `PullRefused("backend_cannot_pull")` | 422 | `pull_not_supported` | false |
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
through this API and, if raised, MUST be reported as `internal_error`. The
`merge_group_interrupted` body carries no completed steps: a client learns the
group's state by listing the decisions again; the `unmerge_interrupted` body
carries no completed steps either: a client reads the survivor's merge history
again.
(Previously: the table had no pull rows.)

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

## ADDED Requirements

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

#### Scenario: Poll to completion

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
