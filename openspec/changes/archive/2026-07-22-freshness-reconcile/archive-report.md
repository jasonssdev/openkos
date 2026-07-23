# Archive Report: freshness-reconcile (S4 — FINAL slice of freshness-lint-v1 arc)

**Date**: 2026-07-22  
**Change**: freshness-reconcile  
**Status**: ARCHIVED AND CLOSED  
**Delivery**: PR #112, merged commit 185446c (squash)  

---

## Executive Summary

**freshness-reconcile (S4)** — the first WRITE verb of the freshness-lint-v1 arc — is FULLY ARCHIVED. This change completes the entire **freshness-lint-v1 arc** (all 4 slices: S1 volatility taxonomy, S2 type_tiers/suggest-volatility, S3 contradictions, S4 reconcile). The `reconcile` command is delivered, verified (PASS: 1483 tests, 6/6 requirements, 10/10 scenarios), and the new canonical `reconcile-command` specification is now source of truth at `openspec/specs/reconcile-command/spec.md`. A CRITICAL data-integrity bug found during 4R review was fixed and re-verified; the change contains zero remaining blockers.

---

## Change Scope

**freshness-reconcile (S4)** introduces `openkos reconcile <id-a> <id-b>` — the arc's first WRITE verb, which records how a human resolves a contradiction surfaced by S3 `contradictions`:
- Default: symmetric `reconciled_with` edges on both concepts (additive, idempotent, git-reversible).
- `--winner <id>`: directional `supersedes` edge (winner only, no back-edge).
- Both shapes: body `# Reconciliation` note on both concepts, `**Reconcile**` log entry.
- Confirm-gate: interactive TTY, `--auto` bypass, config `review:false` bypass, non-TTY refusal without `--auto`.
- Idempotency: no duplicate edges (dedup on `(target,type)`), no duplicate body notes (anchor-gated), "no change" log variant on re-run.
- Additive-only, no status/lifecycle writes, no LLM in write path.

**Capabilities**:
- **NEW**: `reconcile-command` (verb, two write shapes, confirm-gate, body-note, log entry, idempotency contract).
- **MODIFIED**: None. Relation vocabulary is already open; `reconciled_with`/`supersedes` are new string values only.

---

## Delivery Summary

| Field | Value |
|-------|-------|
| **PR** | #112 |
| **Merged Commit** | 185446c (squash) |
| **Branch Commits** | 2: feat a5d70cd + CRITICAL-fix 5ef3b49 |
| **Actual Changed Lines** | 762 (main.py +333/-0, test_reconcile.py +429, new file) |
| **Review Budget** | 800 lines (orchestrator override) |
| **Budget Status** | Medium risk (within 800) |
| **Strategy** | size:exception (single PR for coherent safety unit) |

---

## Verification: PASS

**Verdict**: PASS — 6/6 requirements, 10/10 scenarios, 1483 tests, zero blockers.

### Test Execution
| Gate | Result | Details |
|------|--------|---------|
| **mypy** | ✅ PASS | Success, no issues found in 109 source files |
| **ruff format** | ✅ PASS | 109 files already formatted (1 file reformatted during correction, then clean) |
| **ruff check** | ✅ PASS | All checks passed |
| **pytest (full suite)** | ✅ PASS | 1483 passed (original 1476 + 7 new tests added in correction) |

### Requirement Compliance
All 6 requirements met; all 10 scenarios tested and passing:
1. Workspace Gate and Pair Validation (2 scenarios: unknown id, self-pair)
2. Default Symmetric Reconciliation (1 scenario)
3. Directional Reconciliation via --winner (2 scenarios: winner supersedes, --winner not in pair)
4. Safe-Write Confirm Gate (3 scenarios: decline aborts, --auto bypasses, non-TTY refuses)
5. Idempotent Re-run (1 scenario)
6. Additive-Only, No Status/Lifecycle Write (1 scenario)

---

## 4R Review & Correction

**Original Verdict (pre-correction)**: BLOCKED by CRITICAL data-integrity blocker.

### CRITICAL Finding: Mode-Switch Produces Contradictory State
A 4R review (risk+resilience+reliability lenses converged) found:
- **Bug**: Re-running `reconcile` on the same pair with a DIFFERENT mode (e.g. symmetric → `--winner`, or opposite `--winner`s) produced self-contradictory frontmatter+note state **silently** (exit 0, no error).
- **Root Cause**: `_add_relation_if_absent` dedups on `(target, type)` — when mode changes, the TYPE changes (e.g. `reconciled_with` → `supersedes`), so a NEW edge was added alongside the stale one. Meanwhile, `_reconcile_anchor_present` matched on `target` alone (ignoring `role`), so the note was never updated — frontmatter and note went permanently out of sync, unrecoverable.
- **Impact**: Data corruption (no enforcement of single reconciliation resolution per pair).

### Correction Applied (commit 5ef3b49)
- **C.1 RED**: Added 3 mode-switch tests confirming the bug (all FAILING before fix).
- **C.2 GREEN**: Added `_existing_reconciliation_state()` + `_reconciliation_state_description()` helpers; `reconcile` now classifies the pair's existing state (none / symmetric / directional+winner) BEFORE any new edge computation, and REFUSES with `ValueError` (exit 1, zero writes) if the existing state differs from the requested one. A pair can carry at most one reconciliation resolution written by `reconcile`.
- **C.3 REFACTOR**: Fixed readability WARNING — `_reconcile_sentence`'s `role` is now `Literal["reconciled","supersedes","superseded"]`, raises `ValueError` on unexpected value.
- **C.4 REFACTOR**: Closed coverage-gap WARNING — added 4 missing tests (`test_traversal_id_b_refuses`, `test_traversal_winner_refuses`, `test_reserved_basename_id_a_refuses`, `test_reserved_basename_id_b_refuses`); implementation already correct, only test coverage was missing.
- **C.5 Verify**: All gates re-run green. Net +7 tests (1476 → 1483). Ruff format/check/mypy clean.

**Final Result**: PASS. The refuse-on-conflict gate (which still permits same-state idempotent re-run via the pre-existing no-op path) ensures a pair carries exactly one coherent reconciliation resolution, and that resolution cannot be silently overwritten by a mode-switch re-run.

### Residual Issues
- **WARNING**: 1 residual (relate-edge false-positive in a different subsystem; deferred to follow-up #1619, out of scope for S4).
- **CRITICAL**: 0 remaining.
- **SUGGESTION**: No project-wide coverage tool configured; informational only.

---

## Arc Completion: freshness-lint-v1 is CLOSED

This archive closes the **entire freshness-lint-v1 arc** (MVP-2 deliverable #7). All 4 slices complete:

1. **S1 (volatility-taxonomy)**: Defined volatility class hierarchy, sliding-window age windows, cascade rules.
2. **S2 (type_tiers+suggest-volatility)**: Per-type volatility tier assignment; CLI `suggest-volatility` advisory verb.
3. **S3 (contradictions)**: Read-only `contradictions` query verb; find pairs with conflicting ages/classes.
4. **S4 (reconcile)** ← **THIS CHANGE** — Write verb `reconcile`; record human resolution; additive, idempotent, git-reversible.

**Remaining toward MVP-3**:
- Gap #8 (lifecycle+sensitivity) — fully open, high-impact (depends on reconcile foundation S4 now provides).
- Follow-up #1592 (volatility advisory tuning), #1606 (suggest-volatility interactive refinement), #1619 (relate-edge false-positive, separate subsystem).

---

## Source of Truth: New Canonical Spec

### reconcile-command Specification
- **Location**: `openspec/specs/reconcile-command/spec.md` ✓ CREATED
- **Type**: NEW capability (no prior version; delta spec is canonical verbatim)
- **Content**: 6 requirements, 10 scenarios, full Given/When/Then + RFC 2119.
- **Status**: Source of truth; delta spec merged to canonical location.

### Unchanged: ADR Discipline
- **ADR-0007** (or any ADR) unchanged ✓
- **docs/adr/** verified untouched ✓
- **Verdict**: Per proposal's explicit "no new ADR" verdict (this arc's write is additive/git-reversible, not lossy). No new ADR required.

---

## Archive Contents

All change artifacts copied to `openspec/changes/archive/2026-07-22-freshness-reconcile/`:
- ✓ proposal.md
- ✓ design.md
- ✓ tasks.md (21/21 complete + correction complete)
- ✓ verify-report.md (PASS verdict)
- ✓ specs/reconcile-command/spec.md (canonical merged)
- ✓ archive-report.md (this file)

**Active source folder** (`openspec/changes/freshness-reconcile/`) — scheduled for orchestrator removal after archive validation.

---

## Traceability: Engram Observation IDs

All SDD artifacts persisted to Engram for audit trail and cross-phase reference:

| Artifact | Topic Key | Observation ID | Status |
|----------|-----------|---|--------|
| Proposal | `sdd/freshness-reconcile/proposal` | #1610 | ✓ Retrieved |
| Specification | `sdd/freshness-reconcile/spec` | #1611 | ✓ Retrieved |
| Design | `sdd/freshness-reconcile/design` | #1612 | ✓ Retrieved |
| Tasks | `sdd/freshness-reconcile/tasks` | #1614 | ✓ Retrieved (all [x]) |
| Apply-Progress | `sdd/freshness-reconcile/apply-progress` | #1615 | ✓ Retrieved (CRITICAL fix documented) |
| Verify-Report | `sdd/freshness-reconcile/verify-report` | #1616 | ✓ Retrieved (PASS verdict) |
| Archive-Report | `sdd/freshness-reconcile/archive-report` | (this save) | ✓ Persisting now |

---

## Sign-Off: Cycle Complete

| Gate | Status | Evidence |
|------|--------|----------|
| **Task Completion** | ✅ PASS | All 21 tasks [x]; correction tasks [x] |
| **Verification** | ✅ PASS | 1483 tests, 6/6 req, 10/10 scenarios, CRITICAL fix verified |
| **Spec Merge** | ✅ PASS | reconcile-command canonical at `openspec/specs/reconcile-command/spec.md` |
| **Archive Folder** | ✅ PASS | All artifacts in `openspec/changes/archive/2026-07-22-freshness-reconcile/` |
| **No ADR Changes** | ✅ PASS | `docs/adr/` untouched; proposal verdict honored |
| **Observation IDs** | ✅ PASS | All artifacts #1610-#1616 recorded for traceability |

**SDD cycle for freshness-reconcile closed. Change ready for release as part of MVP-2 deliverable #7 (freshness-lint-v1 arc complete).**

---

## Next Steps

1. **Orchestrator**: Remove `openspec/changes/freshness-reconcile/` source folder (archive complete).
2. **Release notes**: freshness-lint-v1 arc complete; MVP-3 foundation (gap #8) unblocked.
3. **Follow-ups**: #1592, #1606, #1619 tracked independently; arc is closed.
