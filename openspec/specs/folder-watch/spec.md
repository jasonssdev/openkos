# Folder Watch Specification

## Purpose

A source dropped into a watched folder is ingested without an invocation.
The watched folder is an external inbox the ingest service copies from,
never `raw/` itself, so raw immutability and Source identity are exactly
what a person's `ingest` already enforces (ADR-0038). A save is imported
only once it has settled, and a file edited after import is imported as a
new version whose supersession of the earlier Source is proposed in the
pending-work queue, never written unattended (ADR-0041).

## Non-Goals

This spec does not define: moving, renaming, or deleting inbox files; watching more than
one inbox per workspace; recursive watch semantics beyond the inbox's own
subdirectories; spending rules (`unattended-budget`); job scheduling (`job-runtime`).

## Requirements

### Requirement: The Inbox Is Configured, External, And Read-Only To The Engine

The watch MUST be off unless `unattended.inbox` is set. The value MUST be a
path, resolved against the workspace root when relative, and the config
read MUST refuse it when it resolves to, or inside, `raw/`, `bundle/`,
`.openkos/`, or the workspace root itself, or when it is an ancestor of
the workspace root (a folder that contains the workspace would feed the
engine's own files back in), or when it does not name a directory. The engine MUST NOT create, modify, rename, move, or delete any
file or directory inside the inbox.

#### Scenario: raw/ cannot be the inbox

- GIVEN `unattended: {inbox: raw}`
- WHEN the config is read
- THEN the read is refused naming `unattended.inbox`

#### Scenario: A folder containing the workspace cannot be the inbox

- GIVEN `unattended: {inbox: ..}` naming the workspace's parent directory
- WHEN the config is read
- THEN the read is refused naming `unattended.inbox`

#### Scenario: The inbox is left exactly as found

- GIVEN an inbox with files that the watch imports
- WHEN the watch job completes
- THEN every inbox file and directory has the same name, bytes, and
  modification time as before the job

### Requirement: The Watch Applies The Text-Source Allowlist

The watch is a sweep of a folder, not an explicit single-file choice, so it
MUST consider only files whose extension is on the same text-source allowlist
that `ingest` applies to a directory, matched case-insensitively. Every other
file, including an extensionless one, MUST be neither observed, counted as
waiting, nor imported. Dot-entries and symlinks stay excluded as before. The
count of files skipped for this reason MUST be recorded in the daemon log.

#### Scenario: A binary in the inbox is not imported

- GIVEN an inbox holding `note.md` and `report.docx`, both settled
- WHEN a watch job runs
- THEN only `note.md` is imported and `report.docx` is never observed

#### Scenario: Skipped files are not counted as waiting

- GIVEN an inbox holding one new `.md` file and one new `.docx` file
- WHEN a watch job runs inside the quiet window
- THEN the announcement says one file was seen

### Requirement: A File Is Imported Only After It Settles

The watch MUST treat an inbox file as a candidate only when its size and
modification time have not changed across at least `unattended.quiet_seconds`
of observation. Before committing an import, the watch MUST verify that the
bytes it imported still match the file's current bytes, and MUST treat a
mismatch as not yet settled, writing nothing for it.

#### Scenario: A file being saved is not imported

- GIVEN a file in the inbox whose modification time changed within the quiet
  window
- WHEN a watch job runs
- THEN the file is not imported in that job

#### Scenario: A file that has not settled is announced

- GIVEN a new inbox file and a TTY, with the quiet window not yet elapsed since
  the watch first observed it
- WHEN a watch job runs
- THEN nothing is imported and one line says how many files were seen and that
  they will import after `unattended.quiet_seconds` of quiet

Settling stays observation-based: the quiet window is measured from when the
watch first saw the current size and modification time, never from the file's
modification time alone, because a copy that preserves an old timestamp would
otherwise be imported before the watch ever observed it stable.

#### Scenario: A settled file is imported through the ingest service

- GIVEN a new file unchanged for longer than the quiet window
- WHEN a watch job runs
- THEN it is ingested as a person's `ingest` of that path would ingest it,
  with no consent flag and the runner's policy

### Requirement: An Already-Imported File With Unchanged Bytes Costs Nothing

The watch MUST remember, per inbox path, the size, modification time, and
digest it last observed, and MUST NOT re-read or re-hash a file whose size
and modification time are unchanged. A file whose bytes match its existing
`raw/` copy MUST NOT start an ingest and MUST NOT make a model call.

#### Scenario: A steady inbox makes no model call

- GIVEN every inbox file is already imported and unchanged
- WHEN a watch job runs
- THEN it makes no model call and imports nothing

### Requirement: A Source Changed After Import Imports As A New Version

When a settled inbox file's bytes differ from the `raw/` copy its path was
imported to, the watch MUST ingest the new bytes as a new raw copy under the
next free name of the file's collision family (`note.md`, `note-2.md`,
`note-3.md`) with a Source of its own that records the same `origin_key`. It
MUST NOT overwrite or modify the earlier raw copy or Source, and it MUST NOT
write the `supersedes` relation or any status. It MUST instead upsert one
pending-work row of kind `relation_type`, produced by `source-supersession/1`,
proposing that the new Source `supersedes` the newest earlier version, as
`openkos relate <new> supersedes <old>` (ADR-0041). Bytes identical to any
version already imported, including a restore of earlier bytes, MUST import
nothing and make no model call. A changed file whose import does not fit the
per-pass budget MUST still be refused into the queue as a `watch_refusal` row.

#### Scenario: An edit keeps both versions

- GIVEN an imported inbox file that was edited and has settled
- WHEN a watch job runs
- THEN `raw/` holds the original and the new bytes under distinct names, each
  with its own Source, and the earlier Source is unchanged

#### Scenario: The supersession is proposed, not written

- GIVEN the edit above was imported
- WHEN the queue is read
- THEN one open `relation_type` row proposes that the new Source supersedes
  the earlier one, and neither Source carries `supersedes` or a deprecated
  status

#### Scenario: A second edit supersedes the first edit

- GIVEN an inbox file imported, edited and imported again, then edited again
- WHEN the third version is imported
- THEN its row proposes superseding the second version, not the original

#### Scenario: Restoring earlier bytes imports nothing

- GIVEN an inbox file imported as two versions
- WHEN its bytes are restored to the first version and settle
- THEN no raw copy is added and no model call is made

#### Scenario: Confirming the supersession resolves the row

- GIVEN an open supersession row
- WHEN the operator runs `openkos relate <new> supersedes <old>`
- THEN the earlier Source is exported as deprecated and the row is `applied`

### Requirement: An Import Leaves The Derived Indexes Fresh

After a watch job has imported at least one file, it MUST run the incremental
derived-index refresh (`derived-index-cache`) once, after its last import,
so the imported material is searchable without waiting for a maintenance
job. A job that imports nothing MUST NOT refresh. A refresh that cannot run
(store contention, a refusal) MUST NOT change the outcome of the imports,
which are already committed; the watch MUST advise that the indexes were not
refreshed and leave the catch-up to the next maintenance job.

#### Scenario: Two imports cost one refresh

- GIVEN two settled inbox files
- WHEN a watch job imports both
- THEN the derived indexes are refreshed once, after the second import

#### Scenario: A job that imports nothing refreshes nothing

- GIVEN an inbox whose only file is still inside its quiet window
- WHEN a watch job runs
- THEN no refresh runs

#### Scenario: A refresh that cannot run does not fail the import

- GIVEN a settled inbox file and a derived store held by another process
- WHEN a watch job imports the file
- THEN the Source is imported, the job's outcome is unchanged, and the daemon advises that the indexes were not refreshed

### Requirement: A Watch Job Honours The Budget, The Stop Flag, And The Deadline

A watch job MUST import settled files in a deterministic order (by path),
MUST stop admitting files when `max_sources_per_pass` or the call budget is
reached (`unattended-budget`), and MUST check the stop flag and deadline
between files (`job-runtime`). Every file not imported for any of those
reasons MUST remain a candidate for the next job.

#### Scenario: Deferred files are picked up next time

- GIVEN `max_sources_per_pass: 1` and two settled new files
- WHEN two watch jobs run
- THEN each job imports one file, and the first records one deferred

### Requirement: A Native Notification Backend Only Wakes The Poll

`unattended.watch_backend` MUST be `poll` (the default) or `native`; any
other value MUST refuse the config read naming the key. With `native` and the
optional `openkos[watch]` extra installed, an OS file-notification event under
the inbox MAY end the daemon's idle wait early, and the pass that follows MUST
be the ordinary polling pass: every settle, re-hash and quiet-window rule of
this spec applies unchanged, and an event MUST NOT import, skip or settle a
file by itself. The periodic poll MUST keep running at its usual interval, so
a missed or coalesced event delays a file and never loses it. A burst of
events MUST be coalesced into one pass. If `native` is configured and the
extra is not installed, or the backend cannot start, the daemon MUST warn on
stderr and in its log and carry on polling; it MUST NOT refuse to run.
`openkos doctor` MUST report the line only when `native` is configured, as an
informational check that fails, with the install command, when the extra is
absent. A `--once` run MUST NOT start a notifier.

#### Scenario: An event ends the idle wait, not the quiet window

- GIVEN `watch_backend: native` and a file written to the inbox
- WHEN the backend signals a change
- THEN the daemon runs a watch pass at once, and the file is imported only
  after it has stayed unchanged for `quiet_seconds`, exactly as under polling

#### Scenario: A missing extra falls back to polling

- GIVEN `watch_backend: native` and `watchdog` not installed
- WHEN `openkos daemon` starts
- THEN it warns that it is polling instead, and the watch behaves exactly as
  under `poll`

#### Scenario: An unknown backend is refused

- GIVEN `unattended: {watch_backend: inotify}`
- WHEN the config is read
- THEN the read fails naming `unattended.watch_backend`
