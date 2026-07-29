# Apply Progress: Backfill sensitivity onto existing provenance descendants (#231)

## Scope of this run

PR1 only (Phases 1-5): extract descendant-scan out of `set_sensitivity_cmd`
(#235) + name landed paths on Phase-B write failure (#233).

## Branch

`feat/extract-descendant-scan`, off `main` (489672a). `tasks.md`'s PR
Assignment section was updated to name this exact branch (see Deviations
from Design).

## Mode

Strict TDD (RED -> GREEN per pinned commit order in `design.md`/prompt).

## Commits (in order)

| # | SHA | Message | Type |
|---|-----|---------|------|
| 1 | `58e67f9` | `test(bundle): pin resolve_source_raises and find_unresolvable_provenance (RED)` | RED |
| 2 | `250060a` | `refactor(cli): extract descendant-scan out of set_sensitivity_cmd (GREEN)` | GREEN |
| 3 | `63122f8` | `test(cli): pin Phase-B landed-path failure message (RED)` | RED |
| 4 | `4bcc7d5` | `fix(cli): name landed paths on set-sensitivity Phase-B write failure (GREEN)` | GREEN |
| 5 | `d6ca47d` | `chore(sdd): mark PR1 tasks complete for backfill-sensitivity` | docs |
| 6 | `3fba241` | `test(bundle): pin and document find_unresolvable_provenance's wider catch` | correction (RED confirmed uncommitted, GREEN already present) |

## Test Count Correction (phase-contract review)

An earlier revision of this artifact incorrectly claimed the design's "29
existing tests" figure for `tests/unit/cli/test_set_sensitivity.py` was
wrong and that the file "actually contains 36 test functions." That
conflated two different units and was itself wrong — corrected here:

- **On `main`**: 29 test **functions**; 36 test **cases** once collected
  (two functions carry `@pytest.mark.parametrize`: `test_bad_concept_id_refused`
  at `:159` with 4 ids, `test_dirty_current_classified_as_lowering_refuses`
  at `:252` with 5 ids — verified via a throwaway `git worktree` on `main`
  and `pytest --collect-only`, which reported `36 tests collected`).
- **On this branch**: 31 test functions (29 + the 2 new landed-path tests);
  38 test cases collected.
- The design/tasks doc's "29" was correct all along (function count); it
  was never off by count. The "Deviations from Design" section below no
  longer claims otherwise.

## TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR |
|------|-----|-------|----------|
| 1.1/1.2 — characterization tests for `resolve_source_raises`/`find_unresolvable_provenance` | `58e67f9`: 11 new tests fail with `AttributeError: module 'openkos.bundle.provenance' has no attribute ...` (import/attr error, functions do not exist) | `250060a`: all 11 pass | N/A — pure move, no further refactor needed |
| 2.1-2.4 — extract helper, rewire `set_sensitivity_cmd` | (covered by 1.1/1.2's RED) | `250060a`: `tests/unit/bundle/test_provenance_source_raises.py` (11) + `tests/unit/cli/test_set_sensitivity.py` (29 functions / 36 collected cases, unchanged) all GREEN | Redundant `sorted()` call removed from `resolve_source_raises` before commit (helper already returns sorted ids) |
| 3.1/3.2 — Phase-B landed-path RED | `63122f8`: `test_phase_b_failure_names_the_landed_paths` and `test_phase_b_failure_with_zero_landed_paths` fail ONLY on the landed-path assertions; the pinned first-sentence assertion (`"failed while writing the set-sensitivity"`) passes on both, confirming it was accurately captured, not invented | `4bcc7d5`: both pass | N/A |
| 4.1/4.2 — landed-path message GREEN | (covered by 3.1/3.2's RED) | `4bcc7d5`: full `test_set_sensitivity.py` — 31 functions / 38 collected cases, all GREEN | None needed |
| Correction — pin `find_unresolvable_provenance`'s exception-widening | `3fba241`: honest RED confirmed by temporarily narrowing the module's `except Exception` to `except (OSError, ValueError)` (uncommitted local edit) and observing the new test fail with an uncaught `yaml.parser.ParserError`, then reverting the narrowing before commit — the committed code never regressed | `3fba241`: with the broad catch restored (the version already committed in `250060a`), the new test passes; no production code change was needed since the wider catch was already correct — only the test and the docstring's inaccurate "byte-identical"/"verbatim" claim were added/fixed | N/A |

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest tests/unit/bundle/test_provenance_source_raises.py tests/unit/cli/test_set_sensitivity.py` -> 50 passed (12 characterization + 38 set-sensitivity collected cases) |
| Full suite | `uv run pytest -q` -> baseline on `main`: 2565 passed; after this PR (including the correction commit): **2579 passed** (2565 + 12 characterization test cases + 2 landed-path test cases) |
| Runtime harness | `uv run python -m openkos.cli.main set-sensitivity <source-id> <level>` exercised indirectly through the full `CliRunner`-based `test_set_sensitivity.py` suite (init workspace, ingest a Source, set-sensitivity with `--auto`, inspect resulting bundle files and git commit) — no separate manual run needed since the CLI test suite already drives the real Typer app end-to-end against a tmp workspace |
| Rollback boundary | `git revert` the 6 commits on `feat/extract-descendant-scan` (or reset the branch to `489672a`) restores `set_sensitivity_cmd`'s inline scan, `_DescendantRaise`, the unappended failure message, and the narrow `except (OSError, ValueError)` catch; no other verb or file is touched |

## Lint / Typecheck (re-run after correction)

- `uv run ruff check .` -> All checks passed
- `uv run ruff format --check .` -> 147 files already formatted
- `uv run mypy .` -> Success: no issues found in 147 source files

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/model/okf.py` | Modified | Added `okf.DescendantRaise` frozen dataclass (no `path` field) next to `ProvenanceRewrite` |
| `src/openkos/bundle/provenance.py` | Modified | Extracted `provenance_closure` fixpoint core; `find_provenance_descendants` now delegates to it; added `resolve_source_raises` and `find_unresolvable_provenance`; corrected the latter's docstring to document its deliberate `except Exception` widening instead of claiming a byte-identical move |
| `src/openkos/cli/main.py` | Modified | Rewired `set_sensitivity_cmd`'s Phase-A scan to call the two new helpers; dropped private `_DescendantRaise`; added `landed` path tracking and the appended failure-message sentence in Phase B |
| `tests/unit/bundle/test_provenance_source_raises.py` | Created/Modified | 11 characterization tests for the two new pure helpers, plus 1 test (added in the correction) pinning that a malformed-frontmatter sibling is skipped, not raised — 12 total |
| `tests/unit/cli/test_set_sensitivity.py` | Modified | Added 2 tests: `test_phase_b_failure_names_the_landed_paths`, `test_phase_b_failure_with_zero_landed_paths` |

## Changed-Line Totals (PR1 vs `main`, code files only)

`git diff --numstat main...feat/extract-descendant-scan` for the 5 touched
code/test files (excludes `openspec/changes/**` docs):

| File | + | - |
|---|---|---|
| `src/openkos/bundle/provenance.py` | 157 | 27 |
| `src/openkos/cli/main.py` | 42 | 74 |
| `src/openkos/model/okf.py` | 22 | 0 |
| `tests/unit/bundle/test_provenance_source_raises.py` | 198 | 0 |
| `tests/unit/cli/test_set_sensitivity.py` | 73 | 0 |
| **Total** | **492** | **101** |

**Total changed lines: 593** (up from the pre-correction 562, +31 for the
new exception-widening test and docstring correction), against the tasks
doc's ~130-200 estimate for PR1. See Risks below.

## Deviations from Design

Three items, corrected per phase-contract review (all three are wording/
naming/branch corrections; no functional deviation from the design's
approach):

1. **Test-count wording (this artifact and `tasks.md`)**: an earlier
   revision incorrectly stated the design's "29 existing tests" figure was
   wrong. It was right — 29 is the FUNCTION count on `main`; 36 is the
   COLLECTED CASE count after `@pytest.mark.parametrize` expansion. Both
   this file and `tasks.md` now state function count and collected-case
   count separately; the false "design was inaccurate" claim is removed.

2. **`find_unresolvable_provenance`'s exception handling is a deliberate
   behaviour change, not a verbatim move.** The original inline loop
   caught only `except (OSError, ValueError)`; the extracted function
   catches broad `except Exception` (matching `_parse_provenance_by_id`'s
   identical convention just above it). `frontmatter.loads` raises
   `yaml.YAMLError` on malformed YAML, which is neither an `OSError` nor a
   `ValueError` — so on `main`, a sibling file with malformed frontmatter
   crashes `set-sensitivity` with an uncaught traceback; on this branch it
   is silently skipped. This is the BETTER behaviour and is kept as-is,
   but the docstring's "extracted verbatim"/"byte-identical" wording was
   wrong and has been corrected (commit `3fba241`), and a test now pins
   the new behaviour honestly (RED confirmed via a temporarily narrowed,
   uncommitted catch; reverted before commit).

3. **Branch name mismatch**: `tasks.md`'s PR Assignment section named
   `feat/extract-source-raises`; the branch actually created and used for
   every commit in this run is `feat/extract-descendant-scan` (per this
   session's explicit branch instruction). `tasks.md` has been updated to
   name the real branch consistently for PR1, and for PR2/PR3's target
   ("-> PR1 branch" now reads "-> `feat/extract-descendant-scan`").

## Issues Found

- **Changed-line estimate miss**: actual PR1 diff (code + tests, excluding
  `openspec/changes/**` docs) is 593 changed lines vs the tasks doc's
  ~130-200 estimate. The overage is concentrated in this codebase's
  exhaustive docstring convention (every other function in
  `bundle/provenance.py` carries comparably detailed docstrings) and the
  two test files, not control-flow complexity. Still within the session's
  cached 800-line review budget, but exceeds the skill's default 400-line
  single-PR guard. Flagged for the orchestrator/reviewer; no code was
  trimmed to force the estimate.
- One-off flake reported by the phase-contract validator on
  `tests/unit/cli/test_forget.py::test_absolute_concept_id_refuses` in one
  full-suite run, not reproduced on a second run or in isolation —
  unrelated to this branch. Per the coordinator's note, the orchestrator
  is handling it separately; not investigated here.

## Remaining Tasks (NOT in this run's scope)

- [ ] Phase 6-10 (PR2): lint/status wiring — `LintDoc.sensitivity`/`.provenance`, `check_below_source_sensitivity`
- [ ] Phase 11-14 (PR3): `backfill-sensitivity` verb + ADR-0012

## Workload / PR Boundary

- Mode: stacked PR slice (chain strategy: `stacked-to-main`)
- Current work unit: PR1 — closes #235, #233
- Boundary: starts at `main` (489672a); ends at commit `3fba241` on
  `feat/extract-descendant-scan`. PR2 (`feat/lint-below-source-sensitivity`)
  and PR3 (`feat/backfill-sensitivity-verb`) both target this branch per
  `tasks.md`'s PR Assignment section (now corrected to the real branch
  name).
- Estimated review budget impact: 593 changed lines (see Risks) — above
  the 400-line single-PR default guard, within the 800-line session
  budget.

## Status

12/12 PR1 tasks complete (Phases 1-5, tasks 1.1 through 5.2), plus one
post-review correction commit (`3fba241`) addressing three phase-contract
findings: a false test-count claim, an undocumented exception-widening
behaviour change, and a branch-name mismatch in `tasks.md`. Full suite:
**2579 passed**. Ready for `sdd-verify` on PR1's scope. PR2 and PR3
(Phases 6-14) remain untouched, as instructed.
