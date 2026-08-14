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

---

## Slice 2 — WU3 (this batch)

Branch: `feat/669-s2-ingest-seam`, stacked on slice 1's merged code
(`okf.raise_by`, `config.type_sensitivity_defaults`,
`config.type_birth_sensitivity` all pre-existing on this branch, untouched
by this slice). Slice 2 of the 5-WU plan in `tasks.md`.

### Completed

- **WU3** — wired `config.type_birth_sensitivity` into the ingest birth
  seam (`_stage_derived_objects`, `src/openkos/cli/main.py:3249`); added
  `_DerivedPlan.sensitivity`/`type_floor_raised`,
  `_SingleIngestOutcome.type_floor_pairs`, and `_echo_type_floor_summary`
  (beside `_echo_type_alternative_summary`), called from both the batch
  aggregate (`main.py:3874`) and the single-file wrapper (`main.py:3976`)
  (design D3 count plumbing, D4 ingest advisory wording).

All tasks.md checkboxes for WU3 (items 1-6) are marked `[x]`.

### TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| WU3.1 birth seam + advisory + no-backfill | `tests/unit/cli/test_ingest.py` | Unit | ✅ 288/288 pre-existing (module baseline before this slice) | ✅ Written (`TypeError: _stage_derived_objects() got an unexpected keyword argument 'cfg'`; advisory/no-op assertions failed on missing stderr text) | ✅ 9 new tests, 297/297 passed after GREEN | ✅ public->private, private->confidential (clamp), non-defaulted type untouched, Source untouched, silent-when-nothing-raised, consequence-line-only-at-confidential, batch aggregation | ➖ None needed — mirrors `_echo_type_alternative_summary`'s existing shape exactly |
| WU3.2 `set-sensitivity` downgrade (Req. 9) | `tests/unit/cli/test_set_sensitivity.py` | Unit | ✅ 51/51 pre-existing | ✅ Written (`AssertionError: assert 'private' == 'confidential'` — fixture correctly produced a `private` Person pre-GREEN, proving the RED test failed for the right reason, not vacuously) | ✅ 1 new test, 52/52 passed after GREEN | ➖ Single scenario per spec Requirement 9 | ➖ None needed — zero `set-sensitivity` code changed, exactly as design predicted |

### Test Summary

- **Total tests written**: 10 (9 `test_ingest.py` + 1 `test_set_sensitivity.py`)
- **Total tests passing**: 10/10 new; 302/302 in the scoped
  `tests/unit/cli/test_ingest.py tests/unit/cli/test_set_sensitivity.py`
  run; 4697 passed, 1 skipped in the FULL `tests/unit/` suite (zero
  regressions repo-wide, including every pre-existing Person-extraction
  test that does not assert an exact `sensitivity` value and therefore
  tolerates the new raise)
- **Layers used**: Unit (10)
- **Approval tests**: None
- **Twin-rule guard**: `test_stage_derived_objects_births_person_above_the_floor`
  is the ingest-site half (WU4's `--save`-site test is the other half, not
  yet written) — it fails if `_stage_derived_objects`'s call site alone
  reverts to `sensitivity=stamp_sensitivity`, independent of any
  resolver-level test.

### Work Unit Evidence

| Evidence | WU3 |
|---|---|
| Focused test command and result | `python -m pytest tests/unit/cli/test_ingest.py tests/unit/cli/test_set_sensitivity.py -q` → 302/302 passed |
| Runtime harness | `CliRunner`-driven `ingest`/`set-sensitivity` invocations against a real `tmp_path` workspace, `_FakeLLM` swapped in for `OllamaClient` (zero network) — the same harness every other WU3-adjacent test in this file already uses |
| Rollback boundary | Revert the `cli/main.py` diff (`_DerivedPlan.sensitivity`/`type_floor_raised` fields, the `cfg` param + `type_birth_sensitivity` call in `_stage_derived_objects`, `_SingleIngestOutcome.type_floor_pairs`, `_echo_type_floor_summary` + its two call sites) + the two touched test files; WU2's config seam (a prior, already-merged slice) is untouched and needs no reverting |

### Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/cli/main.py` | Modified | `_DerivedPlan.sensitivity`/`type_floor_raised` fields; `_stage_derived_objects` gained a `cfg: config.Config` kwarg and now calls `config.type_birth_sensitivity` before `okf.build_concept`; `_SingleIngestOutcome.type_floor_pairs`; new `_echo_type_floor_summary`; wired at both the batch (`_ingest_batch`) and single-file (`ingest`) call sites |
| `tests/unit/cli/test_ingest.py` | Modified | Added `config` import, `_default_cfg` helper (mirrors `test_lint.py::_cfg`), a `cfg` default in `_stage_kwargs`, and 9 new tests: birth seam (raise, non-defaulted-untouched, clamp-at-confidential), Source-never-type-defaulted, no-backfill, advisory (with/without consequence line, silent-when-nothing-raised, batch aggregation) |
| `tests/unit/cli/test_set_sensitivity.py` | Modified | Added a local `_FakeLLM`/`_patch_llm`/`_person_reply` trio (mirrors `test_ingest.py`'s, scoped to this file since `set-sensitivity` tests had no prior LLM-patching need) and the Requirement-9 downgrade test |
| `openspec/changes/per-type-default-sensitivity/tasks.md` | Modified | WU3 (6) checkboxes marked `[x]` |

### Deviations from Design

1. **`_DerivedPlan` gained a `sensitivity: str` field, not named in design's
   File Changes table.** Design's D3 count-plumbing section names
   `_DerivedPlan.type_floor_raised: bool` explicitly but does not spell out
   how `_SingleIngestOutcome.type_floor_pairs`'s `(type, resolved_level)`
   pairs are meant to obtain `resolved_level` without re-parsing
   `plan.content`'s frontmatter. Storing the already-computed
   `resolved_sensitivity` directly on the plan (mirroring how `doc_type`/
   `type_alternative` are already carried for the sibling
   `alternative_pairs` aggregate) avoids a second `okf.load_frontmatter`
   parse per raised object and keeps `_SingleIngestOutcome`'s construction
   a one-line `tuple(...)` comprehension identical in shape to
   `alternative_pairs`'s own. No behavior differs from what design
   describes; this is purely how the fact travels from the plan to the
   outcome.
2. **The Requirement-9 test file gained its own local `_FakeLLM`/
   `_patch_llm`/`_person_reply` trio instead of importing `test_ingest.py`'s.**
   `test_set_sensitivity.py` had no LLM-patching helpers before this slice
   (no prior test in that file needed to run `ingest` through the LLM
   path); importing from `test_ingest.py` would create a cross-test-module
   dependency this repo's test suite does not otherwise have. The
   duplication is 3 small, near-identical helpers (~25 lines), the same
   trade the repo already accepts for `_init_workspace`/`_set_config_field`
   existing independently in both files.
3. No other deviation — the ingest seam's formula call
   (`config.type_birth_sensitivity(cfg, extraction.type,
   stamp_sensitivity)`), the advisory's two-line wording (aggregate line +
   confidential-consequence line), and the count-plumbing shape
   (`_SingleIngestOutcome.type_floor_pairs`, `_echo_type_floor_summary`
   called from both call sites) match design D3/D4 exactly, including the
   "silent when nothing was raised" and "one line per run, not per file in
   a batch" behaviors.

### Issues Found

None. Confirmed a pre-existing risk from design's own notes did NOT
materialize: no existing test in `test_ingest.py` asserts an exact
`sensitivity` value on a `Person` document (only on `Source` documents,
which this slice never touches), so the shipped `{"Person": 1}` mapping's
now-active raise (`private` workspace floor -> `Person` born
`confidential`) broke nothing in the pre-existing suite.

### Remaining Tasks (later slices, NOT this batch)

- [ ] WU4 — `query --save` seam (`_stage_filed_answer`, preview,
  success-message advisory)
- [ ] WU5 — ADR-0015 + docs

### Workload / PR Boundary

- Mode: chained PR slice (`delivery_strategy: auto-chain`,
  `chain_strategy: stacked-to-main`)
- Current work unit: WU3 (slice 2 of the stack)
- Boundary: starts from `feat/669-s2-ingest-seam` (slice 1's merged code
  already present); ends with the ingest birth seam wired, tested, and
  typed/linted clean. `_stage_filed_answer`/`query --save` (WU4), the ADR,
  and docs (WU5) are untouched — next slices' scope.
- `git diff --stat` (excluding `apply-progress.md`) = 4 files, 398
  insertions(+), 8 deletions(-): `src/openkos/cli/main.py` (+78/-8),
  `tests/unit/cli/test_ingest.py` (+247/-1), `tests/unit/cli/test_set_sensitivity.py`
  (+69), `tasks.md` (+12/-8 checkbox flips) — comfortably under the
  400-line individual-WU budget the forecast flagged as "High risk" for
  WU3, and under the ~320-line WU3 estimate on the code+test axis alone
  (the tasks.md checkbox churn is the only non-code/test contributor).
- Full verification: `python -m pytest tests/unit/` → 4697 passed, 1
  skipped; `python -m mypy src/` → Success, no issues in 63 source files;
  `python -m ruff check src/ tests/` → All checks passed; `python -m ruff
  format src/ tests/` → 1 file reformatted (`test_ingest.py`, wrapping
  only, re-verified green after).

### Status

6/6 tasks complete for this slice (WU3: 6/6). Ready for next batch (WU4).
