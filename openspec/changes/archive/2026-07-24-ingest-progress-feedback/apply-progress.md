# Apply Progress: Ingest Progress Feedback (spinner + per-type tally)

**Mode**: Strict TDD
**Status**: All 7 phases / 20 tasks complete. Ready for `sdd-verify`.

## Completed Tasks

- [x] 1.1, 1.2 — RED: `_format_type_tally` unit tests (empty/singular/plural/canonical-order)
- [x] 2.1, 2.2 — GREEN: `_format_type_tally` implemented in `src/openkos/cli/main.py`
- [x] 3.1-3.4 — RED: `ingest()` tally-line emission CliRunner tests
- [x] 4.1-4.3 — GREEN: `Counter` import + tally echo wired into `ingest()`
- [x] 5.1-5.4 — RED: spinner tests (non-TTY silence, success clear, error clear via `Console` spy seam)
- [x] 6.1-6.3 — GREEN: `rich.console.Console` import + spinner wrap in `_stage_derived_objects`
- [x] 7.1, 7.2 — Non-regression + quality gate (full suite, ruff, mypy)

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/cli/main.py` | Modified | Added `Counter` import (stdlib) and `from rich.console import Console`; added `_format_type_tally(counts: dict[str, int]) -> str` helper near `_plural`; added tally `typer.echo(...)` call after the existing ingest summary echo (only when `derived_plans` non-empty); wrapped the blocking `extract_concept` call inside `_stage_derived_objects` in `with Console(stderr=True).status(...)`, inside the existing `try` so it clears on both success and `OllamaError` |
| `tests/unit/cli/test_ingest.py` | Modified | Added `from openkos.cli import main` import, `ClassVar` typing import; added 4 unit tests for `_format_type_tally`; added 3 CliRunner tests for tally-line emission (zero/one/mixed, canonical order); added 3 CliRunner tests for spinner behavior (non-TTY stdout cleanliness, success-path Console spy, error-path Console spy) using a `_FakeConsole`/`_FakeStatus` spy seam |

## TDD Cycle Evidence

| Task Group | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|---|---|---|---|---|---|---|---|
| `_format_type_tally` helper | `tests/unit/cli/test_ingest.py` | Unit | ✅ 76/76 baseline | ✅ Written (4 tests) | ✅ Passed (`uv run pytest -k format_type_tally` → 4 passed) | ✅ 4 cases (empty, singular, plural, canonical order) | ➖ None needed — implementation matched design snippet exactly |
| Tally-line emission in `ingest()` | `tests/unit/cli/test_ingest.py` | Integration (CliRunner) | ✅ 80/80 pre-Phase-3 | ✅ Written (3 tests) | ✅ Passed (`uv run pytest -k tally` → 7 passed) | ✅ 3 cases (zero/one/mixed) | ➖ None needed |
| Spinner wrap | `tests/unit/cli/test_ingest.py` | Integration (CliRunner + spy seam) | ✅ 83/83 pre-Phase-5 | ✅ Written (3 tests) | ✅ Passed (`uv run pytest -k spinner` → 3 passed) | ✅ 3 cases (non-TTY silence, success clear, error clear) | ➖ None needed |

### Test Summary
- **Total tests written**: 10 (4 tally-helper unit + 3 tally-emission CliRunner + 3 spinner CliRunner)
- **Total tests passing**: 10/10 new, 86/86 in `test_ingest.py`, 1973/1973 full suite
- **Layers used**: Unit (4), Integration/CliRunner (6)
- **Approval tests** (refactoring): None — no refactoring tasks, only additive code
- **Pure functions created**: 1 (`_format_type_tally`)

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_ingest.py -k "tally or spinner"` → 10 passed |
| Runtime harness command/scenario and exact result | `uv run pytest tests/unit/cli/test_ingest.py` (full file, CliRunner exercises real `openkos ingest` command end-to-end with fake LLM) → 86 passed |
| Rollback boundary | Single commit reverting `src/openkos/cli/main.py` (3 additive hunks: import, `_format_type_tally` def, tally echo, spinner wrap) and `tests/unit/cli/test_ingest.py` (10 new tests); no schema/data change, no existing line altered |

## Deviations from Design

None — implementation matches design exactly (helper signature, ordering source `_TYPE_TO_SECTION`, per-call `Console(stderr=True)` construction inside the existing `try`, spy-seam test strategy for spinner presence).

## Issues Found

None.

## Quality Gate (Final)

- `uv run pytest tests/unit/cli/test_ingest.py -q` → **86 passed**
- `uv run pytest -q` (full suite) → **1973 passed** in 97.21s
- `uv run ruff check .` → **All checks passed!** (one `RUF012` finding on `_FakeConsole.instances` fixed with `ClassVar` annotation)
- `uv run ruff format --check .` → **132 files already formatted** (one reformat applied to `src/openkos/cli/main.py` for line-wrap of the new `with` statement, then re-verified clean)
- `uv run mypy` → **Success: no issues found in 131 source files**

## Remaining Tasks

None — all 20 tasks across 7 phases complete.

## Workload / PR Boundary

- Mode: single PR (delivery_strategy=auto-forecast, forecast: Low risk, no chaining needed)
- Current work unit: Unit 1 — `_format_type_tally` helper + tally line + spinner wrap, all test-first
- Boundary: starts from baseline (76 passing tests, no tally/spinner code) and ends with full quality gate green (1973 tests, ruff, mypy) — complete, mergeable slice
- Estimated review budget impact: well under 400-line budget (touches only `main.py` + `test_ingest.py`, ~90 lines production-adjacent additions, tests dominate line count)

## Status

20/20 tasks complete. Ready for verify.
