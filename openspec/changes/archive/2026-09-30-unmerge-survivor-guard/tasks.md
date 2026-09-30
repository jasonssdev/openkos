# Tasks: Unmerge Refuses A Survivor Edited Since Its Own Merge (#1110)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~40 (model/okf.py), ~60 (application/lifecycle.py), ~20 (cli/main.py), ~250 (tests), ~30 (docs/spec) |
| 400-line budget risk | Low -- one coherent, self-contained fix |
| Chained PRs recommended | No |
| Delivery strategy | ask-on-risk (default) |

## Phase 1: RED

- [x] 1.1 Confirm the pinned characterization
      (`test_pin_unmerge_does_not_refuse_on_a_survivor_edited_after_merge`,
      `tests/unit/cli/test_merge_status_export.py`) still reproduces the bug
      on current `main` (exit 0, survivor silently overwritten).
- [x] 1.2 Add failing tests pinning the FIXED contract: a survivor edited
      after `merge` (before `unmerge`) refuses closed
      (`tests/unit/cli/test_unmerge.py::test_unmerge_survivor_edited_since_merge_refuses_closed_no_write`,
      `tests/unit/cli/test_merge_status_export.py::test_unmerge_refuses_on_a_survivor_edited_after_merge`);
      observe RED (both currently assert `exit_code == 0`/silent overwrite).

## Phase 2: GREEN -- the fix

- [x] 2.1 `model/okf.py`: add `MergeLedgerEntry.survivor_after_sha256`
      (default `""`), unconditional encode (every schema), and decode via
      `raw.get(..., "")` (never schema-gated, unlike a required-key field).
- [x] 2.2 `application/lifecycle.py`, `merge_core`: bind the tail entry's
      `survivor_after_sha256` to `expected_survivor_sha256` (the SAME hash
      already computed for the pending sidecar) via `dataclasses.replace`,
      immediately before `write_pending`.
- [x] 2.3 `application/lifecycle.py`, `prepare_unmerge`: after
      `bundle_merge.plan_unmerge` returns, compare the survivor's current
      text's hash against `plan.entry.survivor_after_sha256`; raise
      `ValueError` on a mismatch (naming the survivor, the #328 "copy it
      somewhere safe" remedy wording style); set
      `PreparedUnmerge.survivor_drift_unverifiable` when the entry has no
      recorded hash (a pre-fix entry).
- [x] 2.4 `cli/main.py`, `_run_single_unmerge`: print the one-line warning
      when `prepared.survivor_drift_unverifiable`; the refusal itself needs
      no new branch -- it reuses the existing generic `ValueError` -> exit 1
      catch every other Phase A refusal already goes through.
- [x] 2.5 Re-run the Phase 1 tests; observe GREEN.

## Phase 3: Fallout, legacy coverage, and mutation proof

- [x] 3.1 Rewrite the Phase 1.1 pin
      (`test_pin_unmerge_does_not_refuse_on_a_survivor_edited_after_merge` ->
      `test_unmerge_refuses_on_a_survivor_edited_after_merge`) to assert the
      corrected behavior instead of the bug.
- [x] 3.2 Fix the one existing test that hand-constructs `PreparedUnmerge`
      (`tests/unit/application/test_lifecycle.py::test_unmerge_core_is_directly_callable_and_restores_the_pre_merge_state`)
      for the new required field.
- [x] 3.3 Add `tests/unit/cli/test_unmerge.py::test_unmerge_legacy_ledger_entry_warns_it_cannot_verify_survivor_edit`:
      a real merge's committed entry with `survivor_after_sha256` stripped
      (simulating a pre-fix entry) warns once and proceeds rather than
      refusing.
- [x] 3.4 Add `tests/unit/cli/test_merge_core.py::test_merge_core_binds_survivor_after_sha256_to_the_written_bytes`:
      the committed entry's hash equals `bundle_ledger.survivor_sha256` of
      the exact bytes written to the survivor file.
- [x] 3.5 Add model-level round-trip/default/schema-independence tests in
      `tests/unit/model/test_okf.py`
      (`test_survivor_after_sha256_round_trips_through_frontmatter`,
      `test_survivor_after_sha256_defaults_to_empty_sentinel`,
      `test_decode_absent_survivor_after_sha256_defaults_to_empty_sentinel`,
      `test_encode_survivor_after_sha256_is_never_schema_guarded`).
- [x] 3.6 Mutation check: disable the mismatch-raise branch in
      `prepare_unmerge` (`if False and not survivor_drift_unverifiable:`),
      purge `__pycache__`, observe both Phase 1.2 tests go RED for the
      right reason (exit 0 instead of 1); restore the exact inverse edit,
      purge `__pycache__` again, re-confirm GREEN.

## Phase 4: Docs and spec

- [x] 4.1 `docs/cli.md`: extend the `unmerge` "pre-prompt ledger check"
      paragraph to name the survivor case and its remedy.
- [x] 4.2 `openspec/changes/unmerge-survivor-guard/specs/entity-resolution-merge/spec.md`
      delta (this change): ADDED "Unmerge Refuses When The Survivor Was
      Edited Since Its Own Merge".

## Phase 5: Verification

- [x] 5.1 `uv run ruff check .`
- [x] 5.2 `uv run ruff format --check .`
- [x] 5.3 `uv run mypy .`
- [x] 5.4 `uv run pytest --cov`
- [x] 5.5 `uv run python evals/run_self_tests.py`

## Phase 6: Archive

- [x] 6.1 `gentle-ai sdd-archive-compose --canonical openspec/specs/entity-resolution-merge/spec.md --delta openspec/changes/unmerge-survivor-guard/specs/entity-resolution-merge/spec.md --output -` exits 0.
- [x] 6.2 Compose into the living spec; verify the new heading appears
      exactly once; `git mv` the change folder to
      `openspec/changes/archive/2026-09-30-unmerge-survivor-guard/`; write
      `archive-report.md`.
