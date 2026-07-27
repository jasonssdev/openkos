# Delta for LLM Edge Production

## ADDED Requirements

### Requirement: Three-State Empty-Result Messaging For `suggest-relations`

WHEN `suggest-relations` finds zero candidate edges, it MUST distinguish
four mutually exclusive states rather than a single generic message: (1)
the graph has no concept-to-concept edges at all — nothing to work with
yet; (2) concept-to-concept edges exist and none of them are
untyped/unclaimed; (2b) untyped concept-to-concept edges DO still exist,
but every one of them was excluded by pair-level or confidentiality
filtering; (3) candidates are not computable yet because embeddings are
missing (`vectors.db` absent or empty). State 3 MUST use a message
distinguishable from state 1: an absent/empty embedding index MUST NOT be
reported as "no edges."

State 2's message MUST NOT be emitted when untyped rows remain in the
graph projection. Reporting "none are untyped" is a factual claim about
the projection, so it MUST be selected from a count that actually measures
untyped rows — not from a raw row total that pair-level and
confidentiality filtering never touched. State 2b MUST report how many
untyped rows exist and MUST state that they were excluded rather than
absent.

#### Scenario: Empty graph reports nothing-to-work-with

- GIVEN a bundle whose graph projection has zero concept-to-concept edges
  and `vectors.db` is present and populated
- WHEN `suggest-relations` runs
- THEN it prints a message stating the graph has no concept-to-concept
  edges yet, distinct from both other states, and exits 0

#### Scenario: Every edge is typed

- GIVEN a bundle whose graph projection has concept-to-concept edges and
  every one of them carries a `relation_type`
- WHEN `suggest-relations` runs
- THEN it prints a message stating no untyped candidates remain, distinct
  from the empty-graph message, and exits 0

#### Scenario: Untyped edges exist but every one was excluded

- GIVEN a bundle whose graph projection still holds at least one untyped
  concept-to-concept row whose pair is already typed by a separate
  `relations:` row (the state `relate` leaves behind, since it never
  removes the original untyped body-link row)
- WHEN `suggest-relations` runs
- THEN it prints a message reporting how many untyped rows exist and
  stating they were excluded as already-typed-elsewhere or confidential,
  and it MUST NOT claim that none are untyped, and exits 0

#### Scenario: Missing embeddings reports not-computable-yet

- GIVEN a bundle whose `vectors.db` is absent or empty
- WHEN `suggest-relations` runs
- THEN it prints a message stating candidates are not computable yet due
  to missing embeddings, distinct from both other messages, and exits 0
