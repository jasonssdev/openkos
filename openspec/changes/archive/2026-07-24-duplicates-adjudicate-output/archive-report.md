# Archive Report: duplicates / adjudicate output ergonomics (#139, Slice 1)

**Status**: ARCHIVED  
**Merged to main**: 2026-07-24  
**Verification Verdict**: PASS WITH WARNINGS (0 critical findings)  
**Review Findings**: 0 issues  
**PR**: #159

## What Shipped

Two read-only display-only CLI enhancements for entity-resolution advisory commands:

### 1. `openkos duplicates` Output

Added three new output lines (all additive, no existing lines altered):
- **Leading tally**: `N candidate group(s) (X exact, Y near)` — summary of total groups and tier breakdown (HIGH=exact, LOW=near)
- **Legend**: One-time explanation of `[tier] type -- trigger` column meaning, printed before the group loop
- **Next hint**: Trailing `Next: openkos merge <survivor> <absorbed>` line after all groups

Empty state (zero groups) remains: `"No candidates found."` alone — no tally/legend/hint.

### 2. `openkos adjudicate` Output

Added three analogous new output lines:
- **Leading tally**: `adjudicated N: x SAME, y DIFFERENT` (with `, z UNCERTAIN` appended ONLY when z > 0) — summary of verdict distribution over FULL results (independent of `--same-only` display filter)
- **Legend**: One-time explanation of verdict/confidence/rationale columns, printed before the results loop
- **Next hint**: Trailing `Next: openkos merge <survivor> <absorbed>` line after all results

Empty state (zero results OR `--same-only` filters to zero SAME) remains single-line: the pre-existing message only — no tally/legend/hint.

### Implementation Details

- Two pure sibling helpers (not a general one): `_format_group_tally(high, low) -> str` and `_format_verdict_tally(same, different, uncertain) -> str`
- Both return `""` on all-zero counts; reuse `_plural` for correct singular/plural; UNCERTAIN segment omitted when zero
- Placement: tally+legend inserted after empty guards, before loops; `Next:` hint after loops
- All pre-existing per-group/per-result detail lines remain byte-identical; existing CliRunner substring assertions all pass unchanged

**Files modified**:
- `src/openkos/cli/main.py`: +47 lines (2 new helpers + 3 tally/legend/hint insertions per command)
- `tests/unit/cli/test_duplicates.py`: +137 lines (8 new test cases for tally/legend/Next/empty-state behavior)
- `tests/unit/cli/test_adjudicate.py`: +301 lines (12 new test cases for verdict tally with/without UNCERTAIN, legend-once, Next-last-line, both empty-guard suppressions, full-results counting under `--same-only`)

## Scope (Out-of-Scope Deferred)

**In Scope**:
- Display-only enhancements to existing `duplicates` and `adjudicate` commands
- Two pure helpers (no side effects, reusable)

**Out of Scope** (Slice 2, #137):
- `adjudicate --json` and `--interactive` flags
- Guarded batch merge via adjudicate
- Pager integration (issue item 4: resolved/dropped — collision was user's shell pager, not code)

## Spec Merge: Wording Corrections Applied

The delta specs (in the change folder) used inaccurate wording for the tally placement. Pre-existing (unchanged, out-of-scope) workspace banner + blank line print BEFORE the tally in both commands:

```
openkos duplicates: workspace at /path/to/bundle
                                                     <- blank line
N candidate group(s) (X exact, Y near)               <- tally (first line of report body)
```

**Wording Correction Applied During Merge**:
- Changed all references to "FIRST line of stdout" in both specs to clearly state: **"first line of the report body (following the workspace-banner header and blank line)"**
- Keeps exact tally strings unchanged: `N candidate group(s) (X exact, Y near)`, `adjudicated N: x SAME, y DIFFERENT`, `, z UNCERTAIN` when nonzero
- The actual, verified test contract is substring presence and ordering relative to detail lines, not absolute stdout position — all test assertions are substring-only (verified via code read); no false exact-stdout claims

**Main specs updated**:
- `openspec/specs/entity-resolution/spec.md`: 6 new requirements (tally, legend, Next, empty-state, helper, byte-identical)
- `openspec/specs/entity-resolution-adjudication/spec.md`: 5 new requirements (same pattern)

## Verification Summary

**Test Execution**:
- Full suite: `uv run pytest -q` → 1993 passed, exit 0
- Focused: `uv run pytest tests/unit/cli/test_duplicates.py tests/unit/cli/test_adjudicate.py -q` → 56 passed, exit 0
- Linting: `uv run ruff check .` → all checks passed
- Formatting: `uv run ruff format --check .` → 132 files already formatted
- Type checking: `uv run mypy` → success, no issues, 131 source files

**Spec Coverage**:
- 12/12 requirements (6 entity-resolution, 6 entity-resolution-adjudication) satisfied
- 23/23 scenarios mapped to passing tests (9 duplicates, 14 adjudicate):
  - Helper unit tests (empty/singular/plural/UNCERTAIN-present/absent cases)
  - CLI wiring tests (tally-before-detail ordering, legend-once, Next-last-line, both empty-guard suppressions, `--same-only` full-results counting)
  - Zero `== result.stdout` equality assertions in test files (confirmed via grep); all equality is on `result.stderr`

**Build & Non-Regression**:
- Pre-existing tests (12 duplicates baseline, 24 adjudicate baseline): all still pass unchanged
- Scope guard: git diff touches ONLY `src/openkos/cli/main.py`, `tests/unit/cli/test_duplicates.py`, `tests/unit/cli/test_adjudicate.py`
- No changes to verdict logic, similarity scoring, tier bucketing, merge, pyproject.toml, or uv.lock

**Issues Found**:
- **CRITICAL**: None
- **WARNING** (non-blocking):
  1. Pre-existing spec wording imprecision re: "first line of stdout" given out-of-scope banner — not a defect, intent met, no false test claim
  2. Apply-progress self-reported test-count bookkeeping miscount (21 vs actual 20; 8 vs actual 7 adjudicate CLI tests) — non-blocking
- **SUGGESTIONS** (informational):
  1. Engram spec artifact text for helper requirements (`dict[str,int] -> str` contract) diverges from disk spec.md (marked as "implementation detail"); no gap, design's primitive-int signature is fully compliant
  2. mypy file-count difference (131 vs 132) between invocations is benign, both zero-issue

**Verdict**: PASS WITH WARNINGS — 0 critical, all 28 tasks complete, 23/23 spec scenarios compliant with passing covering tests, zero regressions, clean ruff/mypy. Non-blocking warnings do not block archive.

## Engram Artifact Traceability

| Artifact | Observation ID |
|----------|--------|
| Proposal | 1851 |
| Spec | 1852 |
| Design | 1853 |
| Tasks | 1854 |
| Verify Report | 1856 |

## Delivery & Rollback

- Single PR (#159), display-only, additive only, well under 400-line review budget
- Merged to main: commit 73b1e09
- Rollback: revert single commit (no migration, no state changes, no breaking)
