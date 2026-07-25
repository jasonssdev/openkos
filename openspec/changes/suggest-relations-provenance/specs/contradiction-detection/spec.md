# Delta for Contradiction Detection

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
(Previously: candidate generation admitted any edge with a non-`None`
`relation_type`, with no type-specific exclusion; this adds an explicit
`derived_from` exclusion so provenance-mirror edges — now typed
`derived_from` by graph projection instead of remaining `None` — do not
newly become contradiction candidates.)

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
  edge (not `derived_from`)
- WHEN `find_contradictions` runs
- THEN that pair is included in the candidate set and judged, confirming
  the exclusion applies only to `derived_from`, not to all typed edges
