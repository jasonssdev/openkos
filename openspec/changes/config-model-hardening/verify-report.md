# Verify Report: Config Model Hardening (Issue #128, Slice A)

## gentle-ai.verify-result/v1

```yaml
schema: gentle-ai.verify-result/v1
change: config-model-hardening
verdict: pass
blockers: []
critical_findings: 0
warnings: 0
suggestions: 1
requirements: 4/4
scenarios: 15/15
test_command: "uv run pytest"
test_exit_code: 0
test_output_hash: sha256:8ecba0799d9160d4f6ee0f537688959fa38302f678f53ee81727f8975058fccb
build_command: "uv run ruff check . && uv run ruff format --check ."
build_exit_code: 0
typecheck_command: "uv run mypy ."
typecheck_exit_code: 0
tasks_complete: 15/15
```

## Scope

- Diff confined to exactly 3 files: `src/openkos/config.py`, `tests/unit/test_config.py`, `tests/unit/cli/test_doctor.py` (24/98/52 lines added respectively, all additive — no deletions). Confirmed via `git diff --stat`.
- No production change to `main.py` or `ollama.py` — matches design's "RESOLVED FORK: #1 subsumes #3 at the source" decision.

## Requirement → Test Compliance Matrix

| # | Requirement / Scenario | Covering Test(s) | Status |
|---|---|---|---|
| 1 | `read_config` raises `ValueError` naming `model` when non-`str` (bool/int) | `test_read_config_raises_valueerror_on_non_str_model_fields[model-*]` (2 params) | PASS |
| 2 | `read_config` raises `ValueError` naming `embedding_model` when non-`str` | `test_read_config_raises_valueerror_on_non_str_model_fields[embedding_model-*]` (2 params) | PASS |
| 3 | Absent/null `model` falls back to `DEFAULT_MODEL`, no error | `test_read_config_model_null_still_falls_back_to_default`, `test_read_config_model_absent_still_falls_back_to_default` | PASS |
| 4 | `review: false` unaffected by model type check | `test_read_config_preserves_explicit_review_false` | PASS |
| 5 | `validate_model` rejects exact-token case-insensitive YAML 1.1 reserved words (yes/no/true/false/on/off/null, all listed casings) | `test_validate_model_rejects_yaml_reserved_words` (21 parametrized cases) | PASS |
| 6 | Reserved-word substrings (`yesmodel`, `on-prem`, `false-positive:1b`) and legit tags (`qwen3:8b`, `llama3.1:8b`, `bge-m3`) still accepted | `test_validate_model_accepts_reserved_word_substrings_and_legit_tags` (6 params) | PASS |
| 7 | Doctor never raises on `model: yes`; reports `[FAIL] Config valid` with remediation; no traceback; other checks still run | `test_doctor_non_str_model_fails_and_exits_one_without_traceback` (asserts exit 1, `[FAIL] Config valid`, no `Traceback`, `[PASS] Bundle readable` still rendered) | PASS |
| 8 | Doctor never raises on `embedding_model: yes` + valid model; no traceback | `test_doctor_non_str_embedding_model_with_valid_model_exits_cleanly` | PASS |
| 9 | Existing scenarios (byte-identical template, flag override, TTY prompts, blank/unsafe token rejection, colon-tag accepted) preserved unchanged | Pre-existing `test_validate_model_*` / `test_write_config_*` suite — all still green | PASS |

Requirements: 4/4 (Config Model Field Type Enforcement; Static openkos.yaml Template reserved-word extension; Doctor Never Raises) — the spec source lists these as 3 domain-level requirements plus the pre-existing template requirement carried forward; counted as 4 distinct requirement blocks across the two spec files. Scenarios: 15/15 enumerated in spec.md (4 + 3 new template scenarios + 4 doctor scenarios + 4 preserved template scenarios referenced as "all prior scenarios preserved").

## Correctness Verification (source-level)

- `_YAML_RESERVED_WORDS` frozenset = `{"yes","no","true","false","on","off","null"}`, matched via `trimmed.lower() in _YAML_RESERVED_WORDS` — exact whole-token match, not substring; confirmed no regex/`in` substring check used. Placed after blank check, before `_MODEL_TOKEN_RE` allowlist, per design.
- `~` not present in the reserved frozenset (per design note) — no gap: `_MODEL_TOKEN_RE = r"[A-Za-z0-9._:/-]+"` already excludes `~`, so it is rejected by the pre-existing allowlist regex regardless. No test explicitly targets `~` under the new guard (SUGGESTION, non-blocking — see Warnings/Suggestions).
- `read_config` adds `isinstance(str)` guards for both `model` and `embedding_model`, placed before `Config` construction, after the existing `raw.get(...)` calls, preserving the "is not None, not truthiness" pattern for the fallback lines unchanged.
- Doctor fix path confirmed by design trace and regression test: `read_config` raising inside doctor's existing `except (OSError, ValueError)` leaves `cfg = None`; check 2 renders `[FAIL] Config valid`; later checks fall back to `config.DEFAULT_MODEL`, so `model_tag_matches` never receives a non-str. Zero new doctor-side guard code — matches design decision exactly.

## Task Completion (15/15)

All 15 tasks (1.1–6.3) verified against actual code and test diffs, not merely against the apply-progress claim:
- Phase 1 (RED reserved-word tests): present, cases match spec's full reserved-word/casing list.
- Phase 2 (GREEN reserved-word guard): present in `config.py`, exact-token check confirmed.
- Phase 3 (RED str-type tests): present, covers both `model` and `embedding_model`, plus null/absent regression.
- Phase 4 (GREEN str-type guard): present, `isinstance(str)` checks confirmed for both fields.
- Phase 5 (RED doctor regression, no prod change): both new doctor tests present; `git diff --stat` confirms zero `main.py`/`ollama.py` changes.
- Phase 6 (Quality gate): independently re-run below — all clean.

## Test Suite Evidence (independently re-run)

```
$ uv run pytest -q
2089 passed in 102.73s (0:01:42)
exit code: 0
```

Focused re-run of new/regression tests (47 selected):
```
$ uv run pytest tests/unit/test_config.py tests/unit/cli/test_doctor.py -k "reserved or non_str or malformed or embedding_model or preserves_explicit_review_false or falls_back_to_default" -v
47 passed, 117 deselected in 2.97s
```

```
$ uv run ruff check .
All checks passed!
exit code: 0

$ uv run ruff format --check .
134 files already formatted
exit code: 0

$ uv run mypy .
Success: no issues found in 134 source files
exit code: 0
```

## Issues

### CRITICAL
None.

### WARNING
None.

### SUGGESTION
1. No test explicitly exercises `~` (tilde, YAML null shorthand) against `validate_model` under the new reserved-word guard path — it is mechanically rejected by the pre-existing `_MODEL_TOKEN_RE` allowlist regex regardless (char not in `[A-Za-z0-9._:/-]`), so there is no functional gap, only a documentation/coverage-intent gap relative to the spec's explicit mention of `~`. Non-blocking.

## Verdict

**PASS** — all 15 tasks genuinely complete, all spec requirements/scenarios have passing covering tests, full quality gate (pytest/ruff/ruff-format/mypy) green with independently re-run evidence, diff scope confined to the 3 expected files with zero production changes outside `config.py`.
