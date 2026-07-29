# Tasks: Record and Surface Sources Whose Extraction Was Skipped (#187)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~220-280, PR2 ~325-410 (each under 400 alone) |
| 400-line budget risk | Medium (PR2 near upper bound) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (record) -> PR 2 (surface) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Record `extraction_status` on Source degrade paths | PR 1 | `uv run pytest tests/unit/model/test_okf.py tests/unit/cli/test_ingest.py` | `openkos ingest raw/<sample>` against a stubbed failing LLM backend | Revert PR1: inert keys, self-clear on next successful re-ingest, no migration |
| 2 | Surface `unextracted` in `lint` and `status` | PR 2 | `uv run pytest tests/unit/cli/test_lint.py tests/unit/cli/test_status.py` | `openkos lint <bundle>` / `openkos status <bundle>` against a fixture bundle with a `failed` Source | Revert PR2: pure, read-only, no data changes |

Dependency: PR2 branches from PR1's HEAD and targets PR1's branch; PR1 targets `main`.

## Phase 1: PR1 — Vocabulary Foundation (`model/okf.py`)

- [x] 1.1 RED: write `test_okf.py` test asserting `EXTRACTION_STATUS_KEY`, `ExtractionStatus` (4-token `Literal`), `EXTRACTION_STATUS_VALUES`, `EXTRACTION_STATUS_FAILED` exist with expected values.
- [x] 1.2 GREEN: add the constants to `src/openkos/model/okf.py`; add `get_args` to the existing `typing` import (mypy-strict requirement).
- [x] 1.3 RED: write test for `build_source_concept` accepting an optional `extraction_status` param and stamping it into frontmatter only when not `None`.
- [x] 1.4 GREEN: extend `build_source_concept` signature and stamping logic in `okf.py`.

## Phase 2: PR1 — Stage/Build Ordering (`cli/main.py`)

- [x] 2.1 RED: update the four `_stage_derived_objects` degrade-path assertions (or add new ones) in `test_ingest.py` expecting a `(plans, skip_reason)` tuple return instead of `plans` alone.
- [x] 2.2 GREEN: change `_stage_derived_objects` return type to `tuple[list[_DerivedPlan], okf.ExtractionStatus | None]`; set `skip_reason` per path (`no-extractable-text`, `blocked-by-sensitivity`, `failed`, `no-concepts-found`), `None` on success.
- [x] 2.3 GREEN: update the single call site (`ingest`, near main.py:1735) to unpack `(plans, skip_reason)`.
- [x] 2.4 RED: write test asserting the healthy path builds the Source document exactly once (byte-identical to pre-change output).
- [x] 2.5 GREEN: bind a local `_build_source_document(extraction_status)` closure in `ingest`; call once with `None` before staging; re-render only when `skip_reason is not None`, passing it into the closure.
- [x] 2.6 Update `test_confidential_default_sensitivity_floor_skips_extraction` and `test_spinner_cleared_on_ollama_error_and_degrade_proceeds` to keep passing unmodified against the new tuple return (assert they still drive the CLI and stay green, no assertions changed).

## Phase 3: PR1 — Stamping Scenarios and Cross-Guards

- [x] 3.1 RED: write `test_no_extractable_text_writes_extraction_status` (path main.py:1298).
- [x] 3.2 RED: write `test_blocked_by_sensitivity_writes_extraction_status` (path main.py:1305).
- [x] 3.3 RED: write `test_failed_extraction_writes_extraction_status` (path main.py:1316), asserting no raw exception text appears in frontmatter.
- [x] 3.4 RED: write `test_no_concepts_found_writes_extraction_status` (path main.py:1329).
- [x] 3.5 RED: write `test_successful_extraction_writes_no_extraction_status_key` (>=1 derived object -> key absent).
- [x] 3.6 RED: write `test_successful_reingest_clears_a_previous_failed_extraction_status` — OllamaError ingest produces `failed`; re-ingest identical bytes with a working LLM backend ends with the key ABSENT and the derived object present (top functional risk).
- [x] 3.7 RED: write `test_unrecognized_extraction_status_value_ignored` — a Source with an out-of-vocabulary value on disk is read without raising.
- [x] 3.8 RED: write `test_sensitivity_and_extraction_status_independent` (design test #10) — in ONE run, assert `sensitivity` IS read from disk and combined via `combine_sensitivity` while `extraction_status` is NOT read from disk, only freshly computed.
- [x] 3.9 GREEN: implement/adjust stamping in `ingest` (main.py) so 3.1-3.8 pass.
- [x] 3.10 Update `openspec/changes/surface-skipped-extractions/specs/ingestion/spec.md` scenarios to match implemented behavior (verify text already covers all cases; no code copy needed if already accurate).
- [x] 3.11 Run `uv run pytest tests/unit/model/test_okf.py tests/unit/cli/test_ingest.py` and `uv run mypy .` for PR1 scope; confirm all Phase 1-3 tests green.

## Phase 4: PR2 — Lint Detection (`lint.py`)

- [x] 4.1 RED: write `test_lint.py` test asserting `LintDoc` gains `extraction_status: str = ""` and `resource: str = ""`, populated from the existing `metadata` dict at `lint.py:125` (no new read).
- [x] 4.2 GREEN: add the two fields to `LintDoc` and populate them in the existing walk.
- [x] 4.3 RED: write `test_check_unextracted_flags_failed_sources` — a `LintDoc` with `extraction_status == "failed"` produces one `unextracted` finding.
- [x] 4.4 RED: write `test_check_unextracted_ignores_non_failed_values` (parametrized over `no-extractable-text`, `blocked-by-sensitivity`, `no-concepts-found`, and unrecognized values) — no `unextracted` finding, and `blocked-by-sensitivity` never appears in any retry prompt.
- [x] 4.5 RED: write `test_check_unextracted_names_exact_retry_command` — detail text is `openkos ingest <resource>` built from the Source's own `resource` field; generic fallback when `resource` is missing/empty.
- [x] 4.6 RED: write `test_check_unextracted_signature_has_no_bundle_dir_param` — asserts `check_unextracted(docs: list[LintDoc])` has exactly one parameter (structural no-fifth-walk guard).
- [x] 4.7 GREEN: implement `check_unextracted(docs: list[LintDoc]) -> list[LintFinding]` in `lint.py`, add `LintReport.unextracted`, and wire it into the existing `collect_docs` walk (no new walk).
- [x] 4.8 RED: write `test_lint_exits_zero_with_unextracted_findings_present`.
- [x] 4.9 GREEN: confirm/adjust `lint`'s exit-code path so `unextracted` stays non-gating like `stale`/`orphan`/`dangling`.

## Phase 5: PR2 — CLI Wiring (`cli/main.py`)

- [x] 5.1 RED: write test asserting `lint` CLI output includes an `Unextracted sources:` section when findings are present.
- [x] 5.2 GREEN: wire `LintReport.unextracted` into the `lint` command's rendering in `main.py`.
- [x] 5.3 RED: write `test_status.py` test asserting `status` folds `lint`'s `unextracted` findings into `needs_attention`, naming the same retry command.
- [x] 5.4 RED: write `test_status_consumes_same_docs_list_no_new_walk` — spy on `lint_check.collect_docs` and assert call count == 1 during a `status` run (reuses the in-memory `docs` list already bound at main.py:5010).
- [x] 5.5 GREEN: fold `unextracted` findings into `needs_attention` at the existing `status` call site, only for `failed`-sourced findings.
- [x] 5.6 RED: write `test_status_blocked_by_sensitivity_never_in_retry_prompt`.
- [x] 5.7 GREEN: confirm 5.6 passes (no separate code path expected — `check_unextracted` already excludes non-`failed` values).

## Phase 6: PR2 — Docs and Verification

- [x] 6.1 Update `docs/cli.md` to document the `unextracted` lint finding and the `needs_attention` surfacing in `status`, including the example retry command format.
- [x] 6.2 Update `openspec/changes/surface-skipped-extractions/specs/lint/spec.md` and `specs/status/spec.md` scenarios to match implemented behavior (verify text already covers all cases).
- [x] 6.3 Run `uv run pytest tests/unit/cli/test_lint.py tests/unit/cli/test_status.py`, `uv run mypy .`, `uv run ruff check . && uv run ruff format --check .` for PR2 scope; confirm all Phase 4-6 tests green.
- [x] 6.4 Run full suite `uv run pytest --cov` to confirm the 90% branch-coverage threshold holds across both PRs combined.
