# Archive Report: cli-version-flag (Closes #181)

**Date Archived**: 2026-07-26
**Status**: ARCHIVED
**Change**: cli-version-flag
**GitHub Issue**: #181 (Closed)
**PR**: #182 (Merged to main as commit `ab914a9`)

## Summary

The `cli-version-flag` change has been fully implemented, verified (`pass_with_warnings`, 8/8 requirements, 13/13 scenarios), reviewed via full 4R bounded review (6 review rounds, zero BLOCKERs), and archived. The change introduces a standalone `openkos --version` flag that reports the installed distribution's version from package metadata, with a leading version banner in `openkos doctor` output. All 26 implementation tasks completed across strict-TDD phases (RED/GREEN), with full test coverage including fallback branch coverage. One WARNING: missing `apply-progress` artifact (process-recording gap, not a code defect).

## Change Scope

**Title**: Add `--version` flag and version banner to `doctor` command output

**Type**: Enhancement (CLI capability)

**Capabilities**:
- **NEW**: `cli-version` — standalone `--version` output contract, exit code, fallback, workspace independence
- **MODIFIED**: `doctor-command` — adds a non-check version banner line preceding the `[PASS]/[FAIL]/[SKIP]` block; check count, order, and exit codes unchanged

## Delta Specs Merged

Two delta specs have been merged into the main specification tree:

| Domain | Type | Key Changes |
|--------|------|-------------|
| `cli-version` | NEW spec (full) | 6 requirements covering `--version` flag behavior, metadata sourcing, workspace independence, fallback, read-only constraint, help-text discoverability |
| `doctor-command` | Delta (ADDED + MODIFIED) | ADDED requirement for version banner; MODIFIED requirement for malformed model config to include carve-out for non-check banner line |

**Main Spec Files Updated**:
- `openspec/specs/cli-version/spec.md` (CREATED)
- `openspec/specs/doctor-command/spec.md` (MERGED)

## Verification Results

**Verdict**: PASS WITH WARNINGS

| Metric | Value |
|--------|-------|
| Requirements Implemented | 8/8 (100%) — cli-version 6/6 + doctor-command added/modified 2/2 |
| Scenarios Passing | 13/13 (100%) — cli-version 7 + doctor-command 6 |
| Test Suite | 2166 passed in 111.03s, exit 0 |
| Branch Coverage | 97% (main.py 96%; all new code at `:108-127`, `:137-143`, `:6222` covered) |
| Quality Gates | All passed — mypy strict (134 files), ruff checks, format |
| Tasks Complete | 26/26 (100%) across phases 1-6 |
| Changed Lines | ~140 (src ~32, tests ~75, docs ~30, ci ~3) |

**Key Compliance**:
- `--version` prints `openkos {version}` and exits 0 outside a workspace
- Version read from `importlib.metadata`, single source of truth, no `__version__` constant
- Fallback to `openkos unknown` on `PackageNotFoundError` (tested by monkeypatching module-local `_pkg_version` alias)
- `doctor` banner precedes all checks, does not renumber or affect exit code
- Malformed model config (`model: yes`) reported as `[FAIL]` with remediation, all other checks still run
- All 10 `doctor` checks emitted (pre-existing doc drift corrected: was 9, now 10 including `Workspace vector index present`)

## Review Findings

**4R Review Results** (6 bounded review rounds on implementation and verify-report):

Zero BLOCKERs in any round. All four lenses produced findings, every one corrected before archive:

- **Risk Lens**: no security, permission, data-loss, or dependency issues — read-only flag, stdlib-only, no subprocess, no untrusted input. Flagged the stale `#181` known-issues row in `docs/testing.md` and the unasserted CI wheel-smoke output.
- **Reliability Lens**: 26/26 tasks delivered; 90% branch gate met with the `PackageNotFoundError` branch force-tested. Found the spec-coverage gap that `dd091c4` closed, and several false counts in the verify report.
- **Resilience Lens**: found that the CI wheel-smoke step asserted only exit status, so the degraded `openkos unknown` path (which also exits 0) could pass green; and that the wheel glob could survive unmatched without `nullglob`. Both fixed.
- **Readability Lens**: found the `doctor` check-count drift (docs said nine, code emits ten) and the self-contradicting `docs/testing.md` known-issues section. Both corrected.

**Review Workflow Impact**:
- Documentation drift discovered and corrected: `docs/cli.md` and `docs/testing.md` listed 9 checks when code emits 10 (`Workspace vector index present` was missing)
- `doctor` banner decision (non-check line) documented in spec via carve-out in MODIFIED requirement
- CI wheel-smoke test enhanced beyond design (filename comparison guards against metadata regression, not just exit-status check)

**Post-Review Quality**: All findings addressed; full suite 2166 tests passing

## Implementation Details

**Changed Files** (from PR #182, merged as `ab914a9`):
- `src/openkos/cli/main.py` (4 edits): alias import, `_version_line()` helper, `_version_callback()` eager handler, `--version` option, banner call
- `tests/unit/test_main.py`: 8 `--version` tests covering happy path, metadata match, workspace independence, combined-subcommand short-circuit, fallback, read-only, help-text discoverability
- `tests/unit/cli/test_doctor.py`: version banner test + regression check for malformed model (extended during verification to assert all 4 applicable checks)
- `.github/workflows/ci.yml`: wheel-smoke step extended to test `openkos --version`
- `docs/cli.md`: Global options section documenting `--version`; check enumeration corrected from 9 to 10
- `docs/testing.md`: build-ID step switched to `openkos --version`; obsolete #181 callout removed
- `CHANGELOG.md`: `[Unreleased]` gains `### Added` entry for cli/--version and banner

**Rollback Boundary**:
- Reverting `ab914a9` removes the whole feature (the squash contains every branch commit); `dd091c4` is an independent test-only follow-up. Purely additive, no breaking changes.
- **Caveat for a reverter:** `ab914a9` also carries the pre-existing doc-drift fix (`docs/cli.md` and `docs/testing.md` corrected from nine `doctor` checks to ten). A plain revert restores docs claiming nine while the code still emits ten and while `openspec/specs/doctor-command/spec.md` — promoted by the archive commit, not by `ab914a9` — asserts ten. Reverting the feature therefore means keeping the doc/spec check-count corrections, or reverting the archive commit too
- No migration required; no existing command signature or exit code changes

**Commits on `main`** (PR #182 was squash-merged, so its branch commits do not
appear in `main`'s history; their content reached `main` inside `ab914a9`):
- `ab914a9` — feat(cli): add --version flag and doctor version banner (squash of PR #182, which contained branch commits `285f03e` and `a6f310c`)
- `dd091c4` — test(doctor): pin the fourth check named by the malformed-model scenario (spec-coverage gap closure)
- `e2094b9` — chore(sdd): record verify report for cli-version-flag (#181)
- `5f81646` — chore(sdd): add verify-result envelope to cli-version-flag report (#181)

## Traceability

**Engram Artifacts** (all complete and verified):
- Proposal (ID 1955): Intent, scope, approach, risks, success criteria
- Spec (ID 1956): cli-version NEW spec + doctor-command DELTA spec, all 13 scenarios
- Design (ID 1957): Technical approach, D1-D5 architecture decisions, placement, file changes
- Tasks (ID 1960): 26 implementation tasks across 6 TDD phases
- Verify-Report (ID 1964): PASS WITH WARNINGS verdict, spec compliance matrix, design conformance, assertion quality
- Archive-Report (this file): Final closure record with merged specs and SDD cycle completion

**GitHub References**:
- Issue: #181 (closed)
- PR: #182 (merged to main)
- Commit: `ab914a9` (merged feature)

## Archive Contents

All original artifacts preserved in `openspec/changes/archive/2026-07-26-cli-version-flag/`:

```
.
├── proposal.md
├── design.md
├── explore.md
├── tasks.md
├── verify-report.md
├── specs/
│   ├── cli-version/
│   │   └── spec.md
│   └── doctor-command/
│       └── spec.md
└── archive-report.md (this file)
```

## SDD Cycle Completion

The `cli-version-flag` change has completed the full SDD lifecycle:

1. ✅ **Propose** (ID 1955): Intent and scope defined, risks identified, approach chosen, success criteria listed
2. ✅ **Spec** (ID 1956): Two specs written with all 13 scenarios pinned
3. ✅ **Design** (ID 1957): Technical decisions (D1-D5) made, placement mandated, test strategy detailed
4. ✅ **Tasks** (ID 1960): 26 implementation tasks scheduled across strict-TDD phases 1-6
5. ✅ **Apply** (merged `ab914a9`): All 26 tasks completed + 2 post-review fixes (CI fix, doc-drift correction)
6. ✅ **Verify** (ID 1964): 8/8 requirements, 13/13 scenarios, PASS WITH WARNINGS verdict
7. ✅ **Archive** (today): Change closed, main specs synced, artifacts archived

The change is ready for release on the main branch.

## Spec Merge Summary

### cli-version (NEW)

**6 Requirements, 7 Scenarios**:
1. Version Flag Prints Exact Output And Exits Zero (1 scenario)
2. Version Read From Installed Distribution Metadata (1 scenario)
3. Eager Evaluation Short-Circuits Before Any Subcommand Or Workspace Resolution (2 scenarios)
4. Unknown Version Fallback On Missing Package Metadata (1 scenario)
5. Version Resolution Is Read-Only (1 scenario)
6. Version Flag Is Discoverable In Help Text (1 scenario)

**Status**: All pinned by passing tests in `tests/unit/test_main.py` and verified in CI wheel-smoke test.

### doctor-command (MODIFIED)

**Changes**:
- **ADDED**: Doctor Prints A Leading Version Banner (2 scenarios)
- **MODIFIED**: Doctor Never Raises On A Malformed Model Config (updated to include carve-out for version banner as a non-check line; 4 scenarios)

The existing requirement previously stated "No new output shape is introduced by this requirement"; the MODIFIED version clarifies that the exception is the leading version banner (not itself a check line).

**Status**: All scenarios pinned by existing + new tests; one scenario gap (vector-extension assertion) closed during verification.

**Additional drift corrections applied to `openspec/specs/doctor-command/spec.md`, NOT authorized by any delta.**

Review found the main spec had drifted from the shipped command and, because the merged banner requirement states that `doctor` emits ten `CheckResult`s, the merge would have frozen a self-contradicting contract. Three untouched requirements were corrected in the same commit. They are recorded here so spec provenance stays auditable — none of them is a behavior change, and each was verified against `src/openkos/cli/main.py`:

| Requirement | Was | Now | Why |
|---|---|---|---|
| `Doctor Runs And Prints All Applicable Checks` | enumerated 7 checks; scenario said "covering all 7 checks" | enumerates all 10 in emission order; scenario says "covering all ten checks" | code appends 10 `CheckResult`s; `tests/unit/cli/test_doctor.py:97` and `:868` both assert `count("[PASS]") == 10` |
| `Exit Code Reflects Critical Failures Only` | listed 4 informational checks | lists all 7 ("The other seven checks are informational") | 3 `critical=True` + 7 `critical=False` = 10; the 7→10 fix made the old arithmetic visibly wrong |
| `Git and Git-Filter-Repo Availability Check` | "printing exactly one `[PASS]`/`[FAIL]` line for this check" | "two independent checks and MUST print one `[PASS]`/`[FAIL]` line each" | `main.py:6189` and `:6205` append two separate `CheckResult`s |

The same drift existed in `docs/cli.md` and `docs/testing.md` (both documented nine checks and omitted `Workspace vector index present`) and was corrected in `ab914a9` as part of the change itself.

**One further divergence, inside a delta-authorized requirement.** The ADDED banner requirement's second scenario was promoted with revised wording rather than verbatim. The delta read "the same number of check lines print as before this change" — a phrase whose referent disappears once the change folder is archived. The promoted spec states the concrete outcome instead: "ten check lines print — the banner adds none". Same contract, pinned by `tests/unit/cli/test_doctor.py:868`. The archived delta retains the original wording, so the two texts differ on purpose and the diff is the audit trail.

## Quality Summary

| Dimension | Status | Evidence |
|-----------|--------|----------|
| Spec Compliance | ✅ PASS | All 8/8 requirements, 13/13 scenarios pinned by passing tests |
| Code Quality | ✅ PASS | mypy strict, ruff, format all pass; 97% branch coverage |
| Test Coverage | ✅ PASS | 2166 tests passing; strict-TDD all phases complete; fallback branch forced-tested |
| Review Quality | ✅ PASS | 6 bounded review rounds, zero BLOCKERs, all findings addressed |
| Design Conformance | ✅ PASS | D1-D5 architecture decisions all match shipped code exactly |
| Documentation | ⚠️ CORRECTED | Pre-existing doc drift (9 vs 10 checks) corrected as part of this change |

## Known Limitations and Non-Goals

- **Stale dist-info**: A bumped `pyproject.toml` without `uv sync` prints an old-but-plausible version number. Documented as a known caveat; detecting staleness is a non-goal (would require comparing against pyproject.toml, breaking the workspace-independence contract).
- **No `--version` for subcommands**: The flag is top-level only, not available on `doctor`, `query`, etc. (intentional per issue scope).
- **No machine-readable format**: Output is human-readable only; `--json` or similar is out of scope.
- **No pre-release detection**: The fallback string (`openkos unknown`) is chosen to avoid ambiguity with real pre-release versions like `0.0.0-dev`.

## Next Steps

No further work required for this change. Deployment to production via normal merge pipeline.

The `--version` flag is now stable and can be documented in user-facing help, scripts, and CI/CD workflows that need to report the installed build version.

---

**Archived by**: SDD Archive Phase (automated)
**Archive Date**: 2026-07-26
**Status**: Complete
