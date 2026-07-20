# Proposal: Entity Resolution — Confirm-Gated Reversible Merge

## Intent

Slice 3 (final) of the entity-resolution mini-chain and the first DESTRUCTIVE
op: fuse two objects a human confirms are the same real-world entity. Slices 1-2
only *surface* duplicates (`duplicates`/`adjudicate`, read-only); nothing acts on
them. This closes the boundary problem by uniting the graph — while honoring
KOM's stated bar that merges are REVERSIBLE with no info loss (KOM:317-328,
255-272). Success = a merge that a non-git user can fully undo, keeps every
inbound link live, and recomputes (never copies) sensitivity.

## Scope

### In Scope
- `merge <survivor-id> <absorbed-id>` — 2-way, explicit ordering, confirm-gated.
- Reversible `merged_from` frontmatter ledger: EMBEDDED snapshot of the absorbed
  object + the recorded list of inbound-link rewrites performed.
- First-class `unmerge <survivor-id> <absorbed-id>` — two-arg, LIFO-enforced:
  reverses ONLY the most-recent unreversed `merged_from` entry (the LIFO tail),
  and the supplied `absorbed-id` MUST equal that tail entry's `absorbed_id`
  else the command refuses with no write (reversing a non-tail entry is unsafe
  due to nested snapshots / overlapping rewrites). Restores the survivor and
  absorbed object from snapshot, REVERSES the recorded link rewrites, and
  undoes catalog/log surgery.
- Sensitivity high-water-mark: `combine_sensitivity(a, b)` + ordering constant
  (public < private < confidential); FIRST implementation in the codebase.
- Inbound-link REWRITE: repoint `(/absorbed-id.md)` links to the survivor.
- Content combine: body APPEND (never overwrite), provenance UNION; frontmatter
  conflict rule pinned (survivor wins scalars, union lists, sensitivity
  recomputed, freshness/as-of = most recent).
- Phase A preview surfacing sensitivity outcome + links to be rewritten; docs.

### Out of Scope
- Re-opening slices 1-2 (`candidates.py`/`adjudication.py` unchanged).
- Embeddings; any similarity-model change.
- No-confirm automatic merge (violates principle #3).
- N-way single-shot merge (HIGH group >2 → sequential pairwise).
- Batch / `--from-adjudicate` mode (manual pairwise only this slice).

## Capabilities

### New Capabilities
- `entity-resolution-merge`: `merge`/`unmerge` verbs, reversible embedded-snapshot
  ledger, inbound-link rewrite, sensitivity high-water-mark, content/provenance
  combine, confirm-gate UX.

### Modified Capabilities
None. `entity-resolution`, `entity-resolution-adjudication`, `forget-command`,
`ingestion`, `graph-projection` specs are unchanged.

## Approach

Mirror `forget`'s Phase A (resolve/guard) → confirm gate (`--auto` >
`review:false` > TTY prompt > non-TTY refusal) → Phase B (catalog first, file
last) shape, doubled for two objects. Reuse `bundle/index.py`
remove/insert_index_entry and `bundle/log.py` insert_log_entry. Add a fail-closed
merge builder + `combine_sensitivity` in `model/okf.py`. Link rewrite is a scoped
whole-bundle markdown text pass, its edits recorded for reversal. `unmerge` reads
the ledger and inverts every step. Likely a multi-unit feature-branch chain:
sensitivity high-water-mark → merge core (snapshot ledger + combine) →
inbound-link rewrite → merge CLI + confirm gate → unmerge.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | New | `merge`/`unmerge` commands |
| `src/openkos/model/okf.py` | Modified | `combine_sensitivity`, merge builder |
| `src/openkos/bundle/index.py`, `bundle/log.py` | Reused | catalog/log surgery |
| `src/openkos/bundle/` (link rewrite) | New | scoped inbound-link repoint pass |
| `docs/knowledge-object-model.md`, `docs/cli.md` | Modified | merge/unmerge docs |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Sensitivity high-water-mark has zero prior code — subtle ordering/unknown-value bugs | Med | Dedicated tests; fail closed to more restrictive side |
| Link-rewrite over-matches substrings across bundle | Med | Anchor on exact `(/id.md)` link form; record every edit for reversal |
| Frontmatter-conflict rule becomes load-bearing convention | Med | Pin explicitly in spec/design; surface conflicts in preview |
| Destructive op with reviewer cognitive load (large 2-file diff) | Med | Multi-unit chain; human merge-to-main checkpoint |
| Ledger snapshot drift making unmerge partial | Low | Embed full snapshot + rewrite list; unmerge validates before restore |

## Rollback Plan

In-product: `unmerge <survivor-id> <absorbed-id>` (two-arg, LIFO-enforced:
reverses only the tail `merged_from` entry, refusing if `absorbed-id` is not
that tail) restores the absorbed object and reverses all rewrites from the
ledger. Repo-level: revert the feature-branch chain; each unit
(sensitivity, merge core, link rewrite, CLI, unmerge) is independently
revertible. No persisted graph store to repair (ephemeral rebuild).

## Dependencies

- Shipped slices 1-2 (`duplicates`/`adjudicate`) — read-only inputs, unchanged.

## Success Criteria

- [ ] `merge <survivor> <absorbed>` produces one survivor: body appended,
      provenance unioned, sensitivity RECOMPUTED, `merged_from` ledger embedded.
- [ ] All inbound `(/absorbed-id.md)` links repointed to survivor; none dangling.
- [ ] `unmerge <survivor-id> <absorbed-id>` (two-arg, LIFO-enforced: reverses
      only the tail entry, refusing a non-tail `absorbed-id`) fully restores
      survivor and absorbed object AND reverses every recorded link rewrite
      (round-trip parity).
- [ ] Confirm-gate preview shows sensitivity outcome + links to be rewritten.
- [ ] `combine_sensitivity` fails closed to the more restrictive side on
      unknown/missing values, with dedicated tests.
