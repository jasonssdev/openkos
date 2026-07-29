# Archive Report: Re-ingest Must Not Lower a Source's Sensitivity

## Change Overview

**Change name**: inherit-sensitivity-on-reingest
**Issue**: #229 — Re-ingest is a silent declassification path
**Resolution**: PR #237 (merged as commit `7feff1d` on 2026-07-28)
**Status**: CLOSED — archived 2026-07-28

## Final State Summary

The change shipped as a single squash-merged PR targeting `main`. All verification gates passed with warnings only. The scope of the bug was larger than the issue description indicated, and both rounds of canonical 4R review found and corrected real defects before final merge.

### What Shipped

On the idempotent re-ingest path, the Source's sensitivity is now resolved as `okf.combine_sensitivity(on_disk_value, cfg.default_sensitivity)` **before** `okf.build_source_concept` is called and passed through the existing `sensitivity=` argument, so one raise-only value flows into both the written document bytes and the readback that feeds `stamp_sensitivity`. Re-ingest can raise or preserve a classification, never lower it.

### The Bug Was Larger Than Filed

Issue #229 described derived objects being stamped with the config default. The undescribed half: re-ingest overwrote the on-disk Source concept itself, silently downgrading a level a human had raised with `set-sensitivity`, with no `--allow-downgrade` and no prompt, routing around the gate ADR-0008 enforces — and it did so whether or not any derived object was extracted.

### Design Edges

Two edges the design pinned:

1. **Post-`forget` path** (`regenerate` true, concept absent): Takes the config default directly, because feeding `None` into the combine would floor at `private` and silently raise a `public` workspace.

2. **Unreadable frontmatter**: ABORTS rather than degrading to the config default, since degrading would write a lower level over an unreadable classification.

## Implementation Quality

### Test Coverage

- **Initial state**: Zero tests combined `regenerate=True` with a raised on-disk value (Exploration Finding 6).
- **Final state**: 17 new unit tests in strict TDD, 13/13 scenarios pass, 97.52% branch coverage against a 90% gate.
- **Safety net**: 105-115 baseline tests confirmed at each phase gate.

### Review Quality: Two Rounds of Canonical 4R

**Round 1 findings** (per verify-report ID #2090, section "Issues Found / WARNING"):
- Test `test_reingest_with_missing_on_disk_sensitivity_resolves_to_private` could not discriminate fixed from unfixed (both landed on `private` because config default was `private`)
- Test `test_reingest_with_blank_on_disk_sensitivity_resolves_to_private` did not exist
- Spec Scenario 6 ("Malformed...") grouped missing/blank as resolving to `confidential`, contradicting the actual implementation and the design's own correction

**Round 1 correction** (commit `cf36e57`):
- Split Scenario 6 into three separate scenarios (missing → private, blank → private, unrecognized/non-string → confidential)
- Strengthened first test with `default_sensitivity: public` to make implementations diverge
- Added second test for the blank-string case using the same technique
- Added both scenarios to the delta and canonical spec files
- Confirmed all three refined scenarios in the revised tests

**Round 2 findings** (this archive round, current review):
- Found that the initial round-1 correction assertion `test_reingest_raises_when_workspace_default_exceeds_on_disk` could pass by simply writing `cfg.default_sensitivity` unconditionally (not actually combining)
- Discovered untested non-string disjunct in the specification

**Round 2 correction** (this round):
- Strengthened `test_reingest_raises_when_workspace_default_exceeds_on_disk` with a final re-ingest step to force the combine to occur
- Added `test_reingest_with_non_string_on_disk_sensitivity_fails_closed_to_confidential` to test the non-string disjunct
- Verified all tests discriminate the fixed implementation from the unfixed one via differential and mutation experiments

### Verification Evidence

**Per verify-report (#2090)**: All 13 scenarios fully compliant, 0 partial, 0 CRITICAL issues.

**Quality gates**:
- Linter: ✅ (`uv run ruff check .`)
- Formatter: ✅ (`uv run ruff format --check .`)
- Type checker: ✅ (`uv run mypy .`)
- Tests: ✅ 2414 passed (final state on merged commit)
- Coverage: ✅ 97.52% total, 96% in changed file

**Independent validation** (7 differential/mutation experiments):
1. RED tests on unmodified `main` using this branch's test file — 9/32 reingest tests fail as expected
2. Derived-object stamping reaches real path (not coincidence) via inverted assertion values
3. `workspace_floor` pin is load-bearing via mutation reversal
4. Post-forget path does not escalate public→private via forced trap reproduction
5. Unparseable frontmatter aborts via code-path analysis and test assertion
6. Existing derived objects untouched via create-only reconciliation audit
7. Fresh ingest byte-identical via pre-existing test suite

### Artifact State

**Specs**: Delta spec merged into canonical `openspec/specs/ingestion/spec.md` **on the merged commit** (verified byte-identical). No manual re-application needed.

**ADR-0010**:
- Status flipped from Proposed → Accepted (per archive convention)
- Index row in `docs/adr/README.md` updated
- Does not supersede ADR-0003, ADR-0008, ADR-0009 (those remain unedited)

**Tasks**: All 32 tasks marked complete (verified at archive time).

## Delivery

**Delivery strategy**: auto-chain (single PR, no chaining)
**Changed lines**: ~370-490 authored (prod 45-60, tests 230-300, docs 60-80, spec 30-50)
**Review budget**: 800 lines (Medium risk against 400-line default)
**PR#**: 237
**Commit**: 7feff1d (squash-merged to main)
**Date**: 2026-07-28

### Build & Test Results (Final)

```
uv run pytest -q
2414 passed in ~88s (exit 0)

uv run ruff check .
All checks passed! (exit 0)

uv run ruff format --check .
143 files already formatted (exit 0)

uv run mypy .
Success: no issues found in 143 source files (exit 0)

Branch coverage: 97.52% (threshold 90%) ✅
```

## Open Follow-ups (Intentional Deferral)

These are deliberately out-of-scope for this change, **not** gaps:

1. **Abort on unreadable/unparseable Source frontmatter names no remediation step for an operator holding a corrupted bundle** — tracked separately; end-user guidance needed for recovery procedure.

2. **`forget` and `set-sensitivity` parse frontmatter inside handlers that do not cover the YAML error type** — pre-existing; same corrupted file this change cleanly refuses would raise there instead. Out of scope: this change makes reaching it more likely but does not fix the handler.

3. **Broad exception catch in the new read helper flattens distinct failure causes into one message** — defensible for a security field; trade-off between specificity and attack surface documented in design.

4. **Tracked separately in issue log**:
   - #230: merge-orphaned provenance
   - #231: bulk backfill of existing bundles
   - #232: bundle-wide dangling-provenance scan scope
   - #233, #234, #235, #236: other improvements

## Traceability: SDD Artifacts

All artifacts persisted to both openspec and Engram (hybrid mode):

| Artifact | Engram ID | Location |
|----------|-----------|----------|
| Proposal | #2085 | `sdd/inherit-sensitivity-on-reingest/proposal` |
| Spec (delta) | #2086 | `sdd/inherit-sensitivity-on-reingest/spec` |
| Design | #2087 | `sdd/inherit-sensitivity-on-reingest/design` |
| Tasks | #2088 | `sdd/inherit-sensitivity-on-reingest/tasks` |
| Verify Report | #2090 | `sdd/inherit-sensitivity-on-reingest/verify-report` |
| Archive Report | (this document) | `sdd/inherit-sensitivity-on-reingest/archive-report` |

Filesystem archive: `openspec/changes/archive/2026-07-28-inherit-sensitivity-on-reingest/`

## Archive Checklist

- [x] Task Completion Gate: all 32 implementation tasks marked done
- [x] Spec Sync: delta merged into canonical `openspec/specs/ingestion/spec.md`
- [x] ADR-0010: Status flipped Proposed → Accepted
- [x] ADR README: Index updated
- [x] Change folder moved to archive (filesystem): `openspec/changes/archive/2026-07-28-inherit-sensitivity-on-reingest/`
- [x] Archive report written (this document)
- [x] Engram topics persisted (all 6 artifacts)

## Why This Change Matters

Re-ingest is a bulk mechanical verb intended to refresh a Source on the next ingest of the same input document. It was a silent declassification path — a human's deliberate `set-sensitivity` correction could be reversed without any prompt or flag, because re-ingest never read the disk. The high-water-mark resolution means re-ingest is now idempotent and safe to run repeatedly: it can raise a Source, but never lower it. The only downgrade path remains the explicit `set-sensitivity --allow-downgrade` verb, per ADR-0008.

The specification, implementation, and testing all carry this invariant forward: `workspace_floor` stays the config default, affecting only how re-ingest resolves the Source's own field, not the extraction gate's LLM-send decision. Two review rounds caught subtle test and spec accuracy gaps that an initial round would have missed.

---

**Archived**: 2026-07-28 by sdd-archive executor
**Report Status**: PASS WITH WARNINGS (2 non-blocking warnings from verify round, both documented as resolved)
