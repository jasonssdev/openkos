# Tasks: openkos renames NFD names to NFC itself (`normalize-names`, #474 part 2)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1500-1750 (impl ~500-550: lint.py ~150, fsio.py ~60, main.py verb ~230, wording ~30, edge-case handling ~30; tests ~1000-1200: fsio ~150, lint scan ~180, CLI verb ~600-700, regression edits ~10) |
| Session review budget | 2000 (overridden for this session; skill default is 400) |
| 400-line budget risk | Medium (against the 2000-line session budget; ~75-85% utilization) |
| Chained PRs recommended | No — design's Migration/Rollout section requires one PR: the scan refactor is meaningless without its consumer (the verb) and the wording fix is false until the verb exists |
| Suggested split | Single PR (design-mandated); if the estimate lands over ~1800 at apply time, split Phase 5/6 (CLI verb tests+impl) into its own PR against a `feat/nfc-rename-migration-core` base |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `fsio.rename_two_step` primitive | single PR, Phases 1-2 | `uv run pytest tests/unit/test_fsio_rename_two_step.py` | N/A — pure filesystem primitive, no CLI boundary; exercised end-to-end via Unit 3 | Revert commit; primitive unreferenced by any verb yet |
| 2 | `scan_non_nfc_entries`/`NonNfcEntry` + `check_non_nfc_names` projection | single PR, Phases 3-4 | `uv run pytest tests/unit/test_lint_scan_non_nfc_entries.py tests/unit/test_lint_non_nfc.py cli/test_lint.py` | `uv run python -m openkos.cli.main lint` against a decomposed-name fixture bundle | Revert commit; `lint` output byte-identical to pre-change (regression guard) |
| 3 | `normalize-names` verb (Phase A/B, confirm ladder, autocommit) | single PR, Phases 5-8 | `uv run pytest tests/unit/cli/test_normalize_names.py` | `uv run python -m openkos.cli.main normalize-names` against a temp bundle fixture with NFD names | Revert commit; renames never applied, `lint` still reports findings as before |
| 4 | Wording corrections + follow-up issue draft | single PR, Phases 7,10 | `uv run pytest tests/unit/test_lint_non_nfc.py -k detail` | N/A — docstring/string-literal change only | Revert commit; wording reverts to pre-change text |

## Phase 1: RED — `fsio.rename_two_step` (Unit 1)

- [x] 1.1 Create `tests/unit/test_fsio_rename_two_step.py`: happy path reaches byte-exact NFC listing; verification failure raises and leaves no temp entry; temp name matches `RENAME_TEMP_PREFIX`; **injected normalization-insensitive `os.rename`** (monkeypatch `os.rename` in `fsio`'s namespace to no-op when `NFC(src.name) == NFC(dst.name)`) still yields the byte-exact NFC name via the two-step path
- [x] 1.2 Confirm 1.1 fails RED (`fsio.rename_two_step` does not exist)

## Phase 2: GREEN — implement primitive (Unit 1)

- [x] 2.1 Add `RENAME_TEMP_PREFIX = "okos-nfc-tmp-"` and `rename_two_step(src: Path, nfc_name: str) -> Path` to `src/openkos/fsio.py`: `src -> src.parent/f"{RENAME_TEMP_PREFIX}{uuid4().hex}" -> src.parent/nfc_name`; verify via `nfc_name in os.listdir(src.parent)` byte-exact (never `Path.exists()`); restore temp to `src.name` and raise `OSError` on verification or step-2 failure
- [x] 2.2 Run `uv run pytest tests/unit/test_fsio_rename_two_step.py` — GREEN
- [x] 2.3 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on `fsio.py`

## Phase 3: RED — `scan_non_nfc_entries` + regression guard (Unit 2)

- [x] 3.1 Create `tests/unit/test_lint_scan_non_nfc_entries.py`: raw `Path`/`raw_name`/`nfc_name`/`rel_posix` preserved; `depth` matches `len(path.relative_to(bundle_dir).parts)`; `is_dir`/`is_symlink` computed only for offending entries; sorted by `rel_posix`; degraded-walk `OSError` mid-iteration collects partial results; `scan_stranded_rename_temps` finds `RENAME_TEMP_PREFIX` entries
- [x] 3.2 Add a `Path.rglob` monkeypatch assertion (mirrors existing `…broken_walk_degrades_to_findings_collected_so_far`) asserting `pattern == "*"` and incremental `next()` pull, not `sorted(...)`
- [x] 3.3 Run existing `tests/unit/test_lint_non_nfc.py` (all 7 tests) and `cli/test_lint.py::test_lint_flags_non_nfc_names` unmodified — confirm still GREEN against current code (baseline for the projection regression guard)
- [x] 3.4 Confirm 3.1-3.2 fail RED (`NonNfcEntry`/`scan_non_nfc_entries`/`scan_stranded_rename_temps` do not exist)

## Phase 4: GREEN — scan + projection (Unit 2)

- [x] 4.1 Add frozen `NonNfcEntry` dataclass and `scan_non_nfc_entries(bundle_dir) -> list[NonNfcEntry]` to `src/openkos/lint.py`: move the existing `rglob("*")` incremental-pull walk body verbatim; stat `is_dir`/`is_symlink` only for entries that fail the NFC test
- [x] 4.2 Add `scan_stranded_rename_temps(bundle_dir) -> list[Path]` (verb-only helper, names-only walk for `RENAME_TEMP_PREFIX` entries)
- [x] 4.3 Rewrite `check_non_nfc_names` as a thin 1:1 projection of `scan_non_nfc_entries` into `LintFinding`s; drop its now-redundant `findings.sort(key=...)`
- [x] 4.4 Run `uv run pytest tests/unit/test_lint_scan_non_nfc_entries.py tests/unit/test_lint_non_nfc.py cli/test_lint.py::test_lint_flags_non_nfc_names` — new tests GREEN, all pre-existing #490 tests GREEN unedited, projection byte-identical to Phase 3.3 baseline except nothing (wording deferred to Phase 7)
- [x] 4.5 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on `lint.py`

## Phase 5: RED — `normalize-names` verb (Unit 3)

- [x] 5.1 Create `tests/unit/cli/test_normalize_names.py`, mirroring `tests/unit/cli/test_backfill_sensitivity.py`'s fixture shape. Cover: deepest-first ordering (child before decomposed parent dir); directory-carries-subtree preview (one line, not per-descendant); collision skip (not overwritten); symlink skip (never followed); drift re-check demotes a vanished entry to skip, not a crash; idempotent second run (zero plan, zero write, zero commit); confirm ladder (`--auto` skips prompt / `review: false` skips prompt / TTY decline exits 1 nothing written / non-TTY without `--auto` refuses, exits non-zero); empty-plan/all-skip run writes nothing and creates no commit; stranded-temp WARNING on next run naming the path; exactly one `log.md` entry per run stating renamed/skipped counts; `index.md` byte-identical before/after
- [x] 5.2 Add autocommit-scope tests: staging scope names each old+new path plus `log.md`, no unrelated path (assert on the paths passed to `_autocommit`, not the resulting diff)
- [x] 5.3 Add `@pytest.mark.skipif(sys.platform == "darwin")` test: on a byte-exact FS, commit shows `D` old / `A` new per rename
- [x] 5.4 Add unconditional (all-platforms) test for the macOS-shaped case: renames contributing nothing to the diff still exit 0, commit `log.md`, print **no** WARNING for the renamed paths
- [x] 5.5 Add not-a-repo and no-git-identity tests: non-fatal stderr WARNING, exit code unchanged, renamed files remain on disk
- [x] 5.6 Add threat-matrix tests: `test_normalize_names_never_touches_paths_outside_bundle_dir`; `test_symlink_is_reported_as_skip_and_never_renamed`; `test_normalize_names_not_a_repo_warns_and_exits_zero`; `test_second_run_plans_nothing_and_creates_no_commit`
- [x] 5.7 Add residual-edge-case test A (untracked old path): an offending entry that was never committed to git — after rename, `git add -- <old> <new> log.md` fails to match the vanished untracked `<old>` pathspec, `commit_paths` raises `GitError`, and the run takes the existing non-fatal stderr-WARNING path (renames stay on disk, exit code unchanged) — no new code path, exercising D7's already-general `GitError` contract
- [x] 5.8 Add residual-edge-case test B (log entry size): a large batch (e.g. 12 renamed + 3 skipped) produces exactly one single-line `log.md` entry stating counts only (no per-path enumeration, no newline); a small batch (<=5 total) may additionally list the renamed pairs inline per D6's example, still one line
- [x] 5.9 Add `@pytest.mark.skipif(sys.platform != "darwin")` fsio-level test in `tests/unit/test_fsio_rename_two_step.py` (if not already in Phase 1): on real APFS, `rename_two_step` leaves the parent listing containing the byte-exact NFC name, asserted via `os.listdir`
- [x] 5.10 Confirm all of 5.1-5.8 fail RED (`normalize-names` command does not exist)

## Phase 6: GREEN — implement `normalize-names` verb (Unit 3)

- [x] 6.1 Implement `normalize_names_cmd` Phase A in `src/openkos/cli/main.py`, mirroring `backfill_sensitivity_cmd`: `require_workspace` -> `read_config` -> `lint.scan_non_nfc_entries(layout.bundle_dir)` + `lint.scan_stranded_rename_temps` stderr WARNING -> classify skips (collision/symlink) -> sort `(-depth, rel_posix)` -> empty-plan no-op (exit 0, no write) -> preview (ascii-escaped, directory-as-one-entry) -> confirm ladder
- [x] 6.2 Implement drift re-check immediately before Phase B: re-validate `raw_name` presence, `is_dir`/`is_symlink`, `nfc_name` absence via `os.listdir`; demote failures to skip, never crash; `_reject_drifted_targets` for `log.md` only
- [x] 6.3 Implement Phase B: `fsio.rename_two_step` per entry in apply order; build the bounded `log.md` line per 5.8's resolution (counts always; renamed pairs listed inline only when total entries <= 5); `insert_log_entry`; `_autocommit(root, [old…, new…, "bundle/log.md"], "openkos: normalize-names")`; track `landed` renames for failure reporting, no rollback on partial failure
- [x] 6.4 Run `uv run pytest tests/unit/cli/test_normalize_names.py tests/unit/test_fsio_rename_two_step.py` — all GREEN
- [x] 6.5 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean on `main.py`

## Phase 7: Wording corrections (Unit 4)

- [x] 7.1 Update `lint.py:1109-1112` module/function docstring per design D8's replacement text (lint never writes; migration now performed by `normalize-names`)
- [x] 7.2 Update the `non-nfc-name` finding `detail` (`lint.py:1167-1171`) to name `openkos normalize-names` as remediation, keeping `is not NFC`, the `́` escape, and the NFC target substring
- [x] 7.3 Update `main.py:8096-8097` docstring per design D8's replacement text
- [x] 7.4 Update `tests/unit/test_lint_non_nfc.py` module docstring (lines 15-16) and add one new assertion pinning the verb name in `detail`; no other existing assertion in this file changes
- [x] 7.5 Run `uv run pytest tests/unit/test_lint_non_nfc.py cli/test_lint.py` — all 8 pre-existing assertions plus the new verb-name assertion GREEN

## Phase 8: Full-suite checkpoint

- [x] 8.1 `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` clean repo-wide
- [x] 8.2 `uv run pytest -q` — full suite green, note before/after count
- [x] 8.3 Record actual changed-line count (additions+deletions) against the 2000-line session budget; if it exceeds ~1800, split per the Suggested Split note before opening the PR

## Phase 9: Spec/proposal reconciliation

- [x] 9.1 Confirm `specs/name-normalization/spec.md` and `specs/lint/spec.md` (already amended post-spike) match the implemented behavior byte-for-byte on the one-step-fails-at-primitive-level wording and the staging-scope-not-diff wording; no further edits expected

## Phase 10: Follow-up issue draft (D8, out of scope for apply)

- [x] 10.1 Draft the follow-up GitHub issue body: `next_action` integration for `normalize-names` recommendations, blocked on a memoization story because `next`'s tiers are contractually zero-walk/memoized-signal only (`cli/next_action.py:5`) while non-NFC detection is a live unmemoized `rglob` walk. Orchestrator files this issue at delivery and links it from the change's close-out.

**Draft body** (orchestrator to file at delivery, title suggestion: "next_action: recommend `normalize-names` once non-NFC detection is memoized"):

> `openkos lint`'s `non-nfc-name` finding (issue #474 part 1, PR #490) and the `openkos normalize-names` verb that now remediates it (#474 part 2) are both live, unmemoized `bundle_dir.rglob("*")` walks. `next_action`'s tiers (`cli/next_action.py:5`) are contractually zero-walk / memoized-signal only, so recommending `normalize-names` from `next` today would violate that cost contract on every invocation, clean bundle or not.
>
> This issue tracks giving non-NFC detection a memoization story (e.g. a cached "last known non-NFC count" keyed on a cheap invalidation signal) so that `next_action` can cheaply check "does this bundle have non-NFC names" without re-walking, and only then wire a `next` tier that recommends `openkos normalize-names` when the answer is yes.
>
> Out of scope for #474: no walk-cost or memoization design is proposed here; this issue exists only to record the gap discovered during #474 part 2's design (design.md D8) so it is not silently lost.

## Key Decisions Recorded

- **Untracked-old-path GitError (edge case a)**: no new guard code. `git add -- <old> <new> log.md` failing on a never-committed, now-vanished `<old>` pathspec surfaces through the existing generic `GitError` -> non-fatal-WARNING contract (D7/Requirement "Scoped, Best-Effort, Non-Fatal Autocommit"). Staging is reached, not unreachable — it just fails gracefully like any other `GitError` cause. Pinned by Phase 5.7.
- **Log entry size (edge case b)**: resolved toward proposal D6 — the entry states renamed/skipped **counts** always; per-path enumeration (as shown in design D6's example) is included inline only for small batches (<=5 total entries) to keep the line bounded and single-line (`insert_log_entry` rejects newlines). Pinned by Phase 5.8.

## Key Learnings

1. The macOS spike (design S1) proved `Path.exists()` unusable as a rename-verification check — `os.listdir` byte-exact comparison is a hard requirement, not defensive taste.
2. `core.precomposeunicode=true` means git never observes the NFD spelling on macOS, so the D/A commit assertion is platform-gated while the no-warning assertion runs everywhere.
3. The untracked-old-path edge case needs no new code path — it is already covered by the existing generic GitError-to-WARNING contract, avoiding special-casing inside a primitive shared by thirteen callers.
