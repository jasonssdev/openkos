# Delta for Workspace Lock

## MODIFIED Requirements

### Requirement: Every Command Is Classified

Every registered command MUST belong to exactly one of three classes:
read-only (never takes the lock), locked (takes it for its commit phase,
or, for `purge`, for its whole run), or self-locking (a long-running
command that takes it per unit of work, never for its own lifetime). A
command added without a classification MUST fail a test. `openkos daemon`
MUST be self-locking, `openkos pending` MUST be read-only, `openkos export`
MUST be read-only (it writes only outside the workspace, and guards its
snapshot by re-reading its inputs instead of locking; see `okf-export`),
`openkos import` MUST be locked, with a commit phase in which it re-reads
and re-validates its input and writes (see `okf-import`; its preview phase
takes no lock), and `openkos query` MUST be locked, with a commit phase that
exists only under `--save`.
(Previously: the requirement did not classify `openkos import`.)

#### Scenario: A new command without a class fails

- GIVEN a command registered without being classified
- WHEN the classification test runs
- THEN it fails naming that command

#### Scenario: Export does not wait on a writer

- GIVEN one process holds the workspace lock for its commit phase
- WHEN `openkos export out/ --auto` runs
- THEN it is not refused for lock contention

#### Scenario: Import is classified as locked

- GIVEN the command classification table
- WHEN `openkos import` is looked up
- THEN it is in the locked class, with a commit phase

#### Scenario: Import preview does not wait on a writer

- GIVEN one process holds the workspace lock for its commit phase
- WHEN `openkos import <dir>` computes its preview
- THEN the preview is not refused for lock contention, while the write phase
  is
