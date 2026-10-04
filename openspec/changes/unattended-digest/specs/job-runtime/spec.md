# Delta for Job Runtime

## ADDED Requirements

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

## MODIFIED Requirements

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
