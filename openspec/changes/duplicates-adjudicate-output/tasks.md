# Tasks: duplicates / adjudicate output ergonomics (#139, Slice 1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~180-220 (production ~40-50; tests dominate ~140-170) |
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
| 1 | Two pure tally helpers + wiring into `duplicates`/`adjudicate` + full test coverage | PR 1 | `uv run pytest tests/unit/cli/test_duplicates.py tests/unit/cli/test_adjudicate.py -q` | `uv run openkos duplicates` / `uv run openkos adjudicate` against a sample bundle | Revert the single commit; helpers and echo lines are additive, no callers elsewhere |

## Phase 1: Helper Unit Tests (RED) + Implementation (GREEN)

- [x] 1.1 RED: add `test_format_group_tally_empty_returns_empty_string` in a new/existing helper test module — `_format_group_tally(0, 0) == ""`.
- [x] 1.2 RED: add `test_format_group_tally_single_high` — `_format_group_tally(1, 0) == "1 candidate group (1 exact, 0 near)"`.
- [x] 1.3 RED: add `test_format_group_tally_single_low` — `_format_group_tally(0, 1) == "1 candidate group (0 exact, 1 near)"`.
- [x] 1.4 RED: add `test_format_group_tally_mixed_plural` — `_format_group_tally(2, 3) == "5 candidate groups (2 exact, 3 near)"`.
- [x] 1.5 GREEN: implement `_format_group_tally(high: int, low: int) -> str` in `src/openkos/cli/main.py:356-373` (sibling to `_format_type_tally`, reuse `_plural`); run 1.1-1.4 to green.
- [x] 1.6 RED: add `test_format_verdict_tally_empty_returns_empty_string` — `_format_verdict_tally(0, 0, 0) == ""`.
- [x] 1.7 RED: add `test_format_verdict_tally_same_and_different_only` — `_format_verdict_tally(2, 1, 0) == "adjudicated 3: 2 SAME, 1 DIFFERENT"` (no UNCERTAIN segment).
- [x] 1.8 RED: add `test_format_verdict_tally_with_uncertain` — `_format_verdict_tally(2, 1, 1) == "adjudicated 4: 2 SAME, 1 DIFFERENT, 1 UNCERTAIN"`.
- [x] 1.9 RED: add `test_format_verdict_tally_all_same` and `test_format_verdict_tally_all_different` — `(3,0,0)` and `(0,3,0)` cases.
- [x] 1.10 GREEN: implement `_format_verdict_tally(same: int, different: int, uncertain: int) -> str` in `src/openkos/cli/main.py:356-373`; run 1.6-1.9 to green.

## Phase 2: `duplicates` Command Wiring (RED) + Implementation (GREEN)

- [x] 2.1 Re-read `tests/unit/cli/test_duplicates.py` in full before editing (confirm all current asserts are `in result.stdout` substrings, per design fact).
- [x] 2.2 RED: add test asserting the tally line `"N candidate group(s) (X exact, Y near)"` is the first stdout line for a mixed HIGH/LOW fixture (`main.py:3582-3591`).
- [x] 2.3 RED: add test asserting the legend line `"Legend: [tier] type -- trigger (HIGH = exact normalized key, LOW = near-match score)"` appears exactly once, before the first group's detail lines, for a multi-group fixture.
- [x] 2.4 RED: add test asserting the last stdout line is `"Next: openkos merge <survivor> <absorbed>"` when ≥1 group is found.
- [x] 2.5 RED: add test asserting the zero-groups path's stdout is exactly `"No candidates found."` with no tally/legend/`Next:` lines (extend/reuse existing empty-bundle test).
- [x] 2.6 GREEN: wire tally (via `_format_group_tally` fed by tier counts over `groups`) + legend + `Next:` echo into `duplicates` at `main.py:3582-3591`, respecting the existing empty guard; run 2.2-2.5 to green.

## Phase 3: `adjudicate` Command Wiring (RED) + Implementation (GREEN)

- [x] 3.1 Re-read `tests/unit/cli/test_adjudicate.py` in full before editing (confirm substring-only asserts on stdout, `==` only on stderr, per design fact).
- [x] 3.2 RED: add test asserting first stdout line `"adjudicated N: x SAME, y DIFFERENT"` for mixed SAME/DIFFERENT results with zero UNCERTAIN (no `UNCERTAIN` segment present).
- [x] 3.3 RED: add test asserting `", z UNCERTAIN"` segment present when results include ≥1 UNCERTAIN verdict.
- [x] 3.4 RED: add test asserting the tally counts the FULL `results` set (not the `--same-only`-filtered `displayed` set) when `--same-only` is passed with mixed verdicts.
- [x] 3.5 RED: add test asserting the legend line (verdict/confidence/rationale columns) appears exactly once, before the first result's detail lines.
- [x] 3.6 RED: add test asserting the last stdout line is `"Next: openkos merge <survivor> <absorbed>"` when ≥1 result is displayed.
- [x] 3.7 RED: add test asserting BOTH empty guards (`if not results` -> `"No candidates found."` at `:3710-3712`, and `if not displayed` -> `"No SAME-verdict candidates to display (--same-only)."` at `:3717-3719`) suppress tally/legend/`Next:` entirely.
- [x] 3.8 GREEN: import/use `Counter` (already imported `main.py:7` — do NOT re-import) to compute verdict counts over full `results`; wire tally + legend + `Next:` echo into `adjudicate` at `main.py:3708-3733`, after both empty guards, before the loop; run 3.2-3.7 to green.

## Phase 4: Non-Regression and Quality Gates

- [x] 4.1 Run full `uv run pytest tests/unit/cli/test_duplicates.py tests/unit/cli/test_adjudicate.py -q` and confirm every pre-existing substring/exit-code assertion still passes unchanged.
- [x] 4.2 Run full `uv run pytest -q` to catch any cross-suite regression.
- [x] 4.3 Run `ruff check .` and `ruff format --check .`; fix any violations.
- [x] 4.4 Run `mypy` (project-configured target) and resolve any new typing errors from the two helpers.
