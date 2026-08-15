# Delta for Ingestion

## ADDED Requirements

### Requirement: Participant-Lane Truncation Is Disclosed

WHEN the participant budget lane truncates `Person`/`Organization`
candidates beyond its capacity, `ingest` MUST surface that truncation to
the user, in wording distinct from the subject backstop cap notice
(`_extraction_cap_notice`), the judge-failure notice, and the pre-judge
notice — mirroring how `participant_anchorless_discarded_titles` already
gets its own notice, separate from the general cap notice.

#### Scenario: Participant-lane truncation is reported

- GIVEN a source whose participant-lane candidates exceed the lane's
  capacity
- WHEN `openkos ingest <path>` completes
- THEN the run reports that participant candidates were truncated, in
  wording distinct from the cap, judge, and pre-judge notices

#### Scenario: No truncation, no notice

- GIVEN a source whose participant candidates are within the lane's
  capacity
- WHEN `openkos ingest <path>` completes
- THEN no participant-lane truncation notice appears
