# Archive Report: add-status-command

**Change**: add-status-command | **Archived**: 2026-07-17 | **Status**: Complete | **Repository**: openkos (main 4d78301)

This archive report closes the SDD cycle for the `add-status-command` change. The feature implements the first read-only command (`openkos status`) that reports what a bundle contains — source/concept counts from a disk scan, recent activity from the log, and OKF §9 conformance findings — without mutating any bundle file. It establishes the bundle-reader precedent that future `query`/`lint` commands will follow.

## Change Summary

**Purpose**: Deliver the first read-only command promised in `docs/cli.md`: MVP-1 `openkos status` providing workspace diagnostic output (counts, recent activity, conformance findings) and establishing the read-only command pattern for downstream features.

**Scope**:
- `openkos status` command in `cli/main.py` — Phase-A-only (read-only, no confirm, no Phase B)
- Disk-scan counts of sources and concepts via `survey_bundle` in `model/okf.py`
- Recent activity reader `read_recent_entries` in `bundle/log.py` — newest-first, bounded to 5 entries
- OKF §9 conformance findings surfaced under "needs attention" (informational, non-fatal)
- Shared workspace-presence check `require_workspace` extracted from `ingest`'s duplicated logic into `config.py`
- Single-walk bundle survey via shared `_iter_docs` generator — counts + findings in one pass

**Key Decisions**:
- D1: `require_workspace(root) -> str | None` returns refusal reason or None; config layer returns data, CLI maps to exit codes
- D2: Single-walk survey via shared `_iter_docs` generator; `check_conformance` rewritten to consume it (byte-identical behavior); `survey_bundle` consumes the same walk
- D3: Per-file unreadable/malformed = a finding (not a crash); scan continues to completion
- D4: Recent activity = most-recent 5 log entries, newest-first, bounded output
- D5: Recent activity degrades leniently (exit 0) on malformed log; counts/conformance do not; workspace-unreadable is the only Exit-1 path

**Resilience Review & Correction**: A bounded 4R review (HIGH risk, 1377 lines) identified 2 CRITICAL resilience findings, both resolved:
- CRITICAL-1: `require_workspace` docstring falsely claimed `is_file()` swallows `OSError`; `is_file()` re-raises `PermissionError`, causing traceback on permission-denied `bundle/`. Fixed: wrap `is_file()` checks in `try/except OSError`; return distinct `_UNREADABLE_WORKSPACE_REASON_PREFIX` message for permission errors.
- CRITICAL-2: `survey_bundle`'s `rglob` silently swallows directory-scan `OSError`, undercounting and hiding corruption. Fixed: added `_walk_errors` helper to surface one finding per unreadable directory. `check_conformance` remains byte-identical.

Correction: +180 lines (within 200-line budget). Post-correction gate: 201 tests passed, 100% line+branch coverage, ruff+mypy clean.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-17-add-status-command/proposal.md` | Moved from change folder; includes scope, approach, risks, open questions |
| Specification | `archive/2026-07-17-add-status-command/specs/status/spec.md` | Promoted to main spec tree at `openspec/specs/status/spec.md` + moved to archive |
| Design | `archive/2026-07-17-add-status-command/design.md` | Moved from change folder; includes 5 architecture decisions (D1–D5), module map, data flow |
| Tasks | `archive/2026-07-17-add-status-command/tasks.md` | 18/18 checked; all phases complete (7 phases: regression safety net, survey, config gate, log reader, CLI wiring, docs, verification) |
| Verification Report | `archive/2026-07-17-add-status-command/verify-report.md` | PASS WITH WARNINGS (all 18 tasks verified complete, all 9 spec scenarios covered by passing tests, full gate independently re-run: 197 passed, 100% coverage, ruff+mypy clean) |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `status` | Created new capability spec at `openspec/specs/status/spec.md` |
| Requirements at archive time | 5 | Workspace Presence Check, Disk-Scan Source and Concept Counts, Recent Activity from log.md, Needs-Attention via §9 Conformance, Read-Only and Human-Readable Only |
| Total scenarios at archive time | 9 | 1 (workspace check), 3 (disk-scan counts), 2 (recent activity), 2 (conformance), 1 (read-only) — full coverage of all Phase-A paths, degrade behavior, empty states, and mutation verification |
| Source | Delta spec from change folder | `openspec/changes/add-status-command/specs/status/spec.md` promoted to `openspec/specs/status/spec.md` |
| Merge mode | NEW capability | The `status` capability did not exist before; this slice establishes it. |

## Verification Status

**Final Verdict**: PASS WITH WARNINGS (0 CRITICAL blocking, 1 WARNING non-blocking)

**Evidence Summary**:
- All 18/18 tasks checked and verified complete via independent source inspection
- Spec compliance: all 9 scenarios covered by passing tests
- Test execution (final, post-correction): **201 passed**, 0 failed, 0 skipped (original 197 + 4 correction-regression tests for permission handling)
- Coverage: **100% line + 100% branch** (gate ≥90%)
- Quality gates:
  - `uv run ruff check .` pass (exit 0)
  - `uv run ruff format --check .` pass (21 files already formatted)
  - `uv run mypy .` pass (strict mode, 21 source files, no issues)
  - `uv build` succeeded, wheel smoke test passed

**Review Gate & Closure**:
- Bounded 4R review: lineage `review-c500afff9a14003f`, HIGH risk (1377 lines including openspec+tests), all 4 lenses (risk, reliability, readability, resilience)
- Findings: risk=clean, reliability=clean (check_conformance byte-identical verified), readability=3 SUGGESTIONs (accepted as-is), resilience=2 CRITICAL (fixed in correction batch) + 1 SUGGESTION
- Correction applied: config.py `require_workspace` — wrapped `is_file()` in `try/except OSError`; okf.py `survey_bundle` — added `_walk_errors` to surface unreadable directory findings
- Correction budget: 200 lines; used 180 lines
- Terminal state: **APPROVED** (review-c500afff9a14003f, terminal_state: approved)
- Receipt: `.git/gentle-ai/review-transactions/v2/review-c500afff9a14003f/review-receipt.json`

**Non-blocking findings**:
1. WARNING: Design deviation — `design.md` D4/Q4 specifies a trailing `(… and N more)` recent-activity line when entries exceed limit. NOT implemented. Verified as acceptable: no spec scenario or task RED item requires it; design.md's Interfaces section (`list[LogEntry]` return) and Output-layout section are internally inconsistent. Apply correctly followed the interface contract. Recommend follow-up task or design.md update before future commands assume this line exists.

## Implementation Details

**Modules added/modified**:
- `config.py`: `require_workspace(root) -> str | None` (returns None if workspace valid, else refusal reason with permission-error handling via `try/except OSError`)
- `model/okf.py`: `_iter_docs(bundle_dir)` generator, `DocScan` frozen dataclass, `BundleSurvey` frozen dataclass, `survey_bundle(bundle_dir) -> BundleSurvey` (single walk, yields sources/concepts/findings), `_walk_errors(os.walk onerror)` helper for directory-level error surfacing. `check_conformance` rewritten to consume `_iter_docs` (byte-identical outputs, same raise contract).
- `bundle/log.py`: `LogEntry` frozen dataclass (`date`, `text`), `read_recent_entries(log_text, limit) -> list[LogEntry]` (pure parser, newest-first across `## YYYY-MM-DD` sections, no sort)
- `cli/main.py`: `status` command (Phase-A only) — gate via `require_workspace`, survey via `survey_bundle`, activity via `read_recent_entries` with lenient degrade on `(OSError, ValueError)`, render three sections via `typer.echo`, exit 0 on success. `ingest` refactored to call `config.require_workspace` (byte-identical message preserved).
- `docs/cli.md`: Record `status`'s read-only, three-section behavior, non-goals (no `--json`, no lint checks, no non-zero on findings)

**Read-only guarantee**: `status` has zero `fsio.write_*`/`Path.write_*` calls; `test_status_never_writes_to_the_workspace` snapshots full directory tree (bytes of every file) before/after a run with conformance findings, asserts equality.

**No mutation path**: All Phase-A, zero Phase B writes.

## Delivery History

This change was delivered as a **single PR**:
- **PR #15** (merged to main, 2026-07-17, commit 4d78301, after review approval + correction): `openkos status` command + `config.require_workspace` extraction + `okf.survey_bundle` + `log.read_recent_entries` + docs. Underwent bounded 4R review (HIGH risk), correction round (2 CRITICAL resilience findings fixed), verify closure (full gate re-run confirmed post-correction state).

**Repository State**: main @ 4d78301 (commit: "feat(cli): add openkos status — read-only bundle overview" — the PR merge commit)

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree created at `openspec/specs/status/spec.md`
- [x] New `status` capability spec with 5 requirements, 9 scenarios
- [x] Change folder moved to `openspec/changes/archive/2026-07-17-add-status-command/`
- [x] All change artifacts archived (proposal, design, tasks, verify-report, specs)

**Engram**:
- [x] Archive report saved with topic key `sdd/add-status-command/archive-report`
- [x] All artifact observation IDs recorded for traceability:
  - Proposal: #898
  - Specification: #899
  - Design: #900
  - Tasks: #902
  - Apply-progress: #903
  - Verify-report: #904
  - Bounded-review approved: #906

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-17-add-status-command/`
- Main spec tree updated: `openspec/specs/status/spec.md` is the canonical, promoted spec for the `status` capability
- `openkos status` is now available in the CLI and can be used by developers and end users to diagnose workspace contents and conformance
- No follow-up changes required for this slice (read-only diagnostic complete; extraction, filtering, lint checks deferred to future changes)

**Recommended follow-ups** (optional, non-blocking):
1. Design.md reconciliation: mark the `(… and N more)` truncation line explicitly out-of-scope for this slice, or file a task before future commands assume it exists
2. Extraction reader: add `openkos query` command with similar read-only pattern, reusing `_iter_docs` for concept queries
3. Lint command: add `openkos lint` with orphan-page detection, freshness checks, and extended conformance rules

## Traceability

This archive report records the final state of the `add-status-command` change from proposal through implementation, verification, and archival. The change has been:
- Fully specified (5 requirements, 9 scenarios, `status` capability spec)
- Fully designed (5 architecture decisions D1-D5, module map, data flow, test strategy, threat analysis)
- Fully implemented (one PR, 5 source modules + tests + docs, 201 tests, 100% coverage)
- Fully verified (all 18 tasks verified complete, all spec scenarios passing tests, all CRITICAL findings resolved, WARNING non-blocking, 100% line/branch coverage, ruff/mypy/build all green)
- Fully reviewed & approved (bounded 4R review, HIGH risk, 2 CRITICAL findings fixed in correction, terminal state: approved)
- Fully delivered (PR #15 merged to main, branch feat/status-command, review lineage review-c500afff9a14003f)

The SDD cycle is CLOSED. The change is archived and ready for the next change.

**Archive Date**: 2026-07-17 (ISO format)
**Repository Head**: 4d78301 (main)
**Specification**: `openspec/specs/status/spec.md` (canonical, promoted from delta spec)
**Verification Date**: 2026-07-17 (post-correction, post-verify)
**Archival Status**: COMPLETE
**Artifact Observation IDs**: proposal #898, spec #899, design #900, tasks #902, apply-progress #903, verify-report #904, bounded-review-approved #906
