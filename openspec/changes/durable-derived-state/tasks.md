# Tasks: Relocate the merge ledger to `bundle/.state/ledger/`

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~550 (PR#1) + ~350 (PR#2) + ~500-700 (PR#3) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR #1 (1a-i) → PR #2 (1a-ii) → PR #3 (1b) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

**Constraint (from design, restated so it survives resequencing): PR #2 (1a-ii)
MUST NOT slip past the same release as PR #1 (1a-i).** PR #1 alone relocates
the ledger but leaves privacy sweeps (`forget`/`purge`/sensitivity) not yet
extended to the new path — a net privacy regression if shipped alone. The
tracker branch enforces "not merged to main independently," but only landing
both PRs in the same release closes the gap; state it here explicitly.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Ledger store + two-phase write + crash recovery | PR 1 (base: tracker branch) | `uv run pytest tests/unit/bundle/test_ledger.py -v` | Crash-injection integration test (monkeypatched `fsio.write_atomic`/`os.replace`) | `git revert` PR #1; on-disk format is plain `okf.dump_frontmatter`, no data migrated yet |
| 2 | Read wiring + privacy sweep coverage | PR 2 (base: PR #1 branch) | `uv run pytest tests/unit/resolution/test_contradiction.py tests/unit/cli/test_forget.py tests/unit/cli/test_purge.py -v` | `openkos forget`/`openkos purge` against a fixture bundle with a multi-entry ledger | `git revert` PR #2; PR #1's store is unaffected |
| 3 | Reindex, doctor, repair verb | PR 3 (base: PR #2 branch) | `uv run pytest tests/unit/state/test_reindex.py tests/unit/cli/test_doctor.py -v` | `openkos doctor` + repair verb against a fixture bundle (clean and corrupted ledgers) | `git revert` PR #3; repair verb is opt-in, never auto-runs |

---

## PR #1 (1a-i) — Ledger store and crash semantics (base: tracker branch)

### Phase 1: Foundation

- [x] 1.1 Generalize `okf.concept_path_for`'s resolver (`src/openkos/model/okf.py:1179-1269`) to accept `(root, suffix)`; export `STATE_DIRNAME`.
- [x] 1.2 RED: unit tests for `ledger.py` path mapping incl. NFC/NFD-on-byte-exact-FS, mirroring `concept_path_for`'s own suite (new `tests/unit/bundle/test_ledger.py`).
- [x] 1.3 GREEN: create `src/openkos/bundle/ledger.py` — `ledger_path_for`, `read_entries`, `iter_ledgers`. Leaf module; must not import `openkos.graph`.

### Phase 2: Two-phase write and recovery

- [x] 2.1 RED: recovery truth table test — construct each on-disk state (`.pending` absent; present+hash-match; present+hash-mismatch) directly, assert the verdict (none / roll-forward / roll-back).
- [x] 2.2 GREEN: implement `write_pending`, `commit_pending`, `discard_pending`, `recover` in `ledger.py` per the truth table.
- [x] 2.3 RED: crash-injection integration tests — monkeypatch `fsio.write_atomic`/`os.replace` to raise at each of S1/V/S2/D, run `recover`, assert the truth-table verdict.
- [x] 2.4 RED: `_autocommit` staging test (threat matrix row) — assert `MergeResult.committed_paths` contains the new sidecar path under `bundle/.state/ledger/**`; must fail before the wiring exists.
- [x] 2.5 GREEN: `plan_merge` (`src/openkos/bundle/merge.py`) takes `existing_entries`, returns `ledger_entries` instead of writing `MERGED_FROM_KEY` into frontmatter; `plan_unmerge` takes `entries` instead of decoding from `survivor_text`.
- [x] 2.6 GREEN: `merge_core` (`src/openkos/cli/main.py:6689-6734`) writes S1 (pending sidecar) → V (survivor) → S2 (commit pending) → D (delete absorbed); commit_paths includes the sidecar path so 2.4 goes green.
- [x] 2.7 GREEN: `unmerge_core` restores survivor/absorbed/index/log, pops the ledger tail last (re-runnable no-op on retry).
- [x] 2.8 RED then GREEN: preflight refusal — `merge`/`unmerge` refuse with no `--force` while a `.pending` exists for the touched survivor; test asserts the refusal message and no partial write.
- [x] 2.9 RED then GREEN: merge → unmerge byte-for-byte parity test comparing survivor/absorbed/index/log bytes before and after a round trip. (Already fully covered by the pre-existing `tests/unit/cli/test_merge_roundtrip.py::test_single_merge_then_unmerge_is_byte_identical_modulo_log` and `test_sequential_lifo_merge_then_unmerge_is_byte_identical_modulo_log`, which continue to pass unchanged against the sidecar-backed ledger — no new test authored, since these already satisfy the acceptance criterion.)

### Phase 3: Documentation

- [x] 3.1 Create `docs/adr/0013-relocate-merge-ledger-to-bundle-state.md` from `docs/adr/template.md`, status `Proposed`, superseding ADR-0002's storage clause only (not Context/Decision/Consequences/Alternatives), cross-referencing ADR-0005 (relation_rewrites), ADR-0011 (provenance_rewrites), ADR-0008 (per-entry sensitivity gate rationale).
- [x] 3.2 Edit `docs/adr/0002-reversible-merge-ledger.md` **Status line only** — mark superseded-in-part by ADR-0013; do not touch Context/Decision/Consequences/Alternatives (`openspec/config.yaml:76-77`).
- [x] 3.3 Add ADR-0013's row to `docs/adr/README.md` index.

---

## PR #2 (1a-ii) — Read wiring and privacy sweep coverage (base: PR #1 branch)

### Phase 1: Read-path wiring

- [x] 1.1 RED: per-entry sensitivity-gate call-count test — assert `merged_content_blocked` is called once per ledger entry for a three-entry survivor; mutate the loop to hoist the call to per-survivor and confirm the test goes red first, then revert the mutation.
- [x] 1.2 GREEN: `src/openkos/resolution/contradiction.py:471` — swap `okf.decode_merged_from(metadata)` for a ledger-sidecar read. **Deviation from the task text**: `ledger.read_entries` could not be called directly — `resolution` may import `openkos.model.okf` only, never `openkos.bundle` (`tests/unit/resolution/test_layering.py`, undocumented by design.md). Added a local `_read_ledger_entries` helper in `contradiction.py` that reconstructs the sidecar path via `okf.concept_path_for(..., suffix=...)` (the same primitive task 1.1 of PR#1 generalized), plus a parity test (`test_ledger_suffix_constant_matches_bundle_ledger_module`) pinning its duplicated `_LEDGER_SUFFIX` constant against `bundle.ledger.LEDGER_SUFFIX`, mirroring the `vcs/git.py` `_identity`/parity-test precedent. `_merged_body_candidates`'s per-entry loop and the `:567` gate call site are unchanged. `sensitivity.py` not modified.

### Phase 2: EXCLUDE walk regression guard

- [x] 2.1 RED then GREEN: EXCLUDE assertion test — `sorted(bundle_dir.rglob("*.md"))` and `okf._iter_docs` counts are unchanged when a `bundle/.state/ledger/*.ledger.okf` sidecar exists alongside concepts (`tests/unit/bundle/test_ledger_walk_exclusion.py`). Confirmed free (zero code change, as design predicted) — passed on first run; mutation-tested by temporarily changing `LEDGER_SUFFIX` to end in `.md`, confirmed both tests fail, reverted. `src/openkos/bundle/references.py` untouched.

### Phase 3: INCLUDE walk — privacy sweep coverage

- [x] 3.1 GREEN: `ledger.iter_ledgers` already existed from PR#1 with its own coverage in `tests/unit/bundle/test_ledger.py`; confirmed as the shared primitive the three sites below reuse — no new code needed.
- [x] 3.2 RED then GREEN: `forget` sweep includes `bundle/.state/ledger/` via new shared `cli/main.py::_sweep_ledger_sidecars_for_ids(bundle_dir, purge_ids)`: each purge-set member's OWN sidecar is deleted, and any OTHER survivor's sidecar entry whose `absorbed_id` is in the purge set is dropped/rewritten. Wired into `forget`'s Phase B write and `_autocommit` path list. Tests: `tests/unit/cli/test_forget.py::test_forgetting_a_survivor_deletes_its_own_ledger_sidecar`, `::test_sweep_ledger_sidecars_drops_matching_entries_from_other_survivors`. Both mutation-tested.
- [x] 3.3 RED then GREEN: `purge` sweep reuses the SAME `_sweep_ledger_sidecars_for_ids` primitive in its live-tree cleanup (after `expunge_paths`, alongside `_purge_clean_live_index`/`_purge_clean_live_log`), covering both the `GitFinalizeError` and success paths; touched paths folded into the post-rewrite auto-commit's `commit_paths_rel`.
- [x] 3.4 RED then GREEN (scoped): `expunge_targets` now includes each purge-set member's OWN `bundle/.state/ledger/` sidecar path (whole-file history removal) — covers privacy-purge spec Scenario 1 exactly. Test: `tests/unit/cli/test_purge.py::test_purging_a_merge_survivor_removes_its_ledger_sidecar_from_history`, mutation-tested (reverting the `expunge_targets` addition makes the test fail). **Known gap, documented not implemented**: Scenario 2 ("purging a previously-absorbed concept removes its snapshot from another survivor's sidecar") is unreachable via the CLI as designed — `_resolve_concept_path`'s existence gate means an absorbed id (whose own file the merge already deleted) can never be a live purge target in the first place, so this scenario cannot be exercised end-to-end without loosening a security-relevant existence check (out of scope for this slice; flagged as a risk for follow-up). The `_sweep_ledger_sidecars_for_ids` primitive DOES correctly drop matching entries from other live survivors' sidecars in the reachable case (id reuse / `--scope source` landing on a live survivor) — proven directly by `test_sweep_ledger_sidecars_drops_matching_entries_from_other_survivors` — but content-level historical rewriting of OTHER survivors' sidecars inside `git filter-repo` (extending `_FILE_INFO_CALLBACK_SNIPPET`, which is hardcoded to `bundle/index.md`/`bundle/log.md`'s line format) was not implemented.
- [x] 3.5 RED then GREEN (scoped): re-scoped from "`set-sensitivity` sweep" to the sensitivity-aware-llm delta's actual MODIFIED requirement text — "Walk-Incompleteness Observability" covering `bundle/.state/`. `okf._walk_errors`'s `os.walk(bundle_dir, onerror=...)` already descends into every subdirectory unconditionally (unlike the `rglob("*.md")` EXCLUDE walks), so `bundle/.state/` was always in its reach — zero code change, confirmed free like 2.1. Regression-guard test: `tests/unit/cli/test_observability.py::test_warn_if_walk_incomplete_covers_unlistable_state_subdirectory`. `set_sensitivity_cmd`/`backfill_sensitivity_cmd`'s own descendant-propagation walks were investigated and found NOT to need ledger awareness — `merged_content_blocked` already reads the survivor's CURRENT on-disk sensitivity live at judge time (design Decision 4), so no ledger entry ever needs retroactive rewriting on a `set-sensitivity` change.
- [x] 3.6 GREEN: new `lint.check_state_dir_contains_no_markdown`/`scan_markdown_under_state_dir` flagging any `.md` file under `bundle/.state/`, wired into `LintReport`/the `lint` CLI command's new "State-dir markdown:" section (non-gating, matching every other finding kind). Tests: `tests/unit/test_lint_state_dir_markdown.py` (5 tests) + 3 CLI tests in `tests/unit/cli/test_lint.py`. Mutation-tested.

---

## PR #3 (1b) — Reindex composition, doctor, repair verb (base: PR #2 branch)

### Phase 1: Reindex embed composition

- [ ] 1.1 RED then GREEN: `src/openkos/state/reindex.py` embed text matches `fts.py:220-234`'s title/description/tags/body scheme instead of `raw_bytes.decode("utf-8")` verbatim; verify and close #554 explicitly.

### Phase 2: Doctor checks A and B

- [ ] 2.1 RED then GREEN: Check A (torn ledger write) — any `*.ledger.okf.pending` with hash mismatch/match reported as `doctor`'s check 12 (mechanically exact truth-table check, read-only).
- [ ] 2.2 RED then GREEN: Check B (post-merge mutation) — nested-prefix equality: entry k's `survivor_before` embeds entries `0..k-1`; decode and compare to the survivor's current entries `0..k-1`; any inequality flags `[FAIL]`. Doctor check 13, informational only (does not affect exit code), stays read-only.
- [ ] 2.3 RED then GREEN: doctor scenario tests from `openspec/changes/durable-derived-state/specs/doctor-command/spec.md` — clean ledgers pass; corrupted ledger fails with both remediation paths; check never writes; no-ledger workspace passes trivially.
- [ ] 2.4 RED then GREEN (gap — no artifact covered this): `doctor`'s corrupted-ledger `[FAIL]` remediation MUST verify a git reset point actually exists (auto-commit history reachable back to `<first-merge>~1`) before printing "reset and replay" as the remedy. If no reset point exists (no git repo, no configured git identity, or history does not reach the first merge), the remediation text MUST say so explicitly and MUST NOT claim reset-and-replay is available.
- [ ] 2.5 RED (gap — required by orchestrator, no artifact covered this): test covering a workspace with a corrupted ledger AND no configured git identity (`_autocommit` never ran) — assert `doctor` reports "no reset point available" rather than the standard reset-and-replay remediation.

### Phase 3: Repair verb

- [ ] 3.1 RED then GREEN: repair verb refuses on Check A (torn `.pending` present) with no override.
- [ ] 3.2 RED then GREEN: repair verb refuses whenever any survivor in the bundle carries ≥2 entries bundle-wide (cross-survivor pollution gate), regardless of Check B's per-ledger result.
- [ ] 3.3 RED then GREEN: repair verb on a clean, single-entry-per-survivor bundle extracts entries out of frontmatter into `bundle/.state/ledger/` verbatim.
- [ ] 3.4 GREEN: repair verb prints the `git reset --hard` inverse and the reset-point-exists caveat (Phase 2.4) before writing.

---

## Key Learnings

1. `references.py` and `sensitivity.py` need zero edits — both are structurally excluded/generic already, so tasks only assert the exclusion holds rather than change the files.
2. The `_autocommit` non-fatal fallback creates a genuine remedy gap for corrupted ledgers with no git identity — doctor must detect and report a missing reset point, not just recommend one blindly.
3. Two opposite walks (EXCLUDE for reference scans, INCLUDE for privacy sweeps) must stay structurally separate — one shared `iter_ledgers` helper serves only the INCLUDE side.
4. Feature-branch-chain state alone does not guarantee PR #2 ships with PR #1 in the same release — that constraint had to be written into the tasks file explicitly.
