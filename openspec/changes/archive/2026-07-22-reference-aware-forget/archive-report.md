# Archive Report: Reference-Aware Forget + Tombstones

**Change**: `reference-aware-forget` (MVP-3 gap #8 · S2a)  
**Status**: ARCHIVED  
**Date**: 2026-07-22

## Delivery

**Commits**:
- PR #114 squash-merged to `main` @ commit `cb4568f` (2026-07-22)
- Specification amendments applied in commit `d8ef7d5` (2026-07-22)

**Mode**: Hybrid (OpenSpec + Engram)

## Capability Status

**Type**: MODIFIED Capability  
**Capability**: `forget-command` (already existed in `openspec/specs/forget-command/spec.md`)  
**Capability Count**: 25 (unchanged — modified existing, no new capabilities)

## Specification Changes

The delta spec has been merged into `openspec/specs/forget-command/spec.md`:

### Revised Requirements
- **Log Entry on Forget**: Upgraded from plain `**Forget**` line to tombstone-marked entry with distinct marker, timestamp, removed id, and title read from frontmatter before deletion.

### Added Requirements
1. **Inbound Reference Detection**: Enumerate all inbound markdown links and typed relations targeting the forgotten concept, using detect-only mode on Phase A, after path-safety checks, before any write; surface in preview.
2. **Unverifiable Referrer Detection (Fail-Closed)**: Run independent fail-closed check on files with unparseable frontmatter or relations — surface as unverifiable when file text contains target id (fail-closed guard against silent deletion with existing inbound edge); ignore unparseable files that do not mention the target.
3. **Refuse Forget When Inbound References Exist, Unless `--force`**: Phase A refusal (exit non-zero, no writes) when inbound references or unverifiable referrers detected, unless `--force` passed; when `--force` used, forget proceeds leaving references dangling as accepted tradeoff.
4. **`--force` Is Orthogonal to the Confirm Gate**: `--force` bypasses ONLY inbound-reference refusal; does NOT skip or alter confirm-gate precedence (`--auto`, `review: false`, TTY, non-TTY); `--force` and `--auto` are independent flags.
5. **Resurrection Interaction Disclosure**: When forgotten concept carries outbound `supersedes` edge (X supersedes Y), disclosure in Phase A preview must name Y before confirm gate, stating Y re-enters retrieval once X is removed.

### Removed Sections
- **Known Limitation** (H2 prose section): Dangling inbound refs are no longer silently accepted limitation — replaced by detection + refusal requirements above.

### Non-Goals Update
- Updated Non-Goals section to clarify: detecting inbound refs is now IN scope; rewriting/retargeting them remains OUT of scope (refuse-not-strip is deliberate).

## Implementation Summary

### Code
- **New module**: `src/openkos/bundle/references.py` — `InboundReference` dataclass + `find_inbound_references(files, *, target_id)` helper, reusing merge scanners in detect-only mode; extended to handle unverifiable (unparseable) referrers via substring check on raw file text.
- **Modified**: `src/openkos/cli/main.py` forget() function (~L796-1030):
  - Added `--force` option
  - Phase A: read concept text/title, build `other_files`, detect inbound refs + unverifiable refs, detect outbound `supersedes`, compute preview lines
  - Gate 1 (NEW): refuse if (verified_refs OR unverifiable_refs) AND NOT force, before existing confirm gate
  - Gate 2 (UNCHANGED): existing confirm logic (`--auto`, `review: false`, TTY/non-TTY precedence)
  - Phase B: write tombstone via `insert_log_entry`, concept-file unlink LAST

### Tests
- **New**: `tests/unit/bundle/test_references.py` — unit tests for `find_inbound_references` (link, relation, unverifiable, empty, fenced-code, self-ref cases)
- **New**: Test additions to `tests/unit/cli/test_forget.py` for Phase 3-5 scenarios (inbound refusal, force override, gate orthogonality, path-safety, integration)

## Full-4R Review

**Status**: PASS WITH WARNINGS (0 CRITICAL, 2 WARNING, 1 SUGGESTION)

### Full Review Execution
All four 4R lenses applied:
1. **review-risk**: Security/architecture reviewed (path traversal gate unchanged; refuse-not-strip tradeoff intentional; no new routing/subprocess)
2. **review-resilience**: Process integration, partial failure recovery reviewed (Phase B write ordering preserved; delete-LAST unchanged; git-recoverable; idempotent re-run supported)
3. **review-readability**: Naming, structure, maintainability reviewed (new module placement in bundle/ consistent with links.py/relations.py; function naming clear; scenarios RFC-2119 compliant)
4. **review-reliability**: Behavior, state, tests, determinism reviewed (all delta requirement scenarios mapped to named tests; CRITICAL fail-closed guard added and proven non-vacuous; three reliability gaps from resilience review closed via correction batch)

### Critical Fix Applied (S2a Resilience Review Correction)
**Fail-Open Issue Identified & Fixed**:
- **Issue**: Inbound-reference scanner reuses merge scanners which silently skip unparseable files. Without fail-closed backstop, a malformed referrer could prevent detection, leading to silent deletion while dangling edge exists (fail-open).
- **Fix**: New `Unverifiable Referrer Detection (Fail-Closed)` requirement added; implemented in `find_inbound_references` via independent parse pass with `okf.load_frontmatter` / `okf.decode_relations` exception handling; on parse failure, substring-check target_id in raw text; report as `InboundReference(kind="unverifiable")`.
- **Integration**: Wired into gate 1 refusal check: `(verified_refs OR unverifiable_refs) and not force`.
- **Non-Vacuity Proof**: Throwaway revert-and-rerun test (C.4, documented in tasks.md) confirmed pre-fix behavior was fail-open (exit 0, silent delete); post-fix behavior is fail-closed (exit 1, refuses).

### Reliability Test Gaps Closed (Correction Batch C.1-C.14)
1. **C.1-C.2**: Unit tests for unverifiable-referrer detection (malformed frontmatter mentioning/not mentioning target)
2. **C.3**: `find_inbound_references` independent parse with exception handling; `kind` Literal extended to include "unverifiable"
3. **C.4-C.7**: CLI integration tests for unverifiable-referrer refusal and distinct preview line
4. **C.8**: TTY gate-ordering test (gate 1 refuses before confirm prompt invoked)
5. **C.9**: Tombstone title-fallback test (missing/blank title → use canonical_id)
6. **C.10-C.11**: Multi-referrer scenario tests (distinct referrers + distinct relation types both reported)

### Test Results
- **Unit**: 51 tests in reference/forget modules (was 41; +2 references, +8 forget)
- **Full Suite**: `pytest tests/unit -q` → **1567 passed** (was 1557), 0 regressions
- **Linting**: `ruff check .` → clean
- **Type Checking**: `mypy .` (whole tree) → **Success, no issues in 113 source files** (checked both `references.py` consumer + module together)
- **Formatting**: `ruff format --check .` → 113 files already formatted

### Locked Decisions Honored
1. ✅ Tombstone marker in log.md only (no status field, no frontmatter, no leftover file)
2. ✅ Reference-aware refuse-unless-force gate (silent strip/rewrite deliberately excluded)
3. ✅ `--force` orthogonal to confirm gate (two independent decision points, gate 1 before gate 2)
4. ✅ Self-scope only (no cascade/descendant code path; S2b deferred)
5. ✅ Non-transactional Phase B shape with concept-file unlink LAST (unchanged)

### Review Findings Summary
- **Verdict**: PASS WITH WARNINGS
- **Changed lines**: ~260-330 (within 400-line review budget)
- **Delivery**: Single PR (no chaining needed)

#### WARNING 1: Spec/Impl Divergence (ADDRESSED BY AMENDMENT d8ef7d5)
- **Issue**: Initial delta spec described only "link" and "relation" kinds; CRITICAL fix added third kind "unverifiable".
- **Resolution**: Spec amended in d8ef7d5 to include "Unverifiable Referrer Detection (Fail-Closed)" requirement (lines 62-86) and updated `design.md` to reflect `InboundReference.kind` as `"link" | "relation" | "unverifiable"`.
- **Status**: ✅ FIXED

#### WARNING 2: Task-Trail Fragmentation (NON-BLOCKING)
- **Issue**: Correction batch (C.1-C.14, CRITICAL fix) tracked separately in Engram (#1656) rather than appended to `tasks.md`; on-disk `tasks.md` contains only original Phase 1-5 tasks.
- **Impact**: Non-blocking; code and tests are complete; documentation trail is split.
- **Note**: Recorded in archive for traceability.

#### SUGGESTION: Non-Goals Prose Clarity (ADDRESSED BY MERGE)
- **Issue**: Non-Goals section stated "detecting... inbound links is out of scope" — contradicted by new Detection requirement.
- **Resolution**: Non-Goals updated during archive merge to clarify detection is now IN scope; rewriting/retargeting remains OUT.
- **Status**: ✅ FIXED

### Pre-Existing Follow-Ups (NOT INTRODUCED BY THIS CHANGE)
1. **Unescaped title in log link**: Tombstone title interpolated unescaped into markdown link (`f"...[{title}](/{canonical_id}.md)..."`); shared with ingest's `**Ingest**` line. Recommend escaping markdown in titles as future follow-up (affects both commands).
2. **Tombstone dedup on partial-failure retry**: If Phase B interrupted after log.md write but before concept-file unlink, re-running `forget` will re-insert another tombstone (no dedup check in `insert_log_entry`). Existing behavior consistent with "recovery via git status/checkout" model; recommend dedup logic as future follow-up.

## Engram Artifacts

| ID | Artifact | Type | Topic |
|----|----------|------|-------|
| 1653 | Proposal | architecture | sdd/reference-aware-forget/proposal |
| 1654 | Spec Delta | architecture | sdd/reference-aware-forget/spec |
| 1655 | Design | architecture | sdd/reference-aware-forget/design |
| 1656 | Tasks | architecture | sdd/reference-aware-forget/tasks |
| 1664 | Verify Report | architecture | sdd/reference-aware-forget/verify-report |

## Archive Contents

- ✅ `proposal.md` (39 lines) — ARCHIVED at `openspec/changes/archive/2026-07-22-reference-aware-forget/proposal.md`
- ✅ `specs/forget-command/spec.md` (delta, 179 lines) — ARCHIVED at `openspec/changes/archive/2026-07-22-reference-aware-forget/specs/forget-command/spec.md`
- ✅ `design.md` (full design, 114 lines) — ARCHIVED at `openspec/changes/archive/2026-07-22-reference-aware-forget/design.md`
- ✅ `tasks.md` (70 checkmarks, all complete) — ARCHIVED at `openspec/changes/archive/2026-07-22-reference-aware-forget/tasks.md`

## Source of Truth Updated

The canonical spec has been updated in place:
- **File**: `openspec/specs/forget-command/spec.md`
- **Changes**: Merged all delta requirements (1 revised, 5 added, 1 removed, 1 section updated)
- **Status**: Git add and commit pending orchestrator

## SDD Cycle Complete

All phases complete:
- ✅ Proposal: Approved (#1653)
- ✅ Spec: Delta spec written (#1654) and merged into canonical (#1654→canonical)
- ✅ Design: Technical approach finalized (#1655)
- ✅ Tasks: 70/70 tasks complete (Phases 1-5) + correction batch (C.1-C.14, 14/14 complete)
- ✅ Apply: Implementation merged PR #114 (cb4568f) + amendments (d8ef7d5)
- ✅ Verify: Full verify PASS WITH WARNINGS (#1664); spec amendments applied
- ✅ Archive: Delta specs synced to canonical; change folder ready for move

**Arc Note**: S2a of gap #8 arc complete (reference-aware detection + tombstones). S2b (cascade/scope-depth) deferred to future slice.

**Capability Evolution**: `forget-command` now includes reference-aware refusal + tombstone marker, completing the destructive-verb safety gap flagged in the proposal.
