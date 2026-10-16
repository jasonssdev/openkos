# Tasks: Expose Unmerge Through `openkos.api` And The Local API

Tracking issue: #1432. Inputs: `proposal.md`, `design.md` (authoritative),
`specs/stable-python-api/spec.md`, `specs/local-http-api/spec.md` (deltas) and
ADR-0064 (Proposed, written with this plan). Paths are under `src/openkos` unless
they start with `tests/`, `docs/` or `openspec/`.

PR text names the change in prose ("the `unmerge-api` change") and uses
`Refs #1432` on every slice but the last, which uses `Closes #1432`.

Spec shorthand. Stable-python-api: `P1` Merge History Is Readable; `P2` Unmerge
Is Previewed, Then Applied Against Its Version; `P3` Unmerge Extends v1
Additively. Local-http-api: `H1` The Operations Are Served Under `/v1`
(MODIFIED); `H2` Outcomes And Errors Map To Status Codes (MODIFIED); `H3` The
Unmerge Version Travels Unchanged From Preview To Apply. `D1`..`D8` are the design
decisions.

## Review Workload Forecast

Authored changed lines are additions plus deletions; the generated
`tests/unit/api/api_surface.json` and `tests/unit/localapi/openapi.json` are
excluded. The ~400-line figure is advisory: no test or doc is cut to fit it.

| Slice | Title | Forecast (authored) | Depends on |
|-------|-------|---------------------|------------|
| S1 | Plan: proposal, design, delta specs, tasks, ADR-0064 Proposed | ~600 (docs) | none |
| S2a | History read and unmerge preview with its version (application + `openkos.api`) | ~450 | S1 |
| S2b | Apply against the version, `UnmergeInterrupted` | ~400 | S2a |
| S3 | Local HTTP API: three routes, error rows, OpenAPI snapshot | ~300 | S2b |
| S4 | Wording brief, CHANGELOG, spec merge, ADR-0064 Accepted, archive | ~200 | S3 |

Delivery strategy: auto-chain, stacked-to-main (each slice branches from `main`
after the previous one is squash-merged, and regenerates the snapshots; snapshot
JSON is never hand-merged).

## S1 Plan

- [x] S1.1 Write `proposal.md`, `design.md`, `tasks.md` and the two delta specs.
- [x] S1.2 Write ADR-0064 (Proposed) and its README index row;
  `uv run pytest tests/unit/test_adr_index.py` passes.

## S2a History and preview (P1, P2 preview half, P3)

- [ ] S2a.1 RED: `tests/unit/application/test_unmerge_api_service.py` for
  `merge_history` (newest first, title from the snapshot, invalid id and no ledger
  list nothing) and `preview_unwind` (one step for the newest merge, a chain for a
  deeper one, `survivor_missing` with `absorbed_into`, `not_merged`,
  `survivor_edited` for the first step and for a later step decided from the
  ledger, `discard_survivor_edits` reported, digest stable across calls and moved
  by a changed rewrite file, the absorbed path, the flag). Writes nothing.
- [ ] S2a.2 GREEN: `application/unmerge_service.py` gains `MergeHistoryEntry`,
  `merge_history`, `UnwindPreview`, `UnwindRefusal`, `preview_unwind` and the
  digest (D2, D4, D6).
- [ ] S2a.3 RED then GREEN: `tests/unit/api/test_unmerge_api.py` previews a real
  pairwise merge and a real group merge through the facade; `api/_unmerge.py`
  with `MergeRecord`, `UnmergeStep`, `UnmergeVersion`, `UnmergePreview`,
  `NotUnmergeable`, `merge_history`, `preview_unmerge`; exports in `__all__`.
- [ ] S2a.4 Regenerate `api_surface.json`; the classifier reports only additive
  changes. Mutation-check the survivor-edit decision and the digest's flag line.

## S2b Apply (P2 apply half, P3)

- [ ] S2b.1 RED: application apply under one section: digest mismatch is stale
  with nothing written; a refusal at apply is returned; `UnwindStopped` at step 1
  re-raises its cause; at step N > 1 it becomes `UnmergeInterrupted`.
- [ ] S2b.2 GREEN: `unwind_to_version` in `unmerge_service`;
  `UnmergeInterrupted(WriteFailed)` in `write_gate` (`operation="unmerge"`).
- [ ] S2b.3 RED then GREEN: facade `apply_unmerge`, `Unmerged`, `UnmergeStale`,
  `UnmergeInterrupted` export; real round trips (pair: byte parity except
  `log.md`, one commit; group: two steps, two commits), busy lock writes nothing,
  flipped echoed flag is stale, `on_warning` and `committed_confidential` wiring.
- [ ] S2b.4 Regenerate the surface snapshot; mutation-check the digest comparison
  and the step-number branch.

## S3 Local HTTP API (H1, H2, H3)

- [ ] S3.1 RED: `tests/unit/localapi/test_unmerge_routes.py`: history, preview,
  apply round trip, the three `NotUnmergeable` rows, `stale`,
  `unmerge_interrupted`, unknown key 422, wait ceiling.
- [ ] S3.2 GREEN: routes in `_routes.py`, request/response models in
  `_models.py`, rows in `_errors.py`; regenerate `openapi.json` and check
  `UnmergeVersion` is one component. Mutation-check each new row.

## S4 Close

- [ ] S4.1 `docs/decision-wording.md`: drop the #1432 gap and its open question.
- [ ] S4.2 CHANGELOG entry.
- [ ] S4.3 Merge the deltas into `openspec/specs/` (name-match every heading;
  check each requirement against shipped code).
- [ ] S4.4 ADR-0064 to Accepted (frontmatter, body, README row).
- [ ] S4.5 Move the change folder to `openspec/changes/archive/<date>-unmerge-api/`
  with `archive-report.md`.

## Applicable checks (every slice)

`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, focused
pytest per slice, `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov` at least
once on the cumulative content, `uv run python evals/run_self_tests.py`.

## Progress

| Slice | PR | Squash SHA | Notes |
|-------|----|------------|-------|
