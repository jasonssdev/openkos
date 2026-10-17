# Delta for local-http-api

## ADDED Requirements

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
own daemon thread; the start MUST wait for admission, so a refusal
(`remote_not_allowed`, 422) or an unreachable runtime (503
`backend_unavailable`) answers the start itself. `RebuildStatus` MUST carry
`rebuild_id`, `state` (`running`, `stopping`, `stopped`, `finished`,
`failed`), `phase`, `done`, `total`, `outcome` and `error`. An unknown id MUST
answer 404 `not_found`.

#### Scenario: A rebuild is started, polled and finished

- WHEN a client starts a rebuild against a local AI model for search and polls
  it
- THEN the poll reaches `finished` with an `outcome`

#### Scenario: A second rebuild is refused while one runs

- GIVEN a running rebuild
- WHEN another is started
- THEN it answers 409 `rebuild_active`
