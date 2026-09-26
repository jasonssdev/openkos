# Delta for Contradiction Detection

## Non-Goals Correction (for archive)

The main spec's Non-Goals paragraph currently ends: "...or a seeded
`contradicts` relation type (all typed edges are candidates)." That
parenthetical is no longer accurate once this delta merges: `derived_from`
edges are already excluded (prior behavior), and this delta additionally
excludes any pair joined by a resolution edge (`supersedes`,
`reconciled_with`, or `revises`). At archive, narrow the parenthetical to
something like: "...(excluding `derived_from` edges and any pair joined by
a resolution edge — `supersedes`, `reconciled_with`, or `revises`)."

## MODIFIED Requirements

### Requirement: Candidate Generation From Typed Graph Edges, Deduped

`find_contradictions(bundle_dir, *, llm)` MUST derive candidate pairs only
from typed graph edges (`relation_type is not None`) via `build_graph`.
Each unordered pair MUST be deduped by `frozenset({source_id, target_id})`
so symmetric, duplicate, and multi-edge pairs are judged exactly once.
Candidate generation MUST NOT surface edges whose `relation_type ==
"derived_from"`: a `derived_from` relationship is a derivation/provenance
link, never a contradiction candidate. This exclusion applies to EVERY
`derived_from` edge regardless of origin — both graph-projection-synthesized
provenance-mirror edges and any hand-authored `derived_from` entry in
`relations:` frontmatter — since candidate generation has no signal to
distinguish the two, and a derivation is never a contradiction candidate
either way.

Candidate generation MUST also exclude every pair joined, in either
direction, by a `supersedes`, `reconciled_with`, or `revises` typed edge —
the three resolution relation types written by `reconcile`. This exclusion
MUST be applied before the pair is counted or checked against the cap, so
the `curate` cost gate's reported count and the actual spend stay exact.
Once a pair carries one of these resolution edges, it is not re-judged,
even after a later edit to either concept's content — removing the
resolution edge is what restores the pair to candidacy.
(Previously: candidate generation admitted any edge with a non-`None`
`relation_type`, with no type-specific exclusion; then, in an earlier
delta, an explicit `derived_from` exclusion was added so provenance-mirror
edges — typed `derived_from` by graph projection instead of remaining
`None` — do not become contradiction candidates. This delta widens the
exclusion further to cover the three resolution relation types, applied
before the count and the cap. This is a deliberate change to shipped
behavior, confirmed by the project owner: a pair joined by
`reconciled_with`, `supersedes`, or `revises` is no longer offered as a
contradiction candidate, even after a later edit to either concept — to
re-judge such a pair, the resolution edge must be removed.)

#### Scenario: Symmetric and multi-edge pairs judged once

- GIVEN two concepts connected by both `A --relation1--> B` and
  `B --relation2--> A`
- WHEN `find_contradictions` runs
- THEN exactly one judgment is produced for the pair, not two

#### Scenario: Provenance-only bundle yields zero contradiction candidates

- GIVEN a bundle whose only typed edges are provenance-mirror edges typed
  `derived_from` by graph projection (concept-to-source links backed by
  `provenance:` frontmatter membership)
- WHEN `find_contradictions` runs
- THEN zero candidate pairs are generated, no concept-to-source pair is
  judged, and no LLM call is made — matching prior behavior when those rows
  were untyped and already excluded

#### Scenario: Genuine typed contradiction-eligible edge is still surfaced

- GIVEN a bundle with two event concepts connected by a `related_to`-typed
  edge (not `derived_from`, `supersedes`, `reconciled_with`, or `revises`)
- WHEN `find_contradictions` runs
- THEN that pair is included in the candidate set and judged, confirming
  the exclusion applies only to `derived_from` and the three resolution
  types, not to all typed edges

#### Scenario: A resolved pair is excluded from candidates, before the count and the cap

- GIVEN two concepts joined by a `reconciled_with` edge — or, in separate
  cases, a `supersedes` edge or a `revises` edge — in either direction
- WHEN `find_contradictions` runs
- THEN no candidate pair includes that pair, and the exclusion is applied
  before the total candidate count and the cap are computed, in every case

#### Scenario: Resolution exclusion applies regardless of which member holds the edge

- GIVEN concept A holds an outbound `supersedes` (or `revises`) edge
  targeting concept B
- WHEN `find_contradictions` runs
- THEN the A/B pair is excluded from candidates; the same holds if the
  edge instead runs from B to A

#### Scenario: An unrelated live pair is still judged

- GIVEN a bundle with one resolved pair (joined by any of the three
  resolution types) and one unrelated pair connected only by an ordinary
  typed edge
- WHEN `find_contradictions` runs
- THEN the resolved pair is excluded from candidates, and the unrelated
  pair is still generated as a candidate and judged

#### Scenario: The exclusion keeps the cost gate exact

- GIVEN a bundle whose deduped candidate count would exceed the cap only
  if resolved pairs were counted
- WHEN `find_contradictions` computes `total_count` and applies the cap
- THEN resolved pairs are excluded before that computation, so the
  reported count and the cap match exactly the pairs actually judged
