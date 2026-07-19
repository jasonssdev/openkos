# Archive Report: align-user-journey-and-init-coverage

**Archived**: 2026-07-19  
**Change**: align-user-journey-and-init-coverage  
**Repository State**: main @ 8ad20c0 (PR #42 squash-merged)  
**Status**: Complete

## Executive Summary

This SDD change successfully closes two independent, non-product-logic gaps in MVP-1:

1. **Item A — TTY coverage test**: Added `test_tty_init_prints_exact_next_step_hint` to `tests/unit/cli/test_init.py`, asserting the exact verbatim next-step hint under a simulated TTY. The test passes immediately (pre-existing behavior; no product code changed).

2. **Item B — documentation reframe**: Coordinated reframe of `docs/user-journey.md` across 11 cited spots (lines 23-30, 69, 74, 75, 77-79, 82-113, 115-117, 119-141, 150-158, 191), correcting 9 categories of overclaims while preserving the MVP-2 roadmap vision under explicit later-MVP fencing.

Both items are now complete, verified, and archived.

## Change Summary

### Two Bundled Items

| Item | Scope | Files | Lines Changed | Status |
|------|-------|-------|----------------|--------|
| A | Init TTY exact-string hint coverage | `tests/unit/cli/test_init.py` | +22 | Complete |
| B | User-journey doc MVP-1 accuracy reframe | `docs/user-journey.md` | +45/-36 | Complete |
| — | Product code (zero change) | `src/openkos/cli/main.py` | 0 | Verified untouched |

**Total scope**: ~67 lines added, ~36 lines removed. Single PR, bundled delivery.

### Overclaims Corrected (Item B)

1. Compiler LLM synthesis claim → Null-compiler (verbatim embed into single `Source` concept)
2. Multi-concept extraction (person/decision pages) → Single `Source` concept only
3. Review panel `[e]dit` option and reclassification panel → Plain yes/no confirm
4. `--sensitivity` CLI flag (lines 69, 75) → `openkos.yaml` `default_sensitivity` only
5. Batch/glob ingest (`./inbox/*.txt`) → Single-source-at-a-time only
6. Extracted concept citations (`concepts/stoicism.md`) → `Source` → `raw/` original
7. Content-hash reconciliation on next command → No automatic reconciliation (MVP-1)
8. Auto-commit on `ingest` or `--auto` → Manual/optional commit (lines 196, 128)
9. Enforced `.txt/.md` allowlist → Any file accepted, text embeds UTF-8, non-text gets binary note

**Fencing approach**: Inline bold labels **In MVP 1:** and **Later MVPs:** inserted at every overclaim location, matching the doc's existing style (lines 146, 180). No content deleted; all richer narrative preserved as vision.

## Verification Results

### Round 1 → Round 2

**Round 1 Findings**:
- **CRITICAL** (caught during verify): `docs/user-journey.md:196` ("Two ways to work" table, "Before saving" row, Unattended column) contradicted Step 4 (line 128) — table claimed `--auto` "commits directly," but Step 4 (correctly) stated commit is manual/optional in both modes.
- **SUGGESTION** (carried): `docs/user-journey.md:198` ("Safety net" row) reviewed and judged non-blocking (no false claim; uses "git history" which is accurate whether commits are made or not).

**Round 2 Fix**:
- Single-line edit to line 196: changed `Saves and commits directly` to `Saves directly to disk (git commit stays manual/optional, same as interactive)`.
- This aligns the table with Step 4 exactly. Both now state: MVP-1 writes to disk without automatic commit in both interactive and `--auto` modes.
- Line 198 re-reviewed; confirmed non-blocking, carried forward as SUGGESTION (no action required).

**Round 2 Verdict**: **PASS**
- All 10/10 spec requirements compliant.
- No residual overclaims in grep sweep.
- Internal consistency verified across all doc sections (line 196 vs line 128 etc.).

### Test Evidence

**Test Execution**:
```
$ uv run pytest --cov
...
Required test coverage of 90.0% reached. Total coverage: 98.90%
============================= 439 passed in 0.88s ==============================
```

- **Tests**: 439 passed / 0 failed / 0 skipped
- **Coverage**: 98.90% (gate: 90%) → PASS, well above threshold
- **Build**: N/A (Python project, no separate build/type-check step)
- **Product code impact**: Zero — `src/openkos/cli/main.py` unchanged (confirmed `git diff --stat -- src/openkos/cli/main.py` is empty)

### Bounded Review

**Review**: review-76ad67e30c84fcce (review-gate.result: allow)
- **Tier**: MEDIUM (doc + test, no product logic)
- **Lens**: review-reliability (1 lens, single-risk dominant selection)
- **Findings**: 0 (no issues raised)
- **Decision**: APPROVED

### Task Completion

**All 21 tasks complete** (all checkmarks `[x]`):
- Phase 0: 2/2 (ground-truth verification)
- Phase 1: 4/4 (TTY test implementation)
- Phase 2: 8/8 (doc reframe spots 1–11)
- Phase 3: 3/3 (verification and grep sweeps)

**No stale checkboxes**: All implementation work is reflected in the persisted tasks artifact.

## Artifact Traceability

All artifacts retrieved from engram persistent memory (project: openkos, scope: project):

| Artifact | Topic Key | Engram ID | Created | Revisions |
|----------|-----------|-----------|---------|-----------|
| Proposal | `sdd/align-user-journey-and-init-coverage/proposal` | #1031 | 2026-07-19 01:39:42 | 1 |
| Spec | `sdd/align-user-journey-and-init-coverage/spec` | #1032 | 2026-07-19 01:41:21 | 1 |
| Design | `sdd/align-user-journey-and-init-coverage/design` | #1033 | 2026-07-19 01:41:55 | 1 |
| Tasks | `sdd/align-user-journey-and-init-coverage/tasks` | #1034 | 2026-07-19 01:43:01 | 2 |
| Verify Report | `sdd/align-user-journey-and-init-coverage/verify-report` | #1036 | 2026-07-19 01:51:40 | 2 |

**No capability spec merges**: This change modifies no capability specs (documentation and test only). No spec sync to `openspec/specs/*` was required.

**Archive persistence**: This archive report saved to engram at `sdd/align-user-journey-and-init-coverage/archive-report` (topic key enables idempotent upserts).

## Follow-up Backlog

Intentional deferral (explicitly scoped out of this change):

1. **UX #4**: FTS→LLM short-circuit visibility in query results — requires new behavior logic and interaction design (later-MVP, roadmap tracked separately).
2. **Install story at release-time**: Package installation / CLI entry-point setup — out of scope, handled at release workflow (later-MVP, orchestrated via packaging/CI).
3. **Automated doc-vs-CLI-help consistency check**: Doc verification tool to catch future drift between user-journey.md and actual CLI output — flagged as possible tooling follow-up, not required for MVP-1.

None of these block the closure of this change.

## Rollback Plan

The change is fully reversible:
- **Item A**: Delete `test_tty_init_prints_exact_next_step_hint` from `tests/unit/cli/test_init.py`.
- **Item B**: Revert `docs/user-journey.md` to the pre-change commit.
- **Product impact**: None (no product code touched).

## Closure

This change has been fully planned, implemented, verified (round 2 PASS with critical issue resolved), bounded-reviewed (APPROVED, 0 findings), and archived. The SDD cycle is complete.

**Next step**: Commit archive folder to main, merge feature PR #42 (already squash-merged), and proceed to the next change.
