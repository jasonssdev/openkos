# Apply Progress: init-embedding-model-choice

## Scope of this batch

PR1 only — Phase 1: Foundation — `config.py` + template. Phases 2-5 (llm/ollama.py,
state/reindex.py, cli/main.py) are explicitly out of scope for this run.

## Mode

Strict TDD (RED → GREEN → REFACTOR per task).

## Completed Tasks

- [x] 1.1 RED: `tests/unit/test_config.py::test_default_embedding_model_in_allowlist`
- [x] 1.2 GREEN: `EMBEDDING_MODEL_ALLOWLIST: tuple[str, ...] = (DEFAULT_EMBEDDING_MODEL,)` in `config.py`
- [x] 1.3 RED: `validate_embedding_model` parity tests (trim/colon, unsafe values, YAML indicators, reserved words, off-allowlist acceptance)
- [x] 1.4 GREEN: extracted `_validate_model_token(tag, field)`; `validate_model`/`validate_embedding_model` both delegate to it
- [x] 1.5 RED: `write_config` dual-placeholder substitution + independent placeholder-count guard tests
- [x] 1.6 GREEN: `write_config(root, model=DEFAULT_MODEL, embedding_model=DEFAULT_EMBEDDING_MODEL)` — validates and substitutes both placeholders independently
- [x] 1.7 GREEN: added `embedding_model: __OPENKOS_EMBEDDING_MODEL__  # 1024-dim; changing it forces a full re-embed` under `model:` in `openkos.yaml.template`
- [x] 1.8 GREEN: corrected `Config`'s docstring (no longer claims `embedding_model` is absent from the template); also corrected `DEFAULT_EMBEDDING_MODEL`'s own docstring, which made the same now-stale claim
- [x] 1.9 GREEN (collateral): updated `tests/unit/test_config.py`'s `_expected_config_bytes` helper to substitute both placeholders, and extended it with `embedding_model` param; added corresponding new tests. See Deviations — the actual byte-identity assertions live in `test_config.py`, not `test_init.py`.

## Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/config.py` | Modified | Added `EMBEDDING_MODEL_ALLOWLIST`; extracted `_validate_model_token(tag, field)`; added `validate_embedding_model`; extended `write_config` with `embedding_model` param + independent placeholder guard; corrected `Config` and `DEFAULT_EMBEDDING_MODEL` docstrings |
| `src/openkos/templates/openkos.yaml.template` | Modified | Added `embedding_model: __OPENKOS_EMBEDDING_MODEL__` line under `model:` |
| `tests/unit/test_config.py` | Modified | Added RED tests for 1.1/1.3/1.5, plus embedding-model write/rejection/placeholder-guard tests; updated `_expected_config_bytes` helper to handle both placeholders |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1/1.2 | `tests/unit/test_config.py` | Unit | ✅ 131/131 (baseline) | ✅ `AttributeError: no attribute 'EMBEDDING_MODEL_ALLOWLIST'` | ✅ 1/1 passed | ➖ Single (structural, no branching) | ➖ None needed |
| 1.3/1.4 | `tests/unit/test_config.py` | Unit | ✅ 132/132 | ✅ 30/30 failed on `AttributeError: no attribute 'validate_embedding_model'` | ✅ 30/30 passed | ✅ 4 categories (trim/colon, unsafe chars, YAML indicators, reserved words, off-allowlist) | ✅ Extracted shared `_validate_model_token`; existing `validate_model` tests re-verified green (no regression) |
| 1.5/1.6/1.7 | `tests/unit/test_config.py` | Unit | ✅ 162/162 | ✅ 10/10 failed (missing `embedding_model` param / placeholder / unraised ValueError) | ✅ 10/10 passed | ✅ custom value, both-custom, invalid rejection, missing placeholder, duplicated placeholder | ➖ None needed |
| 1.8 | `tests/unit/test_config.py` | Unit (docstring only) | ✅ 172/172 | N/A — docstring correction, no behavior change | N/A | Triangulation skipped: pure docstring text, zero branching | N/A |

### Test Summary
- **Total tests written**: 40 (1 allowlist + 7 parity groups ≈ 22 parametrized cases + 10 write_config)
- **Total tests passing**: 172/172 in `tests/unit/test_config.py` (up from 131 baseline)
- **Layers used**: Unit (40)
- **Approval tests** (refactoring): None — no refactoring of pre-existing behavior, only extension. `_validate_model_token` extraction was covered by re-running all pre-existing `validate_model` tests as regression guards (all 30 passed unchanged).
- **Pure functions created**: 2 (`_validate_model_token`, `validate_embedding_model`)

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/test_config.py -q` → `172 passed in 0.08s` |
| Runtime harness command/scenario and exact result | N/A — pure unit, no live Ollama needed (per tasks.md's own forecast for Unit 1) |
| Rollback boundary | Revert `src/openkos/config.py`, `src/openkos/templates/openkos.yaml.template`, and `tests/unit/test_config.py` only. New workspaces fall back to default-only (no `embedding_model:` line); already-written explicit keys still parse via `read_config`'s existing fallback. No other file touched. |

## Local Gate (full repo)

- `uv run pytest` → `2303 passed in 81.20s`
- `uv run pytest --cov` → `Required test coverage of 90.0% reached. Total coverage: 97.59%` (`2303 passed`)
- `uv run ruff check src tests` → `All checks passed!`
- `uv run ruff format --check src tests` → `140 files already formatted`
- `uv run mypy` → `Success: no issues found in 140 source files`

## Deviations from Design

1. **Task 1.9 wording correction**: task 1.9 as written says "update `tests/unit/cli/test_init.py` byte-identity assertions." I verified `test_init.py` has zero full-file byte-comparison assertions against the packaged template — it only does substring checks like `"model: gemma3" in content`, and its one snapshot-comparison test (`test_preflight_outcome_never_changes_written_files`) compares different outcomes of the *same* run against each other, not against a static template. All 61 of its tests pass unmodified after the template change. The actual byte-identity assertions that needed updating for the new template line live in `tests/unit/test_config.py` (`_expected_config_bytes`, `test_write_config_byte_identical`, `test_write_config_ignores_directory_name`, `test_write_config_custom_model`) — I updated those instead, which achieves the intended purpose of task 1.9 (keep byte-identity assertions accurate for the new template) without touching a file that had nothing to fix.
2. **`DEFAULT_EMBEDDING_MODEL`'s own docstring**: task 1.8 only names `Config`'s docstring at `config.py:344-347`, but `DEFAULT_EMBEDDING_MODEL`'s docstring (originally lines 24-30) made the identical stale claim ("not written to `openkos.yaml.template`"). Corrected it too for consistency — same root cause, same fix, no separate task needed.

## Issues Found

None.

## Remaining Tasks (out of scope for this run — PR2/PR3)

- [ ] Phase 2: `llm/ollama.py` dimension error (2.1–2.7)
- [ ] Phase 3: `state/reindex.py` fatal handling (3.1–3.5)
- [ ] Phase 4: `cli/main.py` wiring (4.1–4.17)
- [ ] Phase 5: Verification (5.1–5.3)

## Workload / PR Boundary

- Mode: stacked-to-main (chain strategy from tasks.md)
- Current work unit: Unit 1 — `config.py` allowlist/validator/write_config + template placeholder + docstring fix (PR1)
- Boundary: starts from a clean `config.py`/template with no embedding-model resolution surface; ends with `EMBEDDING_MODEL_ALLOWLIST`, `validate_embedding_model`, dual-placeholder `write_config`, and an honest `Config` docstring — all independently revertible per design rollback §1
- Estimated review budget impact: well under the 800-line session budget; diff is confined to `config.py`, the template, and `test_config.py`

## Status

9/9 Phase 1 tasks complete. Ready for verify (of PR1's slice) / ready for `sdd-apply` to continue with PR2 (Phase 2-3) in a subsequent run.
