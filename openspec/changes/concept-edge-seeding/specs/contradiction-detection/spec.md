# Delta for Contradiction Detection

## MODIFIED Requirements

### Requirement: Empty Graph Yields Clear Message, No Crash

WHEN `contradictions` finds zero candidate pairs, it MUST distinguish
three mutually exclusive states rather than a single generic message: (1)
the graph has no typed edges at all — nothing to work with yet; (2) typed
edges exist but none survive candidate-pair generation (e.g. all excluded
as `derived_from`) — candidates existed but none matched; (3) candidates
are not computable yet because embeddings are missing (`vectors.db` absent
or empty), which additionally starves any embedding-sourced candidate
edges. State 3 MUST use a message distinguishable from state 1. Every
state MUST exit `0`, never crash.
(Previously: any zero-candidate-pairs outcome produced the same single "no
candidate pairs" message regardless of cause.)

#### Scenario: No typed edges at all

- GIVEN a bundle whose graph has no typed edges and `vectors.db` is
  present and populated
- WHEN `contradictions` runs
- THEN it prints a message stating the graph has no typed edges yet,
  distinct from the other two states, and exits `0`

#### Scenario: Typed edges exist but none survive candidate-pair generation

- GIVEN a bundle whose graph has typed edges, all excluded from candidate
  generation (e.g. all `derived_from`)
- WHEN `contradictions` runs
- THEN it prints a message stating no candidate pairs remain after
  filtering, distinct from the empty-graph message, and exits `0`

#### Scenario: Missing embeddings reports not-computable-yet

- GIVEN a bundle whose `vectors.db` is absent or empty
- WHEN `contradictions` runs
- THEN it prints a message stating candidates are not computable yet due
  to missing embeddings, distinct from both other messages, and exits `0`
