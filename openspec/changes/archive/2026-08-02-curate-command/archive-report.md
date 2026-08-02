# Archive Report: curate-command

**Change**: curate-command (Issue #266, Priority P1)
**Baseline**: main @ 18830e2
**Archived**: 2026-08-02
**Mode**: Hybrid (Engram + OpenSpec)
**Status**: COMPLETE — shipped and verified

## Final State Summary

The `curate-command` change is FULLY COMPLETE. Both slices shipped as squash-merged PRs to main (#344 slice 1 @ 5792b1a, #345 slice 2 @ 8c9f366). The new `openkos curate` command is live in the codebase, all 53 implementation tasks are complete, all 13 spec requirements pass their covering tests (22/22 scenarios), and native reviews approved both slices. Issue #266 is CLOSED.

## Artifact Traceability

All SDD artifacts persisted to Engram during the change lifecycle:

| Artifact | Engram Obs ID | Type | Created |
|----------|---------------|------|---------|
| Proposal | #2327 | architecture | 2026-08-02 13:05:03 |
| Specification | #2328 | architecture | 2026-08-02 13:07:08 |
| Design | #2329 | architecture | 2026-08-02 13:10:17 |
| Tasks | #2332 | architecture | 2026-08-02 13:19:24 |
| Verify Report | #2334 | architecture | 2026-08-02 15:37:16 |

**Note on OpenSpec Sync**: This is a hybrid-mode change. The proposal, design, and tasks were persisted to Engram only; the full spec was written to Engram and is now synced to the main specs at `openspec/specs/curate-command/spec.md`. An active `openspec/changes/curate-command/` folder was never populated during the change lifecycle (likely because the change was applied via direct commits rather than through the apply phase's file-write path), but all artifacts are reconstructed and archived here for the audit trail.

## Delivery: Two-Slice Strategy (Load-bearing Split)

The change was delivered in two ordered slices, each its own PR to main, as outlined in the proposal D-Delivery section (>800 line forecast, chained PR strategy required):

### Slice 1: Framework & Preconditions/Identity (PR #344, merged main @ 5792b1a)
- **Shipped**: 2026-08-01
- **Tasks complete**: 30/30
- **Scope**: Curate skeleton, stage framework (Stage/StageProbe/StageOutcome dataclasses), cost-gate/decline machinery (cost_line, gate helpers), Preconditions + Identity stages fully live, D7 lazy OllamaClient, _merge_drift_targets extraction (D6), all five _STAGES entries declared with Preconditions/Identity live=True and Structure/Metadata/Contradictions live=False (not yet available), thin Typer command, initial docs/cli.md entry.
- **Significance**: Ships the ADR-0005/ADR-0011 stage ordering guarantee alone (a shippable, user-valuable milestone per assumption 5).
- **Review**: Approved zero-blockers. Lineage: review-d688ef468eeae4da.

### Slice 2: Core Extractions & Full Stages (PR #345, merged main @ 8c9f366)
- **Shipped**: 2026-08-02
- **Tasks complete**: 23/23
- **Scope**: prepare_relate/relate_core + prepare_set_volatility/set_volatility_core extractions (Phase A/Phase B, D5 seams), relate/set-volatility commands refactored onto cores, Structure/Metadata/Contradictions stages fully live with probe/run implementations, three _STAGES entries flipped live=True, sensitivity-gap report-only (Metadata), all-report-only (Contradictions), doc updates (removed "not yet available" label).
- **Significance**: Completes the full decision queue. The relate/set-volatility extractions are behavior-preserving: test_relate.py and test_set_volatility.py pass unedited.
- **Review**: Approved after ONE bounded correction transaction. Lineage: review-3257ff1166f6b2fd. **Correction detail** (CRITICAL resolved): Metadata's empty-type-list edge case (notice unreachable on an empty type list) was identified as a metadata-sensitivity gap. Fixed by folding the notice into the probe's empty_message, with a new real-probe test added. This correction consumed the single allowed bounded correction transaction per review contract. Four additional WARNINGs were reviewed and closed: cost_line/design D3 overclaim (docstring corrected), _identity_probe empty-queue test added, _preconditions_run direct test added, all-confidential-group no-model-call test added.

## Test & Build Evidence (Final State)

Per Verify Report (obs #2334) at verification time:

- **Test suite**: 3174 passed, 0 failed, 0 skipped (full suite time: 106.74s)
- **Lint**: ruff clean (all checks passed)
- **Types**: mypy strict clean (163 source files, 0 issues)
- **Evidence hash**: sha256:d221d48e2ae38cbed9330114a957a63be547415bb357a902b83ea081c8d6ef4a

All 53 tasks mapped to covering tests in `tests/unit/cli/test_curate.py` (52 test functions, ~62 test cases). TDD compliance: all assertions verify real behavior; no tautologies.

## Specification Compliance (13/13 Requirements, 22/22 Scenarios)

All requirements fully compliant per Verify Report (obs #2334):

1. Stage Order Is A Product Invariant — COMPLIANT (test_full_run_visits_stages_in_order, test_declined_stage_does_not_abort_later_stages)
2. Per-Stage Cost Gate — COMPLIANT (7 tests covering gate behavior, non-TTY matrix, --auto consent, cost-line format)
3. Preconditions Stage Halts The Run — COMPLIANT (3 tests including missing vectors.db halt)
4. Identity Stage Reuses Merge Cores — COMPLIANT (2 tests including N>2 pairwise command echo)
5. Structure Stage Writes Through The Relate Core — COMPLIANT (3 tests including post-merge state freshness)
6. Metadata Stage Writes Tiers, Reports Sensitivity — COMPLIANT (2 tests covering sensitivity-gap report-only)
7. Contradictions Stage Is Report-Only And Last — COMPLIANT (1 test)
8. Resumability By Construction — COMPLIANT (test_sequencer_re_derives_each_stage_fresh)
9. Sensitivity Threading Is Fail-Closed — COMPLIANT (3 tests including confidential member exclusion)
10. Output Discipline And Summary — COMPLIANT (3 tests covering NO_COLOR, five-stage summary)
11. Exit Codes Match Existing Verb Conventions — COMPLIANT (3 tests including drift exit 3)
12. Extracted Cores Preserve Standalone Behavior — COMPLIANT (test_relate/test_set_volatility pass unedited; git diff empty)
13. Slice Boundary — COMPLIANT (2 tests confirming five stages declared and live ordering)

**Verification verdict**: PASS (13/13 requirements, 22/22 scenarios, 0 blockers, 0 CRITICAL findings).

## Native Review Authority

Both slices passed native review. Terminal receipts exist in `.git/gentle-ai/review-transactions/v2/`:

- **Slice 1 (PR #344)**: Lineage review-d688ef468eeae4da — Approved, zero blockers.
- **Slice 2 (PR #345)**: Lineage review-3257ff1166f6b2fd — Approved after one bounded correction transaction.
  - **Correction**: Metadata's empty-type-list edge case (critical metadata-sensitivity gap notice unreachable when type_list is empty). **Fix**: Folded notice into the probe's empty_message and added a real-probe test.
  - **Resolution note**: The correction was CRITICAL (metadata-related, user-visible) but was caught and fixed in-review, staying within the one bounded correction budget. The terminal receipt after the fix approves the amended candidate.

## Behavior Preservation

Independent verification (per design D5 gate and test suite evidence):

- **test_relate.py**: UNEDITED vs pre-change main, passing (regression gate for relate core extraction)
- **test_set_volatility.py**: UNEDITED vs pre-change main, passing (regression gate for set-volatility core extraction)
- **git diff main -- tests/unit/cli/test_relate.py tests/unit/cli/test_set_volatility.py**: 0 lines changed

This proves the relate and set-volatility core extractions are byte-behavior-preserving per design D5.

## Design Decisions Confirmed

All design decisions executed as specified (design rev 2 after validator review, obs #2329):

| Decision | Status | Notes |
|----------|--------|-------|
| D1 Placement (cli/curate.py) | Confirmed | CLI package is composition root; precedent next_action.py/observability.py |
| D2 Stage descriptor + five entries | Confirmed | Framework shape frozen; slice 1 declares all five with live flag; slice 2 flips live; no rework needed |
| D3 Cost gate + consent boundary (spend, not write) | Confirmed | Corrected docstring (overclaim removed); gate behavior tested in 7 tests |
| D4 Queue re-derivation (no memoization) | Confirmed | test_sequencer_re_derives_each_stage_fresh + test_structure_sees_post_merge_identity_state |
| D5 Phase A/B extraction (mirrors PreparedMerge) | Confirmed | Standalone tests pass unedited; file diff confirms seam extraction (3715-3753, 3800-3801, etc.) |
| D6 Drift guard (extracted into _merge_drift_targets) | Confirmed | Extracted from merge command (5399-5410); called by both merge and curate's Identity; TOCTOU test proves exit 3 on drift |
| D7 LLM lifecycle (one lazy OllamaClient) | Confirmed | Verified main.py: 7102/7444/7566/7713 are LLM-backed; Preconditions needs_llm=False; all-declined run builds none |
| D8 Sensitivity (--include-confidential/--include-deprecated) | Confirmed | Forwarded into each stage's library call; fail-closed by default; test_identity_all_confidential_group proves no model call |
| D9 Flags/exits (--auto, --include-*, exit 0/1/2/3) | Confirmed | Corrected: exit enumeration finalized; NO --json in v1 (additive later); NO per-stage --skip |
| D10 Slices (framework+Preconditions/Identity in slice 1, core extractions+three stages in slice 2) | Confirmed | Framework untouched by slice 2 diff; all five stages functional in slice 2 |

## Open Assumptions (Unchanged)

From proposal section "Open assumptions (question round not run)":

1. Contradictions stay report-only/terminal (no auto-merge implied).
2. Sensitivity is reported, not written, in Metadata (no set-sensitivity extraction).
3. Declining a stage is per-run, never remembered.
4. `--auto` accepts every gate globally (no per-stage `--skip-<stage>` in slice 1).
5. Slice 1 alone is shippable and user-valuable (confirmed: shipped 2026-08-01 and users benefit from stage-order guarantee).

## Spec Sync Status

**Main spec created**: `openspec/specs/curate-command/spec.md` (new capability, moved whole from Engram content, 13 requirements, 22 scenarios)

No delta spec existed in `openspec/changes/curate-command/` (folder never populated during apply), so the full spec from Engram was synced to main specs per openspec convention (section "If Main Spec Does NOT Exist").

## Archived Artifacts

This archive folder contains:
- `proposal.md` — Full proposal from Engram obs #2327
- `specs/curate-command/spec.md` — Full spec from Engram obs #2328 (also synced to main specs)
- `design.md` — Full design (rev 2) from Engram obs #2329
- `tasks.md` — Full tasks checklist (53/53 complete) from Engram obs #2332
- `verify-report.md` — Full verification report from Engram obs #2334
- `archive-report.md` — This archive report

## Final Authority and Contradiction Handling

Per Final-State Authority hierarchy (sdd-archive skill section):

1. **Native review authority**: Slice 1 approved zero-blockers; Slice 2 approved after one bounded correction (metadata empty-type-list edge case, now fixed). Terminal receipts in `.git/gentle-ai/review-transactions/v2/`.
2. **Persisted tasks artifact**: All 53 tasks marked complete; no stale unchecked implementation tasks.
3. **Explicit final-state facts in launch prompt**: Two squash-merged PRs (#344, #345) to main (commit 8c9f366), Issue #266 CLOSED, 53/53 tasks, full suite 3174 passed, ruff+mypy strict clean, verify report PASS (13/13 requirements, 22/22 scenarios), behavior preservation held.
4. **Intermediate snapshots** (verify-report, apply-progress): Valid as of their write time; superseded by final state facts above.

No contradictions exist between these sources. The change is complete and closed.

## Rollback (Additive)

Per proposal: both slices are additive and independently revertible. Slice 1 reverts as one commit (nothing else imports curate.py). Slice 2 reverts independently, restoring inline relate/set-volatility bodies. Bundle changes curate already made are normal per-item `_autocommit`s (per D5), revertible as if run by hand.

## Archive Completeness Check

- [x] Main spec synced: openspec/specs/curate-command/spec.md created from Engram content
- [x] Archive folder created: openspec/changes/archive/2026-08-02-curate-command/
- [x] Proposal archived
- [x] Spec archived
- [x] Design archived
- [x] Tasks archived (53/53 complete, no stale unchecked tasks)
- [x] Verify report archived
- [x] All artifacts accounted for (5 SDD artifacts + archive report)
- [x] No unchecked implementation tasks in archived tasks.md
- [x] No CRITICAL issues in verify report
- [x] Native review receipts confirmed (both slices approved)

## Session & Project Metadata

- **Session**: 23ad0dbc-aeb5-4ac5-be5f-55f5d08e435c
- **Project**: openkos
- **Scope**: project
- **Archive Date**: 2026-08-02
- **Artifact Store Mode**: hybrid
- **Status at Archive**: COMPLETE — all work shipped, verified, and closed

---

**End of Archive Report**

This report represents the terminal state of the curate-command change at the time of archival (2026-08-02). All claims are sourced from the Final-State Authority hierarchy: native review receipts, persisted artifacts, and explicit final-state facts from the launch prompt.
