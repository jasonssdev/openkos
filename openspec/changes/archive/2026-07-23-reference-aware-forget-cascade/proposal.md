# Proposal: Reference-Aware Forget — Scope/Depth Cascade (S2b)

## Intent

S2a shipped self-scope reference-aware `forget`, which today **refuses**
`forget <source>` because each derived child renders a `## Related` backlink
that `find_inbound_references` detects. S2b lets a user purge a Source **and its
now-orphaned provenance descendants** in one bounded operation, reinterpreting
those blocking backlinks as cascade **members**. Honest scoping: this is the
smaller provenance-orphan cascade over the depth-1 `provenance` frontmatter
tree — **not** the doc's full right-to-be-forgotten purge (git-history rewrite,
`raw/` removal, derived-index clear), which stays git-recoverable and deferred.

## Scope

### In Scope (S2b only)
- `--scope {self,source}`: `self` = exact S2a behavior (default); `source` = X
  plus concepts whose `provenance` is a subset of the purge set.
- New pure helper `bundle/provenance.py::find_provenance_descendants`:
  reverse-lookup over `provenance` + orphan-after-delete closure.
- Set-difference refuse gate, full-set preview, count confirmation, N tombstones.

### Out of Scope / Non-Goals
- `--depth` knob (provenance tree is depth-1 for pipeline data — vacuous today).
- Deleting `raw/<name>` files; git-history rewrite; derived-index purge.
- Typed-graph descendants (`derived_from`/`produced_by` edges are never emitted).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `forget-command`: **ADD** — Scope Selection (`--scope`); Provenance Descendant
  Resolution (orphan-after-delete: never delete a child with an out-of-set
  provenance source); Set-Difference Refuse Gate (intra-set referrers don't
  block; external + `unverifiable` refs still fail-closed); Full-Set Preview +
  Count Confirmation (`--force` never auto-confirms the count); N-Line Tombstone
  loop; Cascade Write Ordering (all `index.md`/`log.md` edits first, then N
  unlinks last, sorted). **REVISE** — Refuse gate, Inbound Detection preview,
  Log Entry, and Catalog-Before-File ordering to operate over the delete SET.

## Approach

Extend the S2a `forget` Phase-A/confirm/Phase-B shape: resolve X, compute the
descendant set via the new canonical helper (must NOT import `openkos.graph`),
apply set-difference to inbound refs, preview the full set with an explicit
count, confirm, then rewrite catalogs and unlink N files last. Reuse S2a's
`except (OSError, ValueError)` → stderr/exit-1 pattern.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Orphan rule inverted → over-delete multi-source child | Med | Subset invariant + tests |
| Silent scope creep (large source → mass delete) | Med | Mandatory full-set preview + count confirm |
| Intra-set vs external ref confusion | Med | Explicit set-difference on referrer∈set |
| Partial N-delete leaves orphan files | Low | Catalog-first, sorted unlinks last, git-recoverable |
| `unverifiable` fail-open regression on set path | Low | Preserve S2a fail-closed on external refs |

## Rollback Plan

Non-transactional but fully git-recoverable: `git checkout` restores deleted
bundle concepts and `index.md`/`log.md`. `raw/` files are never touched.

## Success Criteria

- [ ] `forget <source> --scope source` deletes X + orphaned descendants only.
- [ ] Multi-source child (out-of-set provenance) is preserved.
- [ ] Confirm prompt states the delete count; `--force` does not skip it.
- [ ] External + unverifiable inbound refs still refuse without `--force`.

## Open Decisions (defer to spec/design)
- Exact full-set preview format and grouping.
- `--scope` CLI wiring / value validation surface.
- `find_provenance_descendants` final signature and module placement.
- (Decided: N individual tombstone lines, not a grouped line.)

## Arc Note
S2b of MVP-3 gap #8. **S3 (sensitivity) is the next HARD GATE** before
cloud/export. This is the most dangerous slice of the arc (irreversible
multi-delete), hence mandatory full-set preview + count confirmation.
