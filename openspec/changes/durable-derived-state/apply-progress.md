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

---

## PR #3 (1b) — Reindex composition, doctor, repair verb — DONE

Branch: `feat/reindex-doctor-repair-1b`, based on PR #2's branch. All 10
tasks complete (Phase 1: reindex embed composition, Phase 2: doctor checks
A and B, Phase 3: repair verb).

### TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR | Notes |
|---|---|---|---|---|
| 1.1 | 3 new `test_reindex.py` tests fail (raw-bytes assertions, tag-force assertion) before composition/tag-suffix existed | pass after `_compose_embed_text`/`_effective_model_tag` | mutation-tested (reverted `_compose_embed_text` call, both composition tests caught it, reverted) | 6 pre-existing model-tag-literal tests updated to expect the `#compose-v1` suffix — the tag's stored value genuinely changes; using `_tagged()` helper keyed off `reindex.EMBED_COMPOSITION_TAG` instead of a second hardcoded literal |
| 2.1 | 10 new `test_ledger.py` tests (`iter_pending`/`scan_torn_writes`) fail with `AttributeError` before the functions existed | pass after implementation | n/a (read-only preview logic duplicates `recover`'s truth table deliberately — mutating `recover` itself was rejected, doctor must never write) | |
| 2.2 | `scan_nesting_violations` tests fail with `AttributeError` before implementation | pass after implementation | mutation-tested (`if embedded_entries != entries[:k]` replaced with `if False`, the mutated-legacy-snapshot test caught it, reverted) | |
| 2.3 | 6 new `test_doctor.py` scenario tests (skip-outside-workspace, pass-trivially, torn-write-fails, both-remedies, no-reset-point, never-writes) written against spec.md's 4 scenarios plus design's Check A | doctor CLI wiring written alongside (not strictly test-first for this batch — noted as a process deviation below) then verified GREEN | 4 pre-existing `[PASS]` count assertions updated 12→14/10→12 (two new checks land in every all-pass run) | |
| 2.4/2.5 | Both covered by the SAME `test_doctor_nesting_violation_check_reports_no_reset_point_without_git_identity` test (monkeypatches `has_reset_point` to `False`) plus a dedicated `vcs.git` unit: `test_has_reset_point_false_with_exactly_one_commit`/`_true_with_two_commits`/`_false_outside_a_git_repository` | pass | mutation-tested (`vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root)` replaced with `if True`, the no-git-identity test caught it, reverted) | |
| 3.1 | `test_repair_refuses_with_no_override_when_a_torn_write_exists` written before `repair` command existed (command not registered) | pass after CLI wiring | n/a | |
| 3.2 | `test_repair_refuses_with_no_override_when_any_survivor_has_two_or_more_entries` + cross-survivor variant | pass | mutation-tested (`if bundle_ledger.bundle_wide_max_entries(bundle_dir) >= 2` replaced with `if False`, both tests caught it, reverted) | |
| 3.3 | `test_repair_migrates_a_clean_single_entry_ledger_verbatim` | pass | n/a | |
| 3.4 | Two tests: reset-point-exists prints `git reset --hard`; no-reset-point prints the explicit warning and still migrates | pass | n/a | |

**Process deviation, disclosed rather than glossed**: task 2.3 (doctor CLI
wiring) and task 3's `repair` command body were written together with
their test scaffolding in the same edit batch, rather than strict
RED-then-implementation-after ordering for those two files specifically —
every OTHER task in this PR (1.1, 2.1, 2.2, 2.4/2.5, 3.1, 3.2) followed
literal RED-first. For 2.3/3's CLI wiring, tests were authored immediately
after and run to confirm they exercise real, non-trivial assertions (exact
remediation wording, exit codes, byte-for-byte "nothing written" checks on
refusal paths) rather than tautologies, and the two most safety-critical
gates (2.4/2.5's reset-point gate, 3.2's bundle-wide-entries gate) were
subsequently mutation-tested and confirmed to catch a reverted guard.

### Files Changed

| File | Action | What |
|---|---|---|
| `src/openkos/state/reindex.py` | Modified | `EMBED_COMPOSITION_TAG`/`_effective_model_tag`/`_compose_embed_text`; embed text now composed from title/description/tags/body instead of raw bytes; the composition-scheme change is forced through the existing model-tag full-re-embed gate (closes #554) |
| `src/openkos/bundle/ledger.py` | Modified | `iter_pending`, `scan_torn_writes` (read-only preview of `recover`'s truth table), `scan_nesting_violations` (Check B, migration-era nested-prefix equality), `scan_unmigrated` (repair's migration source), `bundle_wide_max_entries` (repair's cross-survivor-pollution gate) |
| `src/openkos/vcs/git.py` | Modified | `has_reset_point`: `HEAD~1` resolvability probe, backing doctor's/repair's reset-point-exists gate (the orchestrator-flagged gap: `_autocommit` is best-effort and silently no-ops with no repo/identity/`GitError`) |
| `src/openkos/cli/main.py` | Modified | Doctor checks 12 (torn writes) and 13 (post-merge mutation, both remedies named, reset-point-gated); new `repair` command (two no-override refusal gates, verbatim frontmatter→sidecar extraction, reset-point-gated pre-write notice, `_autocommit` on success) |
| `tests/unit/state/test_reindex.py` | Modified | 3 new composition/migration tests; `_tagged()` helper; 6 pre-existing tag-literal assertions updated to the new suffixed format |
| `tests/unit/bundle/test_ledger.py` | Modified | 14 new tests: `iter_pending`, `scan_torn_writes` (×3), `scan_nesting_violations` (×5), `scan_unmigrated` (×3), `bundle_wide_max_entries` (×1) |
| `tests/unit/vcs/test_git_adapter.py` | Modified | 4 new `has_reset_point` tests (zero/one/two commits, outside a repo) |
| `tests/unit/cli/test_doctor.py` | Modified | 6 new ledger-check scenario tests; 4 pre-existing `[PASS]` count assertions updated |
| `tests/unit/cli/test_repair.py` | Created | 9 tests covering both refusal gates (own-survivor and cross-survivor), the happy-path migration, both reset-point branches, multi-survivor migration, and the graceful no-op |

### Deviations from design

1. **Check B's "unmigrated vs. corrupted" ambiguity resolved conservatively**: design's remediation text says a `[FAIL]` names the repair verb "for a ledger that is merely unmigrated, not corrupted" — but Check B only scans `iter_ledgers` (already-committed sidecars), so by construction every entry it inspects already migrated. The implemented remediation text names BOTH remedies unconditionally on any `[FAIL]`, since `doctor` cannot itself distinguish "unmigrated" from "corrupted" for an entry it already found in the sidecar — the human decides based on `openkos repair`'s own (separately gated) refusal-or-success outcome. Documented in the CLI docstring, not silently narrowed.
2. **`scan_nesting_violations`'s scope, not directly spelled out in design's prose**: the design's own text ("Check B is a migration-era check... structurally extinct for post-1a entries") implies but does not spell out the mechanical rule implemented here — an entry whose `survivor_before` embeds NOTHING (no `merged_from` key at all, true for every post-relocation entry) is silently skipped rather than flagged. Without this, a freshly created post-1a-only ledger with 2+ entries would falsely `[FAIL]` on every single run, since `survivor_before` for a post-relocation entry never carries a `merged_from` key. Verified against a dedicated regression test (`test_scan_nesting_violations_skips_a_post_relocation_entry_with_nothing_embedded`) before writing the production code, precisely because this gap was not explicit in either artifact.
3. **spec.md's doctor-command delta only documents Check B**, not Check A (torn writes) — tasks.md and design.md both require Check A as doctor's check 12. Implemented per tasks.md/design.md (the more detailed, later artifacts); spec.md's delta appears to have been scoped before design's Decision 5 split the single "ledger integrity" idea into two independently-mechanized checks. Not corrected in spec.md itself (out of this apply phase's scope — a spec artifact edit belongs to an earlier SDD phase).
4. **`has_reset_point`'s precision is a documented, deliberate approximation**: it verifies `HEAD~1` is resolvable (at least two commits exist), not that history specifically reaches `<first-merge>~1` for the ACTUAL corrupted merge — doctor/design's own remediation text names `<first-merge>~1` as a placeholder the human fills in, and `doctor` has no way to identify which historical commit was the first corrupting merge without deeper git-log analysis, out of scope for this slice. Documented in the function's own docstring as a necessary-not-sufficient condition.

### Issues Found

- None beyond the deviations above.

### Status

10/10 PR #3 tasks complete. All quality gates green:
- `uv run pytest`: **4208 passed, 1 skipped in ~120s** (full suite, unpiped)
- `uv run ruff check .`: All checks passed!
- `uv run ruff format --check .`: 192 files already formatted
- `uv run mypy .`: Success: no issues found in 192 source files
- Coverage: **97.05%** (gate 90%, branch coverage)

`git diff --numstat feat/ledger-readers-1a-ii..HEAD`: `src/` +415/-4,
`tests/` +993/-14. Combined ~1422 changed lines — well over the PR#3
forecast (~500-700) and the 400-line review budget; this is the tracker's
final child PR aggregating into the feature branch, with no further slice
to split into, so the overrun is reported rather than resequenced. The
overrun is driven by the doctor-check and repair-verb test suites (14 new
`test_ledger.py` tests, 9 new `test_repair.py` tests, 6 new
`test_doctor.py` tests) plus fixing 6 pre-existing model-tag-literal
assertions and 4 pre-existing doctor `[PASS]`-count assertions across the
model-tag-suffix and two-new-checks behavior changes.

All 34/34 tasks across PR #1, #2, and #3 are now complete. The
`durable-derived-state` change is ready for `sdd-verify`.
