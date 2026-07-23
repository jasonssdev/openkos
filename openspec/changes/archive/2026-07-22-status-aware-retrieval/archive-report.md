# Archive Report: status-aware-retrieval (S1 — Gap #8 · MVP-3)

**Date**: 2026-07-22  
**Change**: status-aware-retrieval  
**Status**: ARCHIVED AND CLOSED  
**Delivery**: PR #113, merged commit 8627faf (squash)  

---

## Executive Summary

**status-aware-retrieval (S1)** — the first slice of Gap #8 (lifecycle + sensitivity) — is FULLY ARCHIVED. Concept lifecycle state (`status` frontmatter, `supersedes` edges from `reconcile`) now governs visibility across all retrieval inputs (FTS, vector, graph) and candidate-load surfaces (adjudication, contradiction detection). The `status-aware-retrieval` specification is now source of truth at `openspec/specs/status-aware-retrieval/spec.md`. Three review-caught defects were corrected and re-verified; the change contains zero remaining blockers. This slice unblocks the S2–S4 follow-ups (reference-aware forget/tombstones, sensitivity fail-closed filter, export exclusion) and completes the freshness-lint-v1 to MVP-3 handoff.

---

## Change Scope

**status-aware-retrieval (S1)** makes concept lifecycle state gate visibility in retrieval:
- **Core verdict**: Query-time filtering, not index-time exclusion. Deprecated/superseded concepts stay fully present in all search indexes; enforcement is a candidate-pool set-difference at query evaluation time.
- **Effective status resolution**: From `status` field OR inbound `supersedes` edge (target of a different concept's supersedes edge is deprecated). Self-reference guarded to live; cycles fail safe to deprecated.
- **Default exclusion**: Deprecated/superseded concepts excluded from FTS/vector/graph hits, fused lists, adjudication and contradiction candidates by default.
- **Escape flag**: `--include-deprecated` opt-in flag restores full participation for all 4 retrieval commands (query, contradictions, adjudicate, duplicates).
- **Uniform enforcement**: No leak points across FTS/vector/graph/fused paths.
- **Live behavior unchanged**: All-live bundles retrieve identically to status-blind behavior.

**Capabilities**:
- **NEW**: `status-aware-retrieval` (lifecycle-aware query-time filtering, effective status resolution, exclude-by-default, --include-deprecated escape).
- **MODIFIED**: None. `status`/`supersedes` written as before (handled by prior `reconcile` verb).

---

## Delivery Summary

| Field | Value |
|-------|-------|
| **PR** | #113 |
| **Merged Commit** | 8627faf (squash) |
| **Branch Commits** | 5: 439d030 (foundation) + 9902591 (answer path) + 299a5e3 (resolution filters) + c0a1378 (CLI wiring) + a95aaf6 (spec correction) |
| **Actual Changed Lines** | ~770 (lifecycle.py new, answer.py/contradiction.py/candidates.py/cli.py modified, all test files updated) |
| **Review Budget** | 800 lines (orchestrator override) |
| **Budget Status** | High risk in initial estimate; chained PR strategy applied (PR1/PR2/PR3/PR4 work units, each independently revertable) |
| **Strategy** | Chained PRs (feature-branch-chain pattern: PR1 → main, PR2 → main, etc.) |

---

## Verification: PASS

**Verdict**: PASS WITH WARNINGS (1 CRITICAL spec-document staleness issue PRE-ARCHIVED, 0 code/test defects).

### Test Execution
| Gate | Result | Details |
|------|--------|---------|
| **mypy** | ✅ PASS | `src/openkos/lifecycle.py` `src/openkos/retrieval/answer.py` `src/openkos/resolution/contradiction.py` `src/openkos/resolution/candidates.py` `src/openkos/cli/main.py` --strict: Success, no issues found in 5 source files |
| **ruff format** | ✅ PASS | All files formatted correctly |
| **ruff check** | ✅ PASS | All checks passed |
| **pytest (full suite)** | ✅ PASS | 1537 passed in 4.02s |

### Requirement Compliance
All 5 requirements met; all 12 scenarios tested and passing:
1. Effective Status Resolution (3 scenarios: own status, superseded-regardless, self-ref + 2-cycle + 3-cycle)
2. Deprecated Concepts Excluded By Default (3 scenarios: lexical/semantic match, contradiction candidates, only-deprecated-match → NO_MATCH)
3. `--include-deprecated` Escape Flag (2 scenarios: restores match, opt-in default)
4. Uniform Enforcement Across All Retrieval Inputs (2 scenarios: no leak via single input, live-only-reachable-through-deprecated-neighbor)
5. Live Retrieval Behavior Unchanged (1 scenario: all-live bundle)

---

## Review Corrections Confirmed

Three distinct review findings were corrected mid-implementation and re-verified:

### (a) PR1 CRITICAL: Cycle Deprecation False-Negative
- **Finding**: Initial reciprocal-cancellation logic (pair-wise `(s,t)` cancels `(t,s)`) allowed cycle members to escape deprecation under specific conditions (confirmed counter-example: `a→b, b→c, b→a, c→d, d→a` leaked `b` as live).
- **Root Cause**: Per-pair cancellation did not guard against longer cycles or multiple paths.
- **Correction Applied** (commit 1.5): Replaced with fail-safe rule: `superseded = {target for source, target in supersedes if target != source}` — EVERY target of a non-self `supersedes` edge is deprecated, including all cycle members regardless of cycle length.
- **Spec Alignment**: Delta spec explicitly documents this fail-safe behavior (Requirement 1.3, lines 29–33 and scenario lines 46–55); code now matches.
- **Re-verification**: `test_mutual_two_cycle_marks_both_concepts_deprecated`, `test_four_cycle_with_mutual_chord_marks_all_four_deprecated` added and passing; all 18 original lifecycle tests green.

### (b) PR2 Coverage WARNING: Seed-Filter Regression Guard
- **Finding**: During review, identified a coverage gap: no test proved a deprecated concept is withheld from the graph-stage SEED list specifically (contrast with final output absence — different architectural layer).
- **Correction Applied** (commit 2.5): Added `test_deprecated_concept_never_becomes_a_graph_seed` — fixture: deprecated `D` is sole FTS hit and would otherwise seed PPR; live `N` is `D`'s only graph neighbor. Default: `N` absent (D stripped before seed derivation). Flag: `N` surfaces (D seeds, PPR expands). Test vacuity proved by temporary reorder (filter after seed derivation): only this test failed, all 54 prior tests stayed green.
- **Re-verification**: Test present in `tests/unit/retrieval/test_answer.py` L1928, passing in full suite (1537 passed).

### (c) PR3 HIGH: Starvation via Deprecated-Dominated Cap
- **Finding**: `_candidate_pairs` applied the 200-pair cap BEFORE filtering deprecated entries, allowing >200 deduped pairs dominated by deprecated-touching entries to consume every cap slot and starve live pairs sorting beyond index 200 of judgment.
- **Root Cause**: Filter order: `pairs[:_MAX_PAIRS]` THEN filter, instead of filter FIRST.
- **Correction Applied** (commit 3.7): Moved deprecation filtering INSIDE `_candidate_pairs`, BEFORE the cap slice. `total_pair_count` now reflects live-pairs-only.
- **Re-verification**: `test_live_pair_beyond_cap_index_is_not_starved_by_deprecated_pairs_in_cap` and `test_pair_with_deprecated_concept_as_the_alphabetically_first_element_is_excluded` added to `tests/unit/resolution/test_candidates.py`, both passing. Full contradiction/candidates test suite green (100 tests).

### Spec Alignment: Critical Finding Fixed at Archive Time
- **CRITICAL Verification Finding** (per verify-report #1643): Original spec.md documented mutual 2-cycles as staying live, contradicting the actual (correct) fail-safe code behavior (both deprecated).
- **Resolution**: Spec.md was updated in commit a95aaf6 BEFORE this archive: "self-reference stays live, but supersedes cycles fail safe to deprecated" (lines 46–55) now documents the correct fail-safe behavior explicitly.
- **Archive Consequence**: Archived spec matches shipped code. No specification debt.

---

## Locked Decisions Honored

1. **Exclude by default, not down-rank**: No ranking-adjustment code found. Deprecated/superseded fully absent by default, not demoted.
2. **`--include-deprecated` escape flag**: Present on all 4 commands (query ~L2376, contradictions ~L2444, adjudicate ~L2765, duplicates ~L3009) with identical help text.
3. **#1619 (anchor-based reconcile conflict detection) NOT implemented**: Deferred as specified. Zero commits touching reconcile in this branch.

---

## Known Accepted Gap (Non-Blocking)

**docs/cli.md**: Pre-existing documentation debt. No `adjudicate`/`contradictions` sections exist in docs/cli.md (predates this change; confirmed via `rg` — only `query` and `duplicates` sections documented, lines L109/L138). The `--include-deprecated` flag IS implemented on all 4 commands but documented only on `query` and `duplicates` in the existing doc structure. This is a pre-existing gap, not introduced or worsened by this change. Acceptable as a follow-up; not an archive blocker.

---

## Source of Truth: New Canonical Spec

### status-aware-retrieval Specification
- **Location**: `openspec/specs/status-aware-retrieval/spec.md` ✓ CREATED
- **Type**: NEW capability (no prior version; delta spec is canonical verbatim)
- **Content**: 5 requirements, 12 scenarios, full Given/When/Then + RFC 2119.
- **Status**: Source of truth; delta spec merged to canonical location.

### Unchanged: ADR Discipline
- **ADR** (any existing ADR) unchanged ✓
- **docs/adr/** verified untouched ✓
- **Verdict**: Per proposal's explicit "no new ADR" verdict (this arc's filtering is additive/query-time, not lossy). No new ADR required.

---

## Archive Contents

All change artifacts copied to `openspec/changes/archive/2026-07-22-status-aware-retrieval/`:
- ✓ proposal.md
- ✓ design.md
- ✓ tasks.md (18/18 base + 3 corrections complete)
- ✓ verify-report.md (PASS WITH WARNINGS verdict)
- ✓ specs/status-aware-retrieval/spec.md (canonical merged)
- ✓ archive-report.md (this file)

**Active source folder** (`openspec/changes/status-aware-retrieval/`) — scheduled for orchestrator removal after archive validation.

---

## Traceability: Engram Observation IDs

All SDD artifacts persisted to Engram for audit trail and cross-phase reference:

| Artifact | Topic Key | Observation ID | Status |
|----------|-----------|---|--------|
| Proposal | `sdd/status-aware-retrieval/proposal` | #1627 | ✓ Retrieved |
| Specification | `sdd/status-aware-retrieval/spec` | #1630 | ✓ Retrieved |
| Design | `sdd/status-aware-retrieval/design` | #1631 | ✓ Retrieved |
| Tasks | `sdd/status-aware-retrieval/tasks` | #1632 | ✓ Retrieved (all [x]) |
| Apply-Progress | `sdd/status-aware-retrieval/apply-progress` | #1635 | ✓ Retrieved (3 corrections documented) |
| Verify-Report | `sdd/status-aware-retrieval/verify-report` | #1643 | ✓ Retrieved (PASS WITH WARNINGS verdict) |
| Archive-Report | `sdd/status-aware-retrieval/archive-report` | (this save) | ✓ Persisting now |

---

## Sign-Off: Cycle Complete

| Gate | Status | Evidence |
|------|--------|----------|
| **Task Completion** | ✅ PASS | All 18 base tasks [x]; all 3 correction tasks [x]; no unchecked implementation tasks remain |
| **Review Receipt** | ✅ PASS | 4R review completed; 3 corrections applied and re-verified; CRITICAL cycle fix confirmed in shipped code; final code audit clean |
| **Verification** | ✅ PASS | 1537 tests, 5/5 req, 12/12 scenarios, zero code/test blockers, spec-document staleness identified and fixed pre-archive |
| **Spec Merge** | ✅ PASS | status-aware-retrieval canonical at `openspec/specs/status-aware-retrieval/spec.md` |
| **Archive Folder** | ✅ PASS | All artifacts in `openspec/changes/archive/2026-07-22-status-aware-retrieval/` |
| **No ADR Changes** | ✅ PASS | `docs/adr/` untouched; proposal verdict honored |
| **Observation IDs** | ✅ PASS | All artifacts #1627–#1643 recorded for traceability |

**SDD cycle for status-aware-retrieval closed. Change ready for release as part of MVP-3 Gap #8 S1. S2–S4 follow-ups now unblocked.**

---

## Next Steps

1. **Orchestrator**: Remove `openspec/changes/status-aware-retrieval/` source folder (archive complete). Move `openspec/specs/status-aware-retrieval/spec.md` to canonical location (already done in this archive phase).
2. **Capabilities count**: 24 → 25 canonical capabilities (new: `status-aware-retrieval`).
3. **Release notes**: Gap #8 S1 complete; MVP-3 S2–S4 slices (reference-aware forget, sensitivity fail-closed, export exclusion) now unblocked.
4. **Follow-ups**: docs/cli.md adjudicate/contradictions sections (pre-existing gap, deferred); #1619 (relate-edge false-positive, separate subsystem).
