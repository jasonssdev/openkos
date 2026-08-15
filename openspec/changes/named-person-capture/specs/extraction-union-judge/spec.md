# Delta for Extraction Union-Judge

## MODIFIED Requirements

### Requirement: Judge Re-Admission Set Extended to Person/Organization (Additive Only)

The judge re-admission path (the deterministic step that restores a
candidate the judge dropped, applied AFTER judge selection) MUST use a
distinct set covering `Procedure`, `Person`, and `Organization`. This set
MUST NOT be used at either deletion site (source-title twin-drop or
framing-object drop): those deletion predicates MUST remain scoped to
`Procedure` only, byte-identical to their current behavior. Deletion and
additive re-admission are different consumers and MUST NOT share one
predicate.

(Previously: the "Judge-dropped Person on a meeting-shaped source is
re-admitted" scenario required the candidate to carry a valid participant
anchor. The anchor gate is reversed by owner ruling #712 — re-admission no
longer requires one.)

#### Scenario: Judge-dropped Person on a meeting-shaped source is re-admitted

- GIVEN a `Person` candidate on a meeting-shaped source, which the judge's
  selection dropped
- WHEN judge re-admission runs after selection
- THEN the candidate is added back to the final set, deterministically, not
  via any judge prompt clause, and no context-anchor check gates this
  addition

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

### Requirement: Judge Re-Admission Scoped to Meeting-Shaped Sources

Judge re-admission of `Person`/`Organization` candidates MUST only apply to
transcript/meeting-shaped sources, using the same shape test as
`_is_meeting_shaped` — the source's TITLE or its CONTENT shape. A
non-meeting-shaped source (for example a technical article that merely
mentions a person's name, with no speaker-turn structure) MUST NOT produce
a judge-re-admitted `Person`/`Organization` candidate.

(Previously: the meeting-transcript scenario described the dropped
candidate as satisfying "the participant-anchor requirement." That
requirement no longer exists; re-admission on a meeting-shaped source is
unconditional on anchor content.)

(Also corrected here: the requirement named `_MEETING_SHAPED_TITLE_RE` as
the shape test. That has been stale since #673, which made the predicate
`_is_meeting_shaped` — title OR content shape — precisely so a code-titled
transcript would still be recognised. The requirement understated the
shipped behavior. Corrected in this delta because this change already
modifies this requirement; carrying the stale name forward would re-merge
it at archive.)

#### Scenario: Meeting transcript re-admits a judge-dropped participant

- GIVEN a meeting-shaped source and a `Person` candidate that the judge
  dropped
- WHEN judge re-admission runs
- THEN the candidate is added back to the final set

#### Scenario: Non-meeting source does not re-admit a participant

- GIVEN a technical-article source (not meeting-shaped) and a `Person`
  candidate the judge dropped
- WHEN judge re-admission runs
- THEN the candidate is NOT re-admitted through this path

## REMOVED Requirements

### Requirement: Stub Rejection at Judge Re-Admission

(Reason: owner ruling #712 reverses this rule — a person who is only named,
who spoke once, or who spoke minimally, MUST still become a `Person`. The
underlying `_has_participant_anchor` gate discarded zero candidates in
measurement (#706): the actual suppression was two prompt instructions
outside this deterministic step, not this gate. Requiring a role,
affiliation, or relation beyond a bare name before re-admission is
incompatible with "always identified.")
(Migration: `Person`/`Organization` re-admission on a meeting-shaped source
is now unconditional on anchor content — see the MODIFIED requirements
above. Volume is bounded instead by the new participant budget lane below,
not by rejecting name-only candidates.)

## ADDED Requirements

### Requirement: Participant Budget Lane Separate From the Subject Backstop

`Person`/`Organization` candidates re-admitted via judge re-admission MUST
be bounded by a participant-lane capacity that is separate from
`_UNION_BACKSTOP`. `_UNION_BACKSTOP` MUST remain the ceiling for
subject-typed candidates (`Concept`, `Entity`, `Place`, `Event`,
`Procedure`, `Decision`, `Project`) only. A `Person`/`Organization`
candidate MUST NOT consume subject-lane capacity, and a subject-typed
candidate MUST NOT consume participant-lane capacity. This requirement does
not fix the participant lane's numeric capacity or its truncation
ordering; both are set by measurement in a later slice of this change.

#### Scenario: Participant lane does not compete with the subject backstop

- GIVEN a merged candidate set where the subject backstop is already at
  capacity, and one or more `Person`/`Organization` candidates await
  re-admission
- WHEN both lanes are applied
- THEN the `Person`/`Organization` candidates are bounded independently of
  the subject backstop, and their presence does not reduce the number of
  subject-typed candidates retained

#### Scenario: Participant lane bounds Person/Organization volume

- GIVEN more `Person`/`Organization` candidates eligible for re-admission
  than the participant lane's capacity
- WHEN the participant lane's truncation runs
- THEN no more than the lane's capacity of `Person`/`Organization`
  candidates are retained, and the truncated candidates are recorded on the
  `ExtractionReport` distinctly from subject-lane truncation
