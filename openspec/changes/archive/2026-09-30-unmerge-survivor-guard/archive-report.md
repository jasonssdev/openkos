# Archive Report: unmerge-survivor-guard

**Date archived:** 2026-09-30
**Issue:** #1110
**Status:** archived in the same branch as the implementation (`fix/1110-unmerge-survivor-guard`)

## Delta merged into `openspec/specs/entity-resolution-merge/spec.md`

| Section | Requirement | Canonical count after merge |
|---|---|---|
| ADDED | Unmerge Refuses When The Survivor Was Edited Since Its Own Merge | 18 |

Composed with `gentle-ai sdd-archive-compose` (exit 0). The new requirement
heading was appended once, with its four scenarios, and every requirement
already in the canonical spec is byte-for-byte unchanged (`git diff` shows
80 pure insertions, 0 deletions).

**Follow-up (same day, same branch, before this change left `main`):**
orchestrator review found the original refusal a dead end -- "copy it
somewhere safe, then re-run" with no flag hashes the identical edited
survivor and refuses again forever, so a legitimate post-merge rewrite
(including `repair`'s or `sync-tags`'s own) could never be unmerged at
all. Since this change was still unarchived-in-main, the requirement text
was edited IN PLACE (both the living spec and this archived copy) rather
than issued as a second delta: the requirement now additionally specifies
an explicit, named, opt-in `--discard-survivor-edits` flag that bypasses
ONLY the survivor-edit check, is never implied by `--auto`, and never
rescues any other refusal; three new scenarios were added
(`--discard-survivor-edits proceeds and discards the edit`,
`--discard-survivor-edits does not bypass an unrelated refusal`,
`--discard-survivor-edits is never implied by --auto`), and the two
existing refusal scenarios were reworded to name the flag. The canonical
requirement count is unchanged (18) -- one requirement, extended, not a
new one.

## What shipped

- `model/okf.py`: `MergeLedgerEntry.survivor_after_sha256` -- a new field,
  default `""`, encoded/decoded UNCONDITIONALLY on every schema version
  (not tied to a schema bump, unlike `relation_rewrites`/
  `carried_content_ids`/`index_restores`): the `sha256` of the exact
  survivor bytes a merge wrote.
- `application/lifecycle.py`, `merge_core`: binds the committed tail
  entry's `survivor_after_sha256` to the same `expected_survivor_sha256`
  already computed for the pending sidecar's crash-recovery hash, patched
  in with `dataclasses.replace` immediately before `write_pending` --
  after the deprecated-status export (#1075) has had its last chance to
  rewrite `plan.merged_survivor`, so the recorded hash and the disk bytes
  cannot diverge.
- `application/lifecycle.py`, `prepare_unmerge`: compares the survivor's
  current text's hash against the LIFO-tail entry's `survivor_after_sha256`
  as the first step of Phase A, before any preview. A mismatch raises
  `ValueError` (caught by the CLI's existing generic Phase A handler, exit
  1, nothing written), naming the survivor and telling the operator to
  copy the edit somewhere safe before re-running (the #328 remedy wording
  style) rather than suggesting a plain re-run. A tail entry with no
  recorded hash (every entry from before this fix) sets
  `PreparedUnmerge.survivor_drift_unverifiable`, which
  `cli/main.py` turns into a one-line disclosed warning instead of a
  refusal.
- `docs/cli.md`'s `unmerge` "pre-prompt ledger check" paragraph extended to
  name the survivor case alongside the absorbed-path collision and the
  link/relation/provenance checks it already documented.
- `CHANGELOG.md`'s Unreleased/Fixed section.
- The pinned characterization of the bug
  (`test_pin_unmerge_does_not_refuse_on_a_survivor_edited_after_merge`,
  `tests/unit/cli/test_merge_status_export.py`, written during #1075's
  Task 5.1) was rewritten to
  `test_unmerge_refuses_on_a_survivor_edited_after_merge`, asserting the
  corrected (refusing) behavior.
- New tests: `tests/unit/cli/test_unmerge.py`
  (`test_unmerge_survivor_edited_since_merge_refuses_closed_no_write`,
  `test_unmerge_legacy_ledger_entry_warns_it_cannot_verify_survivor_edit`),
  `tests/unit/cli/test_merge_core.py`
  (`test_merge_core_binds_survivor_after_sha256_to_the_written_bytes`),
  and four model-level round-trip/default/schema-independence tests in
  `tests/unit/model/test_okf.py`.
- `tests/unit/application/test_lifecycle.py`'s one hand-constructed
  `PreparedUnmerge` fixture updated for the new required field.

### Follow-up: `--discard-survivor-edits`

- `cli/main.py`: new `unmerge` option `--discard-survivor-edits`, threaded
  through `_run_single_unmerge` (both the classic two-arg path and the
  `--to` chain loop, one flag value for the whole chain) into
  `application_lifecycle.prepare_unmerge`.
- `application/lifecycle.py`, `prepare_unmerge`: takes
  `discard_survivor_edits: bool = False`; on a detected mismatch, raises
  exactly as before when the flag is absent, or sets the new
  `PreparedUnmerge.survivor_edits_discarded` and proceeds when present.
  The flag is checked at ONLY this one comparison -- nothing else in
  `prepare_unmerge` (the absorbed-path collision, the link/relation/
  provenance drift checks) or in `cli/main.py`'s post-confirm
  `_reject_drifted_targets` reads it.
- `cli/main.py`: reworded the refusal to name `--discard-survivor-edits`
  and the "reapply the edit by hand" remedy; prints a new one-line
  disclosure (naming the survivor) when `survivor_edits_discarded` is
  `True`, distinct from the legacy-entry warning.
- New tests in `tests/unit/cli/test_unmerge.py`:
  `test_unmerge_discard_survivor_edits_flag_proceeds_and_restores_pre_merge_survivor`
  (the flag proceeds and restores byte-for-byte to the pre-merge state),
  `test_unmerge_discard_survivor_edits_flag_does_not_bypass_link_drift_refusal`
  (the flag does not rescue an unrelated fail-closed check), and an added
  assertion on the existing refusal test that the message names the flag.
- `tests/unit/application/test_lifecycle.py`'s `PreparedUnmerge` fixture
  updated again for `survivor_edits_discarded`.

## Design decisions worth recording (from the proposal)

- **Compatible field extension, not a schema bump.** The hash is optional
  drift-check metadata, not core reversal information, so every ledger
  schema (v1 through today's) may carry it; bumping to a new schema would
  have forced updating every "is this schema's catalog shape a delta" check
  unrelated to this fix.
- **Bound in Phase B, not Phase A.** `plan_merge`'s entry carries the
  default `""` placeholder; `merge_core` is the only place the FINAL
  survivor bytes (post deprecated-status-export mutation) are known, so
  that is where the real hash is bound.
- **Exit 1, not exit 3.** `docs/cli.md` documents exit 3 as reserved for
  the post-confirm re-validation against what a run previewed; this check
  is a pre-prompt Phase A refusal comparing disk against what the merge
  recorded, the same family as `unmerge`'s existing absorbed-path and
  link/relation/provenance checks (all exit 1).
- **Legacy entries fail open, disclosed.** Refusing every bundle whose
  merges predate this fix would have no recovery path; a printed warning
  matches "human curates, engine maintains -- reviewable, not silently
  automatic" without blocking recovery.
- **Any later rewrite counts, including a legitimate one from another
  verb.** The guard compares against the bytes right after the merge
  commit; allowlisting "safe" rewriters would require trusting each one's
  own drift discipline and reopens the exact hazard being closed.

## Verification

Re-run in full after the `--discard-survivor-edits` follow-up:

- `uv run ruff check .` -- pass
- `uv run ruff format --check .` -- pass (393 files already formatted)
- `uv run mypy .` -- pass (393 source files, no issues)
- `uv run pytest --cov` -- 7616 passed, 2 skipped, 96.73% branch coverage
  (>= 90% required)
- `uv run python evals/run_self_tests.py` -- 44 of 44 harness self-tests
  run, 0 failing

Mutation proof (original fix): disabling the mismatch-raise branch in
`prepare_unmerge` (`if False and not survivor_drift_unverifiable:`), after
purging `__pycache__`, turned both
`test_unmerge_survivor_edited_since_merge_refuses_closed_no_write` and
`test_unmerge_refuses_on_a_survivor_edited_after_merge` RED (exit 0
instead of 1) for the expected reason; the inverse edit and a second
`__pycache__` purge restored GREEN.

Mutation proof (`--discard-survivor-edits` follow-up), each reverted and
`__pycache__`-purged after observing RED:

- Forcing the mismatch-raise to fire regardless of the flag
  (`if True: # ignore discard_survivor_edits`) turned
  `test_unmerge_discard_survivor_edits_flag_proceeds_and_restores_pre_merge_survivor`
  RED (exit 1 instead of 0).
- Making the link-rewrite reversal swallow its own `ValueError` when
  `discard_survivor_edits` is set (a plausible "flag scope creep" bug)
  turned
  `test_unmerge_discard_survivor_edits_flag_does_not_bypass_link_drift_refusal`
  RED (exit 0 instead of 1).
- Dropping `--discard-survivor-edits` from the raised message turned the
  updated `test_unmerge_survivor_edited_since_merge_refuses_closed_no_write`
  RED on its new flag-name assertion.

## Out of scope (unchanged)

- The post-confirm drift guard (`_reject_drifted_targets`) keeps its own,
  narrower job unchanged.
- No `--force` escape hatch for this refusal -- the remedy (copy the edit
  away, re-run) recovers cleanly without one.
- No change to `merge`'s own behavior or preview.
