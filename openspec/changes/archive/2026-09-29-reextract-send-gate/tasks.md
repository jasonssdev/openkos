# Tasks: Gate Re-Extraction On The Source's Own Resolved Sensitivity (#1086)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~10 (code), ~140 (tests), ~120 (specs) |
| 400-line budget risk | Low |
| Chained PRs recommended | No -- one small, coherent fix |
| Delivery strategy | ask-on-risk (default) |

## Phase 1: RED

- [x] 1.1 Add failing tests in `tests/unit/cli/test_ingest.py`: a re-extract
      of a Source raised to `confidential` under a lower workspace default
      calls the LLM (pins the bug); observe RED for the right reason
      (assertion on `fake.calls`/`extraction_status`).

## Phase 2: GREEN -- the fix

- [x] 2.1 `src/openkos/cli/main.py`, `_ingest_single`'s
      `application_ingest.stage_derived_objects(...)` call: change
      `workspace_floor=cfg.default_sensitivity` to
      `workspace_floor=source_plan.source_sensitivity`.
- [x] 2.2 Re-run the Phase 1 tests; observe GREEN.

## Phase 3: Fallout and new coverage

- [x] 3.1 Update `test_extract_gate_still_reads_workspace_floor` and
      `test_reingest_resolved_sensitivity_does_not_leak_into_workspace_floor`,
      which pinned the pre-fix contract as correct, to the corrected
      behavior.
- [x] 3.2 Fix collateral fallout in tests whose forged-sensitivity fixtures
      now land on `confidential` and get blocked by the corrected gate
      (`test_derived_object_inherits_source_document_value_not_config`,
      `test_reingest_stamps_new_derived_objects_with_the_preserved_level`,
      `test_sensitivity_and_extraction_status_independent`).
- [x] 3.3 Add regression tests: `--include-confidential` still calls the LLM
      (proves the block is not vacuous); a private Source on a private
      workspace still extracts; a confidential Source still blocks under
      the most permissive (`public`) workspace default.
- [x] 3.4 Mutation check: revert the fix line to
      `workspace_floor=cfg.default_sensitivity`, observe RED on the Phase 1
      test, restore the exact inverse edit, purge `__pycache__`.

## Phase 4: Audit other send gates

- [x] 4.1 Grep every `stage_derived_objects` caller in `src/`/`evals/`;
      confirm `_ingest_single` is the only one and `_ingest_batch` reuses it
      per file.
- [x] 4.2 Check `retrieval/answer.py`, `resolution/{contradiction,
      edge_typing,adjudication}.py`, and `query.py`'s `stage_filed_answer`
      for the same bug class; document that each already re-checks a
      per-concept stored value (not a stale scalar floor) or only computes a
      write-time stamp, so none share this bug.

## Phase 5: Spec and docs

- [x] 5.1 `openspec/changes/reextract-send-gate/specs/ingestion/spec.md`
      delta (this change).
- [x] 5.2 `openspec/changes/reextract-send-gate/specs/sensitivity-aware-llm/spec.md`
      delta (this change).

## Phase 6: Verification

- [x] 6.1 `uv run ruff check .`
- [x] 6.2 `uv run ruff format --check .`
- [x] 6.3 `uv run mypy .`
- [x] 6.4 `uv run pytest --cov`
- [x] 6.5 `uv run python evals/run_self_tests.py`

## Phase 7: Archive

- [x] 7.1 `gentle-ai sdd-archive-compose` both deltas into their canonical
      specs; `git mv` the change folder to
      `openspec/changes/archive/<date>-reextract-send-gate/`; write
      `archive-report.md`.
