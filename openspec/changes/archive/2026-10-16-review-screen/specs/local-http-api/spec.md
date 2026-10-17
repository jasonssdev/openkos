# Delta for Local HTTP API

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
not. A Concept ID contains `/`, so the unmerge, history and page routes carry it in
a query parameter or the body, never in a path segment.
(Previously: the table had no page-read row.)

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

## ADDED Requirements

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
