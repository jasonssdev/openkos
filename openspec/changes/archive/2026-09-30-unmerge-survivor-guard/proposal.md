# Proposal: Unmerge Refuses A Survivor Edited Since Its Merge (#1110)

## Intent

`unmerge <survivor-id> <absorbed-id>` restores the survivor from the
ledger's `survivor_before` snapshot. It never checked whether the survivor
CHANGED since the merge wrote it: any edit landing on the survivor -- by a
human, or by another verb -- between the merge and the unmerge was
overwritten with no warning. The command's own docstring admitted it: "the
survivor has no pre-prompt drift check at all". The existing post-confirm
drift guard (`_reject_drifted_targets`, issues #306/#313/#319) only catches
an edit landing INSIDE the confirm-prompt window, since its baseline is
Phase A's OWN read -- an edit made any earlier is already baked into that
baseline and can never be seen as drift. The link/relation/provenance
rewrite files already have a genuinely pre-prompt, fail-closed check
(`bundle.links.reverse_link_rewrites` and its relation/provenance
counterparts, comparing disk against what the MERGE recorded); the
survivor was the one write target with no equivalent.

Found while implementing `deprecated-status-export` (#1075): a test
(`test_pin_unmerge_does_not_refuse_on_a_survivor_edited_after_merge`,
`tests/unit/cli/test_merge_status_export.py`) pinned this as the resolved
answer to Task 5.1's design open question, precisely because it needed to
know whether `unmerge`'s projection work could rely on the restored
survivor's post-merge state being trustworthy. It is not, until this fix.

## Scope

### In Scope

- A new committed `MergeLedgerEntry` field, `survivor_after_sha256`: the
  `sha256` (`bundle.ledger.survivor_sha256`) of the EXACT survivor bytes
  the merge wrote, bound in `application.lifecycle.merge_core` at the same
  point the pending sidecar's own `expected_survivor_sha256` is bound (same
  value, reused). NOT tied to a ledger schema bump -- it is an independent,
  optional drift-check hash, never information the core reversal
  computation needs, so every schema version (v1 through today's v5) may
  carry it; an absent key (every entry recorded before this change) decodes
  to the empty "cannot verify" sentinel.
- `application.lifecycle.prepare_unmerge` (Phase A) compares the
  survivor's CURRENT text against the LIFO-tail entry's
  `survivor_after_sha256`, before any preview or prompt. A mismatch raises
  `ValueError` -- exit 1, nothing written, naming the survivor and telling
  the operator to copy the edit somewhere safe before re-running (the #328
  remedy wording style), since a plain re-run only overwrites it again.
  This groups with `unmerge`'s existing pre-prompt "ledger check" family
  (`docs/cli.md`'s Conventions section), which already refuses at exit 1
  for the absorbed-path collision and the link/relation/provenance drift
  checks -- distinct from the separate, narrower post-confirm drift
  refusal at exit 3.
- A tail entry with no recorded hash (a v1-v5 entry from before this
  change) cannot be checked; `unmerge` prints a one-line warning that it
  cannot verify the survivor was unedited and proceeds -- fail-open, but
  disclosed, only for that legacy case.
- The check compares against the bytes on disk right after the merge
  commit, so ANY later rewrite counts as an edit -- including one made by
  another verb entirely (`repair`'s status export/migration, `sync-tags`,
  even the deprecated-status export merge itself writes) rather than a
  human. This is the conservative, correct choice: a write is a write
  regardless of who made it, and the alternative (allowlisting "safe"
  rewriters) reopens the exact silent-discard hazard this fix closes.
- Update the pinned characterization test to the corrected (refusing)
  behavior; add regression coverage for the refusal and the legacy-entry
  warning.
- Delta spec for `entity-resolution-merge` (a new ADDED requirement, since
  the fix is a new refusal precondition, not a change to the round-trip
  mechanism "Unmerge Achieves Round-Trip Parity" already describes).
- `docs/cli.md`'s `unmerge` "pre-prompt ledger check" paragraph, which
  enumerated only the absorbed-path collision and the link/relation/
  provenance checks, extended to name the survivor case.

### Out of Scope

- No change to the post-confirm drift guard (`_reject_drifted_targets`) --
  it keeps its own, narrower job (an edit landing during the confirm
  prompt) unchanged.
- No ledger schema bump (`MERGE_LEDGER_SCHEMA_V6`): the new field is a
  compatible, unconditional extension of every existing schema, not a
  required key tied to one version -- see Scope above.
- No `--force` escape hatch: unlike the doctor-flagged-ledger refusal,
  there is no "I have already confirmed this is safe" case here -- the
  remedy is copying the edit away and re-running, which recovers cleanly.
- No change to `merge`'s own behavior or preview.

## Decisions

| # | Decision | Evidence |
|---|---|---|
| 1 | Compatible field extension, not a schema bump | The field is optional drift-check metadata, not core reversal information (unlike `relation_rewrites`/`carried_content_ids`/`index_restores`, each tied to a required-key schema bump); bumping would also force updating every "is this schema's catalog shape a delta" check (`_restored_catalog_and_log`, the preview branch, `decode_merge_ledger_entry`'s `catalog_snapshots_stored`) for a field unrelated to that shape. |
| 2 | Hash bound in `merge_core` (Phase B), not `plan_merge` (Phase A) | The deprecated-status export (#1075) can still rewrite `plan.merged_survivor` AFTER `plan_merge` returns (`prepare_merge`'s own documented mutation); hashing the FINAL bytes at the point they are written is the only way the recorded hash and the disk bytes cannot diverge. |
| 3 | Exit 1, not exit 3 | `docs/cli.md`'s Conventions section documents exit 3 as reserved for the post-confirm re-validation against what THIS run previewed; every other pre-prompt Phase A refusal in `unmerge` (absorbed-path collision, link/relation/provenance drift) already exits 1 as "this pre-prompt ledger check" comparing disk against what the merge recorded -- the new check is the same family. |
| 4 | Legacy entries fail OPEN, disclosed | Refusing every bundle whose merges predate this fix would be a regression with no recovery path (the hash was never recorded, so it can never be satisfied); a printed warning matches the project's "human curates, engine maintains -- reviewable, not silently automatic" principle without blocking recovery. |
| 5 | Any later rewrite counts, including a legitimate one from another verb | The guard compares against the bytes right after the merge commit; allowlisting specific "safe" rewriters would require trusting each one's own drift discipline and reopens the exact hazard being closed. Conservative and simple. |

## Capabilities

### Modified Capabilities

- `entity-resolution-merge` -- ADDED "Unmerge Refuses When The Survivor Was
  Edited Since Its Own Merge".

## Approach

Extend `MergeLedgerEntry` with the new field and its unconditional encode/
decode (`model/okf.py`); bind the real hash in `merge_core`
(`application/lifecycle.py`); add the pre-prompt comparison to
`prepare_unmerge`, threading the "cannot verify" signal through
`PreparedUnmerge`; print the CLI warning and rely on the existing generic
`ValueError` -> exit 1 path for the refusal (`cli/main.py`). No new
exception type, no new CLI branch beyond the one warning line -- the
refusal reuses machinery that already exists for every other Phase A
`ValueError`.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/model/okf.py` | Modified | `MergeLedgerEntry.survivor_after_sha256` field, unconditional encode/decode |
| `src/openkos/application/lifecycle.py` | Modified | `merge_core` binds the hash; `prepare_unmerge` checks it, `PreparedUnmerge.survivor_drift_unverifiable` |
| `src/openkos/cli/main.py` | Modified | `_run_single_unmerge` prints the legacy-entry warning; docstring updated |
| `tests/unit/model/test_okf.py` | Modified | Round-trip, default, and schema-independence tests |
| `tests/unit/cli/test_merge_core.py` | Modified | `merge_core` binds the correct hash |
| `tests/unit/cli/test_unmerge.py` | Modified | Refusal and legacy-warning tests |
| `tests/unit/cli/test_merge_status_export.py` | Modified | Pinned bug test corrected to the fixed behavior |
| `tests/unit/application/test_lifecycle.py` | Modified | `PreparedUnmerge` fixture updated for the new field |
| `docs/cli.md` | Modified | `unmerge`'s pre-prompt ledger check paragraph |
| `openspec/specs/entity-resolution-merge/spec.md` | Modified | Delta (this change) |

## Rollback Plan

One-line revert per file (`git revert`); no data migration. Every entry
written by this change still decodes under the pre-fix reader (the field
is additive and optional), so a rollback never breaks an existing ledger.

## Success Criteria

- [x] `unmerge` refuses (exit 1, nothing written) when the survivor's
      bytes were edited after the merge wrote it, naming the survivor and
      telling the operator to copy the edit somewhere safe.
- [x] An untouched survivor still round-trips byte-for-byte.
- [x] A pre-fix ledger entry (no recorded hash) warns once and proceeds
      rather than refusing.
- [x] Another verb's legitimate rewrite of the survivor after the merge
      (simulated the same way a human edit is) also refuses -- the
      conservative, documented choice.
- [x] `uv run ruff check .`, `uv run ruff format --check .`,
      `uv run mypy .`, `uv run pytest --cov`,
      `uv run python evals/run_self_tests.py` all green.
