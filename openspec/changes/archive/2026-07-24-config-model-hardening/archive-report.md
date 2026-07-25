# Archive Report: Config Model Hardening (Issue #128, Slice A)

## Status
**ARCHIVED** — 2026-07-24

## Change Summary

**Change**: `config-model-hardening` (Slice A of GitHub issue #128, openkos project)

**Description**: Three independently confirmed defects allowed YAML 1.1 boolean/null literals (e.g. `model: yes` → Python `True`) to flow through the config layer as if they were valid model strings, culminating in a `doctor` crash. This slice hardens the config read/validate contract through two complementary source-layer fixes in `config.py`:
1. Enforce `isinstance(model, str)` in `read_config` (defect #1)
2. Reject YAML 1.1 reserved boolean/null words in `validate_model` (defect #2)
3. Doctor never raises on non-str model; reports `[FAIL]` with remediation (defect #3, subsumed by #1's guard)

**Issue**: GitHub #128 (Slice A — interactive model picker is Slice B, not included in this archive)

**PR**: #169 (merged to main 2026-07-24)

**Note**: This change ADVANCES but does NOT CLOSE issue #128. The interactive model picker (Slice B, `init-model-picker`) builds on this hardened layer and remains active in `openspec/changes/init-model-picker/`.

## Artifacts Archived

### SDD Observation IDs (Engram)
- **#1899**: Proposal (sdd/config-model-hardening/proposal)
- **#1900**: Spec (sdd/config-model-hardening/spec)
- **#1901**: Design (sdd/config-model-hardening/design)
- **#1902**: Tasks (sdd/config-model-hardening/tasks)
- **#1903**: Apply Progress (sdd/config-model-hardening/apply-progress)
- **#1904**: Verify Report (sdd/config-model-hardening/verify-report)

### Filesystem Artifacts (Archived to openspec/changes/archive/2026-07-24-config-model-hardening/)
- `proposal.md` ✅
- `design.md` ✅
- `tasks.md` ✅
- `verify-report.md` ✅
- `specs/workspace-init/spec.md` (delta spec) ✅
- `specs/doctor-command/spec.md` (delta spec) ✅
- `archive-report.md` (this file) ✅

## Specs Merged into Main Specs Tree

### Workspace Init Specification
- **File**: `openspec/specs/workspace-init/spec.md`
- **Action**: MODIFIED + ADDED
- **Changes**:
  - ADDED: New requirement "Config Model Field Type Enforcement" with 4 scenarios
  - MODIFIED: Existing requirement "Static openkos.yaml Template" to include YAML 1.1 reserved-word rejection requirements
  - ADDED: New scenarios for reserved-word rejection (3 new scenarios added to existing requirement)
- **Details**: 
  - Added `validate_model` YAML reserved-word rejection (exact-token, case-insensitive)
  - Added `read_config` type enforcement for `model` field
  - All prior scenarios preserved unchanged

### Doctor Command Specification
- **File**: `openspec/specs/doctor-command/spec.md`
- **Action**: ADDED
- **Changes**:
  - ADDED: New requirement "Doctor Never Raises On A Malformed Model Config" with 4 scenarios
  - Inserted between "Workspace Vector Index Presence Check" and "Git and Git-Filter-Repo Availability Check"
- **Details**:
  - Doctor must not raise on non-str model values
  - Must report `[FAIL]` with actionable remediation
  - Other applicable checks must continue to run

## Task Completion Status

**All 15 tasks complete** ✅

| Phase | Status | Details |
|-------|--------|---------|
| 1 (RED) | ✅ Complete | Parametrized reserved-word rejection tests; substring/tag acceptance tests |
| 2 (GREEN) | ✅ Complete | `validate_model` reserved-word frozenset guard implemented |
| 3 (RED) | ✅ Complete | Parametrized str-type enforcement tests for `model` and `embedding_model` |
| 4 (GREEN) | ✅ Complete | `read_config` isinstance(str) guards implemented |
| 5 (RED) | ✅ Complete | Doctor regression tests (no production change to main.py/ollama.py) |
| 6 (Quality) | ✅ Complete | All pytest/ruff/mypy checks passing |

**Evidence**: All 15 tasks verified against apply-progress and verify-report artifacts. Test suite: 2089 passed, 47 focused tests passed, all quality gates green.

## Verification Status

**Verdict**: **PASS** ✅

- **Requirements**: 4/4 satisfied
- **Scenarios**: 15/15 passing
- **Critical Findings**: 0
- **Warnings**: 0
- **Test Suite**: 2089 passed in 102.73s (exit 0)
- **Code Quality**: ruff/mypy all clean
- **Diff Scope**: 3 files only (config.py, test_config.py, test_doctor.py)

See `verify-report.md` for complete verification matrix.

## Review Status

**Review Gate**: ✅ Passed (receipt from apply phase)

- **PR #169**: Merged to main
- **Findings**: 0 critical, 0 blocking
- **Approval**: Clean review

## Workflow Gates

### Task Completion Gate
✅ **PASS** — All 15 implementation tasks marked complete in `tasks.md` artifact.

### Native Review Receipt Gate
✅ **PASS** — Change merged to main via PR #169 with clean review.

## Archive Integrity

- Change folder moved from `openspec/changes/config-model-hardening/` to `openspec/changes/archive/2026-07-24-config-model-hardening/`
- All 6 original artifacts preserved in archive
- All delta specs preserved in archive under `specs/` subfolder
- Main specs merged (source of truth updated)
- Archive folder created with date prefix in ISO 8601 format

## Notes

- **Future Work**: Slice B (`init-model-picker`, interactive model picker) remains in `openspec/changes/init-model-picker/` and depends on this slice's hardened layer.
- **Rollback**: Single git revert; no schema migrations or persisted-format changes.
- **No Breaking Changes**: All field fallback patterns preserved; existing tests pass.

## Archived at

**Date**: 2026-07-24  
**Archive Root**: `openspec/changes/archive/2026-07-24-config-model-hardening/`  
**Engram Topic**: `sdd/config-model-hardening/archive-report`

---

*SDD cycle complete. The change is now in the permanent audit trail. Do NOT touch archived changes; they are immutable.*
