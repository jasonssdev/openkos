# Delta for Extraction Union-Judge

## MODIFIED Requirements

### Requirement: The Decision Pass Runs Per Window On Meeting-Shaped Sources

WHEN `transcript_decisions` is `true` and the source is meeting-shaped (the
same predicate that gates participant capture), the system MUST run the
decision pass once per chunk window, or once over the whole text when the
source is at or below the chunk threshold. Each call MUST ask only for the
decisions the participants reached in that window, and MUST return items of
shape `{subject, value, evidence}`. An empty array MUST be a valid answer and MUST NOT be retried.

A window whose call raises a backend error other than "backend unavailable",
or whose reply is unparseable, MUST be asked exactly once more with the same
prompt. A window whose first reply parses, even to no verbatim item, MUST NOT
be retried, and an unavailable backend MUST NOT be retried. The pass MUST
report a failure only when the retry also fails, and the failure MUST name
the retry's cause.

#### Scenario: A chunked transcript is asked window by window

- GIVEN a meeting-shaped source that splits into three windows
- WHEN the pass runs
- THEN three calls are made, each carrying only its own window

#### Scenario: A non-meeting source is not asked

- GIVEN `transcript_decisions` is `true` and a source that is not
  meeting-shaped
- WHEN the source is ingested
- THEN no decision-pass call is made

#### Scenario: An unparseable window is asked once more

- GIVEN a window whose first reply is not parseable and whose second is valid
- WHEN the pass runs
- THEN two calls are made for that window, its items are admitted, and no
  failure is reported

#### Scenario: A retry that also fails is reported

- GIVEN a window whose first reply is unparseable and whose retry raises a
  generation error
- WHEN the pass runs
- THEN the failure names the generation error, and the other windows'
  items are still admitted

#### Scenario: An unavailable backend is not retried

- GIVEN a window whose call raises the backend-unavailable error
- WHEN the pass runs
- THEN one call is made for that window and the failure is reported
