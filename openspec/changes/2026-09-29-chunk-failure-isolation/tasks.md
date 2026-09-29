# Tasks: Isolate a Failed Chunk from the Rest of an Extraction (#1053)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~350-450 (code + docs), ~500-650 (tests) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No -- one coherent behavior, one work unit |
| Delivery strategy | ask-on-risk (default) |

## Phase 1: Core retry/skip mechanism (`extraction/concept.py`)

- [x] 1.1 RED: `_fan_out_window_lists` retry/skip tests (retry-succeeds,
      skip-after-retry, all-fail-raises, non-`BackendError` not retried,
      `BackendUnavailable` not retried) in `tests/unit/extraction/test_concept.py`.
- [x] 1.2 GREEN: add `_extract_window_or_skip`; return `(collected, skipped_chunks)`
      from `_fan_out_window_lists`; propagate through `_fan_out_windows`.
- [x] 1.3 Wire `ExtractionReport.skipped_chunks` into both `extract_concept`
      and `extract_concept_union` (both `ExtractionReport(...)` sites).
- [x] 1.4 RED+GREEN: concurrent-path skip test (`_WindowKeyedLLM`).
- [x] 1.5 RED+GREEN: union-path chunked-branch skip + all-fail tests.
- [x] 1.6 Mutation check: remove the retry, remove the all-fail re-raise,
      remove the `BackendUnavailable` special-case -- confirm each mutation
      fails the tests that must catch it, then restore the exact inverse
      edit and purge `__pycache__`.

## Phase 2: Notice plumbing (`model/okf.py`, `application/ingest.py`)

- [x] 2.1 Add `chunk-extraction-partial` to `ExtractionNotice` +
      `EXTRACTION_NOTICE_CHUNK_PARTIAL` constant; update
      `test_extraction_notice_vocabulary_constants`.
- [x] 2.2 RED+GREEN: `stage_derived_objects` stamps the new notice from
      `report.skipped_chunks` (`tests/unit/application/test_ingest.py`).
- [x] 2.3 RED+GREEN: `extraction_retry_due` treats the new token as
      retryable debt, alongside the two judge tokens.

## Phase 3: CLI disclosure (`cli/main.py`)

- [x] 3.1 RED+GREEN: `_chunk_skip_notice` unit tests (silent when nothing
      skipped, names one/several skipped chunks).
- [x] 3.2 Wire into `_render_staged_derived_objects`'s notice tuple (first
      position).
- [x] 3.3 Update the batch-summary/lint-pointer prose for the sixth token
      (no `lint` section covers it, like the sole-object token) and the
      pinned test string.
- [x] 3.4 RED+GREEN: end-to-end `openkos ingest` tests -- retry-recovers,
      skip-keeps-others-and-marks-notice, all-fail-still-degrades.
      Confirm the pre-existing `test_ingest_degrades_when_a_concurrent_
      window_fails` (`BackendUnavailable`) stays green unmodified.

## Phase 4: Docs and spec

- [x] 4.1 `docs/cli.md`: new paragraph describing the retry-then-skip
      contract; correct the two stale "all windows propagate"/"four of
      five" passages; note the retry in the cost-table caveat.
- [x] 4.2 `openspec/changes/2026-09-29-chunk-failure-isolation/specs/ingestion/spec.md`
      delta (this change).

## Phase 5: Verification

- [x] 5.1 `uv run ruff check .`
- [x] 5.2 `uv run ruff format --check .`
- [x] 5.3 `uv run mypy .`
- [x] 5.4 `uv run pytest --cov`
- [x] 5.5 `uv run python evals/run_self_tests.py`
