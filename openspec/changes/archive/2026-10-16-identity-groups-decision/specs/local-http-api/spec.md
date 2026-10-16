# Delta for local-http-api

## MODIFIED Requirements

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
document declares `plan` as a `oneOf` with that discriminator, so a generated
typed client narrows it with `switch (preview.plan.kind)`. Every response type MUST be exercised with a populated value (not
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
| `RunRefused` outcome | 422 | `run_refused` | false |
| A judging run already active | 409 | `run_active` | true |
| `DriftDetected` | 409 | `write_drift` | true |
| `MergeGroupInterrupted` | 500 | `merge_group_interrupted` | false |
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
group's state by listing the decisions again.
(Previously: the table had no `invalid_selection` and no `merge_group_interrupted` row.)

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

## ADDED Requirements

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
