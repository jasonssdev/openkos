# Unattended Budget Specification

## Purpose

Model spend is consequential in its own right -- GPU time on a local
machine, money on a hosted backend -- and in an unattended run nobody is
there to answer a cost gate. This capability bounds that spend per
workspace with an `unattended:` section in `openkos.yaml`, counted in LLM
chat calls because that is the unit the existing cost probes measure
exactly, and makes every deferral visible. It also holds the other
`unattended:` keys the job runtime and folder watch read, so the section
has one owner.

## Non-Goals

This spec does not define: a token or currency budget; a budget shared
across workspaces or per machine; rate limiting of a person's attended
runs; counting embedding calls against the budget (they are reported, not
budgeted); changing what an attended, TTY-confirmed run may spend; the
default values' final calibration (they are provisional and documented as
such).

## Requirements

### Requirement: The `unattended:` Section Is Optional And Validated

`openkos.yaml` MAY carry an `unattended:` mapping. When it is absent or
null, every key MUST take its default. Each key present MUST be validated
when the config is read, and an unknown key inside the section, a value of
the wrong type (including a boolean where an integer is expected), or a
value out of range MUST refuse the config read with a message naming the
key. The keys, their types, and their defaults are:

| Key | Type | Default | Range |
| --- | --- | --- | --- |
| `max_calls_per_pass` | integer | `100` | `>= 0` |
| `max_calls_per_day` | integer | `500` | `>= 0` |
| `max_sources_per_pass` | integer | `10` | `>= 0` |
| `job_deadline_seconds` | integer | `1800` | `>= 60` |
| `maintenance_interval_seconds` | integer | `86400` | `>= 300` |
| `inbox` | path string | absent (watch off) | see `folder-watch` |
| `quiet_seconds` | integer | `30` | `>= 1` |

The defaults MUST be documented as provisional. A value of `0` for a call
or source limit MUST mean that unattended runs make no model call, or
import no source, respectively.

#### Scenario: An absent section takes every default

- GIVEN an `openkos.yaml` with no `unattended:` key
- WHEN the config is read
- THEN `max_calls_per_pass` is `100`, `max_calls_per_day` is `500`, and
  `max_sources_per_pass` is `10`

#### Scenario: An unknown key is refused

- GIVEN `unattended: {max_call_per_pass: 5}`
- WHEN the config is read
- THEN the read is refused naming `max_call_per_pass`

#### Scenario: A boolean is not an integer

- GIVEN `unattended: {max_calls_per_day: true}`
- WHEN the config is read
- THEN the read is refused naming `max_calls_per_day`

### Requirement: The Budget Is Per Workspace And Counts Chat Calls

The budget MUST be enforced per workspace. It MUST count every chat call a
budgeted run issues to a model backend, including retries and re-asks, and
MUST NOT count embedding calls. The daily count MUST be the sum of the
chat calls of every budgeted run that started on the current local calendar
day, read from the workspace's job record. When the job record cannot be
read, a budgeted run MUST refuse to make any model call and MUST say why,
rather than assume nothing was spent.

#### Scenario: Two passes share one day's budget

- GIVEN `max_calls_per_day: 50` and a pass earlier today that made 40 calls
- WHEN another budgeted run starts today
- THEN it may make at most 10 calls

#### Scenario: An unreadable record fails closed

- GIVEN `.openkos/jobs.db` exists but cannot be opened
- WHEN a budgeted run starts
- THEN it makes no model call and reports that its spend record is
  unreadable

### Requirement: Budgeted Runs Are Unattended Runs And `--auto` Runs

Every job the runner starts, and every invocation of an LLM-bound verb with
`--auto`, MUST be a budgeted run. `--auto` MUST continue to skip the cost
question and MUST NOT skip the budget. An attended run -- a verb whose cost
gate a person answered at a terminal -- MUST NOT be limited by the budget
and MUST NOT count toward the daily total.

#### Scenario: --auto skips the question, not the budget

- GIVEN `max_sources_per_pass: 2`
- WHEN `openkos ingest notes/ --auto` runs over five new files
- THEN two files are ingested, three are reported deferred, and no cost
  question is asked

#### Scenario: A person's confirmed run is not truncated

- GIVEN `max_sources_per_pass: 2`
- WHEN a person runs `openkos ingest notes/` at a terminal over five new
  files and answers the cost gate yes
- THEN all five files are ingested

### Requirement: Admission Is By Estimate, Accounting Is By Count

Before starting a unit of work, a budgeted run MUST compare the unit's call
estimate -- the ingest call estimate for a source, the exact probe count
for an advisor stage -- with the calls remaining in both the pass and the
day, and MUST NOT start a unit whose estimate exceeds either. An ingest
source MUST be admitted or deferred whole. An advisor stage whose probe
count exceeds the remainder MUST be truncated to the calls remaining, in
the stage's own deterministic order; the verdicts it persisted are served
from `findings.db` on the next pass, so the next pass resumes rather than
repeats. The count recorded MUST be the calls actually made.

#### Scenario: A source larger than the pass budget is never started

- GIVEN `max_calls_per_pass: 20` and a settled source whose estimate is
  25 calls
- WHEN a watch job considers it
- THEN it is not started, it is recorded as deferred, and a pending-work row
  says it exceeds the per-pass budget and must be ingested by hand

#### Scenario: A truncated contradictions stage resumes

- GIVEN a contradictions probe of 150 calls and 100 calls remaining
- WHEN the maintenance pass runs the stage
- THEN it judges 100 pairs, records `budget_exhausted`, and the next pass
  makes calls only for the remaining pairs

### Requirement: Exhaustion Is A Visible Outcome

A budgeted run that deferred work for budget MUST record `budget_exhausted`
with the number of units deferred, MUST print (when attended by a terminal
or run with `--auto`) one stderr line naming the limit that was reached and
the number deferred, and MUST exit `0` when every unit it did start
succeeded. `status` and `next` MUST surface the most recent exhaustion
until a later run of the same kind completes without deferral.

#### Scenario: --auto prints what it deferred

- GIVEN `max_sources_per_pass: 1`
- WHEN `openkos ingest notes/ --auto` runs over two new files
- THEN stderr carries one line naming `max_sources_per_pass` and `1
  deferred`, and the exit code is `0`
