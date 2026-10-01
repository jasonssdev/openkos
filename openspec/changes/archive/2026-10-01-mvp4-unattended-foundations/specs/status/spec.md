# Delta for Status

## ADDED Requirements

### Requirement: Needs-Attention Surfaces The Pending Queue And Unattended Outcomes

WHEN the pending-work queue exists, `openkos status` MUST report, under
**Needs attention**, the number of open rows per kind, and MUST name
`openkos pending` as where to see them. WHEN the workspace's job record
exists, `status` MUST report the most recent unattended job's kind, end
time, and outcome, and, for `budget_exhausted`, `timed_out`,
`commit_failed`, or `failed`, one line naming what was deferred or failed
and the remedy. A missing or unreadable queue or job record MUST be
reported as not available, never as nothing pending. `status` MUST read
both read-only and MUST NOT create either.

#### Scenario: A deferral is visible in status

- GIVEN the last watch job recorded `budget_exhausted` with three files
  deferred
- WHEN `openkos status` runs
- THEN it names the watch job, `budget_exhausted`, and three deferred

#### Scenario: A workspace that never ran unattended says so

- GIVEN no `.openkos/jobs.db`
- WHEN `openkos status` runs
- THEN it reports no unattended run recorded and creates no file
