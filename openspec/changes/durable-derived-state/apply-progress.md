# Apply Progress: durable-derived-state

## PR #1 (1a-i) — Ledger store and crash semantics — DONE

Branch: `feat/ledger-store-1a-i`, based on `tracker/durable-derived-state`.
All 15 tasks complete (Phase 1: Foundation, Phase 2: Two-phase write and
recovery, Phase 3: Documentation).

**Where**:
- `src/openkos/model/okf.py` — generalized `concept_path_for(concept_id, bundle_dir, *, suffix=".md")`; added `STATE_DIRNAME`.
- `src/openkos/bundle/ledger.py` (new) — `ledger_path_for`, `pending_path_for`, `read_entries`, `iter_ledgers`, `write_pending`/`commit_pending`/`discard_pending`/`recover` (two-phase write + total-function recovery), `write_entries` (unmerge's pop-last).
- `src/openkos/bundle/merge.py` — `plan_merge` takes `existing_entries`, returns `MergePlan.ledger_entries`; `plan_unmerge` takes `entries`, returns `UnmergePlan.remaining_entries`.
- `src/openkos/cli/main.py` — `merge_core` writes S1(write_pending)->V(survivor)->S2(commit_pending)->D(remove absorbed); `MergeResult.ledger_sidecar_path`; `unmerge()` pops the LIFO tail via `ledger.write_entries`; `_reject_torn_ledger_write` preflight guard.
- `docs/adr/0013-relocate-merge-ledger-to-bundle-state.md` (new, Proposed).
- `docs/adr/0002-reversible-merge-ledger.md` — Status line only.
- `docs/adr/README.md` — added ADR-0013 row.

**Learned**: `merge_core`'s two `_autocommit` call sites (the `merge()`
command AND curate's `_commit_one_merge`) build their own inline path
lists — both needed updating. All quality gates green at merge time:
4156 passed/1 skipped; coverage 97.11%.

---

## PR #2 (1a-ii) — Read wiring and privacy sweep coverage — DONE

Branch: `feat/ledger-readers-1a-ii`, based on PR #1's branch. All 8 tasks
complete (Phase 1: Read-path wiring, Phase 2: EXCLUDE walk regression
guard, Phase 3: INCLUDE walk privacy sweep coverage).

### TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR | Notes |
|---|---|---|---|---|
| 1.1 | `test_merged_content_blocked_called_once_per_ledger_entry_not_per_survivor` fails (0 calls vs 3) before wiring | passes after 1.2 | mutation-tested (hoisted-to-per-survivor mutation caught, reverted) | |
| 1.2 | (see 1.1) | `contradiction.py` swaps to `_read_ledger_entries` (layering-forced local helper, not `bundle.ledger`) | added parity test for duplicated `_LEDGER_SUFFIX` constant | Deviation: see below |
| 2.1 | test written, ran GREEN immediately (design-predicted: zero code needed) | n/a | mutation-tested (`.ledger.okf` -> `.ledger.md` caught by both assertions, reverted) | Regression-guard style |
| 3.1 | n/a — `iter_ledgers` already existed + tested from PR#1 | n/a | n/a | Confirmed, not re-implemented |
| 3.2 | `test_forgetting_a_survivor_deletes_its_own_ledger_sidecar` + `test_sweep_ledger_sidecars_drops_matching_entries_from_other_survivors` fail before `_sweep_ledger_sidecars_for_ids` existed | pass after implementation | mutation-tested (own-sidecar-deletion loop disabled, caught, reverted) | |
| 3.3 | covered by 46-test `test_purge.py` suite passing with the reused sweep primitive wired into both purge exit paths | pass | n/a | No new dedicated RED test beyond 3.4's; reuses 3.2's primitive |
| 3.4 | `test_purging_a_merge_survivor_removes_its_ledger_sidecar_from_history` fails before `expunge_targets` extension | passes after | mutation-tested (extension removed, caught, reverted) | Scoped to Scenario 1 only — see Risks |
| 3.5 | test written, ran GREEN immediately (design-predicted: zero code needed once re-scoped to Walk-Incompleteness Observability) | n/a | n/a | Re-scoped from literal task text — see Deviations |
| 3.6 | new `check_state_dir_contains_no_markdown` had no prior coverage; wrote 8 tests (5 unit + 3 CLI) against the new function before it existed in `LintReport`/CLI wiring | pass | mutation-tested (`scan_markdown_under_state_dir` short-circuited to `[]`, caught, reverted) | |

### Files Changed

| File | Action | What |
|---|---|---|
| `src/openkos/resolution/contradiction.py` | Modified | `_read_ledger_entries` local helper (layering-safe), `_LEDGER_SUFFIX` constant + docstring explaining the duplication, swapped `_merged_body_candidates`'s entry source |
| `src/openkos/cli/main.py` | Modified | New `_sweep_ledger_sidecars_for_ids` shared primitive; wired into `forget`'s Phase B + `_autocommit`; wired into `purge`'s live-tree cleanup (both exit paths) + post-rewrite commit path list; `expunge_targets` extended with each purge-set member's own ledger sidecar; new `lint()` "State-dir markdown:" section |
| `src/openkos/lint.py` | Modified | New `scan_markdown_under_state_dir`, `check_state_dir_contains_no_markdown`, `LintReport.state_dir_markdown` field |
| `tests/unit/resolution/test_contradiction.py` | Modified | `_write_survivor_with_merges` helper rewritten to use `ledger.write_entries`; corrupt-ledger test rewritten to corrupt the sidecar, not concept frontmatter; new per-entry call-count test; new `_LEDGER_SUFFIX` parity test |
| `tests/unit/bundle/test_ledger_walk_exclusion.py` | Created | EXCLUDE-walk regression guard (task 2.1) |
| `tests/unit/cli/test_forget.py` | Modified | 2 new tests (task 3.2) |
| `tests/unit/cli/test_purge.py` | Modified | 1 new test (task 3.4) |
| `tests/unit/cli/test_observability.py` | Modified | 1 new test (task 3.5) |
| `tests/unit/test_lint_state_dir_markdown.py` | Created | 5 unit tests (task 3.6) |
| `tests/unit/cli/test_lint.py` | Modified | 3 new tests (task 3.6) |
| `tests/unit/cli/test_curate.py` | Modified | `_write_survivor_with_merges` helper fixed for the same API-shape change as `test_contradiction.py`'s (pre-existing 2 tests broke, now fixed) |

### Deviations from design

1. **Task 1.2 layering constraint (undiscovered by design.md)**: `resolution/contradiction.py` cannot import `openkos.bundle.ledger` — `tests/unit/resolution/test_layering.py::test_resolution_only_imports_model_okf_from_canonical` forbids it (`resolution` may only import `openkos.model.okf` read-only). Design's file-changes table assumed a direct `ledger.read_entries` call. Fixed by adding a local `_read_ledger_entries` helper in `contradiction.py` that reconstructs the sidecar path via `okf.concept_path_for(..., suffix=...)` — the exact primitive PR#1's task 1.1 generalized, apparently for this purpose — plus a duplicated `_LEDGER_SUFFIX` constant with a parity test pinning it against `bundle.ledger.LEDGER_SUFFIX`, mirroring the existing `vcs/git.py` `_identity`/parity-test precedent in this codebase.
2. **Task 3.5 re-scoped**: the task text said "`set-sensitivity` sweep / `sensitivity_concept_ids` (`cli/main.py:5172, 5444`) includes `bundle/.state/ledger/`", pointing at `set_sensitivity_cmd`/`backfill_sensitivity_cmd`'s descendant-propagation walks. Investigation showed those walks have no ledger-awareness need: `sensitivity.merged_content_blocked` already reads the survivor's CURRENT on-disk sensitivity live, at judge time, so no ledger entry needs retroactive updating when `set-sensitivity` changes a concept's level (design Decision 4 already established this). The actual matching requirement is `sensitivity-aware-llm/spec.md`'s MODIFIED "Walk-Incompleteness Observability" requirement, which explicitly names `bundle/.state/` coverage. Re-scoped the task to that requirement; confirmed it needs zero code (same free-structural pattern as 2.1) and added the regression-guard test.
3. **Task 3.4 scoped to Scenario 1 only**: privacy-purge spec's Scenario 2 ("purging a previously-absorbed concept removes its snapshot from another survivor's sidecar") targets an id whose own concept file was already deleted by the merge that absorbed it. `_resolve_concept_path`'s existence gate (a security-relevant, threat-matrix-documented check) means such an id can never be resolved as a live `purge`/`forget` target — so this exact CLI scenario is unreachable as currently designed, independent of this slice's changes. Implemented and tested the reachable, correct subset: (a) whole-file history removal of a purge-set member's OWN sidecar (Scenario 1, fully covered with a RED->GREEN->mutation-tested test), and (b) the `_sweep_ledger_sidecars_for_ids` primitive correctly drops matching entries from OTHER live survivors' sidecars in the reachable case (id reuse, or a `--scope source` cascade landing on a still-live survivor) — proven by a direct unit test on the primitive. NOT implemented: content-level historical rewriting of another survivor's sidecar inside `git filter-repo` (would require extending `_FILE_INFO_CALLBACK_SNIPPET`, currently hardcoded to `bundle/index.md`/`bundle/log.md`'s bullet-line format, to parse/rewrite YAML frontmatter — a nontrivial engineering task, and moot until Scenario 2's precondition becomes reachable through some other change).

### Issues Found

- A test-file duplicate helper (`_write_survivor_with_merges` in `test_curate.py`, separate from the one in `test_contradiction.py`) also needed updating for the same API-shape change — not listed in tasks.md, found by running the full suite after 1.2.
- `forget()`'s function source is guarded by `tests/unit/state/test_fts.py::test_ingest_and_forget_do_not_reference_state_fts`, which forbids the literal substring `"state"` anywhere in `forget`'s AST source segment (including comments) — an architectural guard against `openkos.state` coupling that collided, by pure string coincidence, with a comment mentioning the `bundle/.state/ledger/` directory name. Fixed by rewording the comment to avoid the literal substring while keeping the same meaning.

### Status

8/8 PR #2 tasks complete. All quality gates green:
- `uv run pytest`: **4172 passed, 1 skipped in 131.76s**
- `uv run ruff check .`: All checks passed!
- `uv run ruff format --check .`: 191 files already formatted
- `uv run mypy .`: Success: no issues found in 191 source files
- Coverage: **97.09%** (gate 90%, branch coverage)

`git diff --numstat` (tracked files only; two new test files add ~192
lines on top of this): `src/` +197/-4, `tests/` (tracked) +385/-11.
Combined estimate ~789 changed lines — over the 400-line PR budget,
consistent with the design's own forecast risk (PR#2 forecast was ~350;
actual overrun driven by fixing the two pre-existing
`_write_survivor_with_merges` test helpers across the whole existing
merged-body test suite, which the forecast did not account for).

PR #3 (1b, reindex/doctor/repair) is NOT started — out of this slice's
scope per the orchestrator's explicit boundary.
