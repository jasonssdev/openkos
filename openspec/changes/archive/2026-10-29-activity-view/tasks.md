# Tasks: activity-view

Refs #1698. Proposal: `proposal.md`. Specs: `specs/stable-python-api`,
`specs/local-http-api`, `specs/desktop-client`, `specs/desktop-activity`.

Strict TDD is ON, runner `uv run pytest` and `pnpm test`.

## Delivered

- [x] 1. [TEST+IMPL] `read_engine_activity` over the job record. RED observed
  (module member missing) then GREEN.
- [x] 2. [TEST+IMPL] `openkos.api.engine_activity`, the surface snapshot.
- [x] 3. [TEST+IMPL] `GET /v1/activity`, the route lists and the OpenAPI snapshot.
- [x] 4. [TEST+IMPL] Client `engineActivity()` and the regenerated schema
  (`pnpm check:schema`).
- [x] 5. [TEST+IMPL] The Activity screen: wording, model, render, sidebar entry.
