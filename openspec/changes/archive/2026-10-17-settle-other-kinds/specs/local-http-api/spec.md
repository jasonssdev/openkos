# Delta for local-http-api

## ADDED Requirements

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
