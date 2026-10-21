# Delta for local-http-api

## ADDED Requirements

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
