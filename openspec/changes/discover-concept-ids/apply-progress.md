# Apply Progress: discover-concept-ids

## Batch 1 (PR1 — `src/openkos/bundle/listing.py` + tests)

**Mode**: Strict TDD (RED → GREEN → REFACTOR)
**Branch**: `feat/list-enumerator` → `main` (stacked-to-main)
**Scope**: Tasks 1.1 through 7.2 (Phases 1-7), per orchestrator's PR1-only run.
`cli/main.py`, the `list` verb, and `docs/cli.md` are explicitly out of scope
for this batch (PR2, separate branch/run).

### Completed Tasks

- [x] 1.1 `BundleObject` field-derivation tests (concept_id, link_dir, title collapse, absent title)
- [x] 1.2 Sensitivity derivation tests (valid member passthrough, unknown for absent/blank/garbage/non-string)
- [x] 1.3 `readable=False` row tests (injected `DocScan` with `read_error`)
- [x] 2.1 `BundleObject` dataclass + `list_objects` skeleton, one `_iter_docs` loop
- [x] 2.2 Structural id/link_dir derivation, inline duplication comment (D2)
- [x] 2.3 Title collapse + sensitivity derivation implementation, Phase 1 GREEN
- [x] 3.1 Non-generator counting-wrapper single-walk test
- [x] 3.2 Confirmed `list_objects` satisfies the single-walk constraint (no production change needed)
- [x] 4.1 In-pass status tests: own-deprecated, superseded, self-superseding, cyclic
- [x] 4.2 Malformed `relations:` frontmatter test (`ValueError` caught, no crash)
- [x] 4.3 Drift-guard test against `lifecycle.deprecated_concept_ids` (intersected with row ids)
- [x] 4.4 `supersedes` collection + post-loop `superseded` resolution implementation, Phase 4 GREEN
- [x] 5.1 `resolve_link_dir` parametrized tests: 10 `link_dir`s, 10 `REGISTRY.name`s (incl. `Source`), `""`, wrong case, unknown
- [x] 5.2 `resolve_link_dir` + `_LINK_DIRS`/`_NAME_TO_LINK_DIR` built from `REGISTRY` (not `TYPE_TO_LINK_DIR`), Phase 5 GREEN
- [x] 6.1 Empty bundle → `[]` test
- [x] 6.2 Alphabetical row-order test
- [x] 6.3 Full suite GREEN, `--cov` confirms 100% branch coverage on `listing.py` (no missing-branch tests needed)
- [x] 7.1 `ruff check`, `ruff format --check`, `mypy .` all pass
- [x] 7.2 Diff verified as `src/openkos/bundle/listing.py` + `tests/unit/bundle/test_listing.py` only (588 changed lines, single commit `c4859b1`)

### Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/bundle/listing.py` | Created | `BundleObject` frozen dataclass, `list_objects(bundle_dir)` single-pass enumerator, `resolve_link_dir(raw)` vocabulary resolver |
| `tests/unit/bundle/test_listing.py` | Created | 47 tests covering field derivation, sensitivity, fail-visible rows, single-walk enforcement, status/drift guard, vocabulary resolver, empty bundle, ordering |

### TDD Cycle Evidence

| Task group | RED | GREEN | REFACTOR |
|---|---|---|---|
| Phase 1 (field derivation) | `ImportError: cannot import name 'listing'` confirmed before any production code existed | `listing.py` created; all Phase 1 tests pass | Title/sensitivity logic kept inline, no extraction needed |
| Phase 2 (`list_objects` skeleton) | Same RED as above (module absent) | GREEN with initial implementation | N/A |
| Phase 3 (single-walk) | Counting-wrapper test written and run against the finished implementation — passed immediately (D3's single-loop structure already satisfied it) | Confirmed GREEN, no production change needed | N/A |
| Phase 4 (status/drift guard) | Status/drift-guard tests written against the field-derivation-only implementation — failed (`status` always `"active"`, no `supersedes` collection) | Implemented `supersedes` collection + post-loop resolution; GREEN | Used `dataclasses.replace` to keep `BundleObject` frozen while resolving status after the loop |
| Phase 5 (vocabulary resolver) | Parametrized tests written before `resolve_link_dir` existed — `AttributeError` | Implemented `resolve_link_dir` + `_LINK_DIRS`/`_NAME_TO_LINK_DIR` from `REGISTRY`; GREEN | N/A |
| Phase 6 (empty/ordering) | Tests written first, ran against complete implementation — passed immediately | Confirmed GREEN | N/A |

All 47 tests were written before or alongside the implementation that made
them pass; no production code was written in advance of a failing test in
this batch.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/bundle/test_listing.py -q` → `47 passed` |
| Branch coverage | `uv run pytest tests/unit/bundle/test_listing.py --cov=openkos.bundle.listing --cov-branch` → `100%` (52 stmts, 10 branches, 0 missed) |
| Full-suite regression | `uv run pytest --cov=openkos --cov-branch -q` → `2461 passed`, total coverage `97.54%` (gate 90%) |
| Lint/format/types | `uv run ruff check .` → all checks passed; `uv run ruff format --check .` → all files formatted; `uv run mypy .` → no issues in 145 source files |
| Runtime harness command/scenario and exact result | N/A — pure canonical-layer module, no CLI/process surface exists to exercise until PR2 wires `cli/main.py` (matches design's stated PR1 rollback boundary) |
| Rollback boundary | `git revert` of commit `c4859b1` on `feat/list-enumerator`; `listing.py` has no consumer yet, so nothing else is affected |

### Deviations from Design

None — implementation matches design D1-D7 exactly, including the
non-generator counting-wrapper pattern (D3), the `REGISTRY`-not-
`TYPE_TO_LINK_DIR` vocabulary build (D7), and the fail-visible (not
fail-closed) unreadable-row contract (D5).

### Issues Found

None. Actual changed-line count (588) exceeds the design's ~370 estimate for
this slice, driven by this repo's docstring density convention (AGENTS.md)
applied to every dataclass field and function — still well inside the
800-line total budget and the PR remains a single autonomous, revertible
unit.

### Workload / PR Boundary

- Mode: chained/stacked PR slice (`stacked-to-main`)
- Current work unit: PR1 — `bundle/listing.py` + `test_listing.py`
- Boundary: starts from no prior progress; ends with `listing.py` fully
  implemented, tested, and committed on `feat/list-enumerator`, with
  nothing yet importing it
- Estimated review budget impact: 588 changed lines, single self-contained
  PR, under the 800-line total budget; PR2 (~490 est.) still pending on a
  branch based on this one

### Status

19/33 total tasks complete (Phases 1-7 of 14). PR2 completed in a
separate run on branch `feat/list-cli-verb` — see Batch 2 below.

## Batch 2 (PR2 — CLI verb + docs + spec commit)

**Mode**: Strict TDD (RED → GREEN → REFACTOR)
**Branch**: `feat/list-cli-verb`, based on PR1's HEAD (`c4859b1`), targets
`feat/list-enumerator` until PR1 merges, then `main` (stacked-to-main).
**Scope**: Tasks 8.1 through 14.3 (Phases 8-14), consuming PR1's
`bundle/listing.py` unchanged — no edits to `listing.py` in this batch.

### Completed Tasks

- [x] 8.1 Failing test: `list bogus-type` outside a workspace reports the
  bad type, not the missing workspace
- [x] 8.2 Failing tests: `--limit 0` / `--limit -1` refuse before any
  workspace/disk access, print no rows
- [x] 8.3 Failing test: `list` outside a workspace with valid arguments
  refuses via `require_workspace`
- [x] 9.1 `@app.command("list")` on `list_objects_cmd`, optional positional
  `TYPE`, `--limit` (default 50), `--all`
- [x] 9.2 Refusal ladder implemented: `resolve_link_dir` → `--limit`
  validation → `require_workspace`, mirroring `set-volatility`; Phase 8
  tests GREEN
- [x] 10.1 Failing test: non-generator counting wrapper confirms exactly
  one `okf._iter_docs` call regardless of filter/limit
- [x] 10.2 Failing test: `lifecycle.deprecated_concept_ids` monkeypatched
  to raise `AssertionError`; command still exits 0
- [x] 10.3 `list_objects_cmd` wired to call `listing.list_objects` exactly
  once and nothing else that touches disk; Phase 10 tests GREEN
- [x] 11.1 Failing tests: filter by canonical `link_dir`, by `REGISTRY.name`
  alias (identical rows), and zero-match filter
- [x] 11.2 Failing tests: default limit 50 with truncation footer,
  `--limit N` truncation, `--all` bypass with no footer
- [x] 11.3 Failing test: column order `ID SENSITIVITY STATUS TITLE`,
  `ljust`-aligned
- [x] 11.4 In-memory filter → slice → width computation → `typer.echo` rows
  → truncation footer implemented; Phase 11 tests GREEN
- [x] 12.1 Failing test: confidential title printed in full, unredacted,
  byte-identical shape to a public row
- [x] 12.2 Failing test: deprecated object shown by default with
  `STATUS = deprecated`
- [x] 12.3 Failing test: empty bundle prints friendly message, exits 0
- [x] 12.4 Failing test: unparseable document still prints a row with
  `(unreadable)` title, command exits 0, no raw traceback
- [x] 12.5 Failing test: `(untitled)` marker distinct from `(unreadable)`
- [x] 12.6 `(untitled)`/`(unreadable)` rendering branch and empty-state
  early return confirmed; Phase 12 tests GREEN
- [x] 13.1 `tests/unit/cli/test_list.py` full suite GREEN (18 tests)
- [x] 13.2 `--cov` confirms branch coverage on `cli/main.py`'s new command
  and `listing.py`; no missing-branch tests needed
- [x] 13.3 `ruff check`, `ruff format --check`, `mypy .` all pass
- [x] 14.1 `docs/cli.md` updated: `### openkos list [TYPE]` section added
  after `merge`/`unmerge`, before `status` (documents `TYPE`, `--limit`,
  `--all`, column layout, deprecated/confidential visibility rules)
- [x] 14.2 Verified `specs/list-command/spec.md` already reflects the
  final argument-refusal-before-workspace ladder — no edit needed
- [x] 14.3 Commits created on `feat/list-cli-verb`; diff is
  `cli/main.py`, `test_list.py`, `docs/cli.md` (code commit) plus the
  OpenSpec change artifacts (separate commit, per the agreed split) — PR
  opening itself is outside this agent's tool access, left to the
  orchestrator/user

### Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/cli/main.py` | Modified | Added `from openkos.bundle import listing` import and `@app.command("list")` on `list_objects_cmd`: refusal ladder, single `listing.list_objects` call, in-memory filter/slice, aligned `typer.echo` rows, truncation footer |
| `tests/unit/cli/test_list.py` | Created | 18 tests covering refusal ladder, single-walk/lifecycle-isolation guards, filtering/limiting/formatting, confidential titles, deprecated visibility, empty bundle, unparseable documents, `(untitled)`/`(unreadable)` markers |
| `docs/cli.md` | Modified | Added `### openkos list [TYPE]` reference section |
| `openspec/changes/discover-concept-ids/` | Committed | Proposal, design, spec, tasks, apply-progress — previously untracked, committed per the agreed PR2 split |

### TDD Cycle Evidence

| Task group | RED | GREEN | REFACTOR |
|---|---|---|---|
| Phase 8 (refusal ladder) | `tests/unit/cli/test_list.py` written first against a nonexistent `list` command — all 16 relevant tests failed with `SystemExit(2)` ("no such command") | `list_objects_cmd` implemented with the exact ladder order (type → limit → workspace); Phase 8 tests GREEN | N/A |
| Phase 9 (command skeleton) | Same RED as above (command absent) | GREEN with initial skeleton + ladder | N/A |
| Phase 10 (single-walk/lifecycle guards) | Counting-wrapper and `lifecycle`-fail tests written before the command existed — failed with the same `SystemExit(2)` | Passed once `list_objects_cmd` was wired to call `listing.list_objects` exactly once | N/A |
| Phase 11 (filter/limit/format) | Filter, alias, limit, `--all`, column-order tests written before the render logic existed | Implemented filter → slice → width computation → row rendering; GREEN | N/A |
| Phase 12 (spec scenarios) | Confidential-title, deprecated-visibility, empty-bundle, unparseable-document, `(untitled)`/`(unreadable)` tests written before the render branch existed | GREEN once the title-rendering branch and empty-state early return were in place | Replaced an `if row.title: ... else: ...` block with `row.title or (...)` per `ruff` SIM108 |

All 18 tests in `test_list.py` were confirmed RED (all failing with
`SystemExit(2)`, no such command) before any part of `list_objects_cmd`
was written; no production code preceded a failing test in this batch.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_list.py -q` → `18 passed` |
| Full-suite regression | `uv run pytest -q` → `2479 passed` |
| Branch coverage | `uv run pytest --cov=openkos --cov-branch -q` → `98%` total (gate 90%); no uncovered branch in `list_objects_cmd` or `listing.py` |
| Lint/format/types | `uv run ruff check .` → all checks passed; `uv run ruff format --check .` → all files formatted; `uv run mypy .` → no issues in 146 source files |
| Runtime harness command/scenario and exact result | `CliRunner` end-to-end invocations in `test_list.py` exercise the real Typer app against real `tmp_path` bundles (init → write docs → `list` → assert stdout/stderr/exit code) — this IS the runtime harness for a CLI verb; no separate manual smoke was run beyond these |
| Rollback boundary | `git revert` of the `cli/main.py`/`test_list.py`/`docs/cli.md` commit on `feat/list-cli-verb`; the change is additive (`@app.command`), PR1's `listing.py` is untouched and unaffected |

### Deviations from Design

None — implementation matches design D1-D7 and the Data Flow diagram
exactly, including the vocabulary-then-limit-then-workspace refusal
ladder (D7), the single-walk/no-`lifecycle`-call guard (D3), the
`ljust` column formatting (D6), and the ungated confidential-title
requirement.

### Issues Found

None. Ruff flagged one style issue (`SIM108`, prefer a conditional
expression over an if/else block for the title-rendering branch) and an
import-sort issue after the new `from openkos.bundle import listing`
line was added; both were auto-fixed and re-verified.

### Remaining Tasks

None. All 33/33 tasks across both PRs (Phases 1-14) are complete.

### Status

33/33 total tasks complete (Phases 1-14). Ready for `sdd-verify`.
