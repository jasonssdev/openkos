# Tasks: Backfill sensitivity onto existing provenance descendants (#231)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~130-200, PR2 ~150-250, PR3 ~250-400 (total ~530-850) |
| 400-line budget risk | Low per PR (against 800-line session budget); PR3 alone approaches 400 |
| Chained PRs recommended | Yes |
| Suggested split | PR1 extract+message (#235/#233) -> PR2 lint/status detection -> PR3 backfill verb + ADR-0012 |
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
| 2 | `LintDoc.sensitivity`/`.provenance` + `check_below_source_sensitivity`, wired into `lint`/`status` | PR 2 | `uv run pytest tests/unit/test_lint_below_source.py tests/unit/test_lint.py tests/unit/test_status.py` | `uv run python -m openkos.cli.main lint` / `status` against a fixture bundle with a below-Source and a multi-source-uncovered doc | Revert PR2 branch; findings disappear, nothing was ever written |
| 3 | `backfill-sensitivity` verb + ADR-0012 | PR 3 | `uv run pytest tests/unit/cli/test_backfill_sensitivity.py` | `uv run python -m openkos.cli.main backfill-sensitivity` against a temp bundle with pre-#219 gaps | Revert PR3 branch; already-backfilled bundles keep raised values (raise-only); each sweep is one revertable `_autocommit` |

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

- [ ] 6.1 Create `tests/unit/test_lint_below_source.py`: hand-built `LintDoc` lists for both categories — single-Source below trigger via `combine_sensitivity` inequality (incl. missing/dirty `sensitivity` fail-closed under a `public` Source); same-Source multi-cite is covered (`below-source-sensitivity`, not uncovered); Source-plus-foreign-derived cite is `multi-source-uncovered`; unresolvable cite falls into neither category
- [ ] 6.2 Add a construction test asserting `LintDoc(*seven_non_defaulted_fields)` still constructs without `sensitivity`/`provenance` (guards `tests/unit/resolution/test_volatility_typing.py:612`)
- [ ] 6.3 Confirm 6.1/6.2 fail RED (fields/function do not exist yet)

## Phase 7: GREEN — LintDoc fields + check_below_source_sensitivity (PR2)

- [ ] 7.1 Add `sensitivity: str = ""` and `provenance: tuple[str, ...] = ()` (`.md`-stripped) to `LintDoc`, defaulted like `extraction_status`/`resource`; fill both in `collect_docs` (`lint.py:140-164`) from already-parsed frontmatter
- [ ] 7.2 Add `below_source: list[LintFinding]` and `multi_source_uncovered: list[LintFinding]` fields to `LintReport`
- [ ] 7.3 Implement `check_below_source_sensitivity(docs) -> list[LintFinding]` in `lint.py`, taking only `docs` (no-fifth-walk guard, `lint.py:556-560`): builds the closure map from `LintDoc.provenance`, calls `bundle.provenance.provenance_closure` and `okf.combine_sensitivity`; emits `below-source-sensitivity` (single-Source closure member, `combine_sensitivity` inequality) and `multi-source-uncovered` (non-empty provenance, all cited ids resolve, no single-Source closure membership, sensitivity below cited high-water-mark)
- [ ] 7.4 Run `uv run pytest tests/unit/test_lint_below_source.py` — GREEN

## Phase 8: RED — lint/status wiring (PR2)

- [ ] 8.1 Add scenarios to `tests/unit/test_lint.py`: `below-source-sensitivity`/`multi-source-uncovered` findings surface via `openkos lint`; exit code stays 0; clean bundle reports zero findings; no bundle file created/modified/deleted
- [ ] 8.2 Add scenarios to `tests/unit/test_status.py`: both categories surface under "needs attention", labeled distinctly, `multi-source-uncovered` marked not covered by `backfill-sensitivity`; still exits 0; no second `collect_docs()` call (reuse the existing `docs` list)
- [ ] 8.3 Confirm 8.1/8.2 fail RED (wiring not present)

## Phase 9: GREEN — wire into lint/status (PR2)

- [ ] 9.1 Call `check_below_source_sensitivity(docs)` in `lint` (`main.py:5352`), split its results into `LintReport.below_source`/`.multi_source_uncovered`, render findings, exit code unchanged
- [ ] 9.2 Reuse the same `docs` list in `status` (`main.py:5108`) to surface both categories under "needs attention"
- [ ] 9.3 Run `uv run pytest tests/unit/test_lint_below_source.py tests/unit/test_lint.py tests/unit/test_status.py` — all GREEN

## Phase 10: PR2 checkpoint

- [ ] 10.1 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files

## Phase 11: RED — backfill-sensitivity CLI tests (PR3)

- [ ] 11.1 Create `tests/unit/cli/test_backfill_sensitivity.py`: raise-all-below-Sources scenario; never-lowers scenario; idempotent second run (zero writes, no empty commit); `--auto` skips only the prompt; non-TTY without `--auto` refuses; declining the prompt writes nothing; explicit no-op line on zero staged raises
- [ ] 11.2 Add: `extraction_status: failed` Source still a valid closure root; a Source that is itself a provenance descendant of another Source is raised (D6 scenario); a descendant citing two ids inside the same Source's closure is raised; a descendant citing two unrelated Sources is never raised (skip-and-report, D3)
- [ ] 11.3 Add Phase-B partial-write scenario: patch `fsio.write_atomic` to fail after 2 of 3 descendant writes; assert non-zero exit, first two files raised on disk, failure message names both landed paths (D9, mirrors PR1 Phase 3-4)
- [ ] 11.4 Add merge-by-max scenario: two Sources both cite the same descendant via chained closures; assert the merged raise picks the highest `SENSITIVITY_ORDER.index(new_level)`, never calling `okf._rank`
- [ ] 11.5 Confirm all of 11.1-11.4 fail RED (verb does not exist yet)

## Phase 12: GREEN — implement backfill-sensitivity verb (PR3)

- [ ] 12.1 Implement `backfill_sensitivity_cmd` in `main.py`, Phase A: `require_workspace` -> `read_config` -> one `rglob` bundle snapshot (reserved names skipped) -> for each `sorted(Source ids)` call `resolve_source_raises` -> merge by `concept_id` keeping the highest `okf.SENSITIVITY_ORDER.index(new_level)` (ties: first Source in sorted order) -> explicit no-op line + exit 0 + no log/commit when empty -> sorted preview -> confirm ladder (`--auto` > `cfg.review` > TTY confirm > refuse)
- [ ] 12.2 Implement Phase B: write every merged raise (sorted by `concept_id`), append one `log.md` entry, one `_autocommit`; track `landed` paths and name them verbatim on partial failure (D9), mirroring PR1's message shape
- [ ] 12.3 Do NOT call `find_unresolvable_provenance` anywhere in this verb (D8) — its signal is the existing `dangling` lint finding
- [ ] 12.4 Add commit-state threat-matrix RED test `test_backfill_second_run_stages_nothing_and_creates_no_commit` if not already covered by 11.1's idempotency scenario
- [ ] 12.5 Run `uv run pytest tests/unit/cli/test_backfill_sensitivity.py` — all GREEN

## Phase 13: Docs — ADR-0012 (PR3, split into its own docs PR if budget exceeded)

- [ ] 13.1 Create `docs/adr/0012-sensitivity-backfill-per-source-sweep.md`: per-Source sweep, multi-source closures reported not combined, no `type` filter on descendants, no unresolvable-provenance scan in this verb
- [ ] 13.2 Add ADR-0012's row to `docs/adr/README.md` (index currently ends at 0011)
- [ ] 13.3 Update `docs/cli.md` with the new `backfill-sensitivity` verb and its confirm ladder/no-op behavior

## Phase 14: PR3 checkpoint

- [ ] 14.1 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on touched files
- [ ] 14.2 Full suite: `uv run pytest tests/unit/cli/test_backfill_sensitivity.py tests/unit/cli/test_set_sensitivity.py tests/unit/test_lint_below_source.py tests/unit/test_lint.py tests/unit/test_status.py --cov` — all GREEN, coverage holds on touched files
- [ ] 14.3 Confirm issues #231, #235, #233 closable; #232 and #234 remain untouched (Explicitly Not Changed)

## PR Assignment

- **PR1** (`feat/extract-descendant-scan` -> `main`): Phases 1-5 — closes #235, #233
- **PR2** (`feat/lint-below-source-sensitivity` -> `feat/extract-descendant-scan`): Phases 6-10
- **PR3** (`feat/backfill-sensitivity-verb` -> `feat/extract-descendant-scan`, independent of PR2): Phases 11-14 — closes #231
