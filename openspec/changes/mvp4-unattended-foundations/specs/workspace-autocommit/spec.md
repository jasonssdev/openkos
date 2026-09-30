# Delta for Workspace Autocommit

## ADDED Requirements

### Requirement: Under The Job Runner, A Commit Failure Is A Recorded, Retryable Outcome

WHEN the auto-commit of a job started by the job runner degrades for any
reason `Non-Fatal Degradation` names, the job MUST record the outcome
`commit_failed` together with the workspace-relative paths it wrote and the
failure's class (not a repository, identity unset, git error, git timeout),
instead of only printing a warning. The next job MUST first attempt the
same scoped commit for those recorded paths that are still uncommitted, and
MUST record whether it succeeded. The interactive CLI's behavior is
unchanged: a degraded commit there remains a non-fatal warning with the
verb's normal exit code.

#### Scenario: The next job commits what the last one could not

- GIVEN a job whose commit failed with a git error, leaving its writes
  uncommitted
- WHEN the next job starts and git succeeds
- THEN it commits exactly the recorded paths still uncommitted, with the
  scoped staging this capability already requires, and records the retry as
  successful

#### Scenario: A permanent failure stays visible

- GIVEN a workspace that is not a git repository
- WHEN a job's commit is skipped for that reason
- THEN the job records `commit_failed` and `status` names the cause until it
  is fixed
