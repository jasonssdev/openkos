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

> **Note on the commit SHAs quoted in this section.** Every SHA above predates
> two later events: the rebase of this branch onto `main` after PR1 was squash
> merged, and the review-driven rewrite of the correction commits so that no
> commit in the publication range touched a path outside the frozen review
> candidate. The narrative of what each step did still holds; the identifiers do
> not. The branch's current history is authoritative — read it with
> `git log --oneline main..HEAD`. The same caveat applies to the rollback
> boundary above: reset to `main` rather than reverting the SHAs it names.

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

## Remaining Tasks (NOT in PR1's scope, see PR2 section below)

- [x] Phase 6-10 (PR2): lint/status wiring — `LintDoc.sensitivity`/`.provenance`, `check_below_source_sensitivity`
- [ ] Phase 11-14 (PR3): `backfill-sensitivity` verb + ADR-0012

## Workload / PR Boundary (PR1)

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

## Status (PR1)

12/12 PR1 tasks complete (Phases 1-5, tasks 1.1 through 5.2), plus one
post-review correction commit (`3fba241`) addressing three phase-contract
findings: a false test-count claim, an undocumented exception-widening
behaviour change, and a branch-name mismatch in `tasks.md`. Full suite:
**2579 passed**. Ready for `sdd-verify` on PR1's scope.

A subsequent commit, `cb5a450` (`chore(sdd): correct three phase-contract
findings in PR1 artifacts`), applied the same three corrections directly
to this file and `tasks.md` after a phase-contract validation run; no
production code changed in that commit.

---

# PR2: lint/status below-source-sensitivity / multi-source-uncovered detection (Phases 6-10)

## Scope of this run

PR2 only (Phases 6-10): the read-only `lint`/`status` detection finding,
with two categories keyed on CLOSURE MEMBERSHIP (design D3):
`below-source-sensitivity` and `multi-source-uncovered`. PR1's Phases 1-5
were already `[x]` and are untouched here; PR3 (Phases 11-14,
`backfill-sensitivity` verb + ADR-0012) is explicitly out of scope for
this run.

## Branch

`feat/lint-below-source-sensitivity` was NOT created as a separate branch
this run; per this session's explicit instruction ("Create PR2's branch
off `feat/extract-descendant-scan`... Name it per `tasks.md`'s PR
Assignment section"), all PR2 commits landed directly on
`feat/extract-descendant-scan` (the same branch PR1 used), continuing on
top of PR1's tip (`cb5a450`). `tasks.md`'s PR Assignment section already
names `feat/lint-below-source-sensitivity -> feat/extract-descendant-scan`
as PR2's intended branch/target for the eventual PR; this run did not cut
that branch, matching the instruction's literal wording to build on top of
`feat/extract-descendant-scan`. Flagged under Deviations from Design below
in case a separate branch was actually intended.

## Mode

Strict TDD (RED -> GREEN per phase, mirroring PR1's pinned commit shape).

## Commits (in order, continuing from PR1's `cb5a450`)

| # | SHA | Message | Type |
|---|-----|---------|------|
| 1 | `5f730f6` | `test(lint): pin check_below_source_sensitivity closure-membership rules (RED)` | RED |
| 2 | `3415430` | `feat(lint): add LintDoc.sensitivity/.provenance and check_below_source_sensitivity (GREEN)` | GREEN |
| 3 | `7244388` | `test(cli): pin lint/status wiring for below-source/multi-source findings (RED)` | RED |
| 4 | `df123eb` | `feat(cli): wire check_below_source_sensitivity into lint and status (GREEN)` | GREEN |
| 5 | `ebc801d` | `chore(sdd): mark PR2 tasks complete for backfill-sensitivity` | docs |

## TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR |
|------|-----|-------|----------|
| 6.1-6.3 — pure-function characterization tests | `5f730f6`: all 8 new tests in `tests/unit/test_lint_below_source.py` fail with `TypeError: LintDoc.__init__() got an unexpected keyword argument 'sensitivity'` (fields/function do not exist) | `3415430`: all 8 pass | N/A |
| 7.1-7.4 — `LintDoc` fields, `LintReport` fields, `check_below_source_sensitivity` | (covered by 6.1-6.3's RED) | `3415430`: `tests/unit/test_lint_below_source.py` (8) GREEN; full pure-lint suite (`tests/unit/test_lint.py` + `tests/unit/resolution/test_volatility_typing.py`, 162 tests) unaffected | None needed |
| 8.1-8.3 — lint/status CLI wiring tests | `7244388`: 6 new CliRunner scenarios (3 in `tests/unit/cli/test_lint.py`, 3 in `tests/unit/cli/test_status.py`) fail — the below-source-sensitivity test initially passed FALSELY on a weak substring assertion (the descendant was independently flagged `orphan`), caught and tightened to assert the exact `"Below-source sensitivity:"`/`"Multi-source uncovered:"` section boundaries before re-confirming RED on the multi-source-uncovered and clean-bundle scenarios | `df123eb`: all 6 pass | N/A |
| 9.1-9.3 — wire into `lint`/`status` | (covered by 8.1-8.3's RED) | `df123eb`: `tests/unit/test_lint_below_source.py` + `tests/unit/cli/test_lint.py` + `tests/unit/cli/test_status.py` (63 tests) all GREEN | None needed |

**RED-quality note**: the first draft of `test_lint_flags_below_source_sensitivity`
asserted only `"concepts/derived.md" in result.stdout`, which passed even
before any wiring existed because the same fixture doc was already flagged
under `lint`'s pre-existing `Orphan pages:` section (it is not linked from
`index.md`). This was caught before committing GREEN by re-running the RED
suite and noticing the assertion did not fail; the test was tightened to
slice `result.stdout` between the exact `"Below-source sensitivity:"` and
`"Multi-source uncovered:"` section headers before asserting membership,
and the tightened version was re-confirmed RED prior to the GREEN commit.

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest tests/unit/test_lint_below_source.py tests/unit/cli/test_lint.py tests/unit/cli/test_status.py` -> 66 passed (63 as first written, plus the 3 the bounded review correction added to `test_lint_below_source.py`) |
| Full suite | `uv run pytest -q` -> baseline (PR1 tip, `cb5a450`): 2579 passed; after PR2: **2596 passed** (2579 + 8 pure-function + 3 lint-CLI + 3 status-CLI = 2593, then + 3 more from the bounded review correction = 2596) |
| Runtime harness | `uv run python -m openkos.cli.main lint` / `status` exercised indirectly through the full `CliRunner`-based `tests/unit/cli/test_lint.py`/`test_status.py` suites (init workspace, hand-write a Source + a below-Source descendant + a Source-plus-foreign-derived-cite doc, run `lint`/`status`, inspect rendered sections and exit code) — no separate manual run needed since the CLI test suite already drives the real Typer app end-to-end against a tmp workspace |
| Rollback boundary | `git revert` the 5 PR2 commits (`5f730f6`..`ebc801d`) on `feat/extract-descendant-scan` (or reset to `cb5a450`) restores `LintDoc` to its PR1 shape (no `sensitivity`/`provenance` fields), removes `check_below_source_sensitivity` and both new `lint`/`status` sections; `lint`/`status` are read-only, so no bundle data is ever at risk regardless |

> **Note on the commit SHAs quoted in this section.** Every SHA above predates
> two later events: the rebase of this branch onto `main` after PR1 was squash
> merged, and the review-driven rewrite of the correction commits so that no
> commit in the publication range touched a path outside the frozen review
> candidate. The narrative of what each step did still holds; the identifiers do
> not. The branch's current history is authoritative — read it with
> `git log --oneline main..HEAD`. The same caveat applies to the rollback
> boundary above: reset to `main` rather than reverting the SHAs it names.

## Lint / Typecheck

- `uv run ruff check .` -> All checks passed
- `uv run ruff format --check .` -> 148 files already formatted (one file, `tests/unit/test_lint_below_source.py`, needed one `ruff format` pass before the check was clean — applied before the final GREEN commit)
- `uv run mypy .` -> Success: no issues found in 148 source files

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/lint.py` | Modified | Added `LintDoc.sensitivity: str = ""` / `.provenance: tuple[str, ...] = ()` (defaulted, filled in `collect_docs` from already-parsed frontmatter); added `LintReport.below_source`/`.multi_source_uncovered`; added `check_below_source_sensitivity(docs) -> list[LintFinding]`, importing `openkos.bundle.provenance` for `provenance_closure` (never `resolve_source_raises`) |
| `src/openkos/cli/main.py` | Modified | `lint` calls `check_below_source_sensitivity(docs)` once, splits results by `finding.kind`, renders two new sections (`Below-source sensitivity:`, `Multi-source uncovered:`) with their own empty-state lines; `status` folds the same findings into `Needs attention:`, labeled `[below-source-sensitivity]`/`[multi-source-uncovered]`; both reuse their existing single `docs` list — no new bundle walk in either command |
| `tests/unit/test_lint_below_source.py` | Created | 11 tests. Eight pure-function: seven-field construction guard, dirty-value fail-closed flag, already-covered no-flag, same-Source multi-cite exclusion, Source-plus-foreign-derived uncovered, unresolvable-cite neither-category, no-Sources clean bundle, already-at-high-water-mark no-flag. Three added by the bounded review correction, covering `collect_docs`' provenance decoding: corrupt-key skip with notice, absent-key default, and the regression that a corrupt descendant under a Source is surfaced by the notice rather than silently dropped |
| `tests/unit/cli/test_lint.py` | Modified | Added 3 CliRunner scenarios: below-source-sensitivity section rendering, multi-source-uncovered section rendering (with the same-Source-closure member correctly excluded), clean-bundle zero-findings + no file mutation |
| `tests/unit/cli/test_status.py` | Modified | Added 3 CliRunner scenarios: below-source-sensitivity under "needs attention", multi-source-uncovered marked not covered by `backfill-sensitivity`, and a counting-wrapper regression guard confirming `collect_docs` is still called exactly once |

## Changed-Line Totals (PR2, code + test files, via `git diff --numstat`)

| File | + | - |
|---|---|---|
| `src/openkos/lint.py` | 178 | 0 |
| `src/openkos/cli/main.py` | 41 | 2 |
| `tests/unit/test_lint_below_source.py` | 264 | 0 |
| `tests/unit/cli/test_lint.py` | 99 | 0 |
| `tests/unit/cli/test_status.py` | 118 | 0 |
| **Total** | **700** | **2** |

These rows are the branch's final state (`git diff --numstat main..HEAD -- src
tests`). They exceed the figures quoted in the pre-correction narrative below
because two bounded review corrections landed after it was written: the
invalid-`provenance:` skip with its three tests, and a docstring/figures fix.

**Total changed lines: 702**, against the tasks doc's ~150-250 estimate for
PR2 — a significant overage, flagged plainly below (see Issues Found). No
code was trimmed to force the estimate down.

## Deviations from Design

1. **CLI-wiring test file paths**: `tasks.md`'s Phase 8 named
   `tests/unit/test_lint.py`/`tests/unit/test_status.py` as the targets for
   the new CliRunner scenarios. Those files hold PURE-FUNCTION tests only
   (no `CliRunner`, no `typer.testing` import) — the actual CLI-wiring test
   suites for `lint`/`status` live at `tests/unit/cli/test_lint.py` and
   `tests/unit/cli/test_status.py` (confirmed: `check_unextracted`'s own
   #187 CLI scenarios live there, not in the top-level `tests/unit/`
   files). Scenarios were added to the real CLI test files instead;
   `tasks.md` now notes this explicitly on 8.1/8.2.
2. **PR2 branch**: no separate `feat/lint-below-source-sensitivity` branch
   was cut this run. Per this session's explicit instruction to create
   PR2's branch off `feat/extract-descendant-scan` and continue committing
   there, all 5 PR2 commits landed directly on `feat/extract-descendant-scan`
   (continuing from PR1's `cb5a450`), matching `tasks.md`'s PR Assignment
   target-branch relationship (`feat/lint-below-source-sensitivity` ->
   `feat/extract-descendant-scan`) without actually branching off a new ref
   for it. Flagged for the orchestrator: if a genuinely separate PR2
   branch/PR is wanted for delivery, it still needs to be cut from this
   point (`ebc801d`) before opening the PR.
3. **RED-quality self-correction**: the first draft of the
   below-source-sensitivity CLI test passed on an unrelated substring
   match (the fixture doc was already an `orphan`) before any real wiring
   existed — a false-positive GREEN risk caught during RED confirmation,
   not after. Recorded above under TDD Cycle Evidence; no functional
   design deviation, purely a test-quality catch.

No other deviations: the closure-membership algorithm, the
`combine_sensitivity`-based trigger, the same-Source multi-cite exclusion,
the "neither category" fail-safe for unresolvable cites, and the
no-fifth-walk guard all match `design.md` D2/D3 exactly as specified.

## Issues Found

- **Changed-line estimate miss**: actual PR2 diff (code + tests) is 702
  changed lines vs the tasks doc's ~150-250 estimate — roughly 2.5x. The
  overage is concentrated in: (a) this codebase's exhaustive docstring
  convention, applied to `check_below_source_sensitivity`'s multi-paragraph
  docstring and the two new `LintDoc` field docstrings, mirroring every
  other function in `lint.py`; and (b) test breadth — 8 pure-function
  scenarios plus 6 CLI scenarios across three test files, each with a
  multi-line docstring explaining the design rule it pins. None of the
  overage is control-flow complexity: `check_below_source_sensitivity`
  itself is under 60 lines of actual logic. Still within the session's
  cached 800-line-per-PR budget (PR1's 593 + PR2's 702 = 1295 combined,
  but the forecast's "Low per PR (against 800-line session budget)"
  phrasing evaluates each PR independently against 800, not as a running
  sum — flagged for the orchestrator to confirm that reading is correct
  before PR3 is sized). No code was trimmed to force the estimate.
- None otherwise. Full suite green, ruff/mypy clean, no flakes observed in
  this run's test executions.

## Remaining Tasks (NOT in this run's scope)

- [ ] Phase 11-14 (PR3): `backfill-sensitivity` verb + ADR-0012

## Workload / PR Boundary (PR2)

- Mode: stacked PR slice (chain strategy: `stacked-to-main`)
- Current work unit: PR2 — lint/status below-source-sensitivity /
  multi-source-uncovered detection
- Boundary: starts at PR1's tip (`cb5a450`) on `feat/extract-descendant-scan`;
  ends at commit `ebc801d` on the same branch (see Deviations from Design
  #2 re: no separate branch cut this run). PR3
  (`feat/backfill-sensitivity-verb`) is independent of PR2 and also targets
  `feat/extract-descendant-scan` per `tasks.md`.
- Estimated review budget impact: 702 changed lines (see Issues Found) —
  above the 400-line single-PR default guard and above the tasks doc's own
  ~150-250 estimate, within the 800-line session budget evaluated per-PR.

## Status (PR2)

10/10 PR2 tasks complete (Phases 6-10, tasks 6.1 through 10.1). Full
suite: **2596 passed** (2579 PR1 baseline + 17 new PR2 tests, 14 as first
written plus 3 from the bounded review correction). `ruff
check`/`ruff format --check`/`mypy` all clean. Ready for `sdd-verify` on
PR2's scope. PR3 (Phases 11-14) remains untouched, as instructed.
