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
above. Volume stays bounded by `_UNION_BACKSTOP` applied to the whole
retained set, exactly as before this change. A separate participant budget
lane was specified for slice 3 and closed unshipped: measurement found the
backstop has never bound, so the lane would bound nothing. See
`STATUS.md`.)
