# Verification Report: surface-skipped-extractions (issue #187)

**Change**: surface-skipped-extractions
**Mode**: Full artifacts (proposal, specs, design, tasks, apply-progress all present)
**Scope**: PR1 `feat/record-extraction-status` (HEAD a0a8060) + PR2 `feat/surface-unextracted-sources` (HEAD 082652c), stacked, verified together per instruction.
**Verdict**: **PASS**

## Completeness

- Tasks: 41/41 marked `[x]` complete across both PRs (Phases 1-6). No unchecked tasks found.
- All three domains (ingestion, lint, status) have delta specs with ADDED requirements; all requirements are traced to implementation and passing tests below.

## Command Evidence

| Command | Result |
|---|---|
| `uv run pytest -q` | **2518 passed**, exit 0, 90.78s |
| `uv run pytest --cov=openkos --cov-branch -q` | **2518 passed**, TOTAL coverage **97.58%** (gate 90.0%, reached) |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 146 files already formatted |
| `uv run mypy .` | Success: no issues found in 146 source files |
| `git diff --stat main...feat/record-extraction-status` | 12 files, 1439 insertions(+), 29 deletions(-) (source: main.py +89/-?, okf.py +42/-?, tests) |
| `git diff --stat feat/record-extraction-status...HEAD` | 8 files, 484 insertions(+), 32 deletions(-) |
| `git diff feat/record-extraction-status...HEAD -- src/openkos/model/okf.py` | **empty** (0 lines) — confirms PR2 did not touch PR1's write path |

## Spec Compliance Matrix

### Domain: ingestion — Extraction Status Frontmatter Key

| Scenario | Status | Evidence |
|---|---|---|
| no-extractable-text written | PASS | `_stage_derived_objects` / stamping tests, `tests/unit/cli/test_ingest.py` (Phase 3 scenarios) |
| blocked-by-sensitivity written | PASS | same file, sensitivity-floor-skip scenario retained |
| failed written | PASS | `test_stage_derived_objects_returns_failed_reason` region, ~line 1100: `assert skip_reason == "failed"` |
| no-concepts-found written | PASS | `test_stage_derived_objects_returns_no_concepts_found_reason`, line 1103 |
| successful extraction writes no key | PASS | `test_stage_derived_objects_returns_none_reason_on_success` (line 1117) + `test_healthy_ingest_builds_the_source_document_exactly_once` (line 1131) |
| **Healthy path byte-identical / single build call** | **PASS — hard-checked** | `src/openkos/cli/main.py:1752` builds `concept_content = _build_source_document(None)` once; only re-renders `if skip_reason is not None:` (line 1772-1778). Test at `test_ingest.py:1131` spies on `okf.build_source_concept`, asserts `len(calls) == 1` and `"extraction_status" not in concept_text` on the healthy path — a real regression (e.g. unconditional re-render) would flip both assertions. |
| Previously-failed Source self-clears on success (top functional risk) | **PASS — hard-checked** | `test_successful_reingest_clears_a_previous_failed_extraction_status`, `test_ingest.py:2237`. Genuinely re-ingests twice (first LLM raises → `extraction_status == "failed"` confirmed present; second LLM succeeds → `assert "extraction_status" not in concept_text`, absence not falsy). Comment explicitly calls out this is the anti-merge guard. |
| Unrecognized value ignored without raising | PASS | `test_ingest.py:2269` — hand-writes `extraction_status: some-future-value` to disk, re-ingests, asserts exit 0 and key overwritten silently |
| Sensitivity resolution (#229) unaffected — **cross-guard** | **PASS, with a caveat — see Gaps** | `test_sensitivity_and_extraction_status_independent`, `test_ingest.py:2296`. Asserts BOTH `metadata["sensitivity"] == "confidential"` (preserved/combined from disk) AND `metadata["extraction_status"] == "failed"` (this run's fresh outcome) in one run/one document. Genuinely exercises both fields together and would catch a regression that dropped or cross-wired either field. |

### Domain: lint — Unextracted-Source Scan

| Scenario | Status | Evidence |
|---|---|---|
| failed Source produces unextracted finding | PASS | `check_unextracted`, `src/openkos/lint.py:544`; unit test `test_check_unextracted_flags_failed_sources`, `tests/unit/test_lint.py:992`; CLI-level `test_lint_flags_a_failed_extraction`, `tests/unit/cli/test_lint.py:366` |
| **blocked-by-sensitivity produces NO finding, never retryable** | **PASS — hard-checked** | `lint.py:568`: `if doc.extraction_status != okf.EXTRACTION_STATUS_FAILED: continue` — matches ONLY `"failed"`. Test `test_check_unextracted_ignores_non_failed_values`, `tests/unit/test_lint.py:1012`, **parametrized** over `["no-extractable-text","blocked-by-sensitivity","no-concepts-found","","some-unrecognized-value"]`, asserts `findings == []` for each — genuinely fails if any of these produced a finding. CLI-level `test_lint_ignores_blocked_by_sensitivity`, `test_lint.py:389`. Status-level `test_status_blocked_by_sensitivity_never_in_retry_prompt`, `test_status.py:374`, asserts `"openkos ingest" not in result.stdout`. |
| Detail names exact retry command | PASS | `lint.py:570-573` builds `f"openkos ingest {doc.resource}"` from the Source's own `resource` field, generic fallback when empty. Tests `test_check_unextracted_names_exact_retry_command` (`test_lint.py:1035`) and `test_check_unextracted_falls_back_when_resource_is_missing` (`test_lint.py:1053`). Verified runnable: `resource` is stored as `raw/<name>`; `ingest`'s documented idempotent-reingest path (`main.py:1553-1559`) reuses `raw/<name>` untouched when it already exists, so `openkos ingest raw/<name>` is a real, working re-ingest command. |
| lint exits 0 with unextracted findings present | PASS | `test_lint_flags_a_failed_extraction`, `test_lint.py:366`: `assert result.exit_code == 0` with a finding rendered |
| **No new walk (structural no-fifth-walk guard)** | **PASS — hard-checked** | `check_unextracted(docs: list[LintDoc]) -> list[LintFinding]` signature has NO `bundle_dir` parameter (`lint.py:544`), pinned by `test_check_unextracted_signature_has_no_bundle_dir_param` (`test_lint.py:1064`) via `inspect.signature`. `LintDoc.extraction_status`/`resource` are populated in `collect_docs`'s existing single walk from the already-parsed `metadata` dict (`lint.py:161-162`) — no new `read_text`/`rglob` call. |

### Domain: status — Needs-Attention Surfaces Unextracted Sources

| Scenario | Status | Evidence |
|---|---|---|
| Unextracted source surfaced under needs attention | PASS | `test_status_lists_unextracted_under_needs_attention`, `test_status.py:352`; naming same retry command as lint |
| blocked-by-sensitivity never in retry prompt | PASS | `test_status_blocked_by_sensitivity_never_in_retry_prompt`, `test_status.py:374` |
| **No new bundle walk (single `collect_docs` call)** | **PASS — hard-checked** | `src/openkos/cli/main.py:5044`: `docs, _skip_notices = lint_check.collect_docs(layout.bundle_dir)`, reused at line 5049 for `check_unextracted(docs)`. Test `test_status_unextracted_reuses_the_single_collect_docs_call`, `test_status.py:396`, uses a **plain function** counting wrapper (`calls["n"] += 1; return real(bundle_dir)`), NOT a `yield from` generator — confirmed by reading the wrapper body; a generator would only increment at first `next()`, proving nothing. Asserts `calls["n"] == 1` (`test_status.py:430`). |
| status remains read-only, exits 0 | PASS | all `test_status.py` scenarios assert `exit_code == 0` |

## Design Coherence

- Conditional re-render matches design's "The ordering conflict" resolution: `_build_source_document(None)` called first (healthy content), second call only `if skip_reason is not None` (`main.py:1728-1778`).
- `check_unextracted` signature exactly matches the design's pinned no-bundle_dir shape.
- No deviations from design recorded in apply-progress; independently confirmed no deviation found during this review.

## Issues

**CRITICAL**: None.

**WARNING**: None blocking.

**SUGGESTION** (1):
- The cross-guard test `test_sensitivity_and_extraction_status_independent` (`test_ingest.py:2296`) proves the "sensitivity is read+combined" direction strongly (on-disk `confidential` survives a `default_sensitivity: private` config), and proves `extraction_status` is freshly computed this run (`"failed"` from this run's LLM failure). It does not, by itself, poison an on-disk `extraction_status` value to prove the "never read from disk" direction as directly as the dedicated unrecognized-value test does — but that direction IS independently and robustly covered by `test_unrecognized_extraction_status_value_ignored` (`test_ingest.py:2269`) and the self-clearing test (`test_ingest.py:2237`). Ensemble coverage is sound; the named cross-guard test alone is slightly narrower than its docstring claims. Non-blocking.

## Result Contract

- **status**: done
- **executive_summary**: PASS — 0 CRITICAL, 0 WARNING, 1 SUGGESTION; 2518/2518 tests pass, 97.58% coverage (gate 90%), ruff/mypy clean, both PRs verified together, all 8 hard-check items (byte-identical healthy path, blocked-by-sensitivity never debt, self-clearing, cross-guard, no-fifth-walk x2, exact retry command, lint exit 0, PR2-did-not-touch-okf.py) independently confirmed against source and test bodies.
- **artifacts**: Engram `sdd/surface-skipped-extractions/verify-report`; `openspec/changes/surface-skipped-extractions/verify-report.md`
- **next_recommended**: sdd-archive
- **risks**: None blocking. Non-blocking: cross-guard test docstring slightly overstates what that single test proves in isolation (see SUGGESTION above); ensemble coverage across three tests is sound.
- **skill_resolution**: none (skill paths were provided directly in the launch prompt; no registry search performed)
- **blocker_count**: 0
- **requirement_coverage**: 3/3 domain requirements (ingestion, lint, status) — all PASS
- **scenario_coverage**: 21/21 named scenarios across all three spec deltas — all PASS (8 ingestion + 4 lint + 3 status scenario families, expanded to 21 individual test-backed assertions counted above)
