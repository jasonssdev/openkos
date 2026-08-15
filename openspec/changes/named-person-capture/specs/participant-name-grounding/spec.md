# Participant Name Grounding Specification

## Purpose

An advisory, report-only signal flagging a proposed `Person`/`Organization`
candidate whose name does not appear in the source text. It never rejects a
candidate on this basis. It is inapplicable to transcript corpora whose
meeting-shaped detection matched only via single- or two-letter speaker
labels, since such sources never state a participant's name in text.

## Requirements

### Requirement: Advisory-Only Name Grounding Signal

WHEN a proposed `Person`/`Organization` candidate's name does not appear in
the source's `source_text`, the system MUST record this as an advisory flag
associated with that candidate. It MUST NOT reject, discard, or otherwise
exclude the candidate from extraction on this basis alone. Promoting this
signal into a rejecting filter is out of scope and requires a separate,
measured change.

#### Scenario: Ungrounded name is flagged, not rejected

- GIVEN a proposed `Person` candidate whose name does not appear anywhere in
  `source_text`
- WHEN grounding runs
- THEN the candidate is retained in the extraction output, and an
  ungrounded-name advisory is recorded against it

#### Scenario: Grounded name produces no advisory

- GIVEN a proposed `Person` candidate whose name appears in `source_text`
- WHEN grounding runs
- THEN no ungrounded-name advisory is recorded for that candidate

### Requirement: Label-Only Corpus Exemption

The grounding check MUST be inapplicable to a source whose meeting-shaped
detection matched solely via `_transcript_shaped_text`'s single- or
two-letter speaker-label path (for example `A:`/`B:` labels). A source
detected as meeting-shaped this way MUST NOT produce an ungrounded-name
advisory for any of its `Person`/`Organization` candidates, because the
source text never states a real name to ground against.

#### Scenario: Label-only source is exempt

- GIVEN a source detected as meeting-shaped solely via single/two-letter
  speaker labels
- WHEN grounding runs on that source's `Person` candidates
- THEN no ungrounded-name advisory is recorded for any candidate on that
  source

#### Scenario: Named-source grounding still applies

- GIVEN a meeting-shaped source detected via title regex or content shape
  other than the label-only speaker-code path
- WHEN grounding runs
- THEN the ungrounded-name advisory applies normally, per the Advisory-Only
  requirement
