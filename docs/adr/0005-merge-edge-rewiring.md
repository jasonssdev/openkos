---
type: Decision
title: "ADR-0005: Merge edge rewiring -- refuse-then-rewire reversal, v2 ledger contract"
description: Replacing merge's refuse-or-warn typed-relation guard with reversible rewiring, and the v2 merged_from ledger field that reversibility requires.
status: Accepted
date: 2026-07-20
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-20T00:00:00Z
sensitivity: public
---

# ADR-0005: Merge edge rewiring -- refuse-then-rewire reversal, v2 ledger contract

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

ADR-0004 deliberately staged typed-relation support in two slices: ship
storage/vocabulary/detection first, and REFUSE (fail-closed, no write) any
`merge` that would orphan a typed edge -- either the absorbed object's own
outbound `relations:`, or another bundle file's (including the survivor's)
inbound relation targeting the absorbed object. That ADR named this staging
explicit and temporary: "a later slice extends [the `merged_from`] ledger
to record and reverse edge rewiring, at which point the guard can be
relaxed from refuse to rewire-and-proceed." This is that later slice
(MVP-2 Slice 2a).

The refuse guard is a real user-facing limitation once a bundle's objects
start acquiring typed edges: any legitimate merge of an edge-bearing
concept is blocked outright, with no path forward except manually
stripping the relation first (destroying information) or waiting for
rewiring. KOM's merge contract (`docs/knowledge-object-model.md:317-328`)
and ADR-0002's no-information-loss / full-reversibility bar for merge both
argue against a permanent block: a merge should succeed and stay
reversible, the same way it already does for scalar/list frontmatter
fields and body content.

Full inbound edge rewiring across the whole bundle -- scanning every other
file, rewriting its `relations:`, and recording enough to reverse that
rewrite byte-exact via `unmerge` -- is a multi-surface change too large for
one reviewable unit. This ADR fixes the durable contract (ledger schema,
guard removal, reversal semantics) up front, in the same PR as the
mandatory atomic pair it enables; the inbound-scan trio and CLI wiring
that consume this contract land in two follow-on PRs on the same feature
branch, before the tracker offers a merge to `main`.

## Decision

**Refuse -> rewire.** `merge` MUST succeed regardless of typed relations on
the absorbed object; it never refuses or blocks on outbound `relations:`
or inbound targeting. This REPLACES ADR-0004's guard: `bundle/merge.py`'s
`RelationConflict` dataclass and `find_relation_conflicts` scan, and the
CLI's Phase-A refuse hook built on them, are DELETED rather than merely
deprecated -- there is no residual refusal path, and no code path may be
silently repurposed as a detector. `merge` performs, atomically with the
guard's removal:

- **OUTBOUND move** -- the absorbed object's own `relations:` entries are
  unioned onto the survivor's, via `okf.merge_relations`. This is computed
  directly inside `okf.build_merged_document` (which gains a `survivor_id`
  parameter for the self-loop check below): `relations:` is excluded from
  the generic list-union branch (which cannot distinguish a dangling
  `target: {absorbed_id}` edge or a resulting self-loop from any other
  list value) and instead handled by this dedicated, provably-correct
  path. This surface is reversible FOR FREE, using the ledger fields
  ADR-0002 already defines (`survivor_before`/`absorbed_snapshot`
  whole-file bytes) -- no schema change needed for outbound alone.
- **INBOUND retarget** -- every OTHER bundle file's `relations:` entry
  targeting the absorbed id is rewritten to target the survivor id
  (`bundle/relations.py`, mirroring the existing `bundle/links.py` inbound
  scan shape). This is the one surface that DOES need a ledger extension,
  below.
- **SELF-LOOP drop** -- any relation whose target becomes the survivor id
  as a RESULT of this merge (a retarget from the absorbed id, or an
  absorbed-side edge that already pointed at the survivor) is dropped, not
  emitted -- a document referencing itself is the same failure mode
  `relate`'s self-id posture already avoids. A pre-existing, unrelated
  self-loop on either side, untouched by this merge, is left exactly as it
  was; this rule only catches loops the merge itself introduces.
- **COLLISION dedupe** -- a retarget or union that would duplicate an
  edge already present (`(target, type)` equality) collapses to one entry.

Drops and dedupes are NEVER silent: they surface in the confirm preview
before any write, and in `merge_relations`'s return value for that
preview and for the ledger to record.

**v2 ledger contract (schema preview -- fields, not yet wired).** The
`merged_from` entry gains one field: `relation_rewrites: list[RelationRewrite]`,
`RelationRewrite = {file, snapshot}` -- for every third-party file whose
`relations:` were retargeted, dropped as a self-loop, or deduped by THIS
merge, the file's FULL verbatim bytes immediately before the merge. This
mirrors `link_rewrites`' bookkeeping role but is deliberately coarser:
`link_rewrites` records structured `{file, old_link, new_link, offset}`
because body-link reversal needs an exact character-offset disambiguator
(two links to the same target can collide in the rewritten text).
`relations:` lives in frontmatter with no such positional ambiguity once
re-emitted through the existing `encode_relations`/`decode_relations`
codec (already sorted by `(target, type)` for determinism) -- an
absolute, whole-file snapshot-and-restore is both sufficient and strictly
simpler than a structured offset record, and composes correctly across
sequential, overlapping merges: each entry snapshots a file exactly as it
was immediately before THAT merge, so reversing the most recent
(LIFO-tail) entry first always restores the file to the exact state the
next-oldest entry's own snapshot expects.

The schema version moves from `openkos.merge_ledger/v1` to
`openkos.merge_ledger/v2`. `plan_merge` (once wired) always writes v2.
`decode_merge_ledger_entry` accepts BOTH: a `v1` entry (no
`relation_rewrites` key) decodes with `relation_rewrites=[]` and unmerges
exactly as it did before this ADR -- a pre-slice-2a merge stays fully
reversible with no migration; a `v2` entry REQUIRES the key, failing
closed (`ValueError`) if it is missing or malformed, same fail-closed
posture every other ledger field already has. `unmerge` restores every
file in `relation_rewrites` byte-exact, re-materializing any dropped
self-loop or deduped edge, alongside the survivor/absorbed/catalog
restore ADR-0002 already guarantees.

One correctness note this ADR records for the wiring that follows: a
third-party file may carry BOTH an inbound body-link and an inbound
relation targeting the absorbed object. `relation_rewrites`' whole-file
snapshot restore subsumes `link_rewrites`' offset-based restore for that
same file -- reversing both would try to substitute at a recorded offset
into bytes the relation snapshot already overwrote wholesale. `unmerge`
therefore MUST skip `reverse_link_rewrites` for any file present in that
merge's `relation_rewrites`; the whole-file snapshot always takes
precedence, and a link-only file still reverses by its recorded offset as
before.

## Consequences

Easier: no legitimate merge is ever permanently blocked by a typed
relation; the outbound fix ships with zero ledger-schema cost, since it
reuses ADR-0002's existing whole-file snapshots; the v2 schema's
whole-file-snapshot choice for relations keeps `bundle/relations.py`
free of the offset-disambiguation complexity `bundle/links.py` needs,
while still composing correctly across overlapping sequential merges
(LIFO tail-first reversal). Harder: the survivor's frontmatter grows
further per merge (more embedded snapshots -- an accepted, already-priced
cost per ADR-0002); a v2-schema entry is strictly LARGER than the
outbound-only bytes it could have used absent inbound retargeting, since
every third-party file touched gets a full copy embedded, not a diff;
`unmerge`'s link/relation precedence rule (skip `reverse_link_rewrites`
for a `relation_rewrites` file) is a new invariant every future writer to
either rewrite list must respect, or byte-parity silently breaks.

## Alternatives considered

- **Keep the refuse guard indefinitely, close the vocabulary instead**:
  rejected -- narrowing the vocabulary does nothing for the actual
  problem (a legitimate merge blocked), and ADR-0004 already rejected a
  closed vocabulary for unrelated reasons.
- **Structured relation-rewrite records with byte offsets** (mirroring
  `LinkRewrite` exactly): rejected -- `relations:` entries are
  order-independent, deterministically re-sorted list items, not
  positional substrings in prose; a whole-file snapshot is simpler,
  equally correct, and already proven by the survivor/absorbed side of
  the SAME ledger.
- **Ship inbound rewiring in the same PR as the outbound fix + guard
  removal**: rejected for reviewability -- the outbound fix and the guard
  removal are one atomic, small, provably-correct unit; the inbound trio
  and its CLI wiring are a second, larger surface with their own test
  matrix (overlapping-LIFO, v1 back-compat, link/relation precedence).
  Splitting keeps each PR reviewable while this ADR's ledger contract
  ensures the split is safe: PR1 never partially breaks reversibility,
  since it does not yet touch the schema version at all.
- **Warn-and-proceed without full rewiring** (silently drop or leave
  dangling): rejected -- identical reasoning to ADR-0004's original
  rejection of this option; a silent orphan is strictly worse than either
  a refusal or a correct rewire, and this project's "never silent"
  posture rules it out regardless of which slice ships it.
