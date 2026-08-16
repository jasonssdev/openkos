# Participant Name Grounding Specification

## Purpose

An advisory, report-only signal flagging a proposed `Person`/`Organization`
candidate whose name does not appear in the source text. It never rejects a
candidate on this basis. It is inapplicable to label-only transcript
corpora — those whose speaker labels are predominantly one- or two-letter
codes — since such sources never state a participant's name in text.

## Requirements

### Requirement: Advisory-Only Name Grounding Signal

WHEN a proposed `Person`/`Organization` candidate's name does not appear in
the source's `source_text`, the system MUST record this as an advisory flag
associated with that candidate. It MUST NOT reject, discard, or otherwise
exclude the candidate from extraction on this basis alone. Promoting this
signal into a rejecting filter is out of scope and requires a separate,
measured change.

The name MUST be matched on word boundaries, not as a raw substring: a
substring test grounds `Ana` in `mañana` and `Vega` in `Vegas`, un-firing
the advisory precisely where a short fabricated name is most likely. The
boundary MUST still admit a name written beside a colon, comma or
parenthesis, which is how a transcript writes one. Only `Person`/
`Organization` titles are checked; other types are synthesized rather than
copied from the source, so grounding them would flag every correct object.

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

The grounding check MUST be skipped entirely on a label-only source: one
where at least HALF of the DISTINCT speaker labels found in `source_text`
are at most two characters, as in AMI's `A:`/`B:`/`C:`. Such a source MUST
NOT produce an ungrounded-name advisory for any of its
`Person`/`Organization` candidates, because the text never states a real
name to ground against and every candidate would be flagged by
construction.

The exemption MUST be decided from the source's own labels, independently of
HOW the source was detected as meeting-shaped (title or content shape). It
MUST be a half-majority over DISTINCT labels rather than a rule requiring
every label to be short: a transcript carrying one longer label such as
`Presenter:` alongside its speaker codes still does not state its
participants' real names, and requiring unanimity lets that single line
disable the exemption for an entire transcript.

#### Scenario: Label-only source is exempt

- GIVEN a source whose distinct speaker labels are `A`, `B` and `C`
- WHEN grounding runs on that source's `Person` candidates
- THEN no ungrounded-name advisory is recorded for any candidate on that
  source

#### Scenario: One longer label does not defeat the exemption

- GIVEN a source whose distinct speaker labels are `A`, `B`, `C` and
  `Presenter`
- WHEN grounding runs
- THEN the source is still exempt, because at least half of the distinct
  labels are at most two characters

#### Scenario: Named-source grounding still applies

- GIVEN a source whose speaker labels are predominantly real names, so that
  fewer than half of the distinct labels are at most two characters
- WHEN grounding runs
- THEN the ungrounded-name advisory applies normally, per the Advisory-Only
  requirement
