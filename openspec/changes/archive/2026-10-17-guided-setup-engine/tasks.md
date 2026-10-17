# Tasks: Setup Readiness And A Model Pull Through `openkos.api`

Tracking issue: #1486 (follow-up decision #1487). Inputs: `proposal.md`,
`design.md` (authoritative) and the delta specs under `specs/`. Paths are under
`src/openkos` unless they start with `tests/`, `docs/`, `desktop/` or
`openspec/`.

PR text names the change in prose ("the `guided-setup-engine` change") and uses
`Refs #1486`; the archive slice uses `Closes #1486`.

Spec shorthand. Stable-python-api: `P1` Setup Readiness Is A Model-Free Read;
`P2` A Configured Model Is Pulled; `P3` Setup Extends v1 Additively.
Llm-client: `L1` Ollama Reports Its Version; `L2` Ollama Pulls A Model.
Local-http-api: `H1` Operations (MODIFIED), `H2` Outcomes (MODIFIED), `H3` A
Model Pull Runs As A Single Pollable Job. Desktop-client: `C1` The Client
Exposes Setup. `D1`..`D11` are the design decisions.

## Review Workload Forecast

Authored changed lines are additions plus deletions; the generated
`tests/unit/api/api_surface.json`, `tests/unit/localapi/openapi.json` and
`desktop/client/schema.d.ts` are excluded. The ~400-line figure is advisory.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks | ~700 (docs) | none |
| S2 | Readiness: version probe, service, API, route, client | ~700 | S1 |
| S3 | Pull: client stream, service, API, job registry, routes, client | ~900 | S2 |
| S4 | Archive: merge deltas, archive report, CHANGELOG | ~300 (docs) | S3 |

## 1. Plan (S1)

- [x] 1.1 Proposal, design, delta specs and tasks under
  `openspec/changes/guided-setup-engine/`.

## 2. Readiness (S2) — L1, P1, P3, H1, C1

- [x] 2.1 RED then GREEN: `OllamaClient.version()` against a fake transport
  (success, refused connection, malformed body); `ModelRuntime` Protocol in
  `llm/base.py` (L1).
- [x] 2.2 Extract `doctor.configured_task_models(cfg)` and call it from check 5b;
  the existing doctor tests stay green unchanged (D1).
- [x] 2.3 RED then GREEN: `application/setup.py` `setup_readiness(root, ports)`:
  ready, unreachable, missing embedding, missing judge, memory below floor,
  `openai-compatible`, unreadable config (P1, D2-D4).
- [x] 2.4 `openkos.api.setup_readiness` and its types; snapshot regenerated;
  classifier additive (P1, P3).
- [x] 2.5 `GET /v1/setup/readiness`; OpenAPI snapshot regenerated (H1).
- [x] 2.6 Client `setupReadiness()` with a test; `pnpm --dir desktop generate`,
  `check:schema`, `typecheck`, `test` (C1).

## 3. Pull (S3) — L2, P2, P3, H1-H3, C1

- [x] 3.1 RED then GREEN: `OllamaClient.pull()`: success stream, stop closes,
  error line not-found, other error line, stream without success, malformed
  line, refused connection (L2, D7).
- [x] 3.2 RED then GREEN: `application/setup.py` `pull_model`: refused
  unconfigured (no request), refused backend, already installed, aggregated
  non-decreasing progress, phase mapping, stop (P2, D5-D7).
- [x] 3.3 `openkos.api.pull_model` and its types; snapshot; classifier (P2, P3).
- [x] 3.4 `localapi/_pulls.py` registry tested with no HTTP: admission, active
  refusal, refusal at start, failure after admission, stop, eviction, shutdown
  (H3, D8).
- [x] 3.5 Routes, error rows (`pull_active`, `model_not_configured`,
  `pull_not_supported`), lifespan shutdown; OpenAPI snapshot (H1-H3).
- [x] 3.6 Client `startModelPull`, `modelPull`, `stopModelPull`, the three codes,
  tests; desktop checks (C1).
- [x] 3.7 Mutation-check the key guards: the not-configured refusal, the stop
  check, the progress aggregation.

## 4. Archive (S4)

- [x] 4.1 Merge every delta into `openspec/specs/` (each heading once), move the
  change to `openspec/changes/archive/<date>-guided-setup-engine/`, write
  `archive-report.md`, add the CHANGELOG entry. ADR-0057 stays Proposed.
