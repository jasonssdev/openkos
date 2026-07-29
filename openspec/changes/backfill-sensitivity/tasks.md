# Tasks: Backfill sensitivity onto existing provenance descendants (#231)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 593 (landed), PR2 621 (landed), PR3a ~250-400, PR3b ~250-400 (re-sliced after PR1/PR2 landed ~2.5x over original per-PR estimate) |
| 400-line budget risk | Low per PR (against 800-line session budget) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 extract+message (#235/#233) -> PR2 lint/status detection -> PR3a backfill pure sweep core -> PR3b backfill Typer command + ADR-0012 |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Extract `resolve_source_raises`/`find_unresolvable_provenance` into `bundle/provenance.py`, rewire `set_sensitivity_cmd`, name landed paths on Phase-B failure (#235, #233) | PR 1 | `uv run pytest tests/unit/bundle/test_provenance_source_raises.py tests/unit/cli/test_set_sensitivity.py` | `uv run python -m openkos.cli.main set-sensitivity <source-id> <level>` against a temp bundle fixture | Revert PR1 branch; `set_sensitivity_cmd` returns to its inline block, no data touched |
| 2 | `LintDoc.sensitivity`/`.provenance` + `check_below_source_sensitivity`, wired into `lint`/`status` | PR 2 | `uv run pytest tests/unit/test_lint_below_source.py tests/unit/cli/test_lint.py tests/unit/cli/test_status.py` | `uv run python -m openkos.cli.main lint` / `status` against a fixture bundle with a below-Source and a multi-source-uncovered doc | Revert PR2 branch; findings disappear, nothing was ever written |
| 3a | `resolve_backfill_raises` pure bundle-wide sweep core (merge-by-max over every Source) | PR 3a | `uv run pytest tests/unit/bundle/test_resolve_backfill_raises.py` | N/A -- pure function, no filesystem/CLI boundary; exercised end-to-end once PR3b wires it in | Revert PR3a branch; `resolve_backfill_raises` never referenced by any command yet, no data touched |
| 3b | `backfill-sensitivity` Typer command (wires `resolve_backfill_raises` into confirm/write/log/commit) + ADR-0012 | PR 3b | `uv run pytest tests/unit/cli/test_backfill_sensitivity.py` | `uv run python -m openkos.cli.main backfill-sensitivity` against a temp bundle with pre-#219 gaps | Revert PR3b branch; already-backfilled bundles keep raised values (raise-only); each sweep is one revertable `_autocommit` |

## Phase 1: RED — characterization tests (PR1)

- [x] 1.1 Create `tests/unit/bundle/test_provenance_source_raises.py`: pin `resolve_source_raises(files, source_id, level)` output (sorted by `concept_id`, byte-identical `content` via `okf.dump_frontmatter`) and `find_unresolvable_provenance(files, known_extra_ids)` output, including warning order and resource-shaped entries (`main.py:3369-3386`)
- [x] 1.2 Confirm this file fails RED against current `main.py` (helpers do not exist yet)

## Phase 2: GREEN — extract helper (PR1)

- [x] 2.1 Add `okf.DescendantRaise` frozen dataclass (`concept_id`, `current`, `new_level`, `content`; no `path`) next to `ProvenanceRewrite` in `src/openkos/model/okf.py`
- [x] 2.2 Add `resolve_source_raises(files, *, source_id, level) -> list[okf.DescendantRaise]` and `find_unresolvable_provenance(files, *, known_extra_ids=()) -> list[tuple[str,str]]` to `src/openkos/bundle/provenance.py`; extract `provenance_closure` as the fixpoint core (`provenance.py:129-137` moves verbatim), `find_provenance_descendants` delegates to it
- [x] 2.3 Rewire `set_sensitivity_cmd` (`main.py:3339-3411`) to call `resolve_source_raises`/`find_unresolvable_provenance`; drop `_DescendantRaise`; zero edits to `tests/unit/cli/test_set_sensitivity.py`
- [x] 2.4 Run `uv run pytest tests/unit/bundle/test_provenance_source_raises.py tests/unit/cli/test_set_sensitivity.py` — 1.1's file and all pre-existing tests GREEN, byte-identical behavior. `test_set_sensitivity.py` has 29 test FUNCTIONS on `main` (matching this doc's original estimate) which collect as 36 test CASES once `@pytest.mark.parametrize` (`:159`, `:252`) is expanded; the design's "29 existing tests" was correct all along and referred to function count

## Phase 3: RED — Phase-B landed-path guard (PR1, #233)

- [x] 3.1 Add a CLI test constructing the Phase-B partial-failure scenario (patch `fsio.write_atomic` to fail on the Nth call); assert the existing first-sentence text verbatim (`"failed while writing the set-sensitivity"`) AND assert the landed paths — only the landed-path assertion is red
- [x] 3.2 Confirm 3.1 fails RED on the landed-path assertion only

## Phase 4: GREEN — landed-path message (PR1, #233)

- [x] 4.1 In `set_sensitivity_cmd`'s Phase-B write loop, append each path to a `landed: list[str]` after its `write_atomic` returns; on failure append `Already written (left over-classified, not rolled back): bundle/a.md, bundle/b.md.` or `No path was written.` to the existing first sentence
- [x] 4.2 Run `uv run pytest tests/unit/cli/test_set_sensitivity.py` — all pre-existing tests plus the 2 new landed-path tests GREEN: 31 test functions collecting as 38 test cases

## Phase 5: PR1 checkpoint

- [x] 5.1 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files
- [x] 5.2 `uv run pytest tests/unit/bundle/test_provenance_source_raises.py tests/unit/cli/test_set_sensitivity.py` all GREEN

## Phase 6: RED — pure detection function (PR2)

- [x] 6.1 Create `tests/unit/test_lint_below_source.py`: hand-built `LintDoc` lists for both categories — single-Source below trigger via `combine_sensitivity` inequality (incl. missing/dirty `sensitivity` fail-closed under a `public` Source); same-Source multi-cite is covered (`below-source-sensitivity`, not uncovered); Source-plus-foreign-derived cite is `multi-source-uncovered`; unresolvable cite falls into neither category
- [x] 6.2 Add a construction test asserting `LintDoc(*seven_non_defaulted_fields)` still constructs without `sensitivity`/`provenance` (guards `tests/unit/resolution/test_volatility_typing.py:612`)
- [x] 6.3 Confirm 6.1/6.2 fail RED (fields/function do not exist yet)

## Phase 7: GREEN — LintDoc fields + check_below_source_sensitivity (PR2)

- [x] 7.1 Add `sensitivity: str = ""` and `provenance: tuple[str, ...] = ()` (`.md`-stripped) to `LintDoc`, defaulted like `extraction_status`/`resource`; fill both in `collect_docs` (`lint.py:140-164`) from already-parsed frontmatter
- [x] 7.2 Add `below_source: list[LintFinding]` and `multi_source_uncovered: list[LintFinding]` fields to `LintReport`
- [x] 7.3 Implement `check_below_source_sensitivity(docs) -> list[LintFinding]` in `lint.py`, taking only `docs` (no-fifth-walk guard, `lint.py:556-560`): builds the closure map from `LintDoc.provenance`, calls `bundle.provenance.provenance_closure` and `okf.combine_sensitivity`; emits `below-source-sensitivity` (single-Source closure member, `combine_sensitivity` inequality) and `multi-source-uncovered` (non-empty provenance, all cited ids resolve, no single-Source closure membership, sensitivity below cited high-water-mark)
- [x] 7.4 Run `uv run pytest tests/unit/test_lint_below_source.py` — GREEN

## Phase 8: RED — lint/status wiring (PR2)

- [x] 8.1 Add scenarios to `tests/unit/cli/test_lint.py` (actual CLI-wiring test file; see Deviations from Design): `below-source-sensitivity`/`multi-source-uncovered` findings surface via `openkos lint`; exit code stays 0; clean bundle reports zero findings; no bundle file created/modified/deleted
- [x] 8.2 Add scenarios to `tests/unit/cli/test_status.py` (actual CLI-wiring test file; see Deviations from Design): both categories surface under "needs attention", labeled distinctly, `multi-source-uncovered` marked not covered by `backfill-sensitivity`; still exits 0; no second `collect_docs()` call (reuse the existing `docs` list)
- [x] 8.3 Confirm 8.1/8.2 fail RED (wiring not present)

## Phase 9: GREEN — wire into lint/status (PR2)

- [x] 9.1 Call `check_below_source_sensitivity(docs)` in `lint` (`main.py:5352`), split its results into `LintReport.below_source`/`.multi_source_uncovered`, render findings, exit code unchanged
- [x] 9.2 Reuse the same `docs` list in `status` (`main.py:5108`) to surface both categories under "needs attention"
- [x] 9.3 Run `uv run pytest tests/unit/test_lint_below_source.py tests/unit/cli/test_lint.py tests/unit/cli/test_status.py` — all GREEN

## Phase 10: PR2 checkpoint

- [x] 10.1 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files

## Phase 11: RED — pure sweep core characterization (PR3a)

- [x] 11.1 Create `tests/unit/bundle/test_resolve_backfill_raises.py`: raise-all-below-Sources; never-lowers; idempotent second sweep (zero raises on an already-propagated bundle); Source never written as its own closure root; `extraction_status: failed` Source still a valid closure root; a Source that is itself a provenance descendant of another Source is raised (D6 scenario); a descendant citing two ids inside the same Source's closure is raised; a descendant citing two unrelated Sources is never raised (D3); merge-by-max scenario (two Sources chained via nested closures both claim the same descendant, merged raise picks the highest `SENSITIVITY_ORDER.index(new_level)`, never `okf._rank`); deterministic sorted-by-`concept_id` output; a Source with missing `sensitivity` ranks fail-closed as `private`
- [x] 11.2 Confirm all 11 tests fail RED (`resolve_backfill_raises` does not exist yet)

## Phase 12: GREEN — implement resolve_backfill_raises pure sweep core (PR3a)

- [x] 12.1 Implement `_source_levels(files)` helper and `resolve_backfill_raises(files) -> list[okf.DescendantRaise]` in `src/openkos/bundle/provenance.py`: for each `sorted(Source ids)` call `resolve_source_raises` -> merge by `concept_id` keeping the highest `okf.SENSITIVITY_ORDER.index(new_level)` (ties: first Source in sorted order) -> return `sorted()` by `concept_id`
- [x] 12.2 Do NOT call `find_unresolvable_provenance` anywhere in this function (D8) — a bundle-wide run would emit one WARNING per Source on every invocation. Note: the `dangling` lint finding does NOT cover `provenance:` (it scans `relations:` and body links only), so an unresolvable provenance cite stays unreported — see Known Follow-Ups
- [x] 12.3 Run `uv run pytest tests/unit/bundle/test_resolve_backfill_raises.py` — all GREEN
- [x] 12.4 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean; full suite green (2593 -> 2604)

## Phase 13: PR3a checkpoint

- [x] 13.1 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files
- [x] 13.2 Full suite: `uv run pytest -q` — 2604 passed (baseline 2593 + 11 new)
- [x] 13.3 Changed lines: 321 (222 test + 99 implementation), comfortably under the 400-line budget

## Phase 14: RED — backfill-sensitivity CLI tests (PR3b)

- [x] 14.1 Create `tests/unit/cli/test_backfill_sensitivity.py`: raise-all-below-Sources scenario; never-lowers scenario; idempotent second run (zero writes, no empty commit); `--auto` skips only the prompt; non-TTY without `--auto` refuses; declining the prompt writes nothing; explicit no-op line on zero staged raises
- [x] 14.2 Add Phase-B partial-write scenario: patch `fsio.write_atomic` to fail after 2 of 3 descendant writes; assert non-zero exit, first two files raised on disk, failure message names both landed paths (D9, mirrors PR1 Phase 3-4)
- [x] 14.3 Confirm 14.1-14.2 fail RED (verb does not exist yet) — all 11 tests fail with Typer exit code 2 ("No such command 'backfill-sensitivity'")

## Phase 15: GREEN — implement backfill-sensitivity verb (PR3b)

- [x] 15.1 Implement `backfill_sensitivity_cmd` in `main.py`, Phase A: `require_workspace` -> `read_config` -> one `rglob` bundle snapshot (reserved names skipped) -> call `bundle_provenance.resolve_backfill_raises(snapshot)` (PR3a) -> explicit no-op line + exit 0 + no log/commit when empty -> sorted preview -> confirm ladder (`--auto` > `cfg.review` > TTY confirm > refuse)
- [x] 15.2 Implement Phase B: write every merged raise (sorted by `concept_id`), append one `log.md` entry, one `_autocommit`; track `landed` paths and name them verbatim on partial failure (D9), mirroring PR1's message shape
- [x] 15.3 Do NOT call `find_unresolvable_provenance` anywhere in this verb (D8) — its signal is the existing `dangling` lint finding
- [x] 15.4 Commit-state threat-matrix scenario covered by `test_immediate_rerun_after_a_successful_sweep_is_a_no_op` (14.1's idempotency scenario, asserting `_commit_count` is unchanged after the no-op second run) — a separate `test_backfill_second_run_stages_nothing_and_creates_no_commit` was not needed
- [x] 15.5 Run `uv run pytest tests/unit/cli/test_backfill_sensitivity.py` — all GREEN (11 passed)

## Phase 16: Docs — ADR-0012 (PR3b, split into its own docs PR if budget exceeded)

- [x] 16.1 Create `docs/adr/0012-sensitivity-backfill-per-source-sweep.md`: per-Source sweep, multi-source closures reported not combined, no `type` filter on descendants, no unresolvable-provenance scan in this verb
- [x] 16.2 Add ADR-0012's row to `docs/adr/README.md` (index currently ends at 0011)
- [x] 16.3 Update `docs/cli.md` with the new `backfill-sensitivity` verb and its confirm ladder/no-op behavior

## Phase 17: PR3b checkpoint

- [x] 17.1 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files
- [x] 17.2 Full suite: `uv run pytest tests/unit/cli/test_backfill_sensitivity.py tests/unit/bundle/test_resolve_backfill_raises.py tests/unit/cli/test_set_sensitivity.py tests/unit/test_lint_below_source.py tests/unit/cli/test_lint.py tests/unit/cli/test_status.py` — 113 passed (11 + 11 + 38 collected + 9 + 6 + 6 + ... see Work Unit Evidence for exact composition); full repo suite: 2604 -> 2615
- [x] 17.3 Issues #231, #235, #233 all closable after PR1+PR2+PR3a+PR3b; #232 (bundle-wide unresolvable-provenance WARNING scope) and #234 (ambiguous "failed while preparing" message) remain untouched, matching design's Explicitly Not Changed section

## Known Follow-Ups (out of scope for this change)

- **A doc whose `provenance:` cites an unresolvable id is reported by nothing.**
  It falls into neither detection category, because the sweep cannot reach it
  either, and `check_dangling_targets` does not cover it: that check scans
  `relations:` and body markdown links only, never `provenance:`. Such a doc may
  still sit below its Source. Surfacing it needs its own finding kind, which
  would need a spec delta, so it is deliberately not smuggled into this change's
  two categories. Raised by the R1 risk lens during the native review of PR2.

## PR Assignment

- **PR1** (`feat/extract-descendant-scan` -> `main`): Phases 1-5 — closes #235, #233
- **PR2** (`feat/lint-below-source-sensitivity` -> `feat/extract-descendant-scan`): Phases 6-10 — landed as 5 commits directly on `feat/extract-descendant-scan` (see apply-progress)
- **PR3a** (`feat/backfill-sensitivity-core` -> `feat/lint-below-source-sensitivity`): Phases 11-13 — pure sweep core only, no Typer command, no ADR
- **PR3b** (off `feat/backfill-sensitivity-core`): Phases 14-17 — closes #231; the `backfill-sensitivity` Typer command plus ADR-0012
