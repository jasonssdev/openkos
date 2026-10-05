# Workspace Lock Specification

## Purpose

The workspace lock serializes OpenKOS writers against each other: an
advisory, exclusive, per-workspace lock released by the kernel when its
holder exits, however it exits. This capability states which commands take
it, for how long, where the lock file lives, and what a refusal means. It
synchronizes OpenKOS processes and threads only; a human editor and any
other program remain unsynchronized writers handled by detection
(ADR-0020 Decision Four). Why the lock covers a commit phase rather than a
whole verb is recorded in ADR-0036.

## Non-Goals

This spec does not define: a blocking acquire in the lock primitive; a
shared (reader) lock; holder identity in the lock file (it stays empty);
locking by any program other than `openkos`; the job runner's scheduling
(`job-runtime`); the drift refusal's wording for each verb (each verb's own
spec).

## Requirements

### Requirement: A Locked Verb Holds The Lock Only For Its Commit Phase

A command that writes to the workspace MUST run as a compute phase that
holds no workspace lock, followed by a commit phase that holds it. The
compute phase MUST contain every model call, every embedding call, every
whole-bundle candidate walk, and every question put to a human. The commit
phase MUST contain, in this order: re-validation of the plan's inputs, the
write burst, the scoped auto-commit, and the refresh of the derived stores
whose refresh makes no model call. A command MUST NOT hold the workspace
lock while it waits on a model backend or on human input.

#### Scenario: A prompt does not hold the lock

- GIVEN `openkos merge` is waiting at its confirmation prompt
- WHEN a second process runs `openkos relate` on unrelated concepts
- THEN the second process acquires the lock, writes, and exits 0

#### Scenario: Extraction does not hold the lock

- GIVEN `openkos ingest` is waiting on the model backend during extraction
- WHEN a second process runs a locked verb
- THEN the second process is not refused for lock contention

#### Scenario: The write burst is exclusive

- GIVEN one process holds the lock for its commit phase
- WHEN a second process reaches its own commit phase
- THEN the second process does not write until the first has released the
  lock

### Requirement: The Commit Phase Re-Validates Every Input The Plan's Safety Depends On

Before its first write, the commit phase MUST re-read every file the plan
will write or unlink, and every file whose content decided a sensitivity
level, a provenance list, or a catalog entry in the plan, and compare each
with the bytes the compute phase read. A changed or vanished write or
unlink target, and a changed or vanished sensitivity or provenance input,
MUST refuse the run with exit `3` and nothing written. `index.md` and
`log.md` are the exception: when either changed, the commit phase MUST
re-compose its new bytes from the staged plan and the file's current bytes
instead of refusing, and MUST refuse with exit `3` only when that
re-composition is not possible.

#### Scenario: A concurrent sensitivity raise is not lost

- GIVEN `openkos query --save` computed an insight's sensitivity as
  `private` from a cited concept that was `private`
- WHEN another process raises that concept to `confidential` before the
  save's commit phase
- THEN the save refuses with exit `3` and writes nothing

#### Scenario: A concurrent catalog append does not waste an extraction

- GIVEN `openkos ingest a.txt` finished extraction lock-free
- WHEN another process appended an entry to `index.md` and `log.md` before
  the ingest's commit phase
- THEN the ingest writes, and both processes' entries are present in
  `index.md` and `log.md`

#### Scenario: A changed write target still refuses

- GIVEN a verb's plan rewrites concept `<id>`
- WHEN `<id>` changed on disk between the compute phase and the commit
  phase
- THEN the verb exits `3` and nothing is written

### Requirement: Writes To A Store That Can Hold Bundle Content Happen In A Commit Phase

A write to `fts.db`, `graph.db`, `vectors.db`, or `findings.db` (every
tenant, including the pending-work queue) MUST happen while the writer
holds the workspace lock, and MUST drop every row whose input documents no
longer exist or no longer match the digest the row was computed from. The
model and embedding calls that produce those rows MUST NOT hold the lock.

#### Scenario: A purge during compute is not undone

- GIVEN a findings computation read concept `<id>` and is still computing
- WHEN `openkos purge <id>` completes
- THEN no row naming `<id>` is written to `findings.db` when the
  computation commits

### Requirement: `purge` Holds The Lock For Its Whole Run

`openkos purge` MUST acquire the workspace lock before it reads anything
and MUST hold it until it exits.

#### Scenario: Nothing interleaves with a purge

- GIVEN `openkos purge <id>` is running
- WHEN another process reaches its commit phase
- THEN that process does not write until the purge has exited

### Requirement: A Plain `query` Takes No Lock

`openkos query` without `--save` MUST NOT acquire the workspace lock.
`openkos query --save` MUST acquire it only for its commit phase.

#### Scenario: A busy workspace still answers

- GIVEN another process holds the workspace lock
- WHEN `openkos query "<question>"` runs
- THEN it answers and is not refused for lock contention

### Requirement: Every Command Is Classified

Every registered command MUST belong to exactly one of three classes:
read-only (never takes the lock), locked (takes it for its commit phase,
or, for `purge`, for its whole run), or self-locking (a long-running
command that takes it per unit of work, never for its own lifetime). A
command added without a classification MUST fail a test. `openkos daemon`
MUST be self-locking, `openkos pending` MUST be read-only, `openkos export`
MUST be read-only (it writes only outside the workspace, and guards its
snapshot by re-reading its inputs instead of locking; see `okf-export`),
and `openkos query` MUST be locked, with a commit phase that exists only
under `--save`.

#### Scenario: A new command without a class fails

- GIVEN a command registered without being classified
- WHEN the classification test runs
- THEN it fails naming that command

#### Scenario: Export does not wait on a writer

- GIVEN one process holds the workspace lock for its commit phase
- WHEN `openkos export out/ --auto` runs
- THEN it is not refused for lock contention
### Requirement: Interactive Contention Fails Fast Unless `--wait` Is Given

When a locked command cannot acquire the lock, it MUST by default refuse at
once with exit `3` and the existing busy wording, having read and written
nothing in its commit phase. Every locked command MUST accept
`--wait <seconds>`: a non-negative integer bound on how long to retry
acquisition, with backoff, before refusing the same way. A value that is
not a non-negative integer, or exceeds the documented maximum, MUST be
refused with exit `2` before any work. While waiting, the command MUST
print one stderr line saying it is waiting and for how long at most.
`--wait 0` MUST behave as the default.

#### Scenario: Default contention exits 3

- GIVEN another process holds the lock
- WHEN a locked command runs without `--wait`
- THEN it exits `3` with the busy wording

#### Scenario: A short wait succeeds once the holder releases

- GIVEN another process holds the lock and releases it after one second
- WHEN a locked command runs with `--wait 10`
- THEN it acquires the lock, completes, and exits `0`

#### Scenario: An expired wait refuses as retry-safe

- GIVEN another process holds the lock for longer than the bound
- WHEN a locked command runs with `--wait 1`
- THEN it exits `3` and its commit phase wrote nothing

### Requirement: The Lock Primitive Stays Non-Blocking

The lock module MUST expose only a non-blocking acquisition. Any bounded
wait MUST be a retry loop around it supplied by the caller's adapter, with
the bound chosen by that adapter.

#### Scenario: No blocking acquire exists

- GIVEN the lock module
- WHEN its public surface is inspected
- THEN it offers no acquisition that blocks without a caller-supplied bound

### Requirement: Derived-Store Contention Is The Same Retry-Safe Refusal

A SQLite lock-contention error (`SQLITE_BUSY` or `SQLITE_LOCKED`, decided by
error code, never by message text) from any derived or findings store that
no verb-specific handler took MUST refuse with exit `3`, with one uniform
message that names the verb and says another OpenKOS process is using the
workspace's derived stores. It MUST NOT name a specific concurrent verb.
Any other operational error MUST keep its existing handling.

#### Scenario: A locked findings store exits 3

- GIVEN another connection holds a write transaction on `findings.db` past
  the busy timeout
- WHEN a locked verb that writes findings reaches that write
- THEN it exits `3` with the uniform message and no traceback

### Requirement: The Lock File Lives In A Per-User State Directory

The lock file MUST be named by the sha256 of the workspace root's real path
and MUST live in a `locks` directory under the account's OpenKOS state
directory: `~/.local/state/openkos/` on Linux and other POSIX systems,
`~/Library/Application Support/openkos/` on macOS, and
`%LOCALAPPDATA%\openkos\` on Windows. On POSIX the home directory MUST be
taken from the account database for the effective uid, not from `HOME` or
`XDG_STATE_HOME`. The lock file MUST NOT be created inside the workspace.
Directories the lock creates MUST be created owner-only, and on POSIX the
`locks` directory MUST be refused as a plain refusal (exit `1`, naming the
directory and the fix) when it is a symlink, not a directory, not owned by
the effective uid, or accessible to group or other.

#### Scenario: Two spellings of one workspace share one lock

- GIVEN a workspace reached through a symlinked path and through its real
  path
- WHEN two processes lock it through the two spellings
- THEN the second is refused for contention

#### Scenario: The environment does not move the lock

- GIVEN two processes of the same user with different `HOME` and
  `XDG_STATE_HOME` values
- WHEN both lock the same workspace
- THEN the second is refused for contention

#### Scenario: A refusing command leaves the workspace untouched

- GIVEN a workspace and a busy lock
- WHEN a locked command refuses for contention
- THEN the workspace's files and directories are byte- and
  structure-identical to before the run

#### Scenario: A squatted lock directory is refused

- GIVEN the `locks` directory exists and is owned by another account
- WHEN a locked command runs
- THEN it exits `1` naming the directory and the fix

### Requirement: No Legacy Temp-Directory Lock Is Taken

A lock acquisition MUST take only the state-directory lock. It MUST NOT
acquire or create a lock under the OS temp directory, so an `openkos` of 0.3.0
or older, which locks only that path, does not exclude a current one and the
two MUST NOT run concurrently against the same workspace.

#### Scenario: A lock held on the old temp-directory path does not refuse a run

- GIVEN a lock held on the pre-relocation temp-directory path for a workspace
- WHEN a locked command runs on the same workspace
- THEN it acquires the state-directory lock and is not refused
