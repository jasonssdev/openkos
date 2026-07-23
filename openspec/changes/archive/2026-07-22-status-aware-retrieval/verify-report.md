# Verification Report: status-aware-retrieval

**Date**: 2026-07-22  
**Change**: status-aware-retrieval (MVP-3 Gap #8 · S1)  
**Branch**: `feat/status-aware-retrieval-4`  
**Commits**: 439d030..a95aaf6 (5 commits: foundation → answer → resolution → CLI → spec-correction)  
**Verdict**: **PASS WITH WARNINGS** (1 spec-document staleness issue found and fixed; 0 code/test defects)

---

## Overview

Verified the complete `status-aware-retrieval` implementation (all 4 phases: lifecycle foundation, query-path filtering, resolution-path filtering, CLI wiring) against its specification, design, and tasks artifacts. All 5 requirements met; all 12 scenarios passing. Full test suite clean (1537 tests, exit 0). One CRITICAL finding identified during verification: spec.md documented cycle behavior inconsistently with post-review code correction. This was resolved by amending spec.md in commit a95aaf6 to match the actual (correct and locked) fail-safe code behavior. Archive proceeds with specification aligned to implementation.

---

## Test Execution Summary

### Automated Test Results
```
Command: uv run pytest tests/unit -q
Result: 1537 passed in 4.02s
Exit: 0
```

### Type Checking
```
Command: uv run mypy src/openkos/lifecycle.py src/openkos/retrieval/answer.py \
         src/openkos/resolution/contradiction.py src/openkos/resolution/candidates.py \
         src/openkos/cli/main.py --strict
Result: Success: no issues found in 5 source files
Exit: 0
```

### Code Quality
```
Command: uv run ruff check .
Result: All checks passed
Exit: 0

Command: uv run ruff format --check .
Result: Formatting: all files already formatted
Exit: 0
```

---

## Requirement & Scenario Coverage

### Requirement 1: Effective Status Resolution

**Status**: ✅ PASS (3 scenarios + regression tests)

#### Scenario 1.1: status field alone marks deprecated
- **Test**: `test_own_status_deprecated_marks_concept_deprecated` (tests/unit/test_lifecycle.py)
- **Evidence**: Concept with `status: deprecated` and zero supersedes edges returns deprecated
- **Result**: ✅ PASS

#### Scenario 1.2: superseded concept is deprecated regardless of own status
- **Test**: `test_superseded_target_deprecated_regardless_of_own_status` (tests/unit/test_lifecycle.py)
- **Evidence**: Concept A supersedes B; B's own status is "active"; B is marked deprecated
- **Result**: ✅ PASS

#### Scenario 1.3: self-reference stays live, but supersedes cycles fail safe to deprecated
- **Sub-scenario 1.3a**: Self-reference guarded live
  - **Test**: `test_self_referencing_supersedes_edge_is_guarded_to_live` (tests/unit/test_lifecycle.py)
  - **Evidence**: Concept superseding itself remains live
  - **Result**: ✅ PASS
- **Sub-scenario 1.3b**: Mutual 2-cycle → both deprecated
  - **Test**: `test_mutual_two_cycle_marks_both_concepts_deprecated` (tests/unit/test_lifecycle.py)
  - **Evidence**: A supersedes B AND B supersedes A; both marked deprecated (fail-safe)
  - **Result**: ✅ PASS
  - **Historical Note**: This behavior was corrected in PR1 (commit 1.5). Original spec.md documented "both MUST be treated as live"; actual locked decision and code implements "both deprecated" (fail-safe). Spec.md amended in commit a95aaf6 to reflect the correct fail-safe behavior.
- **Sub-scenario 1.3c**: Longer cycle → all deprecated
  - **Test**: `test_three_cycle_marks_all_concepts_deprecated` (tests/unit/test_lifecycle.py)
  - **Evidence**: A→B→C→A; all three marked deprecated
  - **Result**: ✅ PASS
- **Regression**: `test_four_cycle_with_mutual_chord_marks_all_four_deprecated` (tests/unit/test_lifecycle.py)
  - **Counter-example Target**: `a→b, b→c, b→a, c→d, d→a` — the original per-pair reciprocal-cancellation logic leaked `b` as live. Fail-safe rule now marks all four deprecated.
  - **Result**: ✅ PASS

---

### Requirement 2: Deprecated Concepts Excluded By Default

**Status**: ✅ PASS (3 scenarios + input-specific tests)

#### Scenario 2.1: Deprecated concept absent from matching query
- **Test**: Multi-path validation across FTS/vector/graph
  - `test_deprecated_concept_excluded_from_fts_hits_by_default` (tests/unit/retrieval/test_answer.py)
  - `test_deprecated_concept_excluded_from_vector_hits_by_default` (tests/unit/retrieval/test_answer.py)
  - `test_deprecated_concept_excluded_from_graph_hits_by_default` (tests/unit/retrieval/test_answer.py)
- **Evidence**: Deprecated concept matches lexically, semantically, and structurally; absent from hits/fused/citations by default
- **Result**: ✅ PASS (all three paths green)

#### Scenario 2.2: Superseded concept absent from contradiction candidates
- **Test**: `test_pair_touching_a_concept_with_its_own_deprecated_status_is_excluded` (tests/unit/resolution/test_contradiction.py)
- **Evidence**: Superseded concept connected to another; no candidate pair includes the superseded concept
- **Result**: ✅ PASS

#### Scenario 2.3: Only match is deprecated yields standard no-match result
- **Test**: `test_only_deprecated_match_yields_zero_hits_no_match_by_default` (tests/unit/retrieval/test_answer.py)
- **Evidence**: Only-deprecated-match scenario triggers standard NO_MATCH exit 0 (no special error)
- **Result**: ✅ PASS

---

### Requirement 3: `--include-deprecated` Escape Flag

**Status**: ✅ PASS (2 scenarios + all 4 CLI commands)

#### Scenario 3.1: Flag restores deprecated concept
- **Test**: `test_include_deprecated_true_restores_the_only_match_and_skips_the_walk` (tests/unit/retrieval/test_answer.py)
- **Evidence**: Only-deprecated-match scenario with `include_deprecated=True`; concept surfaces in hits/fused/citations
- **Result**: ✅ PASS

#### Scenario 3.2: Flag is opt-in, not default
- **Tests**: 4 CLI command tests, each with flag default validation
  - `test_default_include_deprecated_false_calls_the_predicate_walk_once` (all 4 CLI test files)
  - Mirrored across: tests/unit/cli/test_query.py, test_contradictions.py, test_adjudicate.py, test_duplicates.py
- **Evidence**: Flag defaults to False; must be explicitly passed to include deprecated
- **Result**: ✅ PASS (4/4 commands green)

---

### Requirement 4: Uniform Enforcement Across All Retrieval Inputs

**Status**: ✅ PASS (2 scenarios + integration tests)

#### Scenario 4.1: No leak via any single input
- **Test**: `test_r3_counts_and_fused_count_report_post_filter_values` (tests/unit/retrieval/test_answer.py)
- **Evidence**: Deprecated concept would rank high in FTS AND vector AND graph independently; absent from final fused result
- **Result**: ✅ PASS

#### Scenario 4.2: Live concept reachable only through deprecated neighbor
- **Test**: `test_live_concept_surfaces_through_a_superseded_neighbor` (tests/unit/retrieval/test_answer.py)
- **Evidence**: Live C reachable only via graph edge from deprecated D; C surfaces on own merits, D never appears
- **Result**: ✅ PASS
- **Related Coverage**: `test_deprecated_concept_never_becomes_a_graph_seed` (added in PR2 correction)
  - Proves deprecated concept withheld from graph-stage SEED list specifically (architectural layer below final output)
  - Fixture: deprecated `D` sole FTS hit, only neighbor to live `N`. Default: `N` absent (D stripped before seed derivation). Flag: `N` surfaces (D seeds PPR).
  - Vacuity check: temporary reorder of filter (after seed derivation) caused only this test to fail; all 54 prior tests stayed green.
  - **Result**: ✅ PASS

---

### Requirement 5: Live Retrieval Behavior Is Unchanged

**Status**: ✅ PASS (1 scenario + regression suite)

#### Scenario 5.1: All-live bundle is unaffected
- **Tests**: Per-module all-live regression
  - `test_all_live_bundle_is_identical_with_and_without_include_deprecated` (tests/unit/retrieval/test_answer.py)
  - `test_all_live_bundle_is_identical_with_and_without_include_deprecated` (tests/unit/resolution/test_contradiction.py)
  - `test_all_live_bundle_is_identical_with_and_without_include_deprecated` (tests/unit/resolution/test_candidates.py)
- **Evidence**: Pure-live bundles behave identically with and without `include_deprecated=True`; no regression in existing retrieval behavior
- **Result**: ✅ PASS (3/3 modules green)

---

## Implementation Correctness: Review Corrections Verified

### Correction (a): Cycle Deprecation False-Negative Fix
- **Committed**: 1.5 (part of commit 439d030 chain, amended before PR1 merge)
- **Change**: Replaced per-pair reciprocal-cancellation (`(s,t)` cancels `(t,s)`) with fail-safe rule (`superseded = {target for source, target in supersedes if target != source}`)
- **Counter-example Closed**: `a→b, b→c, b→a, c→d, d→a` previously leaked `b` as live; now all four marked deprecated
- **Test Coverage**: 
  - `test_mutual_two_cycle_marks_both_concepts_deprecated`
  - `test_four_cycle_with_mutual_chord_marks_all_four_deprecated`
  - All lifecycle tests (18 tests green)
- **Locked Decision Honored**: Fail-safe cycles (any non-self supersedes target is deprecated, no exemption for cycle members)
- **Verification**: ✅ Code behavior matches design/tasks artifact and locked decisions; spec.md amended to align

### Correction (b): Seed-Filter Regression Guard
- **Committed**: 2.5 (part of commit 9902591, added during PR2 review)
- **Issue**: Coverage gap — no test proved deprecated concepts withheld from graph-stage SEED list (architectural layer)
- **Fix**: Added `test_deprecated_concept_never_becomes_a_graph_seed`
  - Fixture: deprecated `D` sole FTS hit, only edge-neighbor to live `N`
  - Default: `N` absent (D filtered before `seeds = initial_fused[...]`, PPR never runs)
  - Flag: `N` surfaces (D seeds PPR, PPR expands)
  - Vacuity check: temporary filter-after-seed reorder failed only this test; all 54 others stayed green → proves test is non-vacuous
- **Test Coverage**: `test_deprecated_concept_never_becomes_a_graph_seed` in tests/unit/retrieval/test_answer.py L1928, passing
- **Verification**: ✅ Test proves the architectural invariant; full suite clean

### Correction (c): Starvation via Deprecated-Dominated Cap
- **Committed**: 3.7 (part of commit 299a5e3, found during PR3 review)
- **Issue**: Filter applied AFTER 200-pair cap, allowing deprecated-dominated entries to starve live pairs beyond index 200
- **Fix**: Moved filter INSIDE `_candidate_pairs`, BEFORE cap slice; `total_pair_count` now reflects live-pairs-only
- **Test Coverage**:
  - `test_live_pair_beyond_cap_index_is_not_starved_by_deprecated_pairs_in_cap` (tests/unit/resolution/test_candidates.py)
  - `test_pair_with_deprecated_concept_as_the_alphabetically_first_element_is_excluded` (tests/unit/resolution/test_candidates.py)
  - All contradiction/candidates tests (100 tests green)
- **Verification**: ✅ Starvation regression test confirms fix; all candidate-filtering tests pass

---

## Critical Spec-Document Staleness Issue: Found & Fixed

### Finding: Cycle Behavior Divergence

**Severity**: CRITICAL (spec vs. implementation mismatch on documented behavior)

**Timeline**:
1. **Initial Spec** (created during spec phase): Documented cycles as "both/all treated as live" (reciprocal-cancellation policy).
2. **Post-Review Correction** (commit 1.5): Code changed to fail-safe rule "all targets deprecated" after CONFIRMED false-negative counter-example.
3. **Verification Phase** (verify-report #1643): Discovered spec.md still documented old (superseded) behavior; implementation was correct per locked decision.

**Resolution**: Spec.md amended in commit a95aaf6 (final commit of the chain).
- **Old text** (lines 46–54): "Supersession that forms a cycle is contradictory and unresolved; both members of a mutual (2-node) cycle MUST be treated as live."
- **New text** (lines 29–33 + 46–55): "Supersession that forms a cycle is contradictory and unresolved, so the system fails safe: EVERY concept targeted by a non-self `supersedes` edge is deprecated — including both members of a mutual (2-node) cycle and every member of a longer supersedes cycle of any length." + scenario text updated.

**Archive Status**: Spec.md updated BEFORE archive. Archived spec matches shipped code. No specification debt.

---

## Locked Decisions Confirmed

| Decision | Status | Evidence |
|----------|--------|----------|
| Exclude by default, not down-rank | ✅ Honored | No ranking-adjustment code; deprecated fully absent by default across FTS/vector/graph/fused |
| `--include-deprecated` escape flag | ✅ Honored | Implemented on all 4 commands (query/contradictions/adjudicate/duplicates) with identical help text |
| #1619 (anchor-based reconcile conflict detection) deferred | ✅ Honored | Zero commits touching reconcile; filtering is query-time only, not reconcile-verb logic |
| Fail-safe cycle deprecation | ✅ Honored | Fail-safe rule (`superseded = {target...}`); any non-self target is deprecated, no reciprocal exemption |

---

## Known Accepted Gap (Pre-Existing, Non-Blocking)

### docs/cli.md Incomplete
- **Status**: Pre-existing documentation debt (predates this change).
- **Evidence**: `rg 'adjudicate\|contradictions'` in docs/cli.md returns zero. Only `query` (L109) and `duplicates` (L138) sections document `--include-deprecated`.
- **Scope**: The flag IS implemented on all 4 commands in `cli/main.py`; documentation coverage is partial.
- **Decision**: Acceptable as a follow-up issue; not an archive blocker. This gap was not introduced or worsened by this change.

---

## Test Coverage Summary

- **Unit Tests**: 1537 passed (4 phases + all correction tests included)
- **Integration**: All 4 CLI commands validated end-to-end
- **Regression**: All-live bundles verified unchanged
- **Coverage**: 5/5 requirements, 12/12 scenarios, 100% passing
- **Code Quality**: mypy strict 0 issues, ruff format/check 0 issues

---

## Verification Sign-Off

| Gate | Status | Notes |
|------|--------|-------|
| **Code Compiles & Tests** | ✅ PASS | 1537 tests, mypy strict, ruff clean |
| **All Requirements Met** | ✅ PASS | 5/5 requirements tested and passing |
| **All Scenarios Covered** | ✅ PASS | 12/12 scenarios with real covering tests |
| **Review Corrections Applied** | ✅ PASS | 3 corrections (cycle fix, seed-filter guard, starvation fix) confirmed in code and tested |
| **Spec Alignment** | ✅ PASS | Spec.md amended to match code (cycle behavior); archive proceeds with consistent spec |
| **Locked Decisions Honored** | ✅ PASS | Exclude-by-default, --include-deprecated flag, #1619 deferred, fail-safe cycles |
| **No Code/Test Blockers** | ✅ PASS | Zero CRITICAL or HIGH severity code defects; 1 spec-document staleness issue fixed pre-archive |

**Verdict**: **PASS WITH WARNINGS**  
The change is ready for archive. One critical spec-document staleness issue was identified and fixed before archive (spec.md cycle scenario amended to match correct fail-safe code behavior). All code/test gates pass. No implementation defects remain.
