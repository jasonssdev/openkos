# Archive Report: api-ingest

**Archived**: 2026-10-17 (S3, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-17-api-ingest/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1496, closed by S3.

## Final State At Close

Two slices before this one merged to `main`, each one squash-merged PR to `main` in order.

| Slice | PR | Squash commit | Content |
|-------|----|---------------|---------|
| S1 plan, ADR-0073 Proposed, the bound, the service, `openkos.api` ingest | #1515 | `d73e1f1f` | docs, engine, API, surface snapshot, tests |
| S2 the ingest routes, the job registry, the client methods | #1528 | `dd308852` | local API, OpenAPI snapshot, generated client, tests |
| S3 wording, CHANGELOG, shape line, spec merge, ADR-0073 Accepted, archive | this PR | | docs only |

- **Shipped surface** (additive, `API_VERSION` unchanged at 1): `quote_ingest`, `quote_refused_file`, `run_ingest`, `IngestQuote`, `IngestFileQuote`, `IngestQuoteToken`, `IngestTokenFile`, `IngestBinding`, `IngestQuoteStale`, `IngestRunOutcome`, `IngestFileResult`, `IngestProgress`, `IngestRunResult`, `IngestRefused`. Engine: `extraction.concept.max_extraction_calls`, `application/ingest_consent.py`, `ingest_service.probe_source`. Over HTTP: `POST /v1/ingest/quote`, `POST /v1/decisions/{key}/ingest-quote`, `POST /v1/ingest/runs` (202, job), `GET /v1/ingest/runs/{run_id}`, `POST /v1/ingest/runs/{run_id}/stop`, the rows `ingest_active` (409, retryable) and `ingest_refused` (503). Client: `quoteIngest`, `quoteRefusedFile`, `startIngest`, `ingestRun`, `stopIngest` and the two codes.
- **ADR-0073**: Accepted in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).
- **Not changed, on purpose**: the CLI and `docs/cli.md` (its batch gate still prints the ordinary estimate); `desktop/ui/`; `--re-extract` and `--event-date` through the API; directory and glob expansion.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 1 MODIFIED in place (The Surface Is Owner-Local And Gates Only An Answer: content reaches a model through an ingest run too; the auto-commit names a settings change and an added file; a stop waits for the file in flight), 2 ADDED appended (Files Are Added Through A Quote And A Run Against It; Ingest Extends v1 Additively). The Purpose lists adding source files; "Ingest and doctor are not part of v1" became "Doctor is not part of v1". |
| local-http-api | Updated | 3 MODIFIED in place (The Operations Are Served Under `/v1`: five ingest rows, and the route-table scenario now names doctor only; Outcomes And Errors Map To Status Codes: `IngestQuoteStale`, `ingest_active`, `ingest_refused`; A Model Pull Runs As A Single Pollable Job: its "Poll to completion" scenario renamed "Poll a pull to completion"), 1 ADDED appended (Adding Files Runs As A Single Pollable Job). The Purpose names adding files and the ingest registry. |
| desktop-client | Updated | 1 ADDED appended (The Client Quotes And Runs Adding Files). |
| extraction-union-judge | Updated | 1 ADDED appended (Extraction Exposes An Upper Bound On Its Calls). |

The deltas' "(Previously: ...)" notes were not carried into the living specs. The local-http-api MODIFIED blocks were re-derived against the living text at archive time (it had gained the settings and finding routes since the plan), and the delta was rewritten to the merged text.

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against its living spec after the merge: each appears exactly once. The model pull's "Poll to completion" scenario, which shared its name with the judging job's, now reads "Poll a pull to completion"; "Poll to completion" appears once, under the judging job. One duplicate scenario heading outside this change remains in stable-python-api ("A configuration changed after the preview is stale", from the model-settings change) and is left as found.

## Requirement-vs-Code Check

| Requirement | Result |
|-------------|--------|
| The Surface Is Owner-Local And Gates Only An Answer | Met. The package docstring names adding files among what sends content and what a stop waits for. |
| Files Are Added Through A Quote And A Run Against It | Met. `application/ingest_consent.py` and `api/_ingest.py`; each scenario has a test in `tests/unit/application/test_ingest_consent.py` or `tests/unit/api/test_ingest_api.py` (free quote, protected and changed files refused, staleness, per-file ceiling, refusal row resolved, busy, stop, `quote_refused_file`). Progress was first specified as every phase for every file; the code emits `extracting` only when the model wait starts and `refreshing` only when the run refreshes, and the requirement was corrected to say so. |
| Ingest Extends v1 Additively | Met. The classifier printed additions only; `API_VERSION` is 1. |
| Extraction Exposes An Upper Bound On Its Calls | Met. `tests/unit/extraction/test_max_extraction_calls.py` drives the real extractors with adversarial replies over chunked and short sources, both titles, both paths and the decision pass on and off. |
| The Operations Are Served Under `/v1` | Met. `test_routes_security.py` and `test_openapi_snapshot.py` list the five routes. |
| Outcomes And Errors Map To Status Codes | Met. `test_errors.py` produces `ingest_refused` and `ingest_active` exactly once. |
| Adding Files Runs As A Single Pollable Job | Met. `localapi/_ingests.py`; tests for 202 + `Location`, progress, warnings, `ingest_active`, stale and missing tokens answering the start, a refusal before admission, a failure after it, stop, 404, eviction after 16, 422 bodies, and real end-to-end runs. The job resource carries `warnings` (commits that could not be made); the requirement was corrected to list it. |
| The Client Quotes And Runs Adding Files | Met. `desktop/client/index.ts`; request-table tests, a 202 test, the `ingest_active` typed error, and type checks that the quote's token is what a run takes. |

## Evidence

- Strict TDD: each step's tests were observed failing first (the bound: `AttributeError`; the service and the API: collection and attribute errors; the local API: collection error; the client: 8 vitest failures and `tsc` errors).
- Mutations, each caught: the per-file ceiling (`>=` to `>`), the `inside_workspace` guard, the stale-files digest check, the bound's judge term, its per-window retry and its meeting reading (S1); the one-active rule, the admission check and the decision-kind check (S2). Each was restored byte-exact with `__pycache__` purged.
- Local gates on S1 and S2: ruff, ruff format --check, mypy, `OPENKOS_REQUIRE_API_BASE_REF=1 pytest --cov` (14,024 and 14,079 passed, 96.80% and 96.79%), evals self-tests 58/58, desktop typecheck/test/check:schema/build. CI green on both PRs.
- Real-model smoke on a scratch copy of a workspace (qwen3:8b on Ollama): one note quoted at 6 calls spent 4 and added 2 pages, the derived stores refreshed; quoted again it was `unchanged` at 0; edited, it was refused `changed_after_import`.

## Follow-ups

- None owed by this change. The add-files screen belongs to the desktop UI work, against the wording in `docs/decision-wording.md` ("Adding files", "A file that wasn't added").
