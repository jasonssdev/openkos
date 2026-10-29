# Local HTTP API Specification (delta)

## ADDED Requirements

### Requirement: The Engine's Activity Is Served Under `/v1/activity`

The server MUST serve `GET /v1/activity`, calling `engine_activity` with the
launch's workspace and answering `{current, recent, waiting_files,
uncommitted_paths, budget, jobs_unreadable}`. It MUST be read-only: it MUST take
no lock, make no model call, write nothing and never create the job record, and
it MUST require the bearer token like every other route.

#### Scenario: No history is an empty activity

- GIVEN a workspace with no job record
- WHEN `GET /v1/activity` is sent
- THEN the response is 200 with `current` null, an empty `recent` and a budget
  of 0 used, and nothing was written

#### Scenario: A finished task is served while another writer holds the lock

- GIVEN a finished watch job and another writer holding the workspace lock
- WHEN `GET /v1/activity` is sent
- THEN the response is 200 and `recent` names that job
