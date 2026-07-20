---
type: Decision
title: "ADR-0004: Typed relationships in frontmatter; guard-then-rewire staging"
description: Where typed edges are stored, how open the relation-type vocabulary is, and how merge is protected until rewiring lands.
status: Accepted
date: 2026-07-20
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-20T00:00:00Z
sensitivity: public
---

# ADR-0004: Typed relationships in frontmatter; guard-then-rewire staging

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

KOM (`docs/knowledge-object-model.md:222`, roadmap `docs/roadmap.md:64`) calls
for typed relationships between objects (`references`, `depends_on`, and
similar), so far unimplemented: the engine only extracts UNTYPED
`[text](/id.md)` body links, and `graph/sqlite_graph.py::Edge.relation_type`
is a column reserved but always `NULL`. Two open questions block a first
slice: WHERE a typed edge is durably stored (a new reserved file/section, or
existing frontmatter — KOM:222 vs. the lighter-weight roadmap:64 sketch), and
whether the relation-type vocabulary is closed (like
`model/types.py::CLASSIFIABLE_TYPES`) or open. A third question is forced by
this slice's own scope boundary: `merge` (ADR-0002) is reversible for
scalar/list frontmatter fields and body content via the `merged_from`
snapshot ledger, but does not yet know about typed edges — merging an object
that bears or is targeted by a `relations:` entry would either orphan that
edge silently or require full edge rewiring through merge/unmerge, which is
a second slice's worth of ledger-extension work.

## Decision

**Storage.** We store typed edges as a first-class `relations:` list directly
in the OKF frontmatter of the object the edge originates FROM — an ordinary
OKF data key, not a new reserved file or object type, per §4.1 tolerance
(the same pattern `merged_from`, ADR-0002, already established). Each entry
is `{target, type}`: `target` is a bundle-relative concept-id with any `.md`
suffix stripped, byte-identical in shape to how `provenance`
(`sources/<slug>`) and `MergeLedgerEntry.absorbed_id` already reference
objects — never a `/...md` link, never a bare slug. `target`/`type` MUST be
non-empty after stripping and MUST NOT contain `\n`/`\r`, mirroring the
existing index/log newline-injection guards. Entries are re-emitted SORTED
by `(target, type)` on every write for deterministic output and stable
dedup. This resolves KOM:222 (frontmatter storage) over the lighter
roadmap:64 sketch: frontmatter is durable, versioned alongside the rest of
the object, round-trips through the existing `dump_frontmatter`/
`load_frontmatter` seam with zero new parsing surface, and needs no new
reserved-file rules in §9.

**Vocabulary.** The relation-type vocabulary is seeded but open, mirroring
KOM's own open-vocabulary intent rather than `types.py::CLASSIFIABLE_TYPES`'s
closed set: `model/relations.py::SEEDED_RELATION_TYPES` lists 8 defaults
(`references`, `depends_on`, `derived_from`, `related_to`, `caused_by`,
`part_of`, `member_of`, `produced_by`). Any other non-empty, single-line
type is still accepted for write — `validate_relation_type` only prints an
advisory note to stderr for an unrecognized type, it never rejects one. The
sole hard fail-closed gate is an empty or whitespace-only type, which is
always rejected with no write.

**Merge guard staging.** `merge`'s Phase A REFUSES (fails closed), rather
than warning and proceeding, when the absorbed object bears its own outbound
`relations:` entries or is the target of an inbound typed relation from
another bundle file. This slice ships detection only
(`bundle/merge.py::find_relation_conflicts`); it does not attempt any
rewiring of typed edges through merge/unmerge. Refuse-then-rewire is a
deliberate two-slice staging: refusing now is trivially reversible (nothing
was written) and keeps every prior reversibility guarantee (ADR-0002) intact
without extending the `merged_from` ledger schema in this slice; a later
slice extends that ledger to record and reverse edge rewiring, at which
point the guard can be relaxed from refuse to rewire-and-proceed.

## Consequences

Easier: no new file type, no new §9 reserved-file rules, no new parser — the
existing frontmatter round-trip absorbs typed edges for free; the open
vocabulary needs no migration when a project's relation-type needs outgrow
the seeded 8; the merge guard is a pure, unit-testable scan
(`find_relation_conflicts`) with no interaction with the snapshot ledger.
Harder: `merge`/`unmerge` on an edge-bearing object is BLOCKED until slice 2
ships rewiring — a real user-facing limitation this ADR accepts explicitly,
not a silent gap; the open vocabulary means `relations:` `type` values are
not validated against a closed set, so typos in an uncommon type silently
create a new de facto type (mitigated only by the stderr advisory, not
rejection); `check_conformance`'s new additive rule must be proven
byte-identical for documents without a `relations:` key, a regression this
slice guards with a dedicated test.

## Alternatives considered

- **New reserved `relations.md` file per bundle**: rejected — a new
  reserved-file type needs new §9 structural rules (mirroring `index.md`/
  `log.md`'s §6/§7 treatment) for a single list that already fits an
  existing OKF data key; also splits an object's edges from the object
  itself, unlike `provenance`/`merged_from`'s co-located precedent.
- **Closed relation-type vocabulary** (like `CLASSIFIABLE_TYPES`): rejected
  — KOM's relationship examples are illustrative, not exhaustive, and a
  closed set would force a vocabulary-registry change for every new project-
  specific relation type; the classifier's closed 9-type set exists because
  an LLM must be constrained, but `relate` is deterministic, user-directed
  input with no LLM in this slice.
- **Merge warns and proceeds, orphaning the edge**: rejected — proceeding
  silently corrupts the graph (a typed edge whose endpoint just vanished,
  with no rewiring performed) in tension with the project's "never silent"
  posture and ADR-0002's fail-closed precedent; a refusal is trivially
  reversible (nothing was written), a silent orphan is not.
- **Ship full reversible rewiring in this same slice**: rejected — rewiring
  typed edges through merge/unmerge requires extending the `merged_from`
  ledger schema (ADR-0002) with edge-rewrite bookkeeping symmetric to
  `link_rewrites`, a second slice's worth of design and testing; staging the
  guard first keeps this slice's diff small and reviewable while never
  regressing merge's existing reversibility guarantee.
