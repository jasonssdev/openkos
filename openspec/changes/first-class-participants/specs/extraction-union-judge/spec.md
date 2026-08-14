# Delta for Extraction Union-Judge

## ADDED Requirements

### Requirement: Judge Re-Admission Set Extended to Person/Organization (Additive Only)

The judge re-admission path (the deterministic step that restores a
candidate the judge dropped, applied AFTER judge selection) MUST use a
distinct set covering `Procedure`, `Person`, and `Organization`. This set
MUST NOT be used at either deletion site (source-title twin-drop or
framing-object drop): those deletion predicates MUST remain scoped to
`Procedure` only, byte-identical to their current behavior. Deletion and
additive re-admission are different consumers and MUST NOT share one
predicate.

#### Scenario: Judge-dropped Person on a meeting-shaped source is re-admitted

- GIVEN a `Person` candidate on a meeting-shaped source that carries a valid
  participant anchor, which the judge's selection dropped
- WHEN judge re-admission runs after selection
- THEN the candidate is added back to the final set, deterministically, not
  via any judge prompt clause

#### Scenario: A Person title-twin of the source is still dropped

- GIVEN a `Person` candidate whose title is a twin of the source title
- WHEN the twin-drop deletion rule runs
- THEN the candidate is dropped; `Person` is NOT exempt from twin-drop, only
  `Procedure` is

#### Scenario: A meeting-titled Person is still dropped by framing removal

- GIVEN a `Person` candidate titled after the meeting itself (a framing
  stub, the shape measured in #522/#533)
- WHEN `_drop_framing_objects` runs
- THEN the candidate is dropped; `Person` is NOT exempt from framing removal

#### Scenario: Procedure behavior is unchanged at all three sites

- GIVEN a `Procedure` candidate that would trigger twin-drop, framing
  removal, or judge re-admission
- WHEN each of the three sites evaluates that candidate
- THEN the outcome is identical to current behavior; only the judge
  re-admission site gained new eligible types

### Requirement: Stub Rejection at Judge Re-Admission

At the judge re-admission step, a `Person` or `Organization` candidate MUST
NOT be re-admitted unless it carries a minimal context anchor beyond its
name: a meeting role, an affiliation, or a relation (for example
`spoke_in`, `member_of`). A name-only candidate is a stub and MUST be
discarded, not re-admitted. This anchor check applies ONLY to the additive
re-admission step, never to the deletion sites.

#### Scenario: Name-only candidate is not re-admitted

- GIVEN a `Person` candidate that the judge dropped, whose only attribute
  is a name, with no role, affiliation, or relation
- WHEN judge re-admission runs
- THEN the candidate remains dropped and does not appear in the final set

#### Scenario: Candidate with a meeting-role anchor is re-admitted

- GIVEN a `Person` candidate that the judge dropped, carrying a meeting
  role (for example "chair") alongside its name
- WHEN judge re-admission runs
- THEN the candidate is added back to the final set

### Requirement: Judge Re-Admission Scoped to Meeting-Shaped Sources

Judge re-admission of `Person`/`Organization` candidates MUST only apply to
transcript/meeting-shaped sources, using the same shape test as
`_MEETING_SHAPED_TITLE_RE`. A non-meeting-shaped source (for example a
technical article that merely mentions a person's name) MUST NOT produce a
judge-re-admitted `Person`/`Organization` candidate.

#### Scenario: Meeting transcript re-admits a judge-dropped participant

- GIVEN a meeting-shaped source and a `Person` candidate that the judge
  dropped and that satisfies the participant-anchor requirement
- WHEN judge re-admission runs
- THEN the candidate is added back to the final set

#### Scenario: Non-meeting source does not re-admit a participant

- GIVEN a technical-article source (not meeting-shaped) and a `Person`
  candidate the judge dropped
- WHEN judge re-admission runs
- THEN the candidate is NOT re-admitted through this path
