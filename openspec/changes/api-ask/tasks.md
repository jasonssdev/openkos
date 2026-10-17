# Tasks: Ask Through `openkos.api` And The Local API

Tracking issue: #1483. Inputs: `proposal.md`, `design.md` (authoritative), the
delta specs under `specs/` and ADR-0068 (Proposed, written with this plan).

PR text names the change in prose ("the `api-ask` change") and uses `Refs #1483`;
the last slice uses `Closes #1483`.

## Review Workload Forecast

Authored changed lines (additions plus deletions); the generated
`tests/unit/api/api_surface.json`, `tests/unit/localapi/openapi.json` and
`desktop/client/schema.d.ts` are excluded. The ~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks, ADR-0068 Proposed | ~700 (docs) | none |
| S2 | `openkos.api.ask` and its types | ~600, most of it tests | S1 |
| S3 | `POST /v1/ask`, `GET /v1/ask/{id}`, the client methods | ~600, most of it tests | S2 |
| S4 | CHANGELOG, shape lines, spec merge, ADR-0068 Accepted, archive | docs only | S3 |

## S1 Plan

- [ ] 1.1 Proposal, design, delta specs, tasks.
- [ ] 1.2 ADR-0068 (Proposed) and its README row.

## S2 `openkos.api.ask`

- [ ] 2.1 RED: API tests for every scenario of "A Question Is Asked Under The
      Query Disclosure Rule" with fake clients patched at `_wiring`.
- [ ] 2.2 `api/_ask.py`: `ask`, `Answer`, `AnswerCitation`, `OmittedConcept`,
      `AskProgress`; `_wiring.default_embedder`; export
      `BackendEmbeddingDimensionMismatch`.
- [ ] 2.3 Package docstring: the one gated result; spec scenario test updated.
- [ ] 2.4 Regenerate the surface snapshot; classifier output additive only.
- [ ] 2.5 Mutation-check the withheld-answer and the consent guards.

## S3 Local API and client

- [ ] 3.1 RED: registry and route tests for "A Question Is Asked As A Single
      Pollable Job" and the gated-answer scenario.
- [ ] 3.2 `localapi/_asks.py` registry, `AskRequest`/`AskJob` models, the two
      routes, the `ask_active` row; the package docstring scope.
- [ ] 3.3 Regenerate the OpenAPI snapshot and `schema.d.ts`; `ask`/`askJob`
      client methods and tests.

## S4 Archive

- [ ] 4.1 CHANGELOG; `docs/architecture.md` shape line.
- [ ] 4.2 Merge the deltas; each heading exactly once; ADR-0068 Accepted in all
      three places; `archive-report.md`; move to `archive/`.
