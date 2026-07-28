# Apply Progress: `status` surfaces pending duplicate groups

**Change**: `status-surfaces-pending-duplicates`
**Mode**: Strict TDD
**Batch**: 1 of 1 (no prior apply-progress existed)

## Completed Tasks

All 16 tasks across Phases 1-4 are complete. See `tasks.md` for the per-task
`[x]` marks and inline evidence notes.

- [x] 1.1 Spec confirmation — no drift found.
- [x] 2.1-2.8 RED phase — six new tests written and run before any production
      change.
- [x] 3.1-3.3 GREEN phase — fourth `needs_attention` source added, docstring
      updated, six tests pass.
- [x] 4.1-4.4 Full verification — full suite, coverage, lint/format/types,
      manual smoke test.

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `src/openkos/cli/main.py` | Modified | Added the fourth `needs_attention` source in `status()`: an inline `Tier.HIGH` filter over `find_candidates(layout.bundle_dir)`, appending a line naming `openkos duplicates` when `exact_title_groups > 0`. Inserted before the `vectors_missing` assignment, per D3. Updated the `status` docstring: "THREE independent walks" → "FOUR", documenting the new unconditional `resolution.find_candidates` walk. |
| `tests/unit/cli/test_status.py` | Modified | Added a `_write_doc` helper (copied from `test_duplicates.py:66`) and six new tests (T1-T6) covering: exact-title surfacing, absence of tier-label words, near-match-only staying all-clear (with `seed_vectors_db`), no-groups staying all-clear (with `seed_vectors_db`), deprecated-only exclusion (with `seed_vectors_db`), and plural wording. |
| `openspec/changes/status-surfaces-pending-duplicates/tasks.md` | Modified | All 16 tasks marked `[x]` with evidence notes. |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 2.2 T1 `test_status_surfaces_exact_title_duplicate_group` | `tests/unit/cli/test_status.py` | Unit (CLI, `CliRunner`) | ✅ 43/43 (`test_status.py` + `test_duplicates.py`, baseline) | ✅ Written, failed: `AssertionError: '1 candidate group' not in stdout` (no duplicate line rendered yet) | ✅ Passed after 3.1 | ✅ see T2/T6 (same behavior, different assertions) | ✅ Clean, no further extraction needed |
| 2.3 T2 `test_status_duplicate_line_has_no_tier_labels` | `tests/unit/cli/test_status.py` | Unit | ✅ (same run) | ✅ Written, failed: `StopIteration` (no line containing "candidate group" existed) | ✅ Passed after 3.1 | ➖ Single scenario (negative-word check) | ✅ Clean |
| 2.4 T3 `test_status_near_match_only_duplicates_still_all_clear` | `tests/unit/cli/test_status.py` | Unit | ✅ (same run) | ⚠️ Passed trivially pre-implementation (no feature = no false-positive line, which is the correct pre-existing state) — noted explicitly rather than hidden; this is a regression-guard/characterization test on the tier filter's false arm, not a positive-behavior addition, so it cannot RED via undefined-symbol reference the way T1 does | ✅ Confirmed still passing after 3.1 (real GREEN: `find_candidates` now runs and the `Tier.HIGH` filter correctly excludes the LOW-tier pair) | ➖ Sole pin on the HIGH-only decision; not folded into any other test per instruction | ✅ Clean |
| 2.5 T4 `test_status_no_duplicate_groups_no_new_entry` | `tests/unit/cli/test_status.py` | Unit | ✅ (same run) | ⚠️ Same characterization-test nature as T3 (asserts pre-existing all-clear state) | ✅ Confirmed still passing after 3.1 — covers `if exact_title_groups:` false arm with real (empty) `find_candidates` result | ➖ Single scenario | ✅ Clean |
| 2.6 T5 `test_status_deprecated_only_duplicate_group_excluded` | `tests/unit/cli/test_status.py` | Unit | ✅ (same run) | ⚠️ Same characterization-test nature | ✅ Confirmed still passing after 3.1 — proves `find_candidates`'s default `include_deprecated=False` excludes the group before it ever reaches the `Tier.HIGH` filter | ➖ Single scenario | ✅ Clean |
| 2.7 T6 `test_status_duplicate_line_plural_wording` | `tests/unit/cli/test_status.py` | Unit | ✅ (same run) | ✅ Written, failed: `AssertionError: '2 candidate groups' not in stdout` | ✅ Passed after 3.1 | ✅ Triangulates T1's singular case via `_plural()`'s true/false arms | ✅ Clean |

### Test Summary
- **Total tests written**: 6
- **Total tests passing**: 6 (all 6, plus full suite 2339/2339)
- **Layers used**: Unit (CLI, `typer.testing.CliRunner`) — 6
- **Approval tests** (refactoring): None — no refactoring tasks; T3/T4/T5 function as characterization/regression guards on pre-existing negative behavior, documented above rather than treated as silent trivial passes
- **Pure functions created**: 0 (inline expression per design D1, matching the established `duplicates` pattern at `main.py:4744`; no new symbol)

## Deviations from Design

None — implementation matches design exactly (D1-D4, the line's exact wording, insertion point, and docstring change).

One process note (not a design deviation): task 2.8 says to confirm all six
new tests FAIL before touching `main.py`. Three of the six (T3, T4, T5) are
regression-guard tests that assert the CURRENT (pre-feature) negative state —
"no duplicate line, still all-clear" — which is unavoidably already true
before any production code exists, since there is no code yet to produce a
false positive. They cannot RED via the "reference code that doesn't exist"
mechanism the way T1/T2/T6 do. This was verified honestly (not silently
accepted): confirmed via a raw pre-implementation test run that these three
passed for the CORRECT reason (empty `needs_attention` addition, not a
fixture bug), then re-confirmed all three still pass post-implementation
proving the branch they exist to pin (T3: `Tier.HIGH` filter false arm; T4:
`if exact_title_groups:` false arm; T5: deprecated-exclusion via
`find_candidates`'s own default) is real and exercised.

## Issues Found

None.

## Remaining Tasks

None — 16/16 complete.

## Workload / PR Boundary

- Mode: single PR (Low risk, no chaining, no `size:exception` needed)
- Current work unit: Unit 1 (the only suggested work unit) — complete
- Boundary: `main.py:4564-4575` (the new `needs_attention` block) +
  `main.py:4498-4514` (docstring) + the new `tests/unit/cli/test_status.py`
  block (`_write_doc` + T1-T6) + `tasks.md` checkbox updates. No other file
  touched.
- Estimated review budget impact: ~20 production lines + ~140 test lines,
  well under the 400-line budget (forecast: Low risk).

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_status.py -k duplicate` → 6 passed |
| Runtime harness command/scenario and exact result | `uv run openkos status` against two scratch workspaces: (1) two identically-titled `Concept` docs → `1 candidate group with identical titles — run \`openkos duplicates\` to review.` rendered under "Needs attention"; (2) a near-match-only pair (`Stoicism` / `Stoic Philosophy`) with `vectors.db` seeded → `Nothing needs attention.` rendered, no duplicate line |
| Rollback boundary | Revert the `main.py:4564-4575` block, the `main.py:4498-4514` docstring diff, and the new `test_status.py` block (`_write_doc` + T1-T6) — no other file touched |

## Status

16/16 tasks complete. Ready for verify.
