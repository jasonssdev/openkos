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
| `POST /v1/ask` | `ask`, as a job (see the ask job requirement) |
| `GET /v1/ask/{id}` | the ask job's state |
| `POST /v1/ingest/quote` | `quote_ingest` |
| `POST /v1/decisions/{key}/ingest-quote` | `quote_refused_file` |
| `POST /v1/ingest/runs` | `run_ingest`, as a job (see the ingest job requirement) |
| `GET /v1/ingest/runs/{id}` | the ingest job's state |
| `POST /v1/ingest/runs/{id}/stop` | the ingest job's cooperative stop |

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

(Previously: no route existed for ingest, and the route-table scenario said so.)

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

(Previously: its first polling scenario was named "Poll to completion", the
name of the judging job's scenario too.)

## ADDED Requirements

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
`{run_id, state, index, total, phase, calls, outcome, error}`, where `state` is
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
