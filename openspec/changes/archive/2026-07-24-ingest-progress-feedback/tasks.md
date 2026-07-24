# Tasks: Ingest Progress Feedback (spinner + per-type tally)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~120-160 (production ~25-30 lines in `main.py`; tests dominate) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `_format_type_tally` helper + tally line in `ingest()` + spinner wrap, all test-first | PR 1 | `uv run pytest tests/unit/cli/test_ingest.py -k "tally or spinner"` | `uv run openkos ingest <path>` manual TTY smoke (spinner visible) + piped smoke (clean stdout) | Revert single commit; both signals are additive, no schema/data change |

## Phase 1: RED — `_format_type_tally` helper tests

- [x] 1.1 In `tests/unit/cli/test_ingest.py`, add failing tests for `_format_type_tally`: `{}` -> `""`; `{"Concept": 1}` -> `"extracted 1 object — 1 Concept"`; `{"Entity": 3}` -> `"extracted 3 objects — 3 Entity"`; `{"Person": 2, "Concept": 1}` -> `"extracted 3 objects — 1 Concept, 2 Person"` (canonical `_TYPE_TO_SECTION` order, not insertion order). Import target as `main._format_type_tally`.
- [x] 1.2 Run `uv run pytest tests/unit/cli/test_ingest.py -k format_type_tally` and confirm all four fail (`AttributeError`/`ImportError`) — RED confirmed.

## Phase 2: GREEN — implement `_format_type_tally`

- [x] 2.1 In `src/openkos/cli/main.py`, add `_format_type_tally(counts: dict[str, int]) -> str` near `_plural` (after line 351): compute `total = sum(counts.values())`, return `""` if `total == 0`, else order keys by `list(_TYPE_TO_SECTION).index(t)`, join `f"{counts[t]} {t}"` with `", "`, return `f"extracted {total} object{_plural(total)} — {parts}"`.
- [x] 2.2 Run `uv run pytest tests/unit/cli/test_ingest.py -k format_type_tally` and confirm all four pass — GREEN confirmed.

## Phase 3: RED — tally-line emission in `ingest()`

- [x] 3.1 Add failing CliRunner test: `ingest` with zero derived objects (Source-only degrade) asserts NO `extracted ... objects` substring in `result.stdout`.
- [x] 3.2 Add failing CliRunner test: one derived `Concept` asserts `result.stdout` contains `"extracted 1 object — 1 Concept"`.
- [x] 3.3 Add failing CliRunner test: mixed derived types (e.g. `Person`, `Concept`, `Event` written in that order) asserts `result.stdout` contains the tally line in canonical `_TYPE_TO_SECTION` order (`Concept`, `Event`, `Person`), not write order.
- [x] 3.4 Run the three new tests and confirm all fail — RED confirmed.

## Phase 4: GREEN — implement tally emission

- [x] 4.1 In `src/openkos/cli/main.py`, add `from collections import Counter` import.
- [x] 4.2 After the existing `typer.echo(...)` at line ~922-925 (before `_autocommit` at ~927), add: `if derived_plans: typer.echo(_format_type_tally(Counter(p.doc_type for p in derived_plans)))`.
- [x] 4.3 Run the three tests from Phase 3 and confirm all pass — GREEN confirmed.

## Phase 5: RED — spinner tests

- [x] 5.1 Add failing test: default (non-TTY) `CliRunner` invocation of `ingest` asserts `result.exit_code` unchanged (matches pre-change baseline) and `result.stdout` contains no `\x1b[` / spinner control chars.
- [x] 5.2 Add failing test: spy `monkeypatch.setattr("openkos.cli.main.Console", ...)` (fake `Console` returning a fake context-manager status object); run `ingest` with a normal `_patch_llm` success reply; assert the fake `Console` was constructed with `stderr=True`, `.status(...)` was entered, and `__exit__` was called (spinner cleared on success).
- [x] 5.3 Add failing test: same `Console` spy seam, drive `_patch_llm(raises=OllamaUnavailable("boom"))`; assert `.status(...)` `__exit__` was called (spinner cleared on error) and `ingest` proceeds to existing Source-only degrade stdout/stderr behavior unchanged.
- [x] 5.4 Run the three new tests and confirm all fail — RED confirmed.

## Phase 6: GREEN — implement spinner wrap

- [x] 6.1 In `src/openkos/cli/main.py`, add `from rich.console import Console` import.
- [x] 6.2 Inside `_stage_derived_objects`, wrap the existing `try: extractions = extract_concept(...)` block (lines 493-501) so the `extract_concept` call runs inside `with Console(stderr=True).status("openkos ingest: extracting concepts…"):`, constructed per-call, with the `except OllamaError` handler unchanged and still inside the same `try`.
- [x] 6.3 Run the three tests from Phase 5 and confirm all pass — GREEN confirmed.

## Phase 7: Non-Regression and Quality Gate

- [x] 7.1 Run the full `tests/unit/cli/test_ingest.py` suite (`uv run pytest tests/unit/cli/test_ingest.py`) and confirm every pre-existing stdout-substring assertion and exit code still passes unchanged.
- [x] 7.2 Run the full test suite `uv run pytest`, then `ruff check`, `ruff format --check`, and `mypy`; all MUST be green before `sdd-verify`.
