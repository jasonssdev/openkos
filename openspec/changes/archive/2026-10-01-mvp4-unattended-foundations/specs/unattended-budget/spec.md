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
across workspaces or per machine; any limit on a run a person launched
from the CLI, with or without `--auto` (those keep their existing cost
gates); counting embedding calls against the budget (they are reported,
not budgeted); the
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
day, read from the workspace's job record. The job record is disposable: an
absent record MUST be treated as no history and zero calls spent today, with
no error. A record that exists but cannot be read MUST make a budgeted run
refuse to make any model call and say why, naming deletion of the record as
the remedy, rather than guess what was spent.

#### Scenario: Two passes share one day's budget

- GIVEN `max_calls_per_day: 50` and a pass earlier today that made 40 calls
- WHEN another budgeted run starts today
- THEN it may make at most 10 calls

#### Scenario: A deleted record starts fresh counters

- GIVEN `.openkos/jobs.db` was deleted after 40 calls were spent today
- WHEN the next budgeted run starts
- THEN it starts with zero calls spent today and no history, and reports no
  error

#### Scenario: An unreadable record fails closed

- GIVEN `.openkos/jobs.db` exists but cannot be opened
- WHEN a budgeted run starts
- THEN it makes no model call and reports that its spend record is
  unreadable and may be deleted

### Requirement: Only Runner-Started Jobs Are Budgeted

Every job the job runner starts MUST be a budgeted run, and no other run
MUST be. A run a person launched from the CLI -- attended at a terminal,
with `--auto`, or as a non-TTY batch -- MUST NOT be limited by the budget,
MUST NOT count toward the daily total, and MUST keep its existing cost
gate and `--auto` behaviour unchanged. This follows ADR-0037's definition
of unattended: it is decided by who started the run, not by a flag.

#### Scenario: A CLI --auto batch is not budget-limited

- GIVEN `max_sources_per_pass: 2`
- WHEN a person runs `openkos ingest notes/ --auto` over five new files
- THEN all five files are ingested, no deferral is reported, and the run's
  calls are not added to the daily total

#### Scenario: A person's confirmed run is not truncated

- GIVEN `max_sources_per_pass: 2`
- WHEN a person runs `openkos ingest notes/` at a terminal over five new
  files and answers the cost gate yes
- THEN all five files are ingested

#### Scenario: A watch job is budget-limited

- GIVEN `max_sources_per_pass: 2` and five settled new files in the inbox
- WHEN a watch job runs
- THEN it imports two files and records three deferred

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
with the limit that was reached and the number of units deferred, and MUST
log one line saying so. `status`, `next`, and `pending` MUST surface the
most recent exhaustion until a later job of the same kind completes without
deferral.

#### Scenario: status names what a job deferred

- GIVEN a watch job that recorded `budget_exhausted` on
  `max_sources_per_pass` with one file deferred
- WHEN `openkos status` runs
- THEN it names the watch job, `max_sources_per_pass`, and `1 deferred`
