# Delta for LLM Edge Production

## MODIFIED Requirements

### Requirement: Read-Only Suggestion Of Relation Types For Untyped Links

The system MUST provide a CLI verb that reads every existing untyped
body-link edge (source, target, `relation_type = NULL`) from the derived
graph projection and, for each, MUST print an LLM-suggested relation
`type` plus a rationale. The verb MUST perform ZERO writes to any bundle
file, index, or log. Every printed suggested type MUST be a value accepted
by the existing `validate_relation_type` check. The candidate set MUST be
restricted to untyped edges only; edges that already carry a `relation_type`
MUST NOT be listed as suggestion candidates. Because graph projection now
synthesizes `relation_type = "derived_from"` for provenance-mirror edges
(edges whose target is a member of the source document's `provenance:`
frontmatter list), those edges carry a `relation_type` and MUST NOT be
listed as candidates, and MUST NOT trigger an LLM call.
(Previously: the candidate set excluded only edges typed via `relations:`
frontmatter; it now also excludes edges typed by provenance-mirror
projection synthesis, with no code path distinction required since both
sources populate the same `relation_type` field read by this requirement.)

#### Scenario: Verb lists every untyped edge with a valid suggestion

- GIVEN a bundle containing three untyped body-link edges
- WHEN the suggestion verb runs
- THEN it prints all three edges, each with a suggested `type` (a member of
  the relation vocabulary accepted by `validate_relation_type`) and a
  rationale

#### Scenario: Verb performs zero writes

- GIVEN a bundle with untyped body-link edges
- WHEN the suggestion verb runs to completion
- THEN no bundle file, `index.md`, or `log.md` is modified on disk

#### Scenario: Already-typed edges are excluded from suggestions

- GIVEN a bundle where one edge already has a `relation_type` set (via prior
  `relate`) and another edge is untyped
- WHEN the suggestion verb runs
- THEN only the untyped edge appears in the output; the already-typed edge
  is not re-suggested

#### Scenario: Bundle with only provenance-mirror edges surfaces zero candidates

- GIVEN a bundle whose only body links are provenance-mirror edges (every
  link target is a member of its source document's `provenance:`
  frontmatter list, now typed `derived_from` by graph projection)
- WHEN the suggestion verb runs
- THEN it prints zero candidate edges, makes zero LLM calls, and reports
  honestly that there is nothing to type

#### Scenario: A genuine untyped concept-to-concept edge is still surfaced

- GIVEN a bundle containing one provenance-mirror edge (now typed
  `derived_from`) and one genuine untyped concept-to-concept edge whose
  target is not a member of its source's `provenance:` list
- WHEN the suggestion verb runs
- THEN only the genuine untyped edge is printed as a candidate with an
  LLM-suggested type and rationale; the provenance-mirror edge is absent
