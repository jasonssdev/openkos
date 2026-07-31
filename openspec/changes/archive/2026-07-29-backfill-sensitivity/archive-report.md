# Archive Report: backfill-sensitivity (#231)

**Change**: backfill-sensitivity
**Archived to**: `openspec/changes/archive/2026-07-29-backfill-sensitivity/`
**Archive date**: 2026-07-29
**Branch**: feat/backfill-sensitivity-verb, HEAD 02b913c
**All artifacts included**: proposal.md, design.md, apply-progress.md, tasks.md, verify-report.md, verify-report-superseded.md, explore.md, specs/{lint,sensitivity-backfill,status,sensitivity-config}/spec.md

`verify-report.md` is the terminal verification record and the one this report
cites throughout. `verify-report-superseded.md` is an earlier pass, recovered
later from a work-in-progress stash and added after this report was first
written. It is not authoritative, and it is kept because it carries evidence
this record does not — that evidence has not been exhaustively catalogued, so
do not treat any summary of it as complete. Its own opening note lists the
examples found so far.

## Artifact Observation IDs (for Engram traceability)

- Proposal: #2142
- Spec: #2143
- Design: #2144
- Tasks: #2146
- Verify-report (terminal, `verify-report.md`): #2150
- Verify-report (superseded pass): not persisted to Engram; recovered from a stash after this report was written

## Change Summary

Backfill-sensitivity closes the sensitivity gap left by bundles or descendants created before Source-to-descendant propagation existed (#219). This SDD change delivers:

1. **Extract helper (#235)** — extract the inline descendant-scan block from `set_sensitivity_cmd` into shared per-Source helpers (`resolve_source_raises`, `find_unresolvable_provenance`) in `bundle/provenance.py`
2. **Fix partial-write failure message (#233)** — the Phase B write-failure message now names every path that already landed before the failure
3. **Detection findings** — `lint` and `status` report descendants below their Source and multi-source descendants the backfill cannot cover, as two distinct categories
4. **New verb** — `backfill-sensitivity` performs bundle-wide per-Source sensitivity raises in one preview, one confirmation, one log entry, one commit
5. **ADR-0012** — documents the per-Source sweep strategy and coverage limit for MVP-1

## Spec Changes Applied

### Merged into main specs (openspec/specs/)

**lint/spec.md** — ADDED two requirements:
- Below-Source Sensitivity Scan (5 scenarios)
- Multi-Source Uncovered-Descendant Scan (4 scenarios)

**sensitivity-backfill/spec.md** — NEW full spec (7 requirements, 16 scenarios)

**status/spec.md** — ADDED one requirement:
- Needs-Attention Surfaces Below-Source Sensitivity And Uncovered Multi-Source Descendants (4 scenarios)

**sensitivity-config/spec.md** — MODIFIED one requirement with annotation:
- Raise-Only Propagation to Provenance Descendants
  **(Previously: the partial-write-failure message named none of the paths that already landed.)**
  — added new scenario: Partial write failure names every path that already landed (#233)

## Implementation Summary

- **Total changed lines across all 4 PRs**: ~2000 (code + tests)
- **Test coverage**: 2616 passed / 2 skipped with 97.50% branch coverage (target 90%)
- **All 49/49 tasks complete**: Phases 1-17 across PR1, PR2, PR3a, PR3b marked [x]
- **Linting**: ruff check / ruff format / mypy all clean
- **Build**: uv build produces both sdist and wheel, exit 0

## Verification Status

**Verdict: PASS WITH WARNINGS**

- 0 CRITICAL findings
- 1 WARNING (carried forward from 4-lens review, confirmed genuine, non-blocking):
  `test_phase_b_failure_names_the_landed_paths` asserts exit code/exception/stderr but does not read landed files back off disk to confirm they remain raised. Recommend adding on-disk assertion as low-cost follow-up, mirroring the sibling `set-sensitivity` test.

- 1 SUGGESTION (new, informational):
  `status` spec's "clean bundle adds no new needs-attention entries" scenario has only indirect test coverage via a general healthy-bundle render test, with no dedicated negative test. Low risk; the equivalent lint-side negative test exists and exercises the same underlying code path.

**Spec/Implementation Coherence**: No divergence found. Verified that:
- `resolve_backfill_raises` does NOT call `find_unresolvable_provenance` (per Non-Goals)
- No `type` filter on descendant sets (per spec requirement)
- Confirm-gate precedence matches spec exactly
- One log entry + one autocommit confirmed in code and tests

**All 49 tasks verified real**: Spot-checked each phase's claimed artifact against actual code/tests in `/Users/jasonssdev/Dev/Projects/openkos`. No overclaimed task found.

## Design Annotations Carried Over

When merging delta spec for `sensitivity-config`, the following `(Previously: ...)` annotation was carried verbatim to the main spec:

```
(Previously: the partial-write-failure message named none of the paths that
already landed.)
```

This follows the repository's convention documented in issue #239 and confirmed in `openspec/specs/ingestion/spec.md`.

No other delta specs contained such annotations.

## Open Non-Blocking Follow-Ups

Per the user's initial context, two items are recorded here so they are not lost:

1. **WARNING (reliability lens)** — `tests/unit/cli/test_backfill_sensitivity.py:373` `test_phase_b_failure_names_the_landed_paths` asserts only exit code, exception type, and stderr substrings. It never reads landed descendants' `sensitivity` back off disk, leaving unproven the part of the scenario that says those files remain raised and over-classified after an aborted sweep. The sibling `set-sensitivity` test does assert on-disk state. Low-cost test-hardening follow-up.

2. **SUGGESTION** — `status` delta spec's "clean bundle adds no new needs-attention entries" scenario has only indirect coverage via a general healthy-bundle render test, with no dedicated negative test of the kind `lint` has in `test_lint_clean_bundle_reports_zero_below_source_findings`. Low priority; risk is low because the equivalent lint-side test exercises the same `check_below_source_sensitivity` code path.

## SDD Cycle Complete

The change has been fully planned (proposal), specified (4 delta specs), designed (with 9 required amendments applied), implemented (3 chained PRs), verified (4-lens review + runtime verification), and archived. Ready for next change.

---

**Archive summary**: All artifacts listed at the top of this report are preserved in `/Users/jasonssdev/Dev/Projects/openkos/openspec/changes/archive/2026-07-29-backfill-sensitivity/` and persisted to Engram under topic key `sdd/backfill-sensitivity/archive-report` for traceability.
