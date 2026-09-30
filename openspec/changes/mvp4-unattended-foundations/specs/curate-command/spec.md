# Delta for Curate Command

## ADDED Requirements

### Requirement: Curate Reads And Resolves The Pending Queue

WHEN the pending-work queue exists, each `curate` stage MUST take the open
rows of its kind as its candidates before recomputing, MUST enqueue any
proposal it computes that has no open row, and MUST resolve each row it
presents: `applied` (as proposed or modified) when the item is written,
`declined` when the operator declines it through a surface that records the
decline. A row presented and neither written nor declined MUST return to
`pending`. The Contradictions stage stays report-only: it enqueues and
never applies.

#### Scenario: An accepted relation resolves its row

- GIVEN an open relation-type row
- WHEN the operator accepts it in the Structure stage
- THEN the relation is written and the row is `applied` as proposed

### Requirement: `--auto` Is Bounded By The Unattended Budget

`curate --auto` MUST skip the per-stage cost question as it does today and
MUST apply the unattended budget (`unattended-budget`): a stage whose exact
probe count exceeds the calls remaining MUST be truncated to them and the
run MUST record and print the deferral. `--auto` MUST NOT imply any
`--accept`.

#### Scenario: A large stage is truncated under --auto

- GIVEN `max_calls_per_pass: 10` and a Contradictions probe of 40 calls
- WHEN `openkos curate --auto` reaches the stage
- THEN it makes at most 10 calls, prints one deferral line, and the next run
  resumes from the verdicts already persisted
