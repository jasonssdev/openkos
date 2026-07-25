# Apply Progress: set-volatility Write Verb (#140)

**Status**: 20/20 tasks complete (all 4 phases). Ready for verify.
**Mode**: Strict TDD (RED confirmed failing before every GREEN implementation).

## Completed Tasks

All tasks in `tasks.md` are marked `[x]` — see that file for the full list across:
- Phase 1: Pure Core — `config.set_type_tier` (1.1-1.6)
- Phase 2: CLI Verb — `set-volatility` (2.1-2.12)
- Phase 3: `suggest-volatility` Hint Update (3.1-3.2)
- Phase 4: Non-Regression + Quality Gate (4.1-4.2)

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/config.py` | Modified | Added pure `set_type_tier(yaml_text, concept_type, tier) -> str` comment-safe text-surgery core (3 edit cases a/b/b-empty/c + 6 fail-closed shapes) plus private helpers (`_TypeTierEntry`, `_parse_type_tiers_block`, `_append_fresh_type_tiers_block`, `_validate_type_tier_vocab`, `_split_line_ending`); added `from openkos.model import types` |
| `src/openkos/cli/main.py` | Modified | Added `set-volatility` command (mirrors `relate`); updated `suggest-volatility`'s trailing hint line and its docstring reference; added `from openkos.model import types` |
| `tests/unit/test_config.py` | Modified | 16 new tests for `set_type_tier`: 3 edit cases + empty-block + idempotent-identity + 8 fail-closed-shape fixtures (parametrized) + 2 vocab-rejection tests |
| `tests/unit/cli/test_set_volatility.py` | Created | 12 tests: invalid tier/type rejection, unparseable-shape refusal, idempotence (incl. registry-default-override-is-real-write), preview format, confirm-gate matrix, successful write + autocommit message |
| `tests/unit/cli/test_suggest_volatility.py` | Modified | Updated hint assertion (approval-test-style refactor of an existing test) |
| `openspec/changes/set-volatility/tasks.md` | Modified | All 20 tasks marked `[x]` |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1-1.5 | `tests/unit/test_config.py` | Unit | ✅ 80/80 (pre-existing config tests) | ✅ Written (16 tests, `AttributeError` on missing `set_type_tier`) | ✅ 16/16 passed | ✅ 3 cases + empty-block + idempotent + 8 fail-closed fixtures | ✅ Clean |
| 1.6 | `src/openkos/config.py` | — | — | — | ✅ implementation | — | — |
| 2.1-2.2 | `tests/unit/cli/test_set_volatility.py` | Unit (CLI) | ✅ N/A (new file) | ✅ Written (exit code 2, unknown command) | ✅ passed after 2.3 | ➖ 2 distinct rejection cases | ✅ Clean |
| 2.4 | `tests/unit/cli/test_set_volatility.py` | Unit (CLI) | ✅ | ✅ Written | ✅ passed after 2.5 | ➖ Single fixture | ✅ Clean |
| 2.6 | `tests/unit/cli/test_set_volatility.py` | Unit (CLI) | ✅ | ✅ Written | ✅ passed after 2.7 | ✅ idempotent-noop + registry-default-override-is-real-write | ✅ Clean |
| 2.8-2.9 | `tests/unit/cli/test_set_volatility.py` | Unit (CLI) | ✅ | ✅ Written | ✅ passed after 2.10 | ✅ 4 confirm-gate cases (--auto/non-TTY/decline/accept) | ✅ Clean |
| 2.11 | `tests/unit/cli/test_set_volatility.py` | Unit (CLI) | ✅ | ✅ Written | ✅ passed after 2.12 | ➖ Single (reuses Phase 1 core) | ✅ Clean |
| 3.1 | `tests/unit/cli/test_suggest_volatility.py` | Unit (CLI) | ✅ 17/17 (pre-existing suggest-volatility tests) | ✅ Written (assertion updated to new hint text, confirmed failing) | ✅ passed after 3.2 | ➖ Single line change | ✅ Clean |

### Test Summary
- **Total tests written/modified**: 16 (config) + 12 (CLI new file) + 1 (suggest-volatility hint, modified) = 29
- **Total tests passing**: 2051/2051 (full suite)
- **Layers used**: Unit (29 new/modified), all pure-function or `CliRunner`-based
- **Approval tests** (refactoring): 1 (`test_suggest_volatility_renders_tier_type_and_rationale` — hint text updated per spec-mandated behavior change)
- **Pure functions created**: 1 public (`set_type_tier`) + 4 private helpers, all pure text-in/text-out, no I/O

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/test_config.py -k set_type_tier -q` → 16 passed; `uv run pytest tests/unit/cli/test_set_volatility.py -q` → 15 passed; `uv run pytest tests/unit/cli/test_suggest_volatility.py -q` → 17 passed |
| Runtime harness command/scenario and exact result | `CliRunner` + `tmp_path` workspace + TTY simulation (mirrors `test_relate.py`), exercising real `init` → `set-volatility` → `git log` round trip; all green |
| Rollback boundary | `set_type_tier` (config.py) is additive-only, revertable independently; `set-volatility` command block (main.py) is a new, isolated command, revertable independently; the `suggest-volatility` hint line is a single-line change, trivially revertable |

## Deviations from Design

None — implementation matches design exactly, including the `openkos: set-volatility <Type> -> <tier>` commit-message prefix (the design's "Open Questions" note was resolved in favor of the `openkos:`-prefixed form, matching every other verb).

## Issues Found

None.

## Workload / PR Boundary

- Mode: single PR (not a chain)
- Current work unit: N/A — all 3 suggested work units (pure core, CLI verb, hint update) landed together in this single apply batch
- Boundary: this apply batch starts from an unmodified `config.py`/`main.py` and ends with all 20 tasks complete, full suite green
- Estimated review budget impact: comfortably under the 800-line budget per the tasks.md forecast (Low risk, no chaining needed)

## Quality Gate (verbatim)

- `uv run pytest` → **2051 passed** in 110.42s (0:01:50)
- `uv run ruff check .` → **All checks passed!**
- `uv run ruff format --check .` → **134 files already formatted**
- `uv run mypy .` → **Success: no issues found in 134 source files**

## Status

20/20 tasks complete. Ready for verify.
