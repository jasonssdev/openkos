---
type: Decision
title: "ADR-0011: Third-party provenance retargets on merge; v3 reversibility ledger"
description: Merge rewrites inbound provenance to the survivor and records it in a v3 merged_from entry.
status: Proposed
date: 2026-07-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-29T00:00:00Z
sensitivity: public
---

# ADR-0011: Third-party provenance retargets on merge; v3 reversibility ledger

- **Status:** Proposed
- **Date:** 2026-07-29

## Context

`merge <survivor> <absorbed>` retargets every inbound pointer kind except one.
Body markdown links are rewritten (ADR-0002), typed `relations:` are rewritten
(ADR-0005), but a third party's `provenance:` list keeps naming the absorbed
id — an id the merge has just made non-addressable, because the absorbed file
is deleted. Nothing repairs it and nothing fails; `set-sensitivity` prints one
stderr WARNING and continues.

The forces pull in opposite directions.

Against retargeting: `provenance` is the record of a historical fact — "this
object was derived from concept X." AGENTS.md lists provenance/freshness as a
non-negotiable. Rewriting `sources/absorbed` to `sources/survivor` asserts
something that is not literally true: the object was derived from what is now
*folded into* the survivor, not from the survivor's own original content.
Overwriting a historical fact to satisfy referential integrity is exactly the
kind of quiet lie a knowledge engine should not tell.

For retargeting: a pointer to a deleted, non-addressable id is not a better
record of that fact — it is a broken one, and it breaks two live behaviours.
`find_provenance_descendants` (the `forget --scope source` purge closure)
under-expands, so a `forget` leaves orphans behind. `set-sensitivity`'s
raise-propagation (ADR-0009) skips those descendants, so a Source raised to
`confidential` fails to reach objects genuinely derived from it. That is a
confidentiality leak, not a cosmetic dangling reference. Merge already
establishes the precedent that the absorbed id ceases to exist as an
addressable entity; the `merged_from` ledger plus `unmerge` — not an orphaned
pointer — is the audit trail that recovers pre-merge history.

The ledger is the second force. ADR-0002 made `merged_from` a **durable
on-disk contract that later changes must migrate**; ADR-0005 already spent one
version (v1 → v2) to add `relation_rewrites`. Recording provenance snapshots
costs another (v2 → v3), and a ledger entry written by v3 code outlives the
code that wrote it: `decode_merge_ledger_entry` branches on a closed set of
schema strings and rejects anything else outright. A revert of the code does
not revert the artifacts already in users' bundles.

## Decision

We **retarget third-party inbound `provenance:` entries to the survivor** as
part of `merge`, and record enough to reverse it exactly.

The rewrite is retarget-then-dedupe, never a blind substring replace: each
entry naming the absorbed id becomes the survivor id in place, and a list that
named both ids collapses to a single survivor entry at the **earlier** of the
two positions, every other entry keeping its relative order. This mirrors
`build_merged_document`'s established union/dedupe rule rather than inventing a
second one.

The scan is **not gated on the absorbed object's `type`**. `query --save`
writes `provenance=[cited concept ids]`, so any concept — Source or not — can
legitimately be another object's provenance. The gate belongs to sensitivity
propagation semantics (ADR-0009), not to reference-integrity rewriting.

We bump the reversibility ledger to `MERGE_LEDGER_SCHEMA_V3`, adding a required
`provenance_rewrites` key holding one whole-file pre-merge snapshot per
rewritten third party — the same shape `relation_rewrites` uses, because a
`provenance:` list has no stable positional disambiguator analogous to a link
occurrence. `plan_merge` always writes v3; the reader still accepts v1 and v2,
which decode with `provenance_rewrites=[]`. `unmerge` reverses provenance by
drift-checked absolute whole-file restore, and when one file carries several
rewrite kinds the precedence is **provenance > relations > links**: the
whole-file snapshot with the widest coverage wins and the narrower reversals are
skipped.

The historical fact is not destroyed. It is relocated from a broken pointer
into the ledger, where `unmerge` restores it byte-for-byte.

## Consequences

Easier: `forget --scope source` expands correctly across merged sources; an
ADR-0009 sensitivity raise on the survivor now reaches every object genuinely
derived from the absorbed concept (intended, and a real confidentiality fix);
`merge` becomes uniform — every inbound pointer kind is retargeted and every
retarget is reversible; the three-kind precedence rule generalises D5 instead of
adding a special case.

Harder: `provenance` is no longer a naively literal derivation record — reading
pre-merge history now requires the ledger, so tooling that treats `provenance`
as immutable history must consult `merged_from`. The survivor's frontmatter
grows again, now by up to three whole-file snapshots per third party (accepted;
same trade ADR-0002 already made). Most consequentially, **rollback is no longer
purely additive**: a v3 entry written before a revert is unreadable by restored
v2 code, which fails closed with `unsupported merged_from schema version`. The
blast radius is bounded — only `plan_merge` and `plan_unmerge` decode the ledger
— but for an affected survivor both are refused. The supported recovery is to
`unmerge` affected pairs *before* reverting; after the fact, the entry's
`schema` must be hand-edited back to v2 and `provenance_rewrites` dropped, with
the provenance retarget then reversed by hand.

## Alternatives considered

- **Leave provenance dangling; add a `lint` check for it.** Detection is not
  repair. The defect is broken propagation, not invisibility, and a lint rule
  fixes neither `forget` nor `set-sensitivity`. Kept as a follow-up.
- **Resolve absorbed ids lazily through `merged_from` at read time.** Preserves
  the literal historical fact, but every consumer of `provenance` would need
  merge-awareness, and the resolution cost recurs forever on every read. Pushes
  merge's cost into unrelated code paths permanently.
- **Gate the rewrite on the absorbed object being a Source.** Rejected on
  evidence: `query --save` files arbitrary cited concept ids as provenance, so
  the gate would silently miss real orphans.
- **Reuse `bundle/references.py`.** Rejected: it is documented and consumed as
  detect-only by `forget`; adding write/reverse mechanics there misplaces
  merge-only machinery and blurs another verb's contract.
- **Additive v2 with an optional key instead of a v3 bump.** Would keep rollback
  clean, but an optional snapshot key is indistinguishable from a genuinely
  absent one, so `unmerge` could not tell "no provenance rewrites happened" from
  "written by a reader that dropped them" — precisely the silent misread
  ADR-0002's fail-closed decode exists to prevent.
