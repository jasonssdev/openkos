# Apply Progress: `openkos init` — user-selectable local model

**Mode**: Strict TDD (RED → GREEN → REFACTOR, all phases)
**Batch**: First and only batch — all 14 tasks implemented.

## Completed Tasks

- [x] 1.1 RED — `validate_model` tests in `tests/unit/test_config.py`
- [x] 1.2 GREEN — `DEFAULT_MODEL` + `validate_model` in `src/openkos/config.py`
- [x] 2.1 Template placeholder — `src/openkos/templates/openkos.yaml.template` line 1 → `__OPENKOS_MODEL__`
- [x] 2.2 RED — byte-identical/ignores-directory-name updated to token-substituted expectation; `test_write_config_custom_model`, `test_write_config_rejects_invalid_model` added
- [x] 2.3 GREEN — `write_config(root, model=DEFAULT_MODEL)` substitution in `config.py`
- [x] 2.4 REFACTOR — `write_config` docstring rewritten to describe the single-token substitution invariant
- [x] 3.1 RED — flag/TTY-prompt/non-TTY/rejection tests in `tests/unit/cli/test_init.py`
- [x] 3.2 GREEN — `--model` option, `_resolve_model`, refusal wiring in `src/openkos/cli/main.py`
- [x] 3.3 REFACTOR — `init()` docstring documents `--model` resolution order and invalid-model refusal
- [x] 4.1 `docs/cli.md` — deferred-model sentence replaced with actual flag/prompt/default behavior
- [x] 5.1 `uv run pytest --cov` — 89 passed, 100% coverage
- [x] 5.2 `uv run ruff check .` / `ruff format --check .` — clean
- [x] 5.3 `uv run mypy .` — clean (strict mode)
- [x] 5.4 `uv build` + wheel smoke test — passed (default and `--model gemma3` both verified against the isolated wheel)

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/config.py` | Modified | Added `DEFAULT_MODEL`, `validate_model(tag)`, changed `write_config(root, model=DEFAULT_MODEL)` to substitute a single `__OPENKOS_MODEL__` placeholder via `str.replace`, updated docstring |
| `src/openkos/templates/openkos.yaml.template` | Modified | Line 1: `model: qwen3:8b` → `model: __OPENKOS_MODEL__`, trailing spaces + comment preserved verbatim |
| `src/openkos/cli/main.py` | Modified | Added `import sys`, `_resolve_model(flag)` (flag > TTY prompt > default), `--model` Typer option on `init()`, refusal wiring for invalid model before any write, updated docstring |
| `tests/unit/test_config.py` | Modified | Added `validate_model` tests (valid + rejected), redefined byte-identical/ignores-directory-name expectations, added `test_write_config_custom_model`, `test_write_config_rejects_invalid_model`, `test_write_config_raises_on_corrupt_template` |
| `tests/unit/cli/test_init.py` | Modified | Added `_simulate_tty` helper (patches `typer.testing._NamedTextIOWrapper.isatty`), flag/TTY-prompt-default/TTY-prompt-custom/non-TTY-silent-default/blank-unsafe-rejection tests |
| `docs/cli.md` | Modified | Replaced the "deferred" sentence with the actual `--model` flag / TTY-prompt / default (`qwen3:8b`) behavior and a flag table row |
| `openspec/changes/add-model-selection/tasks.md` | Modified | All 14 tasks marked `[x]` |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1/1.2 | `tests/unit/test_config.py` | Unit | ✅ 38/38 (baseline) | ✅ Written — `AttributeError: no attribute 'validate_model'` (10 failures) | ✅ 10/10 passed | ✅ 3 valid-case + 7 invalid-case parametrized inputs | ➖ None needed (already minimal) |
| 2.1-2.3 | `tests/unit/test_config.py` | Unit | ✅ 38/38 (baseline) | ✅ Written — 9 failures (`TypeError: unexpected keyword 'model'`, byte-mismatch) | ✅ 33/33 passed | ✅ default/custom/blank/unsafe/corrupt-template cases | ➖ None needed |
| 2.4 | n/a (docstring) | — | — | — | — | — | ✅ Docstring rewritten as part of GREEN commit for 2.3 |
| 3.1-3.2 | `tests/unit/cli/test_init.py` | Unit/CLI (Typer `CliRunner`) | ✅ 23/23 (baseline) | ✅ Written — 10 failures (`exit_code 2`: no such option `--model`) | ✅ 33/33 passed | ✅ flag / flag-wins-over-TTY / TTY-default / TTY-custom / non-TTY-default / 6x blank-unsafe cases | ➖ None needed |
| 3.3 | n/a (docstring) | — | — | — | — | — | ✅ Docstring rewritten as part of GREEN commit for 3.2 |
| coverage gap fix | `tests/unit/test_config.py` | Unit | ✅ 88/88 (post-3.2) | ✅ Written — `test_write_config_raises_on_corrupt_template` (new behavior: 0 tests exist for this branch) | ✅ 89/89 passed | ➖ Single (defensive packaging-invariant branch, one case sufficient) | ➖ None needed |

### Test Summary
- **Total tests written**: 27 new test functions/parametrizations (10 `validate_model` cases, 4 `write_config` model tests + 1 corrupt-template test, 12 CLI `--model` cases across 6 new test functions)
- **Total tests passing**: 89/89 (full suite)
- **Layers used**: Unit (config.py: 34 tests), CLI/Unit via Typer `CliRunner` (test_init.py: 33 tests)
- **Approval tests** (refactoring): None — no refactoring tasks, only additive behavior
- **Pure functions created**: 1 (`validate_model`) — `_resolve_model` is impure by design (reads `sys.stdin.isatty()`, calls `typer.prompt`), matching the design's split (pure validation in `config.py`, resolution/IO in `cli/main.py`)

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/test_config.py tests/unit/cli/test_init.py -q` → 67 passed (33 + 34, no failures) |
| Runtime harness command/scenario and exact result | `uv build` then `uv run --isolated --no-project --with dist/openkos-*.whl openkos init --model gemma3` in a scratch tmpdir → exit 0, `openkos.yaml` contains `model: gemma3` with every other field byte-identical to the template; repeated with plain `openkos init` (no flag) → `model: qwen3:8b`, byte-identical to today's static file. Both confirmed against the packaged wheel, not just the editable install. |
| Rollback boundary | Fully additive: `git diff` touches 6 files (`docs/cli.md`, `src/openkos/cli/main.py`, `src/openkos/config.py`, `src/openkos/templates/openkos.yaml.template`, `tests/unit/cli/test_init.py`, `tests/unit/test_config.py`), 271 insertions / 28 deletions, no persisted state anywhere else in the repo. `git checkout -- <files>` or a single revert removes the feature cleanly; no migration, no consumer reads `model:` back yet. |

## Full Verification Gate Output

- `uv run pytest --cov`: **89 passed**, coverage **100.00%** (required ≥90%)
- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: **18 files already formatted**
- `uv run mypy .`: **Success: no issues found in 18 source files**
- `uv build`: sdist + wheel built successfully (`openkos-0.1.0.tar.gz`, `openkos-0.1.0-py3-none-any.whl`)
- Wheel smoke test: `openkos --help` resolves via isolated wheel install; `openkos init --model gemma3` and plain `openkos init` both verified end-to-end against the isolated wheel in scratch directories; `dist/` removed after the smoke test (not left in the working tree)

## Deviations from Design

None — implementation matches design exactly:
- Validation lives in `config.py` (pure, `validate_model`), resolution lives in `cli/main.py` (`_resolve_model`), matching Decision 2.
- Colon is allowed; whitespace/quote/`#` rejected, matching Decision 3 and resolving the proposal/design contradiction the design already flagged.
- Single placeholder token + `str.replace`, never a YAML dumper, matching Decision 1. Packaging invariant (`template.count(placeholder) == 1`) is enforced with an explicit `raise ValueError`, not `assert` (S101 — `assert` is banned outside test files by this repo's ruff config), which the design's task text described as `assert` but the actual repo lint rules required a raise instead.
- No new package under `src/openkos/model/` — the LLM-tag logic stays entirely in `config.py`/`cli/main.py`, per Decision 2's explicit "keeps LLM-tag logic OUT of the OKF `src/openkos/model/` package" note.

## Issues Found

One deviation from the literal task text, not from the design: task 2.3 said "assert template.count(...) == 1" but the repo's ruff config bans bare `assert` in non-test source files (`S101`, only ignored under `[tool.ruff.lint.per-file-ignores]` for `tests/`). Implemented as an explicit `if ... : raise ValueError(...)` instead, preserving the same invariant and failure behavior (still raises before any write). Also added one extra test (`test_write_config_raises_on_corrupt_template`) beyond the task list to keep branch coverage at 100% instead of the 99% the raise-based invariant check would otherwise have left uncovered — this is additive test coverage, not a functional deviation.

## Remaining Tasks

None. All 14 tasks across all 5 phases are complete.

## Workload / PR Boundary

- Mode: single PR (forecast: `400-line budget risk: Low`, `Chained PRs recommended: No`)
- Current work unit: Unit 1 — "Land `--model` end-to-end (template, validation, write_config, CLI resolution, docs) as one slice"
- Boundary: starts from HEAD of `feat/model-selection` (post `docs: reconcile the commit-scope list with the codebase`), ends with all 5 phases complete and the full verification gate green
- Estimated review budget impact: 271 insertions + 28 deletions across 6 files (299 changed lines) — within the forecasted ~260-340 line estimate and the 400-line budget

## Status

14/14 tasks complete. Working tree left staged/unstaged (not committed) for orchestrator review, per instructions. Ready for `sdd-verify`.
