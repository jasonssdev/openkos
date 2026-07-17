# Archive Report: Harden `openkos init` Workspace Creation

**Archived**: 2026-07-17
**Change**: harden-init-workspace
**Artifact Store**: hybrid (openspec + Engram)

## Closure Summary

The `harden-init-workspace` change has been fully planned, implemented, verified, and archived. All 17 tasks are complete. The change shipped to main via GitHub PR #5 (rebase-merged at HEAD 9bc1363). Verification passed with no CRITICAL issues (1 non-blocking WARNING about stale docstring in main.py's docstring, documentation-only, does not block archive). The bounded 4R review was `allow` with 0 blockers. Two delta spec requirements (Refusal Idempotency and OKF Conformance) have been merged into the canonical spec at `openspec/specs/workspace-init/spec.md`.

## Artifact Retrieval

All change artifacts were successfully read from `openspec/changes/harden-init-workspace/`:
- ✅ proposal.md
- ✅ design.md
- ✅ tasks.md (all 17 tasks marked `[x]`)
- ✅ verify-report.md
- ✅ apply-progress.md
- ✅ specs/workspace-init/spec.md (delta spec)

## Task Completion Gate

✅ **PASS** — All 17 implementation tasks in `openspec/changes/harden-init-workspace/tasks.md` are marked `[x]`:
- Phase 1 (Foundation): 3/3 complete
- Phase 2 (Symlink refusal + docstring fix): 3/3 complete
- Phase 3 (I/O-vs-conformance split): 3/3 complete
- Phase 4 (Stray-bundle message): 2/2 complete
- Phase 5 (Adopt write_exclusive): 2/2 complete
- Phase 6 (Verification Gate): 4/4 complete

No stale checkboxes detected. Apply-progress.md confirms all work is complete.

## Verification Gate

✅ **PASS WITH WARNINGS** — verify-report.md shows:
- Verdict: `pass`
- Blockers: 0
- Critical findings: 0
- Requirements: 2/2 (all spec scenarios implemented)
- Scenarios: 8/8 (all covered by passing tests)
- Test exit code: 0, 60/60 tests passed
- Branch coverage: 100.00% (threshold: 90%)
- Build: PASSED
- Linter (ruff check): No errors
- Formatter (ruff format): Clean
- Type checker (mypy strict): No issues

**WARNING**: `src/openkos/cli/main.py:24` has stale docstring enumeration (counts 5 conditions but only names 4, missing new symlink condition from #3). This is documentation drift only, not behavioral gap. Recommend follow-up doc touch-up; does not block archive.

## Spec Merge

✅ **MERGE COMPLETE** — Delta spec merged into canonical spec:

**File**: `openspec/specs/workspace-init/spec.md`

**Modified requirements**:

1. **Requirement: Refusal Idempotency**
   - Added: symlink refusal condition (raw/bundle as pre-existing symlink)
   - Added: two new scenarios:
     - "Symlinked raw or bundle target is refused" (6 parametrized test cases)
     - "Stray bundle/ retry names the likely crashed-init cause" (#8 message enhancement)
   - Enhanced: non-empty `bundle/` message now identifies likely crashed-init cause + remediation

2. **Requirement: OKF Conformance**
   - Added: I/O error distinction from conformance violation
   - Added: new scenario "Unreadable file is reported as an I/O error, not a conformance violation"
   - Clarified: read/decode failures propagate as inspection failures, not conformance violations

All other requirements (Workspace Creation, Bundle Index Shape, Bundle Log Shape, Static openkos.yaml Template, Static AGENTS.md Template, No Concept-Type Folders, Write Failure Handling, Adoption of Non-Workspace Directories, Default raw/ Permissions) remain unchanged in canonical spec.

## Archive Contents

Changed artifacts moved to `openspec/changes/archive/2026-07-17-harden-init-workspace/`:
- ✅ proposal.md
- ✅ design.md
- ✅ tasks.md (17/17 tasks complete)
- ✅ verify-report.md (PASS WITH WARNINGS)
- ✅ apply-progress.md
- ✅ specs/workspace-init/spec.md (delta, merged into canonical)
- ✅ archive-report.md (this file)

## Review & Release Information

- **Main Branch**: 9bc1363 (clean, current HEAD)
- **GitHub PR**: #5 (rebase-merged)
- **Review Result**: allow (0 blockers)
- **Review Findings**: 4 non-blocking readability findings logged as follow-ups (no CRITICAL, no behavioral impact)
- **Delivery Strategy**: single PR (400-line budget: Low risk, ~242 actual changed lines)

## Canonical Spec Update

The source of truth for workspace-init behavior is now:
- **File**: `openspec/specs/workspace-init/spec.md`
- **Changes**: +2 modified requirements, +3 new scenarios, 0 removed requirements, 11 unchanged requirements
- **Compatibility**: All existing scenarios preserved; new scenarios add coverage for symlink refusal and I/O-error distinction

## Engram Persistence

Archive artifacts have been persisted to Engram topic `sdd/harden-init-workspace/archive-report` for traceability and future reference.

## Final State

- **Status**: Complete and Archived
- **SDD Cycle**: Closed
- **Ready for**: Next change or production release
- **Next Steps**: None (change is fully delivered and archived)

The `harden-init-workspace` SDD change is now complete. No further work is required for this change. The workspace-init capability has been hardened with symlink escape prevention, I/O error distinction, and improved user messaging for crash recovery.
