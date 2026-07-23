# Verify Report: Reference-Aware Forget + Tombstones

**Change**: `reference-aware-forget` (MVP-3 gap #8 · S2a)  
**Verdict**: PASS WITH WARNINGS (0 CRITICAL, 2 WARNING, 1 SUGGESTION)  
**Verified**: 2026-07-22 on `main` @ commit `cb4568f` (PR #114, squash-merged)

## Completion Summary

### Tasks Verified
- **Phase 1 Tasks**: 11/11 complete ✅ (`bundle/references.py` module)
- **Phase 2 Tasks**: 11/11 complete ✅ (forget scan, tombstone, supersedes)
- **Phase 3 Tasks**: 7/7 complete ✅ (`--force` gate implementation)
- **Phase 4 Tasks**: 5/5 complete ✅ (orthogonality matrix)
- **Phase 5 Tasks**: 5/5 complete ✅ (path-safety, integration)
- **Correction Batch Tasks**: 14/14 complete ✅ (C.1-C.14 resilience review CRITICAL fix + 3 reliability gaps)
- **Total**: 53/53 tasks complete (all checkmarks on disk; correction batch tracked separately in Engram #1656)

### Test Results

All delta requirement scenarios mapped to named, non-vacuous passing tests:

#### Requirement Coverage
1. **Log Entry on Forget**: 2 scenarios ✅
   - `test_tombstone_log_line_recorded`
   - `test_tombstone_survives_idempotent_rerun`

2. **Inbound Reference Detection**: 3 scenarios ✅
   - `test_no_inbound_references_found`
   - `test_inbound_markdown_link_detected`
   - `test_inbound_typed_relation_detected`

3. **Unverifiable Referrer Detection (Fail-Closed)**: 2 scenarios ✅
   - `test_unverifiable_referrer_mentioning_target_is_surfaced`
   - `test_unverifiable_referrer_not_mentioning_target_is_ignored`

4. **Refuse Forget When Inbound References Exist, Unless `--force`**: 4 scenarios ✅
   - `test_inbound_markdown_link_refuses_by_default`
   - `test_inbound_typed_relation_refuses_by_default`
   - `test_unverifiable_referrer_refuses_by_default`
   - `test_force_overrides_refusal`

5. **`--force` Is Orthogonal to the Confirm Gate**: 3 scenarios ✅
   - `test_force_alone_still_prompts_on_tty`
   - `test_force_and_auto_combined_skip_both_gates`
   - `test_force_without_auto_on_non_tty_still_refuses_at_confirm_gate`

6. **Resurrection Interaction Disclosure**: 2 scenarios ✅
   - `test_forgetting_superseding_concept_discloses_resurrection`
   - `test_no_outbound_supersedes_edge_no_disclosure`

#### Test Execution
- **Unit tests** (references + forget): 51 tests (was 41; +2 references, +8 forget)
- **Full suite**: `pytest tests/unit -q` → **1567 PASSED** (was 1557), **0 FAILED**, 0 regressions
- **Linting**: `ruff check .` → **All checks passed**
- **Format verification**: `ruff format --check .` → **113 files already formatted**
- **Type checking**: `mypy .` (whole-tree) → **Success, no issues in 113 source files** (checked both `references.py` module and consumer in `cli/main.py`)

### Critical Fix Verified

**Fail-Open Guard**: `find_inbound_references` unverifiable/fail-closed backstop

- **Issue**: Inbound-reference scanner reuses merge scanners which silently skip unparseable files. Without fail-closed check, malformed referrer could cause silent deletion while dangling edge exists.
- **Fix**: Independent parse pass with exception handling on `okf.load_frontmatter` / `okf.decode_relations`; on failure, substring-check target_id in raw file text; report as `InboundReference(kind="unverifiable")`.
- **Location**: `src/openkos/bundle/references.py` (new module), wired into `src/openkos/cli/main.py` forget() gate 1 (~L978-1025).
- **Tests**: 
  - Unit: `test_find_inbound_references_detects_unverifiable_referrer_mentioning_target`
  - Integration: `test_unverifiable_referrer_mentioning_target_refuses_without_force` + 2 force variants
- **Non-Vacuity Proof**: Throwaway-revert test (C.4 in tasks.md) confirmed pre-fix exited 0 (silent delete); post-fix exits 1 (refuses). Byte-identical restoration via `cp` from scratchpad backup verifies fix is present.

### Code Review Locked Decisions

All locked decisions from proposal/design honored:

1. ✅ **Tombstone marker**: Log.md marker only; no status field, no frontmatter, no leftover file
2. ✅ **Refuse-not-strip**: Inbound refs refused by default; `--force` leaves dangling as accepted tradeoff
3. ✅ **Gate orthogonality**: `--force` bypasses ONLY inbound-ref refusal; does not alter confirm gate
4. ✅ **Self-scope only**: No descendant cascade code path (S2b deferred)
5. ✅ **Phase B shape**: Non-transactional write with concept-file unlink LAST (unchanged)
6. ✅ **Spec frozen**: `src/openkos/lifecycle.py` untouched (`git diff cb4568f~1 cb4568f -- src/openkos/lifecycle.py` → empty)

## Warnings & Suggestions

### WARNING 1: Spec/Impl Divergence (RESOLVED ✅)
**Status at Verify**: Initial delta spec described "link" and "relation" kinds only; CRITICAL unverifiable fail-closed path added by resilience review was not in original spec text.

**Resolution Applied**: Commit d8ef7d5 amended spec to include "Unverifiable Referrer Detection (Fail-Closed)" requirement (lines 62-86 of delta spec) and updated design.md to reflect `InboundReference.kind` Literal as `"link" | "relation" | "unverifiable"`.

**Current Status**: ✅ SPEC NOW DESCRIBES SHIPPED BEHAVIOR

### WARNING 2: Task-Trail Fragmentation (NON-BLOCKING)
**Issue**: Correction batch (C.1-C.14) never appended to on-disk `tasks.md`; tracked separately as Engram #1656 observation whose upsert fully replaced original content. Neither artifact alone has complete history.

**Impact**: Documentation/traceability only; code and tests complete.

**Resolution**: Archive report documents full task history with Engram IDs for traceability.

**Current Status**: ✅ DOCUMENTED IN ARCHIVE REPORT

### SUGGESTION: Non-Goals Clarity
**Issue**: Non-Goals section stated "detecting or rewriting dangling inbound links... is out of scope" — contradicts new Detection requirement.

**Resolution Applied**: Non-Goals section updated during archive merge to clarify detecting is now IN scope; rewriting/retargeting remains OUT.

**Current Status**: ✅ FIXED IN MERGED CANONICAL SPEC

## Pre-Existing Follow-Ups

Both discovered during verify; neither introduced by this change:

1. **Unescaped title in log-link markdown**: Tombstone title interpolated unescaped into log-line markdown link (`cli/main.py` ~L964). Shared pattern with ingest's `**Ingest**` line (~L640). Recommend escaping markdown in titles as future follow-up (affects both commands).

2. **Tombstone dedup on partial-failure retry**: If Phase B interrupted after log.md write but before concept-file unlink and forget re-run, `insert_log_entry` has no dedup check — another tombstone bullet will be inserted. Consistent with existing "recovery via git status/checkout" model. Recommend dedup logic in `bundle/log.py` as future follow-up.

## Files Modified

- ✅ `src/openkos/bundle/references.py` — NEW (detect-only reference scanner + unverifiable guard)
- ✅ `src/openkos/cli/main.py` — MODIFIED (~L796-1030, forget function + tombstone + gates)
- ✅ `tests/unit/bundle/test_references.py` — NEW (unit tests for references module)
- ✅ `tests/unit/cli/test_forget.py` — MODIFIED (Phase 3-5 scenario tests)
- ✅ `openspec/specs/forget-command/spec.md` — MODIFIED (delta merged, ready for archive)

## Next Steps

None blocking. Ready for archive. S2a complete; S2b (cascade/scope-depth) deferred to next slice.
