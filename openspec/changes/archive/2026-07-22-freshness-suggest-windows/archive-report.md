# Archive Report: freshness-suggest-windows (S2)

**Date**: 2026-07-22
**Change**: freshness-suggest-windows (Slice 2 of freshness-lint-v1)
**Status**: COMPLETE — Both PRs merged to main, verified, and archived

## Executive Summary

The freshness-suggest-windows (S2) change has been fully planned, implemented (2 PRs), verified (both PASS with 0 CRITICAL issues), and archived. The change delivers a read-only `suggest-volatility` CLI verb and a `type_tiers:` config override layer, extending ADR-0007 without creating a new ADR. A new canonical capability `volatility-suggestion` was created, and the `concept-volatility` capability was already merged in PR1. No ADR-0007 edits were performed.

## Change Summary

**What**: Slice 2 of freshness-lint-v1, delivering per-type volatility tier suggestions via LLM advisory verb + hand-editable config layer.

**Why**: S1 hardcoded type→tier mappings; S2 closes the "is this the right tier?" loop for domain-specific bundles by adding a read-only advisory verb that proposes tiers per type, with a config layer to apply suggestions by hand-edit (no code change, no unsafe auto-writes).

**Scope**:
- NEW `volatility-suggestion` capability (read-only `suggest-volatility` CLI verb)
- MODIFIED `concept-volatility` capability (added `type_tiers:` config override + new precedence step in window_for_doc)
- NO new ADR (extends ADR-0007 only)

## Delivery: 2 PRs, Both Merged to main

| PR | Commits | Scope | Status |
|-------|---------|-------|--------|
| PR #109 | d0a2110 (includes resilience correction ee23e6b) | Type_tiers config layer + precedence step + concept-volatility spec sync | MERGED main, VERIFIED PASS |
| PR #110 | 0b27518 (folded original PR2+PR3 per orchestrator scope) | Engine leaf resolution/volatility_typing.py + CLI verb suggest-volatility + 33 tests | MERGED main, VERIFIED PASS |

**Workload**: 1,060 authored lines (PR2 only; PR1 ~320 lines). Accepted as size:exception per orchestrator instructions.

## Verification Results

### PR1 Verification (#1590)

**Verdict**: PASS (0 CRITICAL, 0 WARNING, 0 SUGGESTION)
- Spec compliance: 2 requirements (1 ADDED type_tiers, 1 MODIFIED precedence), 7 scenarios — all covered by real passing tests
- Scope verified: config.py, lint.py, template changes only; no cli/main.py, no engine leaf
- ADR-0007: confirmed untouched, extended only via spec layer
- Task completion: Phases 1-3 complete [x], Phases 4-5 correctly deferred [blank] (PR2)
- Independent gate re-run: 1352 passed, ruff/mypy clean

### PR2 Verification (#1593)

**Verdict**: PASS (0 CRITICAL, 0 WARNING, 0 SUGGESTION)
- Spec compliance: 4 requirements (volatility-suggestion), 9 scenarios — all covered by real passing tests (independently re-run)
- Scope verified: resolution/volatility_typing.py, cli/main.py (suggest-volatility command), 3 new test files; no PR1 files touched
- ADR-0007: confirmed untouched, extended only via spec layer
- Task completion: ALL 19 tasks across both PRs checked [x]; 0 unchecked items remain
- Independent gate re-run: 1387 passed (full suite) + 33 passed (focused PR2 tests), ruff/mypy clean

**No material deviations** from design.md; one documented deviation from PR1 (stricter registry-membership guard in window_for_doc) already accepted/merged.

## Spec Syncing

### New Canonical Capability: `volatility-suggestion`

**Action**: CREATED (new spec — no prior canonical version existed)
**Source**: `openspec/changes/freshness-suggest-windows/specs/volatility-suggestion/spec.md`
**Destination**: `openspec/specs/volatility-suggestion/spec.md`
**Delta**: Full spec verbatim (4 requirements, 9 scenarios)
**Status**: COMPLETE ✓

### Modified Canonical Capability: `concept-volatility`

**Action**: VERIFIED ALREADY MERGED in PR1
**Source**: `openspec/changes/freshness-suggest-windows/specs/concept-volatility/spec.md`
**Destination**: `openspec/specs/concept-volatility/spec.md`
**Delta**: 1 ADDED requirement (type_tiers Config Override Layer) + 1 MODIFIED requirement (Deterministic, Never-Raising Window Resolution, 4 scenarios)
**Status**: COMPLETE ✓ (merged as part of PR1 commit d0a2110, verified in canonical spec)

## ADR Assessment

**Verdict**: NO new ADR created; ADR-0007 remains Accepted, untouched.

**Limb A** (hard to reverse?): No. The type_tiers layer is additive, read-only, absent-default {}, degrades on invalid entries (never raises), and is removable without migration.

**Limb B** (new decision?): No. S2 is a natural extension of the type→tier seam ADR-0007 already accepted — one more override source in the same precedence ladder, not a new architectural axis.

**Confirmation**: Both PR1 and PR2 verification reports independently confirmed `docs/adr/0007-volatility-taxonomy.md` diff is empty — no edits performed.

## Folder Move

**Source**: `openspec/changes/freshness-suggest-windows/`
**Destination**: `openspec/changes/archive/2026-07-22-freshness-suggest-windows/`
**Status**: COMPLETE ✓ (original folder removed; no duplicate left)

**Archive Contents**:
- proposal.md ✓
- design.md ✓
- tasks.md ✓ (19/19 tasks complete [x])
- verify-report-pr1.md ✓
- verify-report-pr2.md ✓
- specs/concept-volatility/spec.md ✓ (delta, reference)
- specs/volatility-suggestion/spec.md ✓ (delta, reference)
- archive-report.md (this file) ✓

## Engram Traceability

| Artifact | Observation ID | Phase | Type |
|----------|---|-------|------|
| Proposal | #1580 | sdd-proposal | architecture |
| Spec | #1582 | sdd-spec | architecture |
| Design | #1584 | sdd-design | architecture |
| Tasks | #1585 | sdd-tasks | architecture |
| Apply-progress | #1588 | sdd-apply | architecture |
| Verify-report PR1 | #1590 | sdd-verify | architecture |
| Verify-report PR2 | #1593 | sdd-verify | architecture |
| Archive-report | (persisted to Engram as sdd/freshness-suggest-windows/archive-report) | sdd-archive | architecture |

## Remaining Work (Deferred)

**Follow-up #1592** (pre-existing, out of S2 scope): `read_config` YAML TypeError hardening for graceful handling of type mismatches in config values. Documented in verify-report-pr1 but deferred to a future slice (S3 or hardening change).

**Remaining slices**: S3 (contradiction/staleness detection), S4 (guided reconcile write-verb) — tracked separately in freshness-lint-v1 initiative.

## SDD Cycle Complete

✓ Proposed: defined scope, approach, rollback plan
✓ Specified: 2 capabilities (1 new, 1 modified), 13 requirements/scenarios
✓ Designed: technical approach, file changes, interfaces, data flow, testing strategy
✓ Tasked: 6 phases across 2 PRs, with workload forecasting and delivery strategy
✓ Applied: 2 PRs, both implemented with Strict TDD (RED→GREEN→REFACTOR)
✓ Verified: both PRs verified PASS (0 CRITICAL), spec compliance 100%, all tests passing
✓ Archived: specs merged, change folder moved, this audit trail persisted

The freshness-suggest-windows (S2) change is now closed and ready for the next slice.
