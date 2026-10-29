# Stable Python API Specification (delta)

## ADDED Requirements

### Requirement: What The Engine Is Doing Is Read Without A Lock

`openkos.api` MUST offer `engine_activity(workspace)` returning an
`EngineActivity` with `current` (the task running now as an `EngineJob`, or
`None`), `recent` (the latest finished tasks, newest first, at most ten),
`waiting_files` (files in the watched folder not yet taken), `uncommitted_paths`
(paths a task wrote and could not commit), `budget` (a `DailyBudget` with
`calls_used`, `calls_limit` and `exhausted`) and `jobs_unreadable`. `current`
MUST be the newest job only when it has no end and began within its deadline
plus a short grace; an older unfinished row MUST NOT be reported as running.
`calls_used` MUST count the model calls of the jobs that began on the local
calendar day. The read MUST take no lock, build no model client, write nothing
and never create the job record; a record that exists but cannot be read MUST
set `jobs_unreadable` and nothing else, never read as an idle engine. The change
is additive; `API_VERSION` is unchanged.

#### Scenario: A workspace that never ran the engine is idle

- GIVEN a workspace with no job record
- WHEN `engine_activity(workspace)` is called
- THEN `current` is `None`, `recent` is empty, nothing waits, `calls_used` is 0
  and nothing was written

#### Scenario: A task that died is not running

- GIVEN an unfinished job that began hours before its deadline allows
- WHEN `engine_activity(workspace)` is called
- THEN `current` is `None`

#### Scenario: A damaged record is reported

- GIVEN a `jobs.db` that is not a database
- WHEN `engine_activity(workspace)` is called
- THEN `jobs_unreadable` is true and the file is left as found
