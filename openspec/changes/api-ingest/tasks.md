# Tasks: Add Source Files Through `openkos.api` And The Local API

Tracking issue: #1496. Inputs: `proposal.md`, `design.md` (authoritative), the
delta specs under `specs/` and ADR-0073 (Proposed, written with this plan).

PR text names the change in prose ("the `api-ingest` change") and uses
`Refs #1496`; the last slice uses `Closes #1496`.

## Review Workload Forecast

Authored changed lines (additions plus deletions); the generated
`tests/unit/api/api_surface.json`, `tests/unit/localapi/openapi.json` and
`desktop/client/schema.d.ts` are excluded. The ~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan, ADR-0073 Proposed, the extraction bound, `application/ingest_consent.py`, `openkos.api` ingest | ~2000, most of it tests and docs | none |
| S2 | The ingest routes, the job registry, the client methods | ~800, most of it tests | S1 |
| S3 | Wording, CHANGELOG, spec merge, ADR-0073 Accepted, archive | docs only | S2 |

## S1 Plan, engine and API

- [x] 1.1 Proposal, design, delta specs, tasks; ADR-0073 (Proposed) and its row.
- [x] 1.2 RED: the bound's adversarial tests; then `max_extraction_calls`.
- [x] 1.3 RED: `application/ingest_consent.py` tests (quote statuses and
      refusals, the token's staleness, the ceiling, stop, busy, progress,
      refresh, the `watch_refusal` row); then the service and
      `ingest_service.probe_source`.
- [x] 1.4 RED: `openkos.api` tests; then `api/_ingest.py`,
      `_wiring.ingest_ports`, the exports and the package docstring.
- [x] 1.5 Regenerate the surface snapshot; the classifier prints additions only.
- [ ] 1.6 Mutation-check the ceiling, the path guard, the stale-files check and
      the bound.

## S2 Local API and client

- [ ] 2.1 RED: registry and route tests for "Adding Files Runs As A Single
      Pollable Job" and the route table.
- [ ] 2.2 `localapi/_ingests.py`, the models, the four routes, the
      `ingest_active` and `ingest_refused` rows, the package docstring scope.
- [ ] 2.3 Regenerate the OpenAPI snapshot and `schema.d.ts`; the four client
      methods, the error codes and their tests.

## S3 Archive

- [ ] 3.1 `docs/decision-wording.md`: adding files, the consent line, progress,
      results, refusals. CHANGELOG.
- [ ] 3.2 Merge the deltas, each heading exactly once; the Purpose lines; the
      pull's "Poll to completion" renamed.
- [ ] 3.3 ADR-0073 Accepted in all three places; move to `archive/`;
      `archive-report.md`.
