# Tasks: Config Model Hardening (Issue #128, Slice A)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~130-180 (prod ~20-30 in `config.py`; tests ~110-150 across `test_config.py` + `test_doctor.py`) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Harden `config.py` (`validate_model` reserved words + `read_config` str guard) with full RED/GREEN test coverage + doctor regression test | PR 1 | `uv run pytest tests/unit/test_config.py tests/unit/cli/test_doctor.py -k "reserved or type or malformed or embedding"` | `openkos doctor` in a scratch workspace with `model: yes` in `openkos.yaml` | Revert single commit in `src/openkos/config.py`; no schema/migration to unwind |

## Phase 1: RED — validate_model reserved-word rejection

- [x] 1.1 In `tests/unit/test_config.py`, add a parametrized test asserting `validate_model` raises `ValueError` for each exact-token, case-insensitive YAML 1.1 reserved word (`yes/Yes/YES/no/NO/true/True/TRUE/false/FALSE/on/On/off/OFF/null/NULL`). Confirm it fails for the right reason (no guard yet).
- [x] 1.2 In the same file, add a parametrized test asserting `validate_model` still ACCEPTS reserved-word substrings and legit tags (`yesmodel`, `on-prem`, `false-positive:1b`, `qwen3:8b`, `llama3.1:8b`, `bge-m3`).

## Phase 2: GREEN — validate_model reserved-word guard

- [x] 2.1 In `src/openkos/config.py::validate_model`, after the blank check and before the `_MODEL_TOKEN_RE` allowlist, add an exact-token (lowercased, fully trimmed) rejection against `frozenset({"yes","no","true","false","on","off","null"})`, raising `ValueError` naming the reserved-word rule.
- [x] 2.2 Run Phase 1 tests; confirm GREEN and no regression on existing `validate_model` scenarios (blank, unsafe chars, leading/trailing `:`/`-`).

## Phase 3: RED — read_config str-type enforcement

- [x] 3.1 In `tests/unit/test_config.py`, add a parametrized test asserting `read_config` raises `ValueError` naming `model` when the parsed value is non-str (e.g. `model: yes` -> bool `True`, `model: 8` -> int).
- [x] 3.2 Add the mirrored parametrized test for `embedding_model` (non-str raises `ValueError` naming `embedding_model`).
- [x] 3.3 Add/confirm regression tests: `model: null` / absent `model` still falls back to `DEFAULT_MODEL` (no error); `review: false` still survives untouched (reuse or extend `test_read_config_preserves_explicit_review_false`).

## Phase 4: GREEN — read_config str-type guard

- [x] 4.1 In `src/openkos/config.py::read_config`, add `isinstance(..., str)` checks for both `model` and `embedding_model`, slotted beside the existing `is not None` fallback logic, raising `ValueError` with an actionable message (name the field + expected type) when present but non-str.
- [x] 4.2 Run Phase 3 tests; confirm GREEN and no change to `review`/`default_sensitivity`/other-field fallback behavior.

## Phase 5: RED — doctor regression (subsumed defect, no prod change)

- [x] 5.1 In `tests/unit/cli/test_doctor.py`, following the existing `test_doctor_malformed_config_fails_and_exits_one` pattern, add a test: `openkos.yaml` with `model: yes` (bool) -> `doctor` exits 1, prints `[FAIL] Config valid` (or equivalent config-check label), no traceback, and later checks still render.
- [x] 5.2 Add a second test: `embedding_model: yes` with an otherwise valid `model` -> `doctor` still exits cleanly (no `TypeError`/traceback) — proves the residual crash path fix #3 closes via the `read_config` guard alone.
- [x] 5.3 Run both; confirm GREEN with zero `main.py`/`ollama.py` changes (Phase 4's guard alone subsumes the defect).

## Phase 6: Quality Gate

- [x] 6.1 `uv run pytest` — full suite green.
- [x] 6.2 `uv run ruff check . && uv run ruff format --check .` — clean.
- [x] 6.3 `uv run mypy .` — clean.
