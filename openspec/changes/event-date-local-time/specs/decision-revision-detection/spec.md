# Delta for decision-revision-detection

## MODIFIED Requirements

### Requirement: Decision Event Date Resolution

The system MUST provide a helper that resolves a Decision's event date by
walking `bundle/provenance.provenance_source_ancestors` to the Decision's
reached Sources and reading each one's `event_date` (a calendar date or a naive local
date-time) via `okf.read_event_date`.
The helper MUST return exactly one of: a single resolved date (every reached
Source agrees, or only one Source is reached and it carries a valid date);
`missing` (any reached Source has no `event_date` or a malformed one);
`multiple` (the reached Sources carry more than one distinct valid value, compared
after parsing, so a date and a date-time on the same day are distinct);
or `none-reached` (provenance resolves to no Source at all).

#### Scenario: A single-source Decision resolves one date

- GIVEN a Decision whose provenance reaches exactly one Source with a valid
  `event_date`
- WHEN the helper resolves its date
- THEN it returns that single date

#### Scenario: A Source with no event_date resolves as missing

- GIVEN a Decision whose provenance reaches a Source with no `event_date`
- WHEN the helper resolves its date
- THEN it returns `missing`

#### Scenario: Provenance reaching multiple distinct dates resolves as multiple

- GIVEN a merged Decision whose provenance reaches two Sources with two
  different valid `event_date` values
- WHEN the helper resolves its date
- THEN it returns `multiple`

#### Scenario: No reachable Source resolves as none-reached

- GIVEN a Decision whose provenance resolves to no Source
- WHEN the helper resolves its date
- THEN it returns `none-reached`

#### Scenario: A date-time resolves as a single value

- GIVEN a Decision whose provenance reaches exactly one Source with
  `event_date: '2026-07-14T10:00'`
- WHEN the helper resolves its date
- THEN it returns that single date-time

#### Scenario: Two times on one day resolve as multiple

- GIVEN a merged Decision whose provenance reaches two Sources carrying
  `2026-07-14T10:00` and `2026-07-14T15:30`
- WHEN the helper resolves its date
- THEN it returns `multiple`

#### Scenario: A date and a date-time on the same day resolve as multiple

- GIVEN a merged Decision whose provenance reaches two Sources carrying
  `2026-07-14` and `2026-07-14T10:00`
- WHEN the helper resolves its date
- THEN it returns `multiple`; the two values are not assumed to describe
  one event

### Requirement: Direction Comes Only From Resolved Source event_date

A candidate pair's direction MUST be recorded as known, naming the later
Decision, only when both Decisions individually resolve to exactly one
event date (neither `missing`, `multiple`, nor `none-reached`) AND those
two resolved dates are not equal at the finest precision both carry: when
both carry a time they MUST be compared as date-times, and when either is
date-only they MUST be compared as dates, so the same day is equal. A time
MUST NEVER be assumed for a date-only side. In every other case — either side
missing, multiple, or none-reached, or the two resolved dates being equal at that precision —
direction MUST be recorded as unknown. No field in the judge's reply MUST
be read to set direction.

#### Scenario: Two distinct resolved dates yield a known direction

- GIVEN a pair whose two Decisions each resolve exactly one event date, and
  those dates differ
- WHEN the pair's direction is determined
- THEN direction is known, and the later-dated Decision is named as later

#### Scenario: Equal resolved dates yield an unknown direction

- GIVEN a pair whose two Decisions each resolve the same single event date
- WHEN the pair's direction is determined
- THEN direction is recorded as unknown

#### Scenario: A missing, multiple, or none-reached date yields an unknown direction

- GIVEN a pair where at least one Decision's event date resolves to
  `missing`, `multiple`, or `none-reached`
- WHEN the pair's direction is determined
- THEN direction is recorded as unknown, regardless of what the judge's
  reply says

#### Scenario: Two date-times on one day yield a known direction

- GIVEN a pair whose Decisions resolve `2026-07-14T11:03` and
  `2026-07-14T15:34`
- WHEN the pair's direction is determined
- THEN direction is known, and the Decision resolving `15:34` is named as
  later

#### Scenario: Equal date-times yield an unknown direction

- GIVEN a pair whose Decisions resolve `2026-07-14T10:00` and
  `2026-07-14T10:00:00`
- WHEN the pair's direction is determined
- THEN direction is recorded as unknown

#### Scenario: A date-time against a date on the same day yields an unknown direction

- GIVEN a pair whose Decisions resolve `2026-07-14T15:34` and `2026-07-14`
- WHEN the pair's direction is determined
- THEN direction is recorded as unknown, because the comparison is made on
  dates

#### Scenario: A date-time against a date on another day is directed by the dates

- GIVEN a pair whose Decisions resolve `2026-07-14T09:00` and `2026-07-15`
- WHEN the pair's direction is determined
- THEN direction is known, and the `2026-07-15` Decision is named as later
