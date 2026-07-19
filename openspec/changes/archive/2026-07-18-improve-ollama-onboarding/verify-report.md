# Verification Report: `improve-ollama-onboarding`

**Change**: actionable Ollama errors in `query` (Shape A)
**Mode**: Strict TDD, full artifact set (proposal/spec/design/tasks/apply-progress)
**Verdict**: PASS

## Completeness

| Item | Result |
|---|---|
| Tasks complete | 15/15 (`tasks.md`, all `[x]`; apply-progress confirms 15/15) |
| Unchecked tasks | None |

## Test / Build / Coverage Evidence

| Command | Exit | Result |
|---|---|---|
| `uv run pytest --cov=openkos --cov-report=term-missing` | 0 | 410 passed, 98.76% total coverage (≥90% required) |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 44 files already formatted |
| `uv run mypy .` | 0 | Success: no issues found in 44 source files |
| `uv run pytest -k test_llm_modules_do_not_import_config` | 0 | 1 passed — leaf-module discipline AST test intact |

`src/openkos/cli/main.py` coverage: 99% (uncovered lines 372, 479->481 are pre-existing/unrelated to this change, per apply-progress and confirmed by line inspection).

## Spec Compliance Matrix

Requirement: **LLM And Index Errors Map To Exit 1** (MODIFIED) — 1/1 requirement covered.

| Scenario | Covering test | Status |
|---|---|---|
| Ollama backend unreachable | `test_query_ollama_unavailable_maps_to_exit_one` | PASS |
| Configured model not installed | `test_query_model_not_found_maps_to_exit_one` | PASS |
| Other Ollama error | `test_query_generic_ollama_error_maps_to_exit_one` | PASS |
| FTS index unavailable | `test_query_fts_unavailable_maps_to_exit_one` | PASS |

**Requirements: 1/1. Scenarios: 4/4.**

Additional non-spec-mandated test present and passing: `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic` — direct proof of D1 handler ordering (specific-before-general), guards against future regressions where re-ordering handlers would silently swallow both subclasses into the generic tuple.

## Correctness Checks

| Check | Result |
|---|---|
| Except ordering specific-before-general (`OllamaUnavailable` → `OllamaModelNotFound` → `(FtsUnavailable, OllamaError)`) | Confirmed at `src/openkos/cli/main.py:707-723` — matches design's Interfaces snippet exactly |
| All three branches exit 1, no traceback | Confirmed — each uses `typer.echo(..., err=True)` + `raise typer.Exit(code=1) from exc`; tests assert `"Traceback" not in result.stderr` |
| Model-not-found message names the CONFIGURED model | Confirmed — `f"model '{cfg.model}' is not installed... ollama pull {cfg.model}"`, `cfg.model` in scope from Phase-A `read_config`; test uses a distinct configured tag (`llama3.2:1b-openkos-test`) not a hardcoded placeholder, proving real interpolation |
| Unavailable message references `ollama serve` and keeps host | Confirmed — `{exc}` (carries host from `OllamaUnavailable`'s own message) + appended `"Start it with \`ollama serve\`, then try again."` |
| `src/openkos/llm/ollama.py` unchanged | Confirmed — `git diff HEAD -- src/openkos/llm/ollama.py` is empty; leaf-module discipline preserved |
| `docs/cli.md` updated | Confirmed — L84 clause extended with actionable-guidance sentence, one-line prose only |
| Review workload | 126 changed lines total (main.py +20/-1, tests +98/-5, docs +1/-1) — well under 400-line budget, single PR as forecast |

## TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | Yes | Full RED/GREEN table present in apply-progress |
| All tasks have tests | Yes | 5/5 behavior rows have dedicated or extended test cases |
| RED confirmed (tests exist) | Yes | All listed test functions exist in `tests/unit/cli/test_query.py` |
| GREEN confirmed (tests pass) | Yes | 12/12 tests in `test_query.py` pass on independent re-run (within 410-test full suite) |
| Triangulation adequate | Yes | Ordering (D1) has a dedicated direct test in addition to the two extended branch tests |
| Safety net for modified files | Yes | Apply-progress reports 9 pre-existing tests stayed green through Phase 1 RED, confirmed by full-suite pass now |

**TDD Compliance**: 6/6 checks passed.

## Assertion Quality

No tautologies, no assertion-free tests, no ghost loops, no smoke-test-only patterns found in the reviewed test functions (`test_query_ollama_unavailable_maps_to_exit_one`, `test_query_model_not_found_maps_to_exit_one`, `test_query_generic_ollama_error_maps_to_exit_one`, `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic`, `test_query_fts_unavailable_maps_to_exit_one`). All assertions exercise `runner.invoke(app, ...)` (real production code path) and check concrete stderr substrings/exact strings, not implementation details.

**Assertion quality**: All assertions verify real behavior.

## Issues

**CRITICAL**: None.

**WARNING**: None.

**SUGGESTION**:
- No inline comment on the load-bearing except ordering in `cli/main.py` (accepted non-blocking follow-up per lineage review-e392b0c29a4fac29; not a blocker for this change).
- `result.exit_code != 0` is used instead of `result.exit_code == 1` in several tests (pre-existing pattern in this file, not introduced by this change; combined with `isinstance(result.exception, SystemExit)` it is sufficient to prove the `typer.Exit(code=1)` path was taken).

## Final Verdict

**PASS.** Requirements 1/1, Scenarios 4/4, TDD compliance 6/6, all four gate commands exit 0, `llm/ollama.py` leaf discipline preserved. Ready for `sdd-archive` after merge.
