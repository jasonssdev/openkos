# Apply Progress: per-type-default-sensitivity (#669)

## Slice 1 — WU1 + WU2 (this batch)

Branch: `feat/669-s1-raise-by-config`. Stacked-to-main chain, slice 1 of the
5-WU plan in `tasks.md`.

### Completed

- **WU1** — `okf.raise_by(level, offset) -> str` in `src/openkos/model/okf.py`,
  beside `SENSITIVITY_ORDER`/`combine_sensitivity`. Reuses `_rank`'s
  fail-closed ranking; clamps at `confidential`; raises `ValueError` on a
  negative offset (design D2).
- **WU2** — `DEFAULT_TYPE_SENSITIVITY_DEFAULTS`, `Config.type_sensitivity_defaults`,
  eager per-entry validation in `read_config`, and the
  `type_birth_sensitivity(cfg, doc_type, base) -> str` resolver beside
  `resolve_task_model`, all in `src/openkos/config.py` (design D1, D3).

All tasks.md checkboxes for WU1 (items 1-3) and WU2 (items 1-4) are marked `[x]`.

### TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| WU1.1-3 `raise_by` | `tests/unit/model/test_okf_sensitivity.py` | Unit | ✅ 14/14 (pre-existing `sensitivity_direction` tests) | ✅ Written (`AttributeError: no attribute 'raise_by'`) | ✅ 16/16 passed | ✅ 3 floors x 3 offsets + 6 fail-closed cases + negative-offset case | ➖ None needed (already minimal, pure, stdlib-only) |
| WU2.1-3 config seam | `tests/unit/test_config.py` | Unit | ✅ 243/243 | ✅ Written (`AttributeError: 'Config' object has no attribute`) | ✅ 19/19 passed | ✅ 14 validation cases + 5 `type_birth_sensitivity` cases | ➖ None needed |

### Test Summary

- **Total tests written**: 35 (16 `raise_by` + 19 config-seam)
- **Total tests passing**: 35/35 in the new tests; 517/517 in the scoped
  `tests/unit/model/ tests/unit/test_config.py` run; 4687 passed, 1 skipped
  in the FULL `tests/unit/` suite (zero regressions repo-wide)
- **Layers used**: Unit (35)
- **Approval tests**: None — no refactoring tasks in this slice
- **Pure functions created**: 1 (`raise_by`); `type_birth_sensitivity` is a
  thin `cfg`-consuming resolver, not pure (reads `cfg` fields), matching the
  `resolve_task_model` precedent it sits beside

### Work Unit Evidence

| Evidence | WU1 | WU2 |
|---|---|---|
| Focused test command and result | `python -m pytest tests/unit/model/test_okf_sensitivity.py -v` → 30/30 passed (16 new + 14 pre-existing `sensitivity_direction`) | `python -m pytest tests/unit/test_config.py -k "type_sensitivity or TypeBirthSensitivity" -q` → 19/19 passed |
| Runtime harness | N/A — pure/config-load functions with no runtime boundary in this slice (call-site wiring is WU3/WU4, out of scope here) | N/A — same reason; `read_config` is exercised via `tmp_path`-backed unit tests, the real runtime boundary this repo has for config parsing |
| Rollback boundary | Revert `raise_by` in `okf.py` + its test file; no other file depends on it yet (WU2 is the only consumer, and this slice ships both together) | Revert the `config.py` diff (constant, field, validation block, resolver) + the 4 touched test files; no call site in `cli/main.py` was touched, so this is fully self-contained |

### Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/model/okf.py` | Modified | Added `raise_by(level, offset) -> str` |
| `tests/unit/model/test_okf_sensitivity.py` | Modified | Merged in 16 new `raise_by` tests alongside the 14 pre-existing `sensitivity_direction` tests (see Deviations) |
| `src/openkos/config.py` | Modified | `DEFAULT_TYPE_SENSITIVITY_DEFAULTS`, `Config.type_sensitivity_defaults` field, eager validation block in `read_config`, `type_birth_sensitivity` resolver |
| `tests/unit/test_config.py` | Modified | 19 new tests: `read_config` validation table + `TestTypeBirthSensitivity` class; 1 hand-built `Config(...)` fixture updated with the new required field |
| `tests/unit/test_lint.py` | Modified | 1-line fixture fix: added `type_sensitivity_defaults=` to a hand-built `Config(...)` helper broken by the new required dataclass field |
| `tests/unit/cli/test_confidential_local_exemption.py` | Modified | Same 1-line fixture fix |
| `tests/unit/cli/test_chat_timeout_wiring.py` | Modified | Same 1-line fixture fix (dict-based `_cfg()` helper) |
| `openspec/changes/per-type-default-sensitivity/tasks.md` | Modified | WU1 (3) + WU2 (4) checkboxes marked `[x]` |

### Deviations from Design

1. **First-draft mistake, corrected in-flight**: `Write`-ing
   `tests/unit/model/test_okf_sensitivity.py` initially overwrote the file
   wholesale, silently deleting 14 pre-existing `sensitivity_direction`
   tests (issue #185) that already lived at that path — tasks.md's WU1
   wording ("create `tests/unit/model/test_okf_sensitivity.py`") reads as
   a fresh file, but the file already existed. Caught immediately by the
   post-GREEN sibling-regression check (`tests/unit/model/ -q` showed
   241 instead of the expected 255), so the merge was done before this
   was ever reported as done. Final file has both test classes; nothing
   was lost. Flagging this so `sdd-verify`/future slices know the design's
   "create" wording for this file was inaccurate and to double-check any
   other WU that says "create" against an existing file first.
2. Three test fixture files outside this slice's named scope
   (`test_lint.py`, `test_confidential_local_exemption.py`,
   `test_chat_timeout_wiring.py`) needed a 1-line addition each because
   they hand-construct `config.Config(...)` directly and the new
   `type_sensitivity_defaults` field has no dataclass default (matching
   every other `Config` field's convention — none of them have defaults
   either). This is the minimum necessary fix to keep the full suite green
   and was not itself a design decision; no behavior in those files
   changed.
3. No other deviation — `raise_by`'s signature, clamp behavior, and
   negative-offset refusal match design D2 exactly; `type_sensitivity_defaults`'s
   shape, absence/`{}`/copy semantics, and validation domain
   (`BUILDABLE_TYPES`, `bool` excluded first, `0 <= offset <= 2`) match
   design D1 exactly; `type_birth_sensitivity`'s formula and unmapped-type
   behavior match design D3 exactly.

### Issues Found

None.

### Remaining Tasks (later slices, NOT this batch)

- [ ] WU3 — Ingest seam (`_stage_derived_objects`, advisory, no-backfill)
- [ ] WU4 — `query --save` seam (`_stage_filed_answer`, preview, success-message advisory)
- [ ] WU5 — ADR-0015 + docs

### Workload / PR Boundary

- Mode: chained PR slice (`delivery_strategy: auto-chain`, `chain_strategy: stacked-to-main`)
- Current work unit: WU1 + WU2 (slice 1 of the stack)
- Boundary: starts from a clean `feat/669-s1-raise-by-config` branch with
  only the SDD-artifacts commit ahead of `main`; ends with `raise_by` and
  the full config seam (`type_sensitivity_defaults` + `type_birth_sensitivity`)
  implemented, tested, and typed/linted clean. No call site (`cli/main.py`)
  is touched — WU3/WU4 wire it in the next slices.
- Estimated review budget impact: `git diff --stat` (excluding this
  progress file and tasks.md) = 8 files, 437 insertions(+), 10 deletions(-)
  → ~427 authored changed lines against the 400-line guard, close to but
  slightly over the WU1+WU2 combined forecast (~350) mainly due to the 3
  incidental fixture fixes and the merged (not overwritten) test file. Still
  well under the individual-WU-stays-under-budget goal in aggregate terms,
  and the forecast's stated fallback ("split at a defined boundary")
  applies to WU3, not this slice — flagging for visibility only, not
  requesting a split.

### Status

7/7 tasks complete for this slice (WU1: 3/3, WU2: 4/4). Ready for next batch (WU3).
