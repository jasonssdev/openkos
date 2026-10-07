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
text, model output, or rationale. The record is disposable operational
state, never a source of truth: deleting it MUST lose only job history and
the current day's budget counters, never knowledge, and the next job MUST
recreate it and start with fresh counters without error. A job whose record
cannot be written MUST still end, and the daemon MUST log the failure.
`status`, `next`, and `pending` MUST read the record read-only. `purge`
MUST delete it (`privacy-purge`).

#### Scenario: A deleted job record starts over without error

- GIVEN a workspace whose `.openkos/jobs.db` held today's spend and job
  history, and was then deleted
- WHEN the next job runs
- THEN it recreates the record, starts with zero calls spent today and no
  history, reports no error, and no knowledge under `bundle/` or `raw/`
  changed

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
record MUST NOT contain document text, model output, or rationale, and a
failure MUST be logged by its exception type only, never by its message,
because a message can carry a path or a person's words.

#### Scenario: The daemon's log survives the process

- GIVEN `openkos daemon --once` ran a job
- WHEN the process has exited
- THEN the job's start, outcome, and call count are readable in the log file
  under the per-user log directory

#### Scenario: Nothing is logged into the bundle

- GIVEN any daemon run
- WHEN it has exited
- THEN no file under `bundle/` was created by logging

### Requirement: Logs Of Workspaces That No Longer Exist Are Removed

The daemon MUST record the workspace each of its log files belongs to in a
`<sha256>.workspace` file beside the log, holding only that workspace's
resolved path and written atomically. At startup the daemon MUST remove the
`<sha256>.log`, `<sha256>.log.N` and `<sha256>.workspace` files of a group
whose record is trustworthy (a regular file naming an absolute path that
hashes to the group's name) and whose recorded path no longer holds an OpenKOS
workspace, and MUST report every file it removes on stderr. It MUST NOT remove
the current workspace's files, the files of a workspace that still exists
(including one that merely cannot be read), a group containing a symlink or a
non-regular file, a log with no trustworthy record, or any file whose name is
not one of those three forms. `doctor` MUST report, read-only, the groups
awaiting removal and the count and size of logs that cannot be attributed,
which are removed by hand. `purge` MUST remove the workspace's record along
with its logs.

#### Scenario: A vanished workspace's log is removed at startup

- GIVEN a log group whose record names a path that holds no workspace
- WHEN `openkos daemon` starts
- THEN each file of the group is removed and named on stderr

#### Scenario: A log nothing attributes is kept

- GIVEN a `<sha256>.log` with no `<sha256>.workspace` record
- WHEN `openkos daemon` starts
- THEN the file remains
- AND `openkos doctor` reports it by count and size

### Requirement: A Foreground Daemon Says What It Is Doing

While a job runs, `openkos daemon` MUST give a person watching it the same
liveness signal the other long verbs give: one stderr line as each maintenance
stage starts, and the per-item progress counter of the stage's own verb. Both
MUST appear only when stderr is a TTY, so a piped or service-managed run stays
byte-clean, and neither MUST touch stdout, which carries the job reports. A
daemon run that ends normally MUST log an end line, so a log that records a
start never looks like a crash.

#### Scenario: A long maintenance pass is not silent

- GIVEN a TTY and a maintenance pass with several advisor stages
- WHEN `openkos daemon --once` runs
- THEN each stage is named on stderr as it starts, before the final job report

#### Scenario: A finished run is closed in the log

- GIVEN `openkos daemon --once` completed
- WHEN the process has exited
- THEN the daemon log holds an end line after its start line

### Requirement: A Maintenance Stage Discloses A Candidate Cap That Bound

When an advisor stage's candidate list was truncated by its per-run cap
(duplicate groups, candidate edges, decision-revision pairs, contradiction
pairs), the maintenance pass MUST NOT swallow the notice the same verb prints
when a person runs it: the stage MUST log the notice and pass it to the
runner's `announce` port, prefixed with the stage name. A stage whose cap did
not bind MUST say nothing. The notice is advisory; it never changes a job's
outcome.

#### Scenario: A capped identity list is named

- GIVEN more duplicate candidate groups than the per-run cap
- WHEN the identity stage of a maintenance pass runs
- THEN the log and the announce port carry `identity: note: 50 of N candidate group(s) shown (cap reached)`

#### Scenario: An uncapped stage is silent

- GIVEN a duplicate scan whose cap did not bind
- WHEN the identity stage runs
- THEN nothing is announced about a cap

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
is fixed. A retry that succeeds MUST report its commit as an automatic action,
so the pass's digest lists it.

#### Scenario: A transient commit failure heals on the next job

- GIVEN a job wrote files and its commit failed because `index.lock` was
  held
- WHEN the next job starts and the lock is gone
- THEN it commits the recorded paths first and records the retry as
  successful

#### Scenario: A healed retry is listed in the digest

- GIVEN a retry that committed previously uncommitted paths
- WHEN the pass ends
- THEN the digest lists that commit with its undo
### Requirement: The Runner Uses The Application Services, Never The CLI

The runner MUST call the synchronous application services under
`application/` and MUST NOT import `openkos.cli`, invoke a subprocess of
`openkos`, or render terminal output. The runner itself MUST be
synchronous.

#### Scenario: The runner module never imports the CLI

- GIVEN the runner's modules
- WHEN their imports are inspected
- THEN none imports `openkos.cli`, `typer`, or `rich`
### Requirement: The Daemon And Maintenance Pass Never Auto-Merge

The daemon, every job it runs, and the maintenance pass MUST NEVER merge a
Concept, whatever flags, configuration or queue contents are present. In
particular, `curate --auto-merge` (`identity-auto-merge`) MUST NOT be reachable
from the daemon or the maintenance pass: the runner MUST NOT pass an
auto-merge input to any core, and an identity proposal MUST remain a pending
work row until a person acts on it.

#### Scenario: A daemon maintenance cycle over an in-class pair merges nothing

- GIVEN a workspace with an in-class base/`-N` pair that the measured model
  would judge `same` at high confidence
- WHEN a daemon maintenance cycle completes
- THEN both files are unchanged, no merge commit exists, and the queue holds
  an identity row for the pair

#### Scenario: The runner passes no auto-merge input

- GIVEN the runner's calls into the application services
- WHEN a maintenance job runs
- THEN no call carries an auto-merge input
### Requirement: An Unattended Pass Ends With A What-Changed Digest

A daemon pass that made at least one automatic commit MUST end, after that pass's
job reports, with a digest on stdout: one summary line (`openkos daemon: what
changed -- N automatic commit(s)`, adding `, newest first` when N is greater than
one), then one line per commit, newest first, then one note saying to undo newest
first because a revert is safe only while that commit is the latest. Each commit
line MUST carry, on that one line, the commit's short sha, the commit's summary,
the Concept IDs of the bundle documents it touched (at most four, then `+N
more`), and the command that undoes it (`git revert <sha>`). Every automatic
commit a pass made MUST be listed. A pass that made none MUST print no digest.
On a terminal the digest MUST be its own section and wrap under a hanging
indent; when stdout is not a terminal it MUST be the same lines unwrapped, and no
ANSI escape is ever emitted (ADR-0042). The digest is derived from the commits and
MUST NOT be stored anywhere else.

#### Scenario: A pass that imported two files lists both commits with their undo

- GIVEN a daemon pass whose watch job imported two files and committed each
- WHEN the pass ends
- THEN stdout ends with a summary line naming two automatic commits, then two
  lines, newest commit first, each with its short sha, its concept ids and
  `-- undo: git revert <that sha>`

#### Scenario: Running the printed undo commands restores the bundle

- GIVEN the digest of that pass
- WHEN the printed undo commands are run in the printed order
- THEN each applies cleanly and the raw files, source documents, index and log
  are byte-identical to before the pass

#### Scenario: A pass with no automatic commit prints no digest

- GIVEN a pass that only refreshed the derived indexes and enqueued rows
- WHEN the pass ends
- THEN no `what changed` line is printed

#### Scenario: Every job's commits are listed

- GIVEN a pass whose commit-retry job and watch job each made commits
- WHEN the digest prints
- THEN every one of those commits appears on its own line

### Requirement: The Digest States How Many Relation Suggestions Wait

A daemon pass that prints the what-changed digest MUST end it with one more
stdout line when relation suggestions wait in the pending-work queue (open
`relation_type` rows other than supersessions): `openkos daemon: N relation
suggestion(s) waiting -- review them with `openkos curate --structure`.` It MUST
print no such line when none wait, and a pass that prints no digest prints no
such line either. The count is read from the queue without a model call and
without a lock.

#### Scenario: The digest names the waiting suggestions

- GIVEN two open relation suggestions and one open supersession, and a pass that
  made one automatic commit
- WHEN the pass ends
- THEN the digest is followed by `openkos daemon: 2 relation suggestion(s)
  waiting -- review them with `openkos curate --structure`.`

#### Scenario: Nothing waits, nothing is said

- GIVEN no open relation suggestion and a pass that made one automatic commit
- WHEN the pass ends
- THEN the digest carries no relation-suggestion line
