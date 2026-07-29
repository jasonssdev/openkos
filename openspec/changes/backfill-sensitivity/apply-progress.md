# Apply Progress: Backfill sensitivity onto existing provenance descendants (#231)

## Scope of this run

PR1 only (Phases 1-5): extract descendant-scan out of `set_sensitivity_cmd`
(#235) + name landed paths on Phase-B write failure (#233).

## Branch

`feat/extract-descendant-scan`, off `main` (489672a).

## Mode

Strict TDD (RED -> GREEN per pinned commit order in `design.md`/prompt).

## Commits (in order)

| # | SHA | Message | Type |
|---|-----|---------|------|
| 1 | `58e67f9` | `test(bundle): pin resolve_source_raises and find_unresolvable_provenance (RED)` | RED |
| 2 | `250060a` | `refactor(cli): extract descendant-scan out of set_sensitivity_cmd (GREEN)` | GREEN |
| 3 | `63122f8` | `test(cli): pin Phase-B landed-path failure message (RED)` | RED |
| 4 | `4bcc7d5` | `fix(cli): name landed paths on set-sensitivity Phase-B write failure (GREEN)` | GREEN |

## TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR |
|------|-----|-------|----------|
| 1.1/1.2 — characterization tests for `resolve_source_raises`/`find_unresolvable_provenance` | `58e67f9`: 11 new tests fail with `AttributeError: module 'openkos.bundle.provenance' has no attribute ...` (import/attr error, functions do not exist) | `250060a`: all 11 pass | N/A — pure move, no further refactor needed |
| 2.1-2.4 — extract helper, rewire `set_sensitivity_cmd` | (covered by 1.1/1.2's RED) | `250060a`: `tests/unit/bundle/test_provenance_source_raises.py` (11) + `tests/unit/cli/test_set_sensitivity.py` (36, not 29 as estimated) all GREEN, byte-identical | Redundant `sorted()` call removed from `resolve_source_raises` before commit (helper already returns sorted ids) |
| 3.1/3.2 — Phase-B landed-path RED | `63122f8`: `test_phase_b_failure_names_the_landed_paths` and `test_phase_b_failure_with_zero_landed_paths` fail ONLY on the landed-path assertions; the pinned first-sentence assertion (`"failed while writing the set-sensitivity"`) passes on both, confirming it was accurately captured, not invented | `4bcc7d5`: both pass | N/A |
| 4.1/4.2 — landed-path message GREEN | (covered by 3.1/3.2's RED) | `4bcc7d5`: full `test_set_sensitivity.py` — 38/38 GREEN (36 + 2 new) | None needed |

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest tests/unit/bundle/test_provenance_source_raises.py tests/unit/cli/test_set_sensitivity.py` -> 47 passed (before Phase 3/4) -> 49 passed (after, including the 2 landed-path tests) |
| Full suite | `uv run pytest -q` -> before this change: 2565 passed; after this change: **2578 passed** (2565 + 11 characterization + 2 landed-path) |
| Runtime harness | `uv run python -m openkos.cli.main set-sensitivity <source-id> <level>` exercised indirectly through the full `CliRunner`-based `test_set_sensitivity.py` suite (init workspace, ingest a Source, set-sensitivity with `--auto`, inspect resulting bundle files and git commit) — no separate manual run needed since the CLI test suite already drives the real Typer app end-to-end against a tmp workspace |
| Rollback boundary | `git revert` the 4 commits on `feat/extract-descendant-scan` (or reset the branch to `489672a`) restores `set_sensitivity_cmd`'s inline scan, `_DescendantRaise`, and the un-appended failure message; no other verb or file is touched |

## Lint / Typecheck

- `uv run ruff check src/openkos/cli/main.py src/openkos/bundle/provenance.py src/openkos/model/okf.py tests/unit/bundle/test_provenance_source_raises.py tests/unit/cli/test_set_sensitivity.py` -> All checks passed
- `uv run ruff format --check` on the same files -> all formatted (one file needed `ruff format` applied before commit)
- `uv run mypy src/openkos/cli/main.py src/openkos/bundle/provenance.py src/openkos/model/okf.py` -> Success: no issues found in 3 source files

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/model/okf.py` | Modified | Added `okf.DescendantRaise` frozen dataclass (no `path` field) next to `ProvenanceRewrite` |
| `src/openkos/bundle/provenance.py` | Modified | Extracted `provenance_closure` fixpoint core; `find_provenance_descendants` now delegates to it; added `resolve_source_raises` and `find_unresolvable_provenance` |
| `src/openkos/cli/main.py` | Modified | Rewired `set_sensitivity_cmd`'s Phase-A scan to call the two new helpers; dropped private `_DescendantRaise`; added `landed` path tracking and the appended failure-message sentence in Phase B |
| `tests/unit/bundle/test_provenance_source_raises.py` | Created | 11 characterization tests for the two new pure helpers |
| `tests/unit/cli/test_set_sensitivity.py` | Modified | Added 2 tests: `test_phase_b_failure_names_the_landed_paths`, `test_phase_b_failure_with_zero_landed_paths` |

## Changed-Line Totals (PR1 vs `main`)

`git diff --numstat main...feat/extract-descendant-scan`:

| File | + | - |
|---|---|---|
| `src/openkos/bundle/provenance.py` | 146 | 27 |
| `src/openkos/cli/main.py` | 42 | 74 |
| `src/openkos/model/okf.py` | 22 | 0 |
| `tests/unit/bundle/test_provenance_source_raises.py` | 178 | 0 |
| `tests/unit/cli/test_set_sensitivity.py` | 73 | 0 |
| **Total** | **461** | **101** |

**Total changed lines: 562** (additions + deletions), against the tasks doc's
~130-200 estimate for PR1. See Risks below.

## Deviations from Design

None in behavior or structure. One naming clarification: the tasks doc says
"all 29 existing tests" for `test_set_sensitivity.py`; the file actually
contains 36 test functions (verified via `uv run pytest -q`, both before and
after this change) — the design/tasks estimate was simply off by count, not
a scope change. Recorded in Phase 2's checklist above.

## Issues Found

- **Changed-line estimate miss**: actual PR1 diff is 562 changed lines vs
  the tasks doc's ~130-200 estimate. The overage is concentrated in
  docstrings (this codebase's established convention of exhaustive
  design-rationale docstrings on every public function/dataclass) and in
  the two new test files, not in control-flow complexity — the actual
  production control-flow change is a like-for-like move plus ~16 lines
  net for the landed-path tracking. Still within the session's cached
  800-line review budget, but exceeds the skill's default 400-line
  single-PR guard. Flagged for the orchestrator/reviewer; no code was
  trimmed to force the estimate, since cutting the docstrings would break
  this project's established convention (every other module in
  `bundle/provenance.py` carries comparably detailed docstrings).

## Remaining Tasks (NOT in this run's scope)

- [ ] Phase 6-10 (PR2): lint/status wiring — `LintDoc.sensitivity`/`.provenance`, `check_below_source_sensitivity`
- [ ] Phase 11-14 (PR3): `backfill-sensitivity` verb + ADR-0012

## Workload / PR Boundary

- Mode: stacked PR slice (chain strategy: `stacked-to-main`)
- Current work unit: PR1 — closes #235, #233
- Boundary: starts at `main` (489672a); ends at commit `4bcc7d5` on
  `feat/extract-descendant-scan`. PR2 (`feat/lint-below-source-sensitivity`)
  and PR3 (`feat/backfill-sensitivity-verb`) both target this branch per
  `tasks.md`'s PR Assignment section.
- Estimated review budget impact: 562 changed lines (see Risks) — above the
  400-line single-PR default guard, within the 800-line session budget.

## Status

12/12 PR1 tasks complete (Phases 1-5, tasks 1.1 through 5.2). Ready for
`sdd-verify` on PR1's scope. PR2 and PR3 (Phases 6-14) remain untouched, as
instructed.
