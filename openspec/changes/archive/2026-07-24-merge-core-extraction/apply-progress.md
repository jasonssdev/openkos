# Apply Progress: merge-core Extraction (Slice 2b-i, #137)

**Mode**: Strict TDD
**Status**: 15/15 tasks complete. Ready for verify.

## Completed Tasks

### Phase 1: New Direct Tests (RED)
- [x] 1.1 Created `tests/unit/cli/test_merge_core.py` with `test_prepare_merge_returns_prepared_merge_with_expected_plan_and_preview_data`
- [x] 1.2 Added `test_prepare_merge_raises_oserror_on_missing_absorbed_file`
- [x] 1.3 Added `test_merge_core_writes_index_log_touched_files_survivor_last_and_ledger`
- [x] 1.4 Added `test_merge_core_makes_zero_vcs_side_effect_and_is_unmerge_reversible`
- [x] 1.5 Ran new tests — confirmed RED (`ImportError: cannot import name 'MergeResult' from 'openkos.cli.main'`)

### Phase 2: Extraction (GREEN)
- [x] 2.1 Defined `PreparedMerge` (frozen dataclass) in `src/openkos/cli/main.py`, plus `MergeResult`
- [x] 2.2 Extracted `prepare_merge(...)` from former `main.py:2453-2519` — verbatim logic move, non-interactive, raises `OSError`/`ValueError`
- [x] 2.3 Extracted `merge_core(bundle_dir, index_path, log_path, prepared) -> MergeResult` from former `main.py:2559-2596` — ordered writes, no autocommit, no logic change
- [x] 2.4 Refactored `merge` command body to call `prepare_merge`/`merge_core`; preview echoes now read from `PreparedMerge`; confirm gate, success echo, `_autocommit` call kept verbatim; `preparing`/`writing` error wording pinned in the command
- [x] 2.5 Confirmed `_apply_link_rewrite_idempotently` remains importable from `cli.main` unchanged (still imported by `test_merge.py`)
- [x] 2.6 Ran Phase 1 tests — GREEN after adding `survivor_canonical`/`absorbed_canonical` fields to `PreparedMerge` (the design's ledger-entry-only approach did not expose a `survivor_id`; see Deviations)

### Phase 3: Behavior-Preservation Gate
- [x] 3.1 `uv run pytest tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py -q` → **28 passed**, ZERO edits to either file
- [x] 3.2 Full suite: `uv run pytest -q` → **2007 passed**

### Phase 4: Quality Gate
- [x] 4.1 `ruff check .` → All checks passed
- [x] 4.2 `ruff format --check .` → 133 files already formatted
- [x] 4.3 `mypy .` → Success: no issues found in 133 source files

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1-1.5 | `tests/unit/cli/test_merge_core.py` | Unit | ✅ 28/28 (`test_merge.py` + `test_merge_roundtrip.py` pre-existing) | ✅ Written (ImportError confirmed) | ✅ Passed (5/5) | ✅ Missing-file OSError case + already-merged ValueError case + full write/ledger case | ✅ Clean — logic moved verbatim, only `survivor_canonical`/`absorbed_canonical` added to `PreparedMerge` for correctness |

### Test Summary
- **Total tests written**: 5 (`test_merge_core.py`)
- **Total tests passing**: 5/5 new + 28/28 pre-existing regression + 2007/2007 full suite
- **Layers used**: Unit (5)
- **Approval tests** (refactoring): N/A — `test_merge.py`/`test_merge_roundtrip.py` themselves served as the approval-test safety net (unchanged, all passing before and after)
- **Pure functions created**: 0 new pure functions — `prepare_merge`/`merge_core` are I/O-performing orchestration extractions by design (not pure), matching design's explicit "core functions raise, no I/O purity claimed" contract

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/cli/main.py` | Modified | Added `PreparedMerge`/`MergeResult` dataclasses, `prepare_merge`/`merge_core` module-level functions; refactored `merge` command to orchestrate them (241 insertions, 113 deletions) |
| `tests/unit/cli/test_merge_core.py` | Created | 5 direct tests for `prepare_merge`/`merge_core` (332 lines) |
| `openspec/changes/merge-core-extraction/tasks.md` | Modified | All 15 tasks marked `[x]` |

## Deviations from Design

- Design's `PreparedMerge` field list (Interfaces/Contracts) did not include `survivor_canonical`/`absorbed_canonical` explicitly, relying on `plan.ledger_entry` for those ids. `MergeLedgerEntry` only exposes `absorbed_id` (no `survivor_id` field), so `merge_core` could not recover the survivor's canonical id from the ledger entry alone. Added `survivor_canonical`/`absorbed_canonical` as two extra frozen fields on `PreparedMerge` to carry them through — same information the command already had, no new I/O, no behavior change. All other fields and both function signatures match the design exactly.

## Issues Found

None — the extraction was a pure logic move; the only wrinkle was the ledger-entry field gap above, resolved without touching `bundle/merge.py`'s pure planning module.

## Workload / PR Boundary

- Mode: single PR (per tasks.md forecast; orchestrator resolved delivery=auto-forecast, within the pre-agreed 800-line review budget for this atomic extraction)
- Current work unit: Unit 1 (the only unit — extraction cannot be split without an uncompilable intermediate state)
- Boundary: `src/openkos/cli/main.py` PreparedMerge/prepare_merge/merge_core + merge command refactor, and new `tests/unit/cli/test_merge_core.py`
- Estimated review budget impact: ~354 changed lines in `main.py` + 332 new test lines = ~686 total, above the standard 400-line default but within the 800-line exception band already accepted for this atomic, unsplittable refactor

## Status

15/15 tasks complete. Ready for verify.
