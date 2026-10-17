# Archive Report: api-ask

**Archived**: 2026-10-17 (S4, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-17-api-ask/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1483, closed by S4.

## Final State At Close

Three slices before this one merged to `main`, each one squash-merged PR to `main` in order.

| Slice | PR | Squash commit | Content |
|-------|----|---------------|---------|
| S1 plan, ADR-0068 Proposed | #1484 | `1c41b36f` | docs |
| S2 `openkos.api.ask` and its types | #1493 | `9d67030b` | API, surface snapshot, tests |
| S3 `POST /v1/ask`, `GET /v1/ask/{ask_id}`, client methods | #1499 | `ccefb3bd` | local API, OpenAPI snapshot, generated client, tests |
| S4 CHANGELOG, shape lines, spec merge, ADR-0068 Accepted, archive | this PR | | docs only |

- **Shipped surface** (additive, `API_VERSION` unchanged at 1): `ask`, `Answer`, `AnswerCitation`, `OmittedConcept`, `AskProgress`, and the export of the existing `BackendEmbeddingDimensionMismatch`. Over HTTP: `POST /v1/ask` (202, job), `GET /v1/ask/{ask_id}`, the row `ask_active` (409, retryable). Client: `ask`, `askJob`, the `ask_active` code.
- **ADR-0068**: Accepted in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).
- **Not changed, on purpose**: the CLI and `docs/cli.md`; `query --save` through the API; no ask screen (`desktop/ui/` untouched); no stop for an ask.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 1 RENAMED + MODIFIED in place (The Surface Is Owner-Local And Applies No Disclosure Gate → The Surface Is Owner-Local And Gates Only An Answer), 2 ADDED appended (A Question Is Asked Under The Query Disclosure Rule; Ask Extends v1 Additively). The Purpose lists asking and names the one gated result. |
| local-http-api | Updated | 1 MODIFIED in place (The Operations Are Served Under `/v1`, One Per Python API Operation: two ask rows, and the route-table scenario no longer bars a query route), 1 RENAMED + MODIFIED in place (Responses Are Owner-Local And Apply No Disclosure Gate → Responses Are Owner-Local And An Answer Is Gated Before It Leaves), 1 ADDED appended (A Question Is Asked As A Single Pollable Job). The Purpose names asking and the ask registry. |
| desktop-client | Updated | 1 ADDED appended (The Client Starts And Polls An Ask). |

The deltas' "(Previously: ...)" notes were not carried into the living specs.

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against its living spec after the merge: each appears exactly once. Two ask-job scenarios first shared their names with the judging job's ("Start returns at once", "A second start while active is refused") and were renamed in both the delta and the living spec to "An ask start returns at once" and "A second ask while one runs is refused". Neither old requirement heading survives.

## Requirement-vs-Code Check

| Requirement | Result |
|-------------|--------|
| The Surface Is Owner-Local And Gates Only An Answer | Met. The package docstring names the exception and ADR-0068 (`test_the_docstring_states_the_owner_local_boundary`); the import guard still bars `openkos.mcp.gate`. |
| A Question Is Asked Under The Query Disclosure Rule | Met. `api/_ask.py` calls `run_query` with clients from `_wiring`, the exemption from `backends.resolve_local_exemption`, then `query_report.disclosable_ids` and `build_query_report` after the query. Each scenario has a test in `tests/unit/api/test_ask_api.py`, including an uncited confidential block withholding the answer. |
| Ask Extends v1 Additively | Met. The classifier printed six `additive ... name added` lines; `API_VERSION` is 1. |
| The Operations Are Served Under `/v1` | Met. `test_routes_security.py` and `test_openapi_snapshot.py` list both routes. |
| Responses Are Owner-Local And An Answer Is Gated Before It Leaves | Met. The registry passes `include_confidential` only from the body; the real-`ask` route test finishes `withheld` and names nothing confidential; the `openkos.localapi` docstring states the scope. |
| A Question Is Asked As A Single Pollable Job | Met. `localapi/_asks.py`; tests for 202 + `Location`, progress, `ask_active`, failed with a fixed body, 404, 422 (zero, boolean, unknown key, missing, wrong type) and eviction after 16. |
| The Client Starts And Polls An Ask | Met. `desktop/client/index.ts`; request-table tests, the 202 test, type checks, and the `ask_active` code in `errors.ts` (its test reads the engine's codes). |

## Evidence

- Strict TDD: each slice's tests were observed failing first (S2: 18 failures, `api.ask` missing; S3: collection error, four route-table/OpenAPI failures, four vitest failures and `tsc` errors).
- Mutations, each caught: the disclosable set taken with consent, retrieval forced to consent, the local exemption dropped, the confidential marker dropped (S2); the one-active rule dropped, consent forced in the registry (S3).
- Local gates on S2 and S3: ruff, ruff format, mypy, `OPENKOS_REQUIRE_API_BASE_REF=1 pytest --cov` (13,772 and 13,799 passed, 96.8%), evals self-tests 58/58, desktop typecheck/test/check:schema/build.
- Not run: a real-model ask. No test calls a model.

### Merge Against guided-setup-engine

`guided-setup-engine` was archived to `main` first and touched the same three living specs. The rebase kept both sides: its setup rows and this change's ask rows in the operations table, both Purposes naming setup and asking, and its appended requirements followed by this change's. The route-table scenario now reads "none exists for ingest or doctor", since both setup and query have routes. Its "Poll to completion" scenario shares a name with the judging job's; that duplicate is outside this change and left as found.

## Follow-ups

- None owed by this change.
