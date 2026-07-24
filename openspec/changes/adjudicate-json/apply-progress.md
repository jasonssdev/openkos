# Apply Progress: adjudicate --json (#137, Slice 2a)

**Change**: adjudicate-json
**Mode**: Strict TDD
**Status**: 15/15 tasks complete. Ready for verify.

## Completed Tasks

### 1. Pure payload builder — `_adjudication_payload`
- [x] 1.1 RED — added `tests/unit/cli/test_adjudicate.py` unit tests for `_adjudication_payload`: empty → `[]`, single SAME (exact field set, uppercase tier, no `confidence`), mixed verdicts (order preserved, per-object exact values), `same_only=True` filter. Confirmed failure: `AttributeError: module 'openkos.cli.main' has no attribute '_adjudication_payload'`.
- [x] 1.2 GREEN — added `import json` to stdlib group; added `AdjudicatedCandidate` to the `openkos.resolution.adjudication` import; added `_adjudication_payload(results: Sequence[AdjudicatedCandidate], *, same_only: bool) -> list[dict[str, object]]` next to `_format_verdict_tally`, using `result.candidate.tier.name` (uppercase) and `result.verdict.value.upper()`. 4/4 payload tests green.

### 2. `adjudicate --json` wiring
- [x] 2.1 RED — added CliRunner test `test_adjudicate_json_flag_emits_clean_json_and_suppresses_human_output`. Confirmed failure: exit code 2 (`No such option: --json`).
- [x] 2.2 GREEN — added `json_output: bool = typer.Option(False, "--json", ...)` parameter; inserted short-circuit branch immediately after the `except OllamaError` handler and before the workspace echo; updated the docstring to drop the stale "no `--json`" claim and documented the new flag's suppression/short-circuit behavior. Test green.

### 3. `--json --same-only` composability
- [x] 3.1 RED (verification) — added `test_adjudicate_json_same_only_composability_filters_to_same_verdicts`. Already passed on first run (composability flows through `_adjudication_payload(same_only=same_only)` wired in Task 2) — logged as verified existing coverage, not a new failure, per tasks.md guidance.
- [x] 3.2 GREEN — no additional production code needed; wiring confirmed correct.

### 4. Empty state → `[]` under `--json` (both guards)
- [x] 4.1 RED (verification) — added `test_adjudicate_json_no_candidates_emits_empty_array_not_prose` and `test_adjudicate_json_same_only_all_filtered_out_emits_empty_array_not_prose`. Both passed on first run — the Task 2.2 branch placement already precedes both prose guards (`main.py` "No candidates found." and "No SAME-verdict candidates to display").
- [x] 4.2 GREEN — confirmed branch placement is correct; no reordering needed.

### 5. Error path unaffected by `--json`
- [x] 5.1 RED (verification) — added `test_adjudicate_json_ollama_unavailable_still_errors_on_stderr_with_no_json`. Passed on first run: stderr unavailability message intact, exit 1, empty stdout.
- [x] 5.2 GREEN — no production change needed; the branch sits after all three Ollama handlers as designed.

### 6. Non-regression: human output byte-identical
- [x] 6.1 — ran full pre-existing `tests/unit/cli/test_adjudicate.py` suite (all 36 original tests, unmodified) plus the full project suite (2002 tests total). Zero changes to existing assertions/output; all pass.

### 7. Quality gate
- [x] 7.1 `uv run pytest` — 2002 passed.
- [x] 7.2 `ruff check` — All checks passed!
- [x] 7.3 `ruff format --check` — clean (after `ruff format` normalized 2 files: `src/openkos/cli/main.py`, `tests/unit/cli/test_adjudicate.py`).
- [x] 7.4 `mypy` — clean, 131 source files (fixed one `type-arg` finding: `list[dict]` → `list[dict[str, object]]` on `_adjudication_payload`'s return annotation).

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/cli/main.py` | Modified | Added `import json`; imported `AdjudicatedCandidate`; added pure `_adjudication_payload` helper (uppercase `.tier.name`, `.verdict.value.upper()`, no `confidence`); added `json_output` Typer option; added `--json` short-circuit branch after Ollama error handlers, before all human output; updated `adjudicate` docstring |
| `tests/unit/cli/test_adjudicate.py` | Modified | Added `import json`; added 9 new tests: 4 for `_adjudication_payload` (empty, single, mixed, same_only), 5 for CLI `--json` wiring (success/suppression, same-only composability, both empty-state guards, error-path non-interference) |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1/1.2 | `tests/unit/cli/test_adjudicate.py` | Unit | ✅ 36/36 (pre-existing) | ✅ Written (ImportError confirmed) | ✅ 4/4 Passed | ✅ 4 cases (empty, single, mixed, same_only) | ➖ None needed |
| 2.1/2.2 | `tests/unit/cli/test_adjudicate.py` | Unit (CliRunner) | ✅ 40/40 (after Task 1) | ✅ Written (exit 2 confirmed) | ✅ Passed | ➖ Single scenario (mixed verdicts covers shape+suppression) | ➖ None needed |
| 3.1/3.2 | `tests/unit/cli/test_adjudicate.py` | Unit (CliRunner) | ✅ 41/41 (after Task 2) | ✅ Written (verification RED — passed immediately, existing coverage confirmed) | ✅ Passed | ➖ Single scenario | ➖ None needed |
| 4.1/4.2 | `tests/unit/cli/test_adjudicate.py` | Unit (CliRunner) | ✅ 42/42 (after Task 3) | ✅ Written (verification RED — both guards, passed immediately) | ✅ Passed | ✅ 2 cases (no-candidates guard + same-only-empty guard) | ➖ None needed |
| 5.1/5.2 | `tests/unit/cli/test_adjudicate.py` | Unit (CliRunner) | ✅ 44/44 (after Task 4) | ✅ Written (verification RED — passed immediately, ordering confirmed correct) | ✅ Passed | ➖ Single scenario | ➖ None needed |
| 6.1 | Full suite | Unit/Integration | ✅ 45/45 (adjudicate file) | N/A (confirmation step) | ✅ 2002/2002 (full suite) | N/A | N/A |
| 7.1-7.4 | N/A | N/A | N/A | N/A | ✅ pytest/ruff/ruff-format/mypy all clean | N/A | N/A |

### Test Summary
- **Total tests written**: 9 new (4 payload unit + 5 CLI integration/CliRunner)
- **Total tests passing**: 45/45 in `test_adjudicate.py`; 2002/2002 full suite
- **Layers used**: Unit (9)
- **Approval tests** (refactoring): None — no refactoring tasks; this is purely additive
- **Pure functions created**: 1 (`_adjudication_payload`)

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_adjudicate.py -q` → 45 passed |
| Runtime harness command/scenario and exact result | `uv run pytest -q` (full suite, proving non-json byte-identical behavior across all consumers) → 2002 passed |
| Rollback boundary | Revert the single commit touching `src/openkos/cli/main.py` (import, option, helper, branch, docstring) and `tests/unit/cli/test_adjudicate.py` (9 new tests) — fully additive, no other file touched |

## Deviations from Design

None — implementation matches design exactly. `tier` uses `.name` (uppercase), never `.value`; `verdict` uses `.value.upper()`; no `confidence` or survivor/absorbed field; branch placement after all three Ollama error handlers and before the workspace echo, preceding both prose empty-guards.

## Issues Found

None. Tasks 3, 4, and 5 RED steps passed on first execution (as tasks.md anticipated) because Task 2's single short-circuit branch, combined with `_adjudication_payload`'s `same_only` filter, already satisfied all downstream composability, empty-state, and error-path requirements — no additional production code was needed beyond Task 2.2.

## Remaining Tasks

None. All 15 sub-tasks (1.1 through 7.4) complete.

## Workload / PR Boundary

- Mode: single PR
- Current work unit: adjudicate-json (#137, Slice 2a) — complete
- Boundary: entire change (payload builder + CLI wiring + full test suite) in one PR, as forecast in tasks.md
- Estimated review budget impact: well under 800-line budget (production ~30 lines net, tests ~230 lines across 9 new tests)

## Status

15/15 tasks complete. Ready for verify.
