```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:b0a6bcac1e24a25924d7444d9728d45576b6b53b
verdict: pass
blockers: 0
critical_findings: 0
requirements: 1/1
scenarios: 4/4
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:52b41786b3578162de3d84c5d526459da689fddb9f055479244bdeac79585b5f
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:76d10d900e83b629960c9642e0898e985536ff486e19dc74de72c5c9a9dac58e
```

## Verification Report

**Change**: status-surfaces-pending-duplicates
**Version**: N/A (delta spec, no version header)
**Mode**: Strict TDD

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 16 |
| Tasks complete | 16 |
| Tasks incomplete | 0 |

### Build & Tests Execution

**Build**: PASSED
```text
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
141 files already formatted

$ uv run mypy .
Success: no issues found in 141 source files
```

**Tests**: 2339 passed / 0 failed / 0 skipped
```text
$ uv run pytest -q
2339 passed in 83.05s
```

**Coverage**: 97.61% total / gate 90% → PASSED (above threshold)
```text
$ uv run pytest --cov=src/openkos --cov-branch --cov-report=term-missing -q
TOTAL   5157   117   1544   41   98%
Required test coverage of 90.0% reached. Total coverage: 97.61%
```

Changed-region coverage: the `main.py` coverage-missing line list (85 statement misses,
22 branch misses) contains **zero** lines in either changed range (4495-4514 docstring,
4568-4584 new `needs_attention` block). The new code is fully exercised.

### Spec Compliance Matrix
| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Needs-Attention Surfaces Pending Duplicate Groups | No duplicate groups | `test_status.py::test_status_no_duplicate_groups_no_new_entry` | COMPLIANT |
| Needs-Attention Surfaces Pending Duplicate Groups | Exact-title duplicate groups are surfaced | `test_status.py::test_status_surfaces_exact_title_duplicate_group` (count/naming/all-clear-absent), `::test_status_duplicate_line_has_no_tier_labels` (no tier words), `::test_status_duplicate_line_plural_wording` (plural) | COMPLIANT |
| Needs-Attention Surfaces Pending Duplicate Groups | Only near-match groups still means nothing needs attention | `test_status.py::test_status_near_match_only_duplicates_still_all_clear` | COMPLIANT |
| Needs-Attention Surfaces Pending Duplicate Groups | Deprecated-only duplicate group is excluded by default | `test_status.py::test_status_deprecated_only_duplicate_group_excluded` | COMPLIANT |

**Compliance summary**: 4/4 scenarios compliant (1/1 requirements).

### Mutation Probe (independent confirmation of the HIGH-only pin)

Deleted the `if group.tier is Tier.HIGH` clause from the generator (kept the `sum(1 for
group in find_candidates(...))` unfiltered), reran `pytest tests/unit/cli/test_status.py
-k duplicate`:

```
FAILED test_status_near_match_only_duplicates_still_all_clear
1 failed, 5 passed
```

Exactly the predicted single failure — `test_status_near_match_only_duplicates_still_all_clear`
is the sole test pinning the HIGH-only filter; the other five (including the near-match
fixture's siblings) are unaffected. Mutation reverted; `git status` clean and `pytest -q`
re-confirmed 2339 passed after restore.

### Wording Constraint Check (line: `"{n} candidate group{s} with identical titles — run \`openkos duplicates\` to review."`)

| Constraint | Verified how | Result |
|---|---|---|
| No `HIGH`/`LOW`/`exact`/`near` | `test_status_duplicate_line_has_no_tier_labels` scopes assertion to the matched line; source line literal contains none of the four words | PASS |
| Not phrased as a total | `with identical titles` is a restrictive qualifier scoping the count to a subset (design D4 rationale); present verbatim in `main.py:4576` | PASS |
| Names `openkos duplicates` | Literal substring in the f-string; asserted by T1 | PASS |
| Correct singular/plural | `_plural(exact_title_groups)` (existing helper, main.py:661); T1 asserts singular (`1 candidate group`), T6 asserts plural (`2 candidate groups`) | PASS |
| `with identical titles` survived | Present verbatim in `main.py:4576-4577`, not reworded, not dropped | PASS |

### TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | Yes | `apply-progress.md` "TDD Cycle Evidence" table, 6 rows (T1-T6) |
| All tasks have tests | Yes | 6/6 new behaviors have a dedicated test function |
| RED confirmed (tests exist) | Yes | All 6 test functions exist in `tests/unit/cli/test_status.py:602-751`, none skipped/xfail |
| GREEN confirmed (tests pass) | Yes | 6/6 pass in isolation (`-k duplicate`) and as part of the full 2339-test run |
| Triangulation adequate | Partial (documented) | T1/T2/T6 triangulate the same behavior from different angles; T3/T4/T5 are single-scenario regression guards, each pinning a distinct branch (documented, not a gap) |
| Safety Net for modified files | Yes | Baseline 43/43 (`test_status.py` + `test_duplicates.py`) reported as passing before the change |

**TDD Compliance**: 6/6 checks passed (with one honestly-documented deviation, see below).

### TDD Deviation — Confirmed Honest and Scoped

`tasks.md` task 2.8 states: "T1/T2/T6 failed for the right reason (no duplicate-groups
line rendered yet). T3/T4/T5 assert the pre-existing negative state ... and passed
trivially before the change exists — an inherent property of regression-guard tests on
a not-yet-built feature, not a fixture-trap false pass."

`apply-progress.md` repeats this in "Deviations from Design" (a "process note, not a
design deviation") with the same reasoning, plus the double-confirmation protocol (raw
pre-implementation run confirmed T3/T4/T5 passed for the correct reason, i.e. empty
`needs_attention`, not a fixture bug).

This is architecturally correct: T3/T4/T5 assert an absence (`Tier.HIGH` filter false
arm, `if exact_title_groups:` false arm, deprecated-exclusion). Before any production
code exists, "no line is added" is unconditionally true for any input — there is no
code path capable of emitting a false positive to RED against. The mutation probe above
supplies the missing proof of causality for the highest-value case (T3 / the HIGH-only
filter): deleting the guard flips it from an accidental pass to a real failure, showing
the test is load-bearing today even though it could not RED at inception.

No other task in `tasks.md` shows a similarly skipped or silently-passed RED step — T1,
T2, T6 each report a concrete pre-implementation failure with an `AssertionError`
message (`'1 candidate group' not in stdout`, `StopIteration`, `'2 candidate groups' not
in stdout`), consistent with genuine RED.

### Out-of-Scope Check (diff-based, not claim-based)

`git diff main..HEAD --stat`: 8 files changed — 6 planning artifacts (`explore.md`,
`proposal.md`, `design.md`, `tasks.md`, `apply-progress.md`, `specs/status/spec.md`) +
`src/openkos/cli/main.py` (+31/-7) + `tests/unit/cli/test_status.py` (+138/-0).

- `git diff main..HEAD -- src/openkos/resolution/` → empty. `find_candidates`,
  `duplicates`, `merge`, `adjudicate` are untouched (confirmed both by empty diff on the
  `resolution/` package and by unchanged line numbers for `merge_core` (3416), `merge`
  (3483), `duplicates` (4707), `adjudicate` (4778) in `main.py`).
- `main.py` diff has exactly two hunks, both inside `status()`: the docstring
  (4495-4514) and the new `needs_attention` block (4568-4584). No other function
  touched.
- No `--include-deprecated` (or any new) Typer option added anywhere in the diff.
- No bundle-walk consolidation — the docstring explicitly keeps "#195 consolidation is
  out of scope" verbatim, and the four walks remain four separate calls.

### Commit Split Check

- `da5e8d7` (`fix(cli): surface pending exact-title duplicate groups in status (#186)`):
  touches only `src/openkos/cli/main.py` and `tests/unit/cli/test_status.py`.
- `b0a6bca` (`chore(sdd): add planning artifacts for status-surfaces-pending-duplicates`):
  touches only the 6 `openspec/changes/.../` files.
- No mixing in either commit (confirmed via the 8-file `--stat` split above, cross-
  referenced against each commit's own file list).
- Neither commit message contains `Co-Authored-By` or any AI-attribution trailer
  (`git log -2 --format=%B` inspected directly).
- Working tree is clean; branch `fix/status-surfaces-pending-duplicates`.

### Correctness (Static Evidence)
| Requirement | Status | Notes |
|------------|--------|-------|
| `find_candidates` called with default `include_deprecated=False`, no new flag | Implemented | `find_candidates(layout.bundle_dir)` — no second argument, no new Typer option |
| Exact-title (`Tier.HIGH`) filter | Implemented | Inline generator filter, matches `duplicates`' own established pattern (main.py:4744) |
| Insertion point before `vectors_missing`, unconditional | Implemented | Confirmed at `main.py:4568`, above the `vectors_missing` assignment; not gated on any condition |
| `_format_group_tally` deliberately unused | Implemented | Not referenced in the new block; grep confirms no new call site |
| Docstring "THREE" → "FOUR" walks | Implemented | Diff shows verbatim replacement, `#195 out of scope` sentence and `build_graph` exactly-once guarantee both preserved |

### Coherence (Design)
| Decision | Followed? | Notes |
|----------|-----------|-------|
| D1 — inline filter, no new helper/module | Yes | Matches diff exactly |
| D2 — do not reuse `_format_group_tally` | Yes | Confirmed unused |
| D3 — insertion point before `vectors_missing` | Yes | Confirmed at correct line |
| D4 — exact line wording | Yes | Byte-for-byte match to design.md's specified f-string |

### Issues Found

**CRITICAL**: None

**WARNING**: None

**SUGGESTION**: None

### Verdict

PASS — all 4 spec scenarios have passing covering tests, the HIGH-only pin is
independently mutation-confirmed (deleting the filter breaks exactly one test), the
documented TDD deviation on T3/T4/T5 is legitimate and disclosed (not a silently skipped
RED step), the wording constraints hold byte-for-byte, no out-of-scope code was touched,
and the commit split is clean with no AI-attribution trailers. Full suite 2339/2339
passed, coverage 97.61% (gate 90%), lint/format/types all clean.
