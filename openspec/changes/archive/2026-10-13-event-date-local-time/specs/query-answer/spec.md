# Delta for query-answer

## ADDED Requirements

### Requirement: Revision History Date Phrase Keeps The Stored Precision

The `<date phrase>` of a revision history label MUST render each resolved
event date at the precision it was stored: `YYYY-MM-DD` for a calendar date
and `YYYY-MM-DDTHH:MM[:SS]` for a naive local date-time, using the same
canonical text `ingest` writes. A range (`event dates <D1> to <D2>`) MUST
order its endpoints at the finest precision both carry and MUST NOT invent a
time for a date-only endpoint. A bundle with only date-only event dates MUST
render byte-identically to before.

#### Scenario: A date-time renders with its time

- GIVEN a predecessor whose single resolved event date is
  `2026-07-14T10:00`
- WHEN `answer(...)` renders its history block
- THEN the label reads `event date 2026-07-14T10:00`

#### Scenario: A date-only value renders unchanged

- GIVEN a predecessor whose single resolved event date is `2026-07-14`
- WHEN `answer(...)` renders its history block
- THEN the label reads `event date 2026-07-14`
