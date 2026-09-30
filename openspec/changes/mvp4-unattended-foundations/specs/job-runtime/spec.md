# Job Runtime Specification

## Purpose

The job runtime is what lets a user leave OpenKOS running. `openkos daemon`
is a foreground, long-running command that runs unattended jobs for one
workspace -- importing settled files from the watched inbox
(`folder-watch`) and a scheduled maintenance pass -- one at a time, and
records what each job did. It performs only non-consequential work and
enqueues every consequential proposal (ADR-0037). This capability also
holds the runtime prerequisites every unattended caller depends on: one
logging setup, a cooperative stop, a per-job deadline, prompts expressed as
caller policy, and auto-commit failure as a recorded, retryable outcome.

## Non-Goals

This spec does not define: installing, starting or supervising the daemon
as an operating-system service (the user's service manager or a terminal
runs it); more than one workspace per daemon process; parallel jobs;
preemptive cancellation of a job (ADR-0021 Decision Three: nothing below
the application boundary can be cancelled); applying any consequential
change; the spend budget's keys and arithmetic (`unattended-budget`); the
inbox's semantics (`folder-watch`); a network or remote control surface.

## Requirements

### Requirement: `openkos daemon` Runs Unattended Jobs For One Workspace

`openkos daemon` MUST run in the foreground against the workspace in the
current directory, MUST refuse outside a workspace exactly as other
workspace verbs do, and MUST run at most one job at a time. It MUST run a
watch job whenever the configured inbox holds a settled, not-yet-imported
file, and a maintenance job whenever the configured maintenance interval
has elapsed since the last maintenance job began. `openkos daemon --once`
MUST run every job that is due once, in that order, and exit. The daemon
MUST NOT take the workspace lock for its own lifetime; each job takes it
per commit phase (`workspace-lock`).

#### Scenario: --once runs what is due and exits

- GIVEN a workspace whose inbox holds one settled new file and whose
  maintenance pass is due
- WHEN `openkos daemon --once` runs
- THEN it runs one watch job, then one maintenance job, records both, and
  exits

#### Scenario: The daemon does not hold the lock between jobs

- GIVEN `openkos daemon` is running and idle
- WHEN a person runs a locked verb
- THEN the verb is not refused for lock contention

### Requirement: The Maintenance Pass Computes And Enqueues Only

A maintenance job MUST, in order: refresh the derived stores
(`derived-index-cache`), run the lint checks and record their counts, and
run the findings advisors (duplicate groups, identity adjudication,
contradictions, relation typing, volatility suggestion, decision
revisions), persisting each proposal as a pending-work row
(`pending-work`). It MUST NOT merge, relate, set a volatility tier,
reconcile, forget, or perform any other write to `bundle/` that a
proposal describes, and it MUST NOT pass any consent flag to any core.

#### Scenario: A maintenance pass changes nothing under bundle/

- GIVEN a workspace with an unresolved duplicate group and an untyped edge
- WHEN a maintenance job completes
- THEN no file under `bundle/` changed, and the queue holds an identity row
  and a relation-type row for them

### Requirement: Every Job Records One Outcome

Every job MUST end by recording exactly one outcome in the workspace's job
record (`.openkos/jobs.db`, owner-only): `completed`, `budget_exhausted`,
`timed_out`, `stopped`, `busy`, `commit_failed`, `refused`, or `failed`,
with its start and end time, the model calls it made, the units of work it
completed, and the units it deferred. The record MUST NOT contain document
text, model output, or rationale. A job whose record cannot be written MUST
still end, and the daemon MUST log the failure. `status`, `next`, and
`pending` MUST read the record read-only.

#### Scenario: A deferral is recorded, not silent

- GIVEN a watch job that imported some settled files and deferred the rest
  for budget
- WHEN the job ends
- THEN its record says `budget_exhausted` with the count of deferred files

### Requirement: One Logging Setup Serves The CLI And The Daemon

The engine MUST configure logging in one place used by every entry point.
Under the CLI, log records MUST go to stderr and MUST NOT change any verb's
existing stdout or stderr output unless a verb's own spec says so. Under
the daemon, log records MUST go to a rotating file outside the workspace:
`${XDG_STATE_HOME:-~/.local/state}/openkos/logs/` on Linux and other POSIX
systems, `~/Library/Logs/openkos/` on macOS, and
`%LOCALAPPDATA%\openkos\Logs\` on Windows, named by the sha256 of the
workspace's real path. No log file MUST ever be created inside `bundle/`.
Library modules MUST log through `logging.getLogger(__name__)`. A log
record MUST NOT contain document text, model output, or rationale.

#### Scenario: The daemon's log survives the process

- GIVEN `openkos daemon --once` ran a job
- WHEN the process has exited
- THEN the job's start, outcome, and call count are readable in the log file
  under the per-user log directory

#### Scenario: Nothing is logged into the bundle

- GIVEN any daemon run
- WHEN it has exited
- THEN no file under `bundle/` was created by logging

### Requirement: SIGTERM And SIGINT Stop The Daemon Cooperatively

On SIGTERM or SIGINT the daemon MUST set a stop flag and MUST NOT start a
new unit of work or a new job after it is set. A job MUST check the flag
between units of work and immediately before a commit phase's write burst,
and MUST NOT check it inside the write burst. A job stopped this way MUST
record `stopped`, and the daemon MUST then exit `0`. A second signal while
stopping MUST NOT interrupt a write burst in progress.

#### Scenario: A stop before the write burst writes nothing

- GIVEN a watch job whose extraction has finished and whose commit phase has
  not begun
- WHEN the daemon receives SIGTERM
- THEN the job writes nothing for that file, records `stopped`, and the
  daemon exits `0`

#### Scenario: A stop during the write burst completes the burst

- GIVEN a commit phase whose write burst has begun
- WHEN the daemon receives SIGTERM
- THEN the burst and its auto-commit complete before the daemon exits

### Requirement: Each Job Has A Wall-Clock Deadline

Each job MUST carry a deadline of `unattended.job_deadline_seconds` from
its start. The runner MUST check it where it checks the stop flag, MUST
NOT start a unit of work after it has passed, and MUST record `timed_out`
with the units deferred. A unit in progress when the deadline passes MUST
be allowed to finish, bounded by the backend's own per-call timeouts; the
runner MUST NOT preempt it.

#### Scenario: An expired deadline defers the rest

- GIVEN a watch job with three settled files and a deadline that passes
  while the first is being extracted
- WHEN the first file's unit finishes
- THEN the job records `timed_out` with two deferred and does not start the
  second

### Requirement: Unattended Callers Answer Every Question By Policy

Every question a verb can ask a human, on any path an unattended job
reaches, MUST be expressed as data the caller answers through a policy
object, and an application service MUST NOT prompt, read a TTY, or decide
whether a terminal is attached. The runner's policy MUST answer every
confirmation that would lead to a consequential write with "declined" and
every spend confirmation according to the budget (`unattended-budget`). A
question with no policy answer MUST be treated as unavailable, never as
yes.

#### Scenario: The runner never approves a merge

- GIVEN a path reachable from a maintenance job that would ask "Proceed?"
  before a merge
- WHEN the runner's policy is asked
- THEN it answers declined and no merge is written

### Requirement: Lock Contention Inside A Job Is Retried With Bounded Backoff

When a job's commit phase meets workspace-lock or derived-store contention,
the runner MUST retry acquisition with jittered exponential backoff bounded
by the lesser of a per-acquisition cap and the job's remaining deadline.
When the bound is exhausted the unit MUST be deferred and the job MUST
record `busy` if nothing else was completed.

#### Scenario: A person's short commit does not fail a job

- GIVEN a person's verb holds the lock for its commit phase
- WHEN a job reaches its own commit phase
- THEN the job waits, acquires the lock after the person's verb releases it,
  and completes the unit

### Requirement: A Failed Auto-Commit Is A Recorded, Retryable Outcome

When a job's auto-commit fails (not a git repository, identity unset, a git
error or timeout), the job MUST record `commit_failed` with the
workspace-relative paths it wrote. The next job MUST first retry the commit
of those of the recorded paths that are still uncommitted, and MUST record
the retry's result. A failure that cannot be fixed by retrying (not a git
repository, identity unset) MUST be surfaced by `status` and `next` until it
is fixed.

#### Scenario: A transient commit failure heals on the next job

- GIVEN a job wrote files and its commit failed because `index.lock` was
  held
- WHEN the next job starts and the lock is gone
- THEN it commits the recorded paths first and records the retry as
  successful

### Requirement: The Runner Uses The Application Services, Never The CLI

The runner MUST call the synchronous application services under
`application/` and MUST NOT import `openkos.cli`, invoke a subprocess of
`openkos`, or render terminal output. The runner itself MUST be
synchronous.

#### Scenario: The runner module never imports the CLI

- GIVEN the runner's modules
- WHEN their imports are inspected
- THEN none imports `openkos.cli`, `typer`, or `rich`
