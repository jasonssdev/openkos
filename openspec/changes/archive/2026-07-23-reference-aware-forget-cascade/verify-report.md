# Verify Report: Reference-Aware Forget — Scope/Depth Cascade (S2b)

**Change**: `reference-aware-forget-cascade` (MVP-3 gap #8 S2b)
**Branch**: `feat/forget-cascade-2`
**Commits**: PR1 cbeab08 (provenance helper) + PR2 68cf616 (cascade wiring) + correction batch (squashed into 68cf616)
**Working tree**: clean

## Verdict

**PASS WITH WARNINGS**

- 1 CRITICAL administrative gap (tasks.md on-disk sync — resolved at archive time)
- 0 WARNINGS
- 1 SUGGESTION (K-of-N spec observability amendment)

## Fresh Test Evidence

All tests re-run (not trusted from prior reports):

```
uv run pytest tests/unit -q
→ 1599 passed in 4.22s
→ 0 failures

uv run ruff check .
→ All checks passed

uv run ruff format --check .
→ 115 files already formatted

uv run mypy .
→ Success: no issues found in 115 source files
```

All numbers match the apply-progress-reported figures exactly.

## Requirement/Scenario Coverage

Every ADDED + MODIFIED delta requirement maps to real passing tests:

| Requirement | Test Coverage | Status |
|-------------|---------------|--------|
| Scope Selection / default self | `test_scope_self_default_byte_identical_to_no_scope_flag` | ✅ |
| Provenance Descendant Resolution (single-source) | `test_scope_source_cascade_deletes_source_and_single_source_children` | ✅ |
| Provenance Descendant Resolution (multi-source preserve) | `test_scope_source_preserves_multi_source_child` | ✅ |
| Provenance Descendant Resolution (path-safety-first) | `test_scope_source_path_traversal_refuses_before_descendant_resolution` | ✅ |
| Full-Set Preview + Count | `test_scope_source_preview_states_count` | ✅ |
| `--force` does not auto-confirm | `test_scope_source_force_does_not_auto_confirm` | ✅ |
| Non-TTY without `--auto` refuses | `test_scope_source_non_tty_without_auto_refuses_even_with_force` | ✅ |
| Inbound ref detection over set | `test_scope_source_external_inbound_ref_to_member_refuses_without_force` | ✅ |
| Unverifiable ref on non-root member | `test_scope_source_unverifiable_referrer_mentions_non_root_member_refuses` | ✅ |
| Intra-set backlink does not block | `test_scope_source_intra_set_backlink_does_not_block` | ✅ |
| Intra-set + external ref to same member | `test_scope_source_intra_set_backlink_and_external_ref_to_same_member` | ✅ |
| N-tombstones | tombstone-count assertions + `test_scope_source_tombstones_appear_in_ascending_id_order` | ✅ |
| Per-member resurrection | `test_scope_source_per_member_resurrection_disclosure` | ✅ |
| Intra-set member-to-member resurrection suppressed | `test_scope_source_intra_set_member_to_member_resurrection_suppressed` | ✅ |
| Catalog-before-file N-unlink sorted | `test_scope_source_phase_b_writes_catalog_before_any_unlink_sorted_order` | ✅ |
| K-of-N partial-failure observability | K-of-N message assertion in above test | ✅ |

**Result**: All 61 delta tests (46 in test_forget.py + 15 in test_provenance.py) passing; no untested scenario found.

## Code Locked Decisions Confirmed

**File**: `src/openkos/bundle/provenance.py`
- `--scope {self,source}` option present ✅
- Self byte-identical behavior tested ✅
- Non-empty-provenance guard at ~L94 (`if entry_provenance and entry_provenance <= purge`) ✅
- No `openkos.graph` import (verified via grep) ✅
- Fixed-point iteration to closure ✅

**File**: `src/openkos/cli/main.py::forget`
- Cascade wiring over purge SET ✅
- Set-difference refuse gate (`referrer_id in purge_ids_set` drop) ✅
- N-tombstone lines in order ✅
- Full-set preview + Total count line (source-scope only) ✅
- N-unlink-last `sorted(purge_ids)` ✅
- No `raw_dir` reference anywhere (raw/ never touched) ✅
- No `--depth` option anywhere (grep confirmed absent) ✅

## Spec/Implementation Alignment

Delta spec accurately describes shipped behavior:

- Preview/Count/Refuse wording matches scenarios (e.g., prompt `f"Delete {len(purge_ids)} concepts?"` matches "states 3 concepts" scenario)
- Gate-1 refusal message uses set-difference-filtered `verified_refs`/`unverifiable_refs` per spec
- All scenarios in spec have corresponding tests passing

### Minor Gap (SUGGESTION, Non-Blocking)

The K-of-N partial-failure stderr enrichment (`"removed K of N concept(s) before failing; M remain (recover with git or 'openkos lint')"`) is **not explicitly mentioned** in the delta spec's "Partial cascade deletion is git-recoverable" scenario. This is an **additive diagnostic detail**, not a contradiction:

- The scenario's actual assertions (catalog fully updated, one file may remain as orphan, git-recoverable) **all still hold** and are tested
- The K-of-N message is a convenience addition that does not violate the spec
- **Recommended optional amendment**: Add a sentence to the spec scenario noting the K-of-N reporting for cascade failures (N>1)

## Correction Batch Summary (Post-Review, Squashed into PR2)

Applied on top of PR2 apply phase (Phases 4–6 complete):

### FIX 1 — Tuple-Order Readability
`resurrection_pairs` and `all_refs` used opposite member-tuple field order. Changed both to `(member, ...)` pattern with explicit `key=` lambda on sort to preserve original output ordering. Pure clarity fix; zero behavior change; all pre-existing tests pass unmodified.

**Non-vacuity proof**: Narrowed `relation.target not in purge_ids_set` to `relation.target != member` → `test_scope_source_intra_set_member_to_member_resurrection_suppressed` FAILED (resurrection disclosure leaked) → reverted → passes. ✅

### FIX 2 — K-of-N Partial-Failure Observability (Resilience)
Phase B exception handler now tracks `unlinked_count` and, for cascades (N>1), appends `"removed K of N concept(s) before failing; M remain (recover with git or 'openkos lint')"` to stderr. Self path (N=1) stays byte-identical to S2a.

**Test**: Extended `test_scope_source_phase_b_writes_catalog_before_any_unlink_sorted_order` with 2nd-unlink failure scenario; asserts K-of-N message in stderr. ✅

### FIX 3 — Reliability Test Gaps (3 New Tests)
1. `test_scope_source_intra_set_member_to_member_resurrection_suppressed` — guard is `relation.target not in purge_ids_set`, not `!= member`
2. `test_scope_source_intra_set_backlink_and_external_ref_to_same_member` — set-difference drop is per-referrer, not per-member-blanket
3. `test_scope_source_tombstones_appear_in_ascending_id_order` — asserts N tombstone `(id: ...)` substrings at strictly ascending index positions

**Non-vacuity proofs**:
- Dropped `reversed()` in Phase A loop → tombstone order FAILED (`[282, 191, 103]` != `[103, 191, 282]`) → reverted ✅
- Dropped `reversed()` in Phase A loop → test FAILED (`[282, 191, 103]` != `[103, 191, 282]`) → reverted ✅

### FIX 4 — Count Assertion Tightening (Reliability)
`test_scope_source_preview_states_count` now asserts exact `"Total: 3 concept(s) to delete."` line AND that exactly 3 of 3 pre-created concept files no longer exist on disk after run (previously not checked). ✅

## Known Accepted Follow-Ups (Non-Transactional Model)

All listed in design/proposal as accepted follow-ups, not blockers:

1. **Partial-failure log false-claim**: Tombstones written before unlinks; if an unlink fails, log shows "removed" for concepts not actually unlinked. **Mitigation**: git-recoverable, no log consumer exists yet.

2. **Re-run after partial failure**: Re-run on same root may hit "Nonexistent Concept Refusal" for already-deleted members. **Mitigation**: `openkos lint` + `forget --scope self` on orphans.

3. **Inline scan not extracted**: Per-member inbound scan remains in `cli/main.py`, not extracted to `bundle/` helper. **Rationale**: Single consumer today; deferred refactor.

## Tasks Artifact Status

**File**: `openspec/changes/reference-aware-forget-cascade/tasks.md`
**Status**: All phases marked complete ([x]):
- Phase 1–3 (Provenance helper RED/GREEN/REFACTOR): ✅
- Phase 4–6 (Cascade wiring RED/GREEN/REFACTOR): ✅

**CRITICAL Administrative Gap** (resolved at archive time): On-disk tasks.md file still showed unchecked Phase 4–6 items after apply; Engram and git history showed them complete. **Resolution**: Orchestrator updated on-disk file to reflect completion before archive.

## Implementation Artifacts

| File | Lines | Role | Notes |
|------|-------|------|-------|
| `src/openkos/bundle/provenance.py` | ~150 | New pure helper | Orphan-closure computation; no graph imports |
| `src/openkos/cli/main.py::forget` | ~400 | Modified | --scope wiring, set-based Phase A/B, per-member gates |
| `tests/unit/bundle/test_provenance.py` | ~200 | New test | 15 tests, all pure-function edge cases |
| `tests/unit/cli/test_forget.py` | +950 (46 tests) | Modified | 36 new cascade tests + 10 existing S2a regression tests |

## Where

- **Code**: `src/openkos/bundle/provenance.py`, `src/openkos/cli/main.py` (L800–1211 forget), `tests/unit/cli/test_forget.py`, `tests/unit/bundle/test_provenance.py`
- **Specs**: `openspec/changes/reference-aware-forget-cascade/specs/forget-command/spec.md` (delta), `openspec/specs/forget-command/spec.md` (canonical, merged at archive time)
- **Design/Tasks**: `openspec/changes/reference-aware-forget-cascade/{design.md, tasks.md, proposal.md}`

## Learned

- PR2's correction batch (4 fixes) was squashed into the single 68cf616 commit rather than landing separately
- Working tree is clean and matches Engram-recorded correction content byte-for-byte (verified via grep for K-of-N message, tuple-order comments, and 3 new test names — all present in committed code)

---

**Verified**: 2026-07-23 (fresh full-suite run)
**Verified by**: sdd-verify (executor)
**Result**: PASS WITH WARNINGS — all delta scenarios tested; 1 CRITICAL administrative gap resolved at archive time; 1 SUGGESTION for optional spec amendment; 0 blocking issues
