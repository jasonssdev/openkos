---
type: Decision
title: "ADR-0009: Source sensitivity propagates to provenance descendants, raise-only"
description: set-sensitivity on a Source raises its provenance descendants via combine_sensitivity; ADR-0008's downgrade gate is unchanged.
status: Accepted
date: 2026-07-28
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-28T00:00:00Z
sensitivity: public
---

# ADR-0009: Source sensitivity propagates to provenance descendants, raise-only

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

ADR-0008 recorded `set-sensitivity` as touching exactly one concept — "no
sibling and no derived object" — and rejected `combine_sensitivity` for the
verb, reasoning that a human's explicit assignment must not be folded through
a monotonic combine. That rejection was scoped to the human-assigned target
value, but the *scope sentence itself* was written as if it were absolute.

Issue #219 shows the gap that leaves open: the ingestion spec
(`openspec/specs/ingestion/spec.md:414-427`) already claims a derived object's
`sensitivity` is "inherited" from its Source, but no code path backs that
claim at the moment a Source is corrected after the fact. Raising a Source's
`sensitivity` today leaves every derived object at its old value, still
readable by every `llm.chat` gate — all of which resolve sensitivity by
reading each document's own stored field
(`sensitivity-aware-llm` Requirement 1). A privacy correction on the Source
silently fails to protect what it derived.

## Superseded, precisely

This ADR supersedes **only** ADR-0008's scope sentence — the claim that the
write set is the single named concept. ADR-0008's downgrade gate, its
`--allow-downgrade` contract, and `okf.sensitivity_direction`'s fail-closed
ranking remain in force verbatim: an explicit human assignment on the named
concept itself may still lower that concept's own value, gated exactly as
ADR-0008 describes. **ADR-0008 is not edited.**

## Decision

We adopt raise-only propagation from a Source-typed concept to its
provenance descendants, computed via `okf.combine_sensitivity` (ADR-0003).

The write set for `set-sensitivity <concept-id> <level>` becomes: the named
concept, plus — when `<concept-id>` resolves to a `type: Source` concept AND
the Source's own assignment is itself a raise — every concept reachable via
`bundle.provenance.find_provenance_descendants` starting from it. For each
descendant, the new value is
`okf.combine_sensitivity(descendant_current, level)`; a write is staged only
when that is a strict raise over the descendant's current value. ADR-0008's
rejection of `combine_sensitivity` is preserved for the *human-assigned*
target value — this ADR applies `combine_sensitivity` only to the
*machine-derived* descendant values, which is exactly ADR-0003's own domain,
not the case ADR-0008 declined.

Raise-only needs one direction special-case, not zero: the whole-bundle scan
runs only when the Source's OWN assignment is itself a raise
(`okf.sensitivity_direction(current, level) == "raise"`). Combine alone does
not guarantee an empty staged set on a downgrade — `combine_sensitivity
(descendant_current, level)` can still return a strict raise for a
descendant already sitting *below* the new, lower `level` (e.g. a Source
going `confidential` -> `private` with a `public` descendant). Gating the
scan on the Source's own direction keeps a downgrade run's write set to
exactly the named concept, matching "Cascading downgrades" rejected below. A
provenance reference that cannot be resolved to an existing concept in the
bundle snapshot emits a stderr warning naming it and is excluded from
propagation — fail closed, never treated as absent, never lowered. Every
staged descendant raise is reported: it appears in the confirmation preview
and the post-write success message, and `--auto` still performs the
propagation, only skipping the prompt. Phase B writes every staged
descendant, then the named concept, then `log.md`, then issues one
auto-commit covering every changed path — descendants land first so a
mid-way failure leaves the bundle over-classified, never under-classified.

## Consequences

Easier: the ingestion spec's inheritance claim becomes backed by code; a
privacy correction on a Source is one verb instead of a manual walk of every
derived object. `set-sensitivity` becomes the single place both the
creation-time and set-time write-through rules are documented together.

Harder: `set-sensitivity` on a Source becomes a multi-file write and a
full-bundle read, where before it touched one file. Descendants of a Source
that is never re-set stay stale until the next correction — no bulk backfill
ships with this decision. A derived object whose `provenance` was orphaned
by an absorbing `merge` (which rewrites `relations:` but not `provenance:`)
silently falls outside the closure; today that only produces a warning, not
a repair — tracked as a separate follow-up issue. Multi-source high-water-mark
combination (a derived object citing more than one Source) stays deferred per
the ingestion spec's existing MVP-2/3 non-goal; `find_provenance_descendants`'s
conservative full-provenance-subset rule already excludes that case rather
than combining it incorrectly.

## Alternatives considered

- **Read-time computed sensitivity.** Rejected: rewrites
  `sensitivity-aware-llm` Requirement 1 ("resolve from its own stored
  field"), touches roughly 8 requirements and 6 call sites, adds a
  provenance walk to every gate check, and leaves the on-disk file still
  mislabeled for git, grep, and a human reading the bundle directly.
- **Creation-time propagation only (no set-time write-through).** Rejected:
  that only fixes new ingests; it leaves every already-ingested Source's
  correction as silent as it is today — exactly the bug #219 reports.
- **Cascading downgrades.** Rejected: a Source correction that also silently
  declassified its derived objects would be the opposite failure mode — an
  unreviewed loss of protection instead of an unreviewed gain of exposure.
- **A new reverse index for the provenance closure.** Rejected:
  `bundle.provenance.find_provenance_descendants` already computes exactly
  this closure for `forget --scope source`; reusing it costs one full-bundle
  read on a rare human verb, not a new durable structure to keep in sync.
