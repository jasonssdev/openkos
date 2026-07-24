```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:d4e16590da1b2f9fe59e9293a3157a7cfe0169d983cc8d429a8aa8c7004044c4
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 12/12
scenarios: 23/23
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:d4e16590da1b2f9fe59e9293a3157a7cfe0169d983cc8d429a8aa8c7004044c4
build_command: uv run mypy
build_exit_code: 0
build_output_hash: sha256:not-captured-see-report-mypy-success-no-issues-131-files
```

## Verification Report

**Change**: duplicates-adjudicate-output (#139, Slice 1)
**Version**: N/A
**Mode**: Strict TDD

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 28 |
| Tasks complete | 28 |
| Tasks incomplete | 0 |

### Scope Guard
`git diff --stat` vs merge-base with `origin/main` touches ONLY:
- `src/openkos/cli/main.py` (+47 lines)
- `tests/unit/cli/test_adjudicate.py` (+301 lines)
- `tests/unit/cli/test_duplicates.py` (+137 lines)

No change to verdict logic, similarity scoring, tier bucketing, `merge`, `pyproject.toml`, or `uv.lock`. `_format_type_tally` (the pre-existing sibling helper) is untouched; the two new helpers are additive and distinct. Confirmed via `git diff` on `src/openkos/resolution/` (empty) and on `pyproject.toml`/`uv.lock` (empty).

### Build & Tests Execution
**Tests**: `uv run pytest -q` → ✅ **1993 passed**, exit 0 (re-run independently, matches apply's self-report exactly).
**Focused**: `uv run pytest tests/unit/cli/test_duplicates.py tests/unit/cli/test_adjudicate.py -q` → ✅ **56 passed**, exit 0.
**Lint**: `uv run ruff check .` → ✅ All checks passed.
**Format**: `uv run ruff format --check .` → ✅ 132 files already formatted.
**Types**: `uv run mypy` → ✅ Success: no issues found in 131 source files.
(Note: apply-progress reported "132 source files" via `mypy . --strict`; my verbatim `uv run mypy` invocation uses the project's configured `files = ["src","tests"]` and reports 131. Both zero-issue; the 1-file delta is an invocation-mechanics artifact — `.` as a positional target vs the configured file list — not a real discrepancy. Non-blocking.)

### Spec Compliance Matrix

**Domain: entity-resolution (`duplicates`)**

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Leading Candidate-Group Tally Line | Single group | `test_format_group_tally_single_high/low` (formula) + `test_duplicates_prints_leading_tally_line_for_mixed_groups` (CLI wiring) | ✅ COMPLIANT |
| Leading Candidate-Group Tally Line | Multiple mixed | `test_duplicates_prints_leading_tally_line_for_mixed_groups` | ✅ COMPLIANT |
| Leading Candidate-Group Tally Line | All-exact / All-near | `test_format_group_tally_single_high` / `_single_low` (formula proven for all-HIGH/all-LOW inputs; CLI count derivation is trivial arithmetic already exercised by the mixed-group CLI test) | ✅ COMPLIANT |
| One-Time Trigger-Column Legend Line | Legend once, before loop | `test_duplicates_prints_legend_once_before_the_group_loop` | ✅ COMPLIANT |
| Trailing Next-Action Hint | Hint is final line | `test_duplicates_prints_next_hint_as_the_last_line` | ✅ COMPLIANT |
| Empty State Stays Single-Line | Zero groups → only existing message | `test_duplicates_zero_groups_suppresses_tally_legend_and_next` | ✅ COMPLIANT (see WARNING-1 below re: banner) |
| Reusable Group-Tally Formatting Helper | Zero counts → `""` | `test_format_group_tally_empty_returns_empty_string` | ✅ COMPLIANT |
| Reusable Group-Tally Formatting Helper | Populated counts → tally line | `test_format_group_tally_single_high/low`, `_mixed_plural` | ✅ COMPLIANT |
| Existing Detail Lines Stay Byte-Identical | Pre-existing substrings still pass | All pre-existing `test_duplicates.py` tests pass unchanged (12→20 test functions, 8 net new) | ✅ COMPLIANT |

**Domain: entity-resolution-adjudication (`adjudicate`)**

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Leading Verdict Tally Line Over Full Results | Mixed, no UNCERTAIN | `test_adjudicate_prints_leading_verdict_tally_line` | ✅ COMPLIANT |
| Leading Verdict Tally Line Over Full Results | Mixed with UNCERTAIN | `test_adjudicate_prints_uncertain_segment_when_present` | ✅ COMPLIANT |
| Leading Verdict Tally Line Over Full Results | Zero UNCERTAIN omits segment | `test_adjudicate_prints_leading_verdict_tally_line` (asserts `"UNCERTAIN" not in result.stdout`) | ✅ COMPLIANT — **segment-absence explicitly asserted** |
| Leading Verdict Tally Line Over Full Results | All-SAME / All-DIFFERENT | `test_format_verdict_tally_all_same` / `_all_different` (formula proven; `Counter` indexing wiring verified by other CLI tests) | ✅ COMPLIANT |
| Leading Verdict Tally Line Over Full Results | `--same-only` filters display, not tally | `test_adjudicate_tally_counts_full_results_under_same_only` | ✅ COMPLIANT — **tally over full results while filtered rationale strings absent from stdout, both explicitly asserted** |
| One-Time Verdict-Column Legend Line | Legend once, before loop | `test_adjudicate_prints_legend_once_before_the_results_loop` | ✅ COMPLIANT |
| Trailing Next-Action Hint | Hint is final line | `test_adjudicate_prints_next_hint_as_the_last_line` | ✅ COMPLIANT |
| Empty And Same-Only-Empty States Stay Single-Line | No candidates | `test_adjudicate_no_results_suppresses_tally_legend_and_next` | ✅ COMPLIANT (see WARNING-1) |
| Empty And Same-Only-Empty States Stay Single-Line | `--same-only` filters every result out | `test_adjudicate_same_only_empty_suppresses_tally_legend_and_next` | ✅ COMPLIANT (see WARNING-1) |
| Reusable Verdict-Tally Formatting Helper | Zero counts → `""` | `test_format_verdict_tally_empty_returns_empty_string` | ✅ COMPLIANT |
| Reusable Verdict-Tally Formatting Helper | Populated, UNCERTAIN omitted at 0 | `test_format_verdict_tally_same_and_different_only`, `_with_uncertain`, `_all_same`, `_all_different` | ✅ COMPLIANT |
| Existing Detail Lines Stay Byte-Identical | Pre-existing substrings still pass | All pre-existing `test_adjudicate.py` tests pass unchanged (24→36 test functions, 12 net new) | ✅ COMPLIANT |

**Compliance summary**: 23/23 scenarios compliant (12/12 requirements, both domains).

### Apply-Time Clarification Resolution

**(a) "First line of stdout" vs pre-existing banner.**
Verified by direct code read (`main.py:3609-3613` for `duplicates`, `main.py:3745-3749` for `adjudicate`): both commands print `"openkos {cmd}: workspace at {root}"` + a blank line **before** the empty-guard check — this is pre-existing, unchanged behavior, out of this slice's `File Changes` scope. So the tally is not literally byte-position-zero of stdout, and the empty path is not literally single-line. However:
- No test in either file asserts an absolute first-line position or an exact full-stdout equality that would be false — confirmed by grep: zero `== result.stdout` assertions exist in either file (all `==` equality is on `result.stderr`, matching design's claim).
- New tests correctly assert tally/legend position **relative to the first detail line** (`lines.index(tally) < first_detail_idx`) and use substring (`in`) checks for the empty paths — genuinely true assertions.
- The underlying intent (lead the report with a summary before per-item detail; empty path adds no new noise) is fully met.
Classified **WARNING** (not CRITICAL): spec wording is imprecise given a pre-existing, out-of-scope banner, but no implementation defect and no false test claim exists. Recommend a future spec wording fix ("first line of the report" rather than "first line of stdout") rather than any code change.

**(b) `adjudicate` legend wording.**
Design only locks `duplicates`' legend text; `adjudicate`'s legend was apply's choice: `"Legend: [tier] type -- trigger, then verdict and rationale"`. The spec requirement asks for "exactly one legend line explaining the per-group verdict/confidence/rationale columns" without locking literal text. Confidence is intentionally never rendered (issue #138, pre-existing, unrelated to this slice) — a legend that omitted "confidence" is the *correct* choice since it doesn't describe an unshown column. Covered by `test_adjudicate_prints_legend_once_before_the_results_loop`. **No gap** — requirement satisfied.

### Design Coherence
| Decision | Followed? | Notes |
|---|---|---|
| Two purpose-specific pure helpers (not one general helper) | ✅ Yes | `_format_group_tally(high, low)` / `_format_verdict_tally(same, different, uncertain)`, primitive-int signatures exactly as locked in design's Interfaces/Contracts |
| `Next:` hint, literal placeholders, printed once after loop | ✅ Yes | `typer.echo("Next: openkos merge <survivor> <absorbed>")` in both commands, after the loop |
| Tally source & placement (full `results` via `Counter`, after all empty guards, before loop) | ✅ Yes | `Counter(result.verdict for result in results)` computed over full `results`, not `displayed`; placed after both empty guards |
| `Counter` reused from existing import, not re-imported | ✅ Yes | No new `from collections import Counter` added; existing import at `main.py:7` reused |
| No verdict/scoring/tier/merge logic touched | ✅ Yes | Confirmed via empty `git diff` on `src/openkos/resolution/` |

### TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ✅ | Full "TDD Cycle Evidence" table present in apply-progress.md |
| All tasks have tests | ✅ | 28/28 tasks; both new helper functions provably did not exist pre-change (confirmed via `git diff` — the two functions are pure additions), so pre-implementation calls would raise `AttributeError` as claimed |
| RED confirmed (tests exist) | ✅ | Test files exist and contain the claimed test functions (verified via diff + grep count) |
| GREEN confirmed (tests pass) | ✅ | 56/56 focused, 1993/1993 full suite, independently re-run |
| Triangulation adequate | ✅ | 4 cases for `_format_group_tally` (empty, single-high, single-low, mixed-plural), 5 cases for `_format_verdict_tally` (empty, same+diff, +uncertain, all-same, all-diff) |
| Safety Net for modified files | ✅ | Pre-existing tests (12 duplicates, 24 adjudicate) all still pass; none of their assertions changed |

**TDD Compliance**: 6/6 checks passed

**Self-report accuracy note (WARNING)**: apply-progress's "Test Summary" claims "Total tests written: 21 (9 pure-helper unit tests + 12 CLI wiring tests)" and "5 helper unit tests for `_format_verdict_tally` + 8 CLI wiring tests" for `test_adjudicate.py`. Actual count (verified via `grep -c "^def test_"` before/after): `test_duplicates.py` 12→20 (+8: 4 helper + 4 CLI), `test_adjudicate.py` 24→36 (+12: 5 helper + 7 CLI wiring, not 8). Total net-new tests = **20**, not 21. This is a bookkeeping miscount in the self-report, not a functional defect — all actual tests exist, are correctly triangulated, and pass. Classified **WARNING** (documentation accuracy), non-blocking.

### Assertion Quality
No tautologies, ghost loops, ratio-imbalanced mocks, or implementation-detail couplings found in the new test code. All new assertions call real production code via `CliRunner.invoke` and assert on observable stdout content/position (substring `in`, `.count()`, `.index()` ordering) or direct helper return values. Zero `== result.stdout` equality assertions exist in either file (confirmed via grep); all equality assertions are `result.stderr ==`, consistent with design's disclosed fact and untouched by this change.

**Assertion quality**: ✅ All assertions verify real behavior

### Test Layer Distribution
| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit (pure helper) | 9 | 1 (functions in `main.py`, tested from both test files) | pytest |
| Unit/CLI (CliRunner) | 11 | 2 | pytest + typer.testing.CliRunner |
| **Total (net new)** | **20** | 2 | |

### Engram Artifact Consistency (SUGGESTION)
The Engram-stored `sdd/duplicates-adjudicate-output/spec` artifact's condensed text for both "Reusable ... Formatting Helper" requirements says `contract dict[str,int] -> str`. The authoritative on-disk delta spec files (`specs/entity-resolution/spec.md`, `specs/entity-resolution-adjudication/spec.md`) instead say **"Its argument shape is an implementation detail; only the returned string and the empty-on-zero contract are observable."** This divergence caused `tasks.md` to flag a false spec/design "conflict" (design locked primitive `int` args) that does not actually exist once the disk spec.md is read directly. The design's primitive-arg signature is fully spec-compliant. Recommend keeping the Engram-persisted spec summary byte-consistent with the disk spec.md to avoid this class of false-positive risk flag in future slices.

### Issues Found
**CRITICAL**: None
**WARNING**:
1. Spec wording ("first line of stdout" / stdout "exactly" one line for empty paths) is imprecise given the pre-existing, out-of-scope banner+blank-line output; intent is met and no test asserts a false claim (see Clarification (a) above).
2. apply-progress self-reported test counts (21 total / 8 adjudicate CLI-wiring tests) do not match the actual diff (20 total / 7 adjudicate CLI-wiring tests) — bookkeeping error, not a functional defect.

**SUGGESTION**:
1. Engram-persisted spec artifact text diverges from the disk spec.md wording for the two "Reusable ... Formatting Helper" requirements, which caused tasks.md to flag a non-existent risk. Keep them byte-consistent going forward.
2. `mypy . --strict` (apply) vs `uv run mypy` (verify, per project config) report 132 vs 131 source files respectively — both zero-issue; harmless invocation-mechanics difference, not a defect.

### Verdict
**PASS WITH WARNINGS**
All 28 tasks complete, all 23 spec scenarios across both domains have a passing covering test (re-run independently: 1993/1993 full suite, 56/56 focused), zero regressions, zero scope creep into verdict/scoring/merge logic, and clean ruff/mypy. Two non-blocking WARNINGs (a pre-existing spec-wording imprecision unrelated to this slice, and a bookkeeping miscount in apply's self-reported test tally) and two informational SUGGESTIONs (Engram/disk spec-artifact drift; benign mypy invocation difference) keep this from a clean PASS but do not block archive.
