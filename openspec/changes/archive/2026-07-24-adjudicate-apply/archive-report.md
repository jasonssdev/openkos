# Archive Report: adjudicate --apply (#137 Slice 2b-ii)

**Status**: ARCHIVED  
**Date**: 2026-07-24  
**Change**: adjudicate-apply (interactive merge path, #137 Slice 2b-ii)  
**PR**: #165 (merged to main 2026-07-24)  
**Artifact Store**: HYBRID (openspec filesystem + Engram memory)

## Executive Summary

This SDD change completes the third code slice of #137 (the `entity-resolution-adjudication` capability arc). The interactive `adjudicate --apply` mode is now merged to main and archived. The feature enables users to preview and confirm SAME-verdict 2-member merges one-by-one in a single run, with unmerge reversibility and per-merge auto-commit. Verification passed with 0 critical findings (1 non-critical WARNING on prepare_merge branch coverage, closed pre-merge). The batch `--apply-same` remains deferred and gated on #138 (model verdict quality gates); #137 stays OPEN until batch ships.

## What Shipped

### Core Capability: Interactive Adjudication-Apply

- **Feature**: `adjudicate --apply` flag in `src/openkos/cli/main.py` (~3790)
- **Scope**: Interactive walk after normal adjudication run (reuses Ollama tiers, error handling, entry-point gating unchanged)
- **Eligibility**: SAME verdicts ONLY, exactly 2-member groups ONLY
- **Survivor/absorbed**: Alphabetical-first determinism (member_ids[0] survives)
- **Prompt flow**: Preview (via prepare_merge no-write output) → exact prompt `Merge <absorbed> into <survivor>? [y/N/skip]` → user input parsing → conditional merge_core execution
- **Auto-commit**: Each accepted merge commits independently (per-merge granularity, reversible)
- **Failures**: Mid-run merge_core failure stops immediately, prior commits stay (unmerge-reversible)
- **Summary**: End-of-run tally: `applied X, skipped Y (N>2: a, already-merged: b, declined: c)`

### Building Blocks (Reused Unchanged)

- `prepare_merge(...)` (line 2358) — Phase A, no writes, raises on OSError/ValueError
- `merge_core(bundle_dir, index_path, log_path, prepared)` (line 2471) — Phase B, VCS-agnostic, raises on failure
- `_autocommit(root, paths, message)` (line 156) — Best-effort git commit, non-fatal on swallowed errors
- `_resolve_concept_path(bundle_dir, concept_id)` (line 1049) — Path resolution + stale-id guard via ValueError

### Requirements & Scenarios (12 ADDED to spec)

All 12 requirements verified against implementation:

1. **`--apply` Eligibility Filter**: SAME 2-member → prompted; N>2 → skipped with message; DIFFERENT/UNCERTAIN → silently ineligible
2. **Survivor/Absorbed Preview And Prompt**: Alphabetical-first survivor; prepare_merge preview before exact prompt text
3. **Prompt Response Semantics**: `y`/`Y` applies; empty/`n`/`skip` decline
4. **Accepted Merge Executes And Is Reversible**: merge_core writes, ledger entry written, unmerge round-trip byte-identical
5. **Per-Merge Auto-Commit**: Each merge = 1 commit, not 1-per-run
6. **Stale-Id Guard Across Sequential Merges**: Re-verify both member ids; skip with message if already merged
7. **`--apply` Rejects `--json`**: Contradictory modes → stderr + exit 2, adjudicate_candidates never called
8. **`--apply` Composes With `--same-only` As A No-Op**: `--apply --same-only` ≡ `--apply` (apply is inherently SAME-only)
9. **Mid-Run Write Failure Stops The Run**: First merge_core failure → stop, exit non-zero, prior commits intact
10. **End-Of-Run Summary With Breakdown**: Exact format with N>2, already-merged, declined tallies
11. **Empty / No-Eligible State**: No SAME 2-member groups → "nothing to apply", exit 0, no writes
12. **Plain `adjudicate` Is Unchanged**: Non-`--apply` paths (plain, `--json`, `--same-only`) unaffected

## Guardrails & Constraints

### Decision Lock (From Proposal & Design)

- **Survivor Selection**: Always `member_ids[0]` (sorted asc). Deterministic, zero I/O, alphabetical.
- **N>2 Handling**: SKIP v1 (do NOT merge N>2 in v1; user manual intervention required).
- **Prompt Parsing**: Only `y`/`Y` applies; empty/`n`/`skip`/`N` decline.
- **Only SAME Eligible**: DIFFERENT and UNCERTAIN never prompted or applied.
- **--apply + --json**: Contradictory (interactive vs. machine). REJECT, exit 2.
- **--apply + --same-only**: Harmless no-op (apply inherently SAME-only).
- **Commit Granularity**: One commit per accepted merge, not one per run. Matches single-merge UX.
- **Stale-Id Guard**: Re-verify both member ids before each group; skip-with-message if absorbed by prior merge in this run.
- **Mid-Run Failure**: STOP on first merge_core/OSError/ValueError. Do NOT continue after destructive failure. Prior per-merge commits stay reversible.
- **Empty State**: No prompt, no crash; clear "nothing to apply" message, exit 0.

### Out-Of-Scope (Explicit Non-Goals)

- Batch mode `--apply-same` (unattended, no prompts) — DEFERRED, gated on #138 (model verdict quality).
- N>2 group merging in v1.
- Any survivor heuristic beyond alphabetical-first.
- Changing verdict/similarity logic or merge/unmerge core.
- Tombstones, merge records, sensitivity recompute (slice 3 territory).

## Verification Summary

### Test Coverage & Status

- **Requirements & Scenarios**: 12/12 ADDED requirements verified; 18/18 total scenarios passing
- **Tasks**: 28/28 implementation tasks complete (RED/GREEN TDD phases 1–6 all checked)
- **Test Suite**: Full 2023 tests passed (independently re-run; matching apply-progress self-report)
- **Code Quality**: ruff check/format/mypy all clean (independently re-run)

### Coverage Analysis

- **Spec coverage**: 100% — all 12 requirements have ≥1 scenario test
- **Authored diff**: 142 insertions, 0 deletions (additive only; no churn in prepare_merge/merge_core/existing reqs)
- **One non-critical WARNING** (closed pre-merge): prepare_merge except-branch (line 498-504) has zero runtime coverage in the mid-run-failure test suite. Scenario spec says "fails inside merge_core" (not prepare_merge), so non-blocking; recommend follow-up test for prepare_merge failure path.
- **Zero data-loss findings** from bounded risk review

### Verification Verdict

**PASS WITH WARNINGS**  
Verdict: Change is production-ready. The single WARNING (prepare_merge exception coverage) is acknowledged non-blocking, traceable to scenario spec scope, and documented for follow-up. No CRITICAL findings.

## Modified Capability & Spec Merge

### Spec Artifact

**File**: `openspec/specs/entity-resolution-adjudication/spec.md`

**Pre-existing sections** (preserved):
- Purpose, Non-Goals, 22 requirements from #139 + Slice 2a (--json, tally, legend, next-hint, etc.)

**ADDED sections** (12 new requirements from this delta):
1. `--apply` Eligibility Filter
2. Survivor/Absorbed Preview And Prompt
3. Prompt Response Semantics
4. Accepted Merge Executes And Is Reversible
5. Per-Merge Auto-Commit
6. Stale-Id Guard Across Sequential Merges
7. `--apply` Rejects `--json`
8. `--apply` Composes With `--same-only` As A No-Op
9. Mid-Run Write Failure Stops The Run
10. End-Of-Run Summary With Breakdown
11. Empty / No-Eligible State
12. Plain `adjudicate` Is Unchanged

**Total Requirements**: 34 (22 pre-existing + 12 ADDED)  
**Total Scenarios**: 63+ (all passing)  
**Status**: Merged, no duplicates, no deletions, all pre-existing content preserved.

## Issue Arc Status (#137 & Dependencies)

### Completed Slices (Shipped)

| Slice | Title | PR | Status |
|-------|-------|----|----|
| 2a | adjudicate --json + tally/legend/hint | #139 | CLOSED (merged 2026-07-08) |
| 2b-i | merge + prepare_merge + merge_core + unmerge | #163 | CLOSED (merged 2026-07-09) |
| 2b-ii | adjudicate --apply (this change) | #165 | CLOSED (merged 2026-07-24) |

### Remaining Work (Deferred)

| Slice | Title | Gate | Status |
|-------|-------|------|--------|
| 2c | adjudicate --apply-same (unattended batch) | #138 verdict quality | OPEN (deferred) |

### Issue Closure

**#137 Status**: STAYS OPEN  
**Reason**: Batch mode `--apply-same` (slice 2c) remains deferred, gated on #138 (model verdict quality gates for unattended merges). The issue will not close until slice 2c ships AND #138 gates are met.

**#139 Status**: CLOSED (2026-07-08, by #139 PR merge)  
**Reason**: `--json`, tally, legend, next-hint, and pre-interactive-apply adjudicate features complete.

## Rollback & Reversibility

### Per-Merge Reversibility

Each applied merge via `adjudicate --apply` produces:
1. One standalone commit (per-merge, not bundled)
2. One `merged_from` ledger entry in bundle/log.md
3. Survivor file updated; absorbed file removed

**Unmerge Path**: `openkos unmerge <survivor_id>` restores absorbed member, reverts ledger entry.

**Commit Reversibility**: Each commit can be reverted independently via `git revert <commit>`, leaving later commits in the same run intact.

### Feature Rollback

Revert the entire `--apply` feature:
1. Drop the `--apply` flag from adjudicate (lines ~3790+N in src/openkos/cli/main.py)
2. Restore pre-change non-apply adjudicate behavior (byte-identical, zero regression per spec)
3. Building blocks (prepare_merge, merge_core, _autocommit, unmerge) remain untouched for `merge` and other verbs

## Artifact Traceability

### SDD Observation IDs (Engram)

- **Proposal**: #1878 (sdd/adjudicate-apply/proposal)
- **Spec (Delta)**: #1879 (sdd/adjudicate-apply/spec)
- **Design**: #1880 (sdd/adjudicate-apply/design)
- **Tasks**: #1881 (sdd/adjudicate-apply/tasks)
- **Apply Progress**: (tracked in apply phase, available in apply-progress.md if hybrid mode)
- **Verify Report**: #1883 (sdd/adjudicate-apply/verify-report)
- **Archive Report (This)**: saved after completion

### Filesystem Artifacts (OpenSpec)

- `openspec/changes/adjudicate-apply/proposal.md` ✅
- `openspec/changes/adjudicate-apply/specs/entity-resolution-adjudication/spec.md` (delta, merged into main) ✅
- `openspec/changes/adjudicate-apply/design.md` ✅
- `openspec/changes/adjudicate-apply/tasks.md` ✅
- `openspec/changes/adjudicate-apply/verify-report.md` ✅
- `openspec/changes/adjudicate-apply/archive-report.md` (this file) ✅
- `openspec/specs/entity-resolution-adjudication/spec.md` (MERGED, contains all 34 requirements) ✅

## Archive Contents Verification

- [x] proposal.md present
- [x] specs/entity-resolution-adjudication/spec.md present (delta)
- [x] design.md present
- [x] tasks.md present (28/28 tasks checked, no stale implementation items)
- [x] verify-report.md present
- [x] archive-report.md written

## Summary: SDD Cycle Complete

**Change**: adjudicate --apply (#137 Slice 2b-ii)  
**Proposed**: 2026-07-24 19:29 UTC  
**Designed**: 2026-07-24 19:33 UTC  
**Tasked**: 2026-07-24 19:34 UTC  
**Applied**: 2026-07-24 (PR #165)  
**Verified**: 2026-07-24 19:58 UTC (PASS WITH WARNINGS, 0 critical)  
**Archived**: 2026-07-24 (this report)

The `entity-resolution-adjudication` capability now includes:
- Read-only `adjudicate` verb (slice 1, from prior SDD)
- `--json` output + tally/legend/hint (slice 2a, #139, CLOSED)
- `merge`/`prepare_merge`/`merge_core`/`unmerge` (slice 2b-i, #163, CLOSED)
- Interactive `adjudicate --apply` (slice 2b-ii, #165, THIS CHANGE, CLOSED)
- (Pending) Unattended `--apply-same` batch mode (slice 2c, deferred to #138 gate)

Ready for the next change. #137 remains OPEN pending batch mode and #138 verdict quality gates.
