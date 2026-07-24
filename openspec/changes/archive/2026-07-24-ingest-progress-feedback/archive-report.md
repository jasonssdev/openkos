# Archive Report: Ingest Progress Feedback (Spinner + Per-Type Tally) — #136

**Change**: ingest-progress-feedback  
**Issue**: #136  
**PR**: #157  
**Commit**: 73b1e09 (HEAD of chore/archive-purge-transactional-cleanup)  
**Archive Date**: 2026-07-24

## Summary

Spinner (stderr-only, rich Console activity indicator) + per-type derived-object tally line in `openkos ingest` command output. Strictly additive, non-breaking, 0 exit-code changes. Verification verdict: **PASS** (1973 tests, 0 CRITICAL/WARNING/SUGGESTION).

## What Shipped

### Deliverables

| Component | Description | Status |
|-----------|-------------|--------|
| **Spinner** | Live activity indicator during blocking `extract_concept` LLM call; stderr-only, non-TTY silent | ✅ Implemented |
| **Type Tally** | Summary line after ingest with per-type derived-object counts in canonical registry order | ✅ Implemented |
| **Helper** | Reusable `_format_type_tally(dict[str, int]) -> str` for decoupled formatting logic | ✅ Implemented |

### Type Tally Format

Helper function and caller emit: `extracted {N} objects — {count} {Type}[, {count} {Type}...]`

**Example outputs:**
- Zero derived objects: (no line emitted)
- Single concept: `extracted 1 object — 1 Concept`
- Mixed types: `extracted 3 objects — 1 Concept, 1 Event, 1 Person` (canonical registry order)

### Type Tally Manifest — Per-Requirement Breakdown

A helper function `_format_type_tally(counts: dict[str, int]) -> str` was introduced to satisfy the reusable formatting contract. The function implementation:
- Returns empty string `""` for zero or empty input (no line to print)
- Computes total count and pluralizes "object"/"objects" via existing `_plural` helper
- Orders types by canonical `_TYPE_TO_SECTION` insertion order (registry order: Concept, Entity, Place, Event, Procedure, Decision, Project, Person, Organization)
- Renders comma-separated `{count} {Type}` pairs in that order
- Signature: `def _format_type_tally(counts: dict[str, int]) -> str`
- Location: `src/openkos/cli/main.py` (near `_plural` helper, line ~348-351)

## Scope

### In Scope (✅ Completed)

- Spinner wrap inside `_stage_derived_objects` around the blocking `extract_concept` call (main.py:493-501)
- Spinner construction per-call (not module-level) to bind correct stream under `CliRunner`
- Spinner on STDERR only; automatic silence on non-TTY via rich's built-in detection
- Spinner clears cleanly on both success and `OllamaError` via context-manager `__exit__`
- Tally echo inside `ingest()` after existing summary (main.py:925)
- Tally uses `Counter(p.doc_type for p in derived_plans)` with `_format_type_tally` caller
- Tests: 10 new test cases (4 unit, 3 CLI tally, 3 CLI spinner via spy seam)
- Quality gates: full pytest suite, ruff check/format, mypy — all green

### Out of Scope (✅ Confirmed No Touch)

- **#133 Typed Status Summary**: Do NOT modify `status/_bundle_content_lines`, `BundleSurvey.by_type`, or related status machinery. Confirmed via:
  - Grepped `git diff` hunks for "status", "_bundle_content_lines", "by_type" — zero matches
  - `check_conformance` scope NOT expanded (remains rules 1-3 unchanged)
  - No modification to `model/types.py` beyond importing existing constants
- **Progress bars/ETA**: Not in scope
- **Config/pyproject changes**: Not in scope (rich already transitive via typer==0.27.0 per uv.lock:710)

## Verification Summary

### Test Evidence

- **Full suite**: `uv run pytest -q` → **1973 passed** (97.97s)
  - `tests/unit/cli/test_ingest.py` → **86 passed**
  - New tests (tally + spinner): **10 passed** individually
- **Lint & Format**:
  - `uv run ruff check .` → All checks passed
  - `uv run ruff format --check .` → 132 files already formatted
  - `uv run mypy` → Success, no issues in 131 files

### Specification Coverage

All 9 spec scenarios (delta + scenarios) map 1:1 to passing tests:

**Per-Type Tally (4 scenarios + helper):**
- ✅ Zero derived objects — no tally line
- ✅ Single object, singular wording
- ✅ Multiple objects, one type
- ✅ Multiple objects, mixed types in canonical order
- ✅ Empty dict yields empty string
- ✅ Single-entry dict yields singular line
- ✅ Multi-entry dict ordered by canonical registry, not insertion order

**Spinner (3 scenarios):**
- ✅ Spinner is stderr-only and stdout stays clean
- ✅ Spinner clears on extraction success
- ✅ Spinner clears on OllamaError

### Verification Report

**Verdict**: ✅ **PASS**  
**Critical Issues**: 0  
**Warnings**: 0  
**Suggestions**: 0  

Independent verification run:
- Spec-to-test mapping audited independently (not self-reported by apply)
- Assertion quality audit: no tautologies, no ghost loops
- Ordering test confirms real behavior (Person/Concept/Event write order vs. canonical Concept/Event/Person output)
- Console spy seam verified per design's resolved open question (presence via mock, not raw glyph capture)

## File Changes Summary

| File | Change | Lines |
|------|--------|-------|
| `src/openkos/cli/main.py` | +2 imports (Counter, Console); +_format_type_tally helper (+9); +tally echo (+2); +spinner wrap (+1) | +29/-1 |
| `tests/unit/cli/test_ingest.py` | +10 test cases (4 unit _format_type_tally, 3 CLI tally scenarios, 3 CLI spinner spy seam) | +192 |
| `pyproject.toml` | none | — |
| `uv.lock` | none | — |

## Spec Merge

**Main Spec Updated**: ✅ `openspec/specs/ingestion/spec.md`

**New Requirements Added** (3 total):
1. Per-Type Derived-Object Tally Summary (with 4 scenarios)
2. Blocking-Extraction Activity Indicator (with 3 scenarios)
3. Reusable Type-Tally Formatting Helper (with 3 scenarios)

**Merge Strategy**: Additive. Delta spec contained only ADDED requirements; all three inserted before the OKF §9 section to keep ingest-specific requirements grouped.

## Artifact Lineage

| Artifact | Observation ID | Phase | Status |
|----------|----------------|-------|--------|
| Proposal | #1839 | sdd-propose | ✅ Complete |
| Delta Spec | #1840 | sdd-spec | ✅ Complete |
| Design | #1841 | sdd-design | ✅ Complete |
| Tasks | #1842 | sdd-tasks | ✅ Complete (7/7 phases) |
| Verify Report | #1845 | sdd-verify | ✅ PASS (0 critical) |
| **Archive Report** | (this document) | sdd-archive | ✅ Complete |

## Rollback Boundary

Revert single commit 73b1e09. Both signals (spinner and tally) are strictly additive:
- No schema or data model changes
- No exit code changes
- No configuration changes
- Clean revert with no side effects

## Checklist

- [x] Task completion gate: All 7 phases (28 tasks) marked complete ✅
- [x] Verify verdict: PASS (0 CRITICAL) ✅
- [x] Spec merge: Additive, 3 new requirements inserted ✅
- [x] Main spec updated: `openspec/specs/ingestion/spec.md` ✅
- [x] No #133 scope touched: Confirmed via diff audit ✅
- [x] Quality gates green: pytest (1973), ruff, mypy ✅
- [x] Test coverage: 10 new tests, all pass ✅

## SDD Cycle Complete

**Status**: ✅ CLOSED  
This change has been fully planned (proposal), specified (delta spec merged to main), designed, implemented, verified (PASS verdict), and archived. Ready for the next change.
