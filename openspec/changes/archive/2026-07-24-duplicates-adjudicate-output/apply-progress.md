# Apply Progress: duplicates-adjudicate-output (#139, Slice 1)

**Mode**: Strict TDD
**Status**: 28/28 tasks complete. All done.

## Completed Tasks

All 28 tasks across Phase 1-4 are marked `[x]` in `tasks.md`:
- Phase 1 (1.1-1.10): `_format_group_tally` and `_format_verdict_tally` pure helpers, RED then GREEN.
- Phase 2 (2.1-2.6): `duplicates` command wiring — tally + legend + `Next:` hint, RED then GREEN.
- Phase 3 (3.1-3.8): `adjudicate` command wiring — verdict tally (full `results`, independent of `--same-only`) + legend + `Next:` hint, RED then GREEN.
- Phase 4 (4.1-4.4): full non-regression (1993 tests) + ruff + mypy, all green.

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/cli/main.py` | Modified | Added `_format_group_tally(high, low)` and `_format_verdict_tally(same, different, uncertain)` pure helpers (sibling to `_format_type_tally`, `main.py:376-401`). Wired tally + one-time legend + trailing `Next:` hint into `duplicates` (after the empty guard, before/after the group loop). Wired verdict tally (via `Counter`, already imported) + legend + `Next:` hint into `adjudicate` (after both empty guards, before/after the results loop). |
| `tests/unit/cli/test_duplicates.py` | Modified | Added 4 helper unit tests for `_format_group_tally` + 4 CLI wiring tests (tally-before-detail, legend-once, Next-last-line, empty-suppression). |
| `tests/unit/cli/test_adjudicate.py` | Modified | Added 5 helper unit tests for `_format_verdict_tally` + 8 CLI wiring tests (tally-before-detail, UNCERTAIN segment, `--same-only` full-count tally, legend-once, Next-last-line, both empty-guard suppressions). |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1-1.4 | `tests/unit/cli/test_duplicates.py` | Unit | ✅ 36/36 (pre-existing baseline) | ✅ Written (`AttributeError`, helper did not exist) | ✅ Passed | ✅ 4 cases (empty, single-high, single-low, mixed-plural) | ➖ None needed |
| 1.5 | `src/openkos/cli/main.py` | — | — | — | ✅ 4/4 green | — | ➖ Clean on first pass |
| 1.6-1.9 | `tests/unit/cli/test_adjudicate.py` | Unit | ✅ 36/36 (pre-existing baseline) | ✅ Written (`AttributeError`, helper did not exist) | ✅ Passed | ✅ 5 cases (empty, same+diff, +uncertain, all-same, all-diff) | ➖ None needed |
| 1.10 | `src/openkos/cli/main.py` | — | — | — | ✅ 5/5 green | — | ➖ Clean on first pass |
| 2.2-2.5 | `tests/unit/cli/test_duplicates.py` | Unit (CLI, CliRunner) | ✅ 20/20 pre-wiring baseline (16 pre-existing + 4 new helper) | ✅ Written (3/4 failed pre-implementation: tally-order, legend-once, Next-last-line; 1/4 — empty-suppression — passed trivially as an approval test since the guard already short-circuits) | ✅ Passed | ✅ mixed HIGH/LOW fixture, multi-group legend fixture, single-group Next fixture, zero-group fixture | ➖ None needed |
| 2.6 | `src/openkos/cli/main.py` | — | — | — | ✅ 20/20 green | — | ➖ Clean on first pass |
| 3.2-3.7 | `tests/unit/cli/test_adjudicate.py` | Unit (CLI, CliRunner) | ✅ 36/36 pre-wiring baseline (31 pre-existing + 5 new helper) | ✅ Written (6/8 failed pre-implementation: tally-order, UNCERTAIN segment, same-only full-count, legend-once, Next-last-line; 2/8 — both empty-guard suppressions — passed trivially as approval tests since the guards already short-circuit) | ✅ Passed | ✅ mixed no-UNCERTAIN, +UNCERTAIN, `--same-only` full-count, multi-result legend, single-result Next, both empty-guard paths | ➖ None needed |
| 3.8 | `src/openkos/cli/main.py` | — | — | — | ✅ 36/36 green | — | ➖ Clean on first pass |
| 4.1-4.4 | Full suite | Unit | ✅ | ✅ N/A (regression gate) | ✅ 1993/1993 pytest, ruff check/format clean, mypy --strict clean | N/A | ➖ `ruff format` auto-reformatted `test_adjudicate.py` (whitespace only) |

### Test Summary
- **Total tests written**: 21 (9 pure-helper unit tests + 12 CLI wiring tests)
- **Total tests passing**: 1993/1993 (full suite)
- **Layers used**: Unit (9 pure-function), Unit/CLI (12, CliRunner-based)
- **Approval tests** (pre-existing-guard suppression paths): 3 (`duplicates` zero-groups, `adjudicate` no-results, `adjudicate` same-only-empty) — these pass both before and after implementation because the existing empty guards already short-circuit before the new echo lines; they lock in that the guards continue to suppress the new lines.
- **Pure functions created**: 2 (`_format_group_tally`, `_format_verdict_tally`)

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_duplicates.py tests/unit/cli/test_adjudicate.py -q` → `56 passed` |
| Runtime harness command/scenario and exact result | `uv run openkos duplicates` against a manually created workspace (`/tmp/dupcheck`, `openkos init` + no docs) → confirmed pre-existing banner/blank-line/`No candidates found.` sequence unchanged, informing test design (tally/legend/Next only render on the non-empty path) |
| Rollback boundary | Revert the single commit touching `src/openkos/cli/main.py` (two new helper functions + two `typer.echo` blocks) and the two test files; no other caller depends on the new helpers or echo lines |

## Deviations from Design

None material. One clarification: the spec text for both commands says the tally is "the FIRST line of stdout" / stdout is "exactly" the empty message. The actual pre-existing (unchanged) behavior always prints an `"openkos {cmd}: workspace at {root}"` banner + blank line before any report content — this predates this change and is untouched by design's own `File Changes` table (which only touches the post-banner region). Tests were written to assert order relative to the report content (tally-before-first-detail-line) and substring-only for the empty-suppression paths, consistent with the design's own "Verified Facts" note that both test files use `in result.stdout` substring asserts, not `==` exact-stdout equality. No production banner logic was touched.

The `adjudicate` legend's exact wording is not literally specified in design.md (design only locks the `duplicates` legend's exact text). Chose `"Legend: [tier] type -- trigger, then verdict and rationale"` — consistent style with the `duplicates` legend, omits "confidence" since that field is intentionally never displayed (issue #138, pre-existing decision), matching the design's own header-render comment.

## Issues Found

None.

## Remaining Tasks

None — all 28 tasks complete.

## Workload / PR Boundary

- Mode: single PR (auto-forecast, Low budget risk)
- Current work unit: Unit 1 — Two pure tally helpers + wiring into `duplicates`/`adjudicate` + full test coverage
- Boundary: starts from no prior apply-progress; ends with all 28 tasks complete and quality gates green
- Estimated review budget impact: well under 400 changed lines (production ~45 lines net; tests ~430 lines net, mostly additive fixtures/asserts)

## Status

28/28 tasks complete. Ready for verify.
