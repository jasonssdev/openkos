# Delta for local-http-api

## ADDED Requirements

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

#### Scenario: An altered version is stale

- GIVEN an unreconcile preview whose `content_digest` is changed by the client
- WHEN the apply route is called with it
- THEN the response is 409 `stale` and nothing is written
