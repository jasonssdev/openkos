# Proposal: Merge Edge Rewiring (MVP-2 Slice 2a — reversible typed-relation rewiring)

## Intent

Slice 1 shipped a stopgap: `merge` REFUSES whenever the absorbed object bears outbound `relations:` or is an inbound relation target (`find_relation_conflicts` + the `cli/main.py::merge` Phase-A hook). This blocks legitimate merges and pushes graph-repair onto the user. Slice 2a makes `merge` edge-aware — it always succeeds, rewires typed edges deterministically, and `unmerge` restores every touched file byte-exactly. This is slice 2a of the 2-part slice 2 (2b = LLM edge production, deferred).

## Scope

### In Scope
- Remove the slice-1 REFUSE guard; `merge` always succeeds.
- Rewiring semantics: OUTBOUND move (absorbed's `relations:` union onto survivor), INBOUND retarget (every third-party file targeting absorbed → survivor), SELF-LOOP drop (resulting absorbed↔survivor edge), COLLISION dedupe (retarget duplicating an existing edge).
- Dropped self-loops and deduped collisions shown NON-SILENT in the merge confirm preview (ADR-0004) and recorded in the ledger for exact LIFO restore.
- Ledger schema v1→v2, backward-compatible reader (`decode_merge_ledger_entry` accepts both).
- New `bundle/relations.py` trio (find/apply/reverse inbound relation rewrites, whole-file snapshots), mirroring `bundle/links.py`.
- Fix `okf.build_merged_document` to special-case `relations:` — ATOMIC with guard removal.

### Out of Scope
- LLM typed-edge production/adjudication (slice 2b).
- `relate` self-id refusal, graph-projection, and body-link (`links.py`) changes.
- Relation-type vocabulary validation.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `entity-resolution-merge`: guard requirement REPLACED by reversible rewiring requirement; Reversibility Ledger extended to v2 with a third-party relation-rewrite field (whole-file snapshots), backward-compatible with v1 entries.

## Approach

Phase-A planning computes the merged `relations:` list (outbound union, self-loop drop, collision dedupe) — reversible for free via existing `survivor_before`/`absorbed_snapshot` whole-file bytes. `build_merged_document` moves `relations:` into `_SPECIAL_KEYS` so the generic list-union no longer carries dangling `target:absorbed_id` edges or self-loops (must ship atomically with guard removal). Third-party inbound retargets — the only genuinely new ledger surface — use `bundle/relations.py` over the same `other_files` scope as `find_inbound_link_rewrites`, snapshotting each modified file whole-file for exact LIFO reverse. Ledger v2 adds one field; v1 entries decode and unmerge exactly as today (no migration).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/bundle/merge.py` | Modified | Remove guard; Phase-A outbound-merge planning; v2 ledger append |
| `src/openkos/bundle/relations.py` | New | find/apply/reverse third-party inbound relation rewrites (snapshots) |
| `src/openkos/model/okf.py` | Modified | `build_merged_document` `relations:` special-case; `MergeLedgerEntry` v2 field; v1/v2 decode |
| `src/openkos/cli/main.py` | Modified | merge Phase-A rewiring, non-silent preview; unmerge reverse |
| `tests/unit/bundle/test_merge.py`, `test_merge_roundtrip.py`, `test_unmerge.py`, `tests/unit/cli/test_merge.py` | Modified | Drop guard tests; add rewiring + round-trip property tests |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| v1→v2 ledger reader strands in-flight v1 entries | Med | Backward-compat decode; dedicated v1-unmerge test |
| Dedup-collision round-trip incorrect | Med | Whole-file snapshot record; property round-trip test |
| Multi-merge LIFO across overlapping third-party files (unanalyzed) | High | Flag for design; dedicated overlapping-merge test scenarios |
| Guard removal + `build_merged_document` fix split → silent graph corruption window | High | Land ATOMICALLY in one change |
| Test rewrites exceed 400-line review budget | Med | delivery_strategy=auto-chain, chain_strategy=feature-branch-chain |

## Rollback Plan

Revert the change/PR chain. Ledger v2 field is additive and optional on decode; already-merged bundles keep unmerging under v1. Restores the slice-1 REFUSE guard. No destructive migration, no data loss.

## Dependencies

Builds on slice-1 `typed-relationships` (`relations:` format) and the existing merge/unmerge ledger (ADR-0002). No new runtime deps.

## Success Criteria

- [ ] Merge of an outbound/inbound edge-bearing object succeeds (no refusal), edges rewired.
- [ ] merge→unmerge restores every touched file byte-exact, including dropped self-loops and deduped collisions.
- [ ] Multiple sequential merges unmerge LIFO, byte-exact, across overlapping third-party files.
- [ ] Pre-slice-2a v1 `merged_from` entries still unmerge exactly.
- [ ] Dropped self-loops and deduped collisions appear in the merge confirm preview (non-silent).
- [ ] `build_merged_document` never emits a dangling `target:absorbed_id` edge or a self-loop.
- [ ] `uv run pytest` green; 90% branch coverage held.
