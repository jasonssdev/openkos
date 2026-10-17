# Delta for local-http-api

## RENAMED Requirements

- FROM: `### Requirement: Responses Are Owner-Local And Apply No Disclosure Gate`
- TO: `### Requirement: Responses Are Owner-Local And An Answer Is Gated Before It Leaves`

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
| `POST /v1/ask` | `ask`, as a job (see the ask job requirement) |
| `GET /v1/ask/{id}` | the ask job's state |

The health route is authenticated like every other route. Every successful
response MUST be JSON. A request body that is not valid for its operation (an
unknown action, a kind outside the allowed values, a malformed echoed token,
a wrong type) MUST be rejected with 422 and the standard error body, before any
operation runs. The server MUST NOT add side effects: a `GET` MUST NOT write,
take the workspace lock or call a model, as the corresponding Python reads do
not. A Concept ID contains `/`, so the unmerge, history, page, reopen and
unrelate routes carry it in a query parameter or the body, never in a path
segment.

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
- THEN each maps to one, and none exists for ingest, doctor or setup

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

(Previously: the table had no ask rows and the route-table scenario barred a
query route.)

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

(Previously: the gate was not applied and was deferred to the first ask or
query endpoint.)

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

## ADDED Requirements

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
