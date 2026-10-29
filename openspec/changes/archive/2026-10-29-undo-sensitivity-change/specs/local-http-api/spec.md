# Local HTTP API Specification (delta)

## ADDED Requirements

### Requirement: A Whole Sensitivity Change Is Undone Under `/v1/pages/sensitivity/undo`

The local API MUST serve `POST /v1/pages/sensitivity/undo/preview` (body
`concept_id`) answering `SensitivityUndoPreview`, and `POST
/v1/pages/sensitivity/undo/apply` (body: the echoed `version`, `wait_seconds`
capped at the write wait) answering `{undone, warnings}`. A missing page MUST
answer 404 `page_not_found`; a change that did not record the pages it raised,
409 `sensitivity_no_record`; a page changed since, 409
`sensitivity_changed_since` with a message saying so; a stale version, 409
`stale`; a busy workspace, 503 `workspace_busy` with `Retry-After`.

#### Scenario: A round trip puts the change back

- GIVEN a Source raised to `confidential` with a page raised with it
- WHEN a client previews the undo and applies the returned version
- THEN the apply answers 200 with the page in `undone.restored` and both read
  their previous levels

#### Scenario: A page changed since answers in plain words

- GIVEN a raised page edited after the change
- WHEN a client previews the undo
- THEN the response is 409 `sensitivity_changed_since`
