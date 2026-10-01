# Folder Watch Specification

## Purpose

A source dropped into a watched folder is ingested without an invocation.
The watched folder is an external inbox the ingest service copies from,
never `raw/` itself, so raw immutability and Source identity are exactly
what a person's `ingest` already enforces (ADR-0038). A save is imported
only once it has settled, and a file edited after import is refused into
the pending-work queue once instead of producing an error on every save.

## Non-Goals

This spec does not define: importing a changed source as a new version
(deferred); moving, renaming, or deleting inbox files; watching more than
one inbox per workspace; recursive watch semantics beyond the inbox's own
subdirectories; an OS file-notification backend (the watch polls);
spending rules (`unattended-budget`); job scheduling (`job-runtime`).

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

### Requirement: A Source Changed After Import Is Refused Into The Queue Once

When a settled inbox file's bytes differ from the `raw/` copy its path was
imported to, the watch MUST NOT ingest it and MUST NOT retry it. It MUST
upsert one pending-work row of kind `watch_refusal`, reason `source changed
after import`, keyed by the Source concept id and digested over the file's
current bytes, whose text names the inbox path and the remedy: rename the
file in the inbox, or ingest it under a different name. The same bytes MUST
NOT produce a second open row. The row MUST be retired as `stale` when the
file is removed from the inbox or its bytes return to the imported bytes,
and MUST move to `applied` when a raw copy with exactly the refused bytes
lands.

#### Scenario: Repeated saves produce one row

- GIVEN an imported inbox file that was edited and has settled
- WHEN two watch jobs run with no further edit
- THEN exactly one open `watch_refusal` row exists for its Source and no
  ingest was attempted

#### Scenario: A further edit replaces the row

- GIVEN an open `watch_refusal` row for a Source
- WHEN the inbox file is edited again and settles
- THEN the old row is `stale` and one new open row carries the new digest

#### Scenario: Renaming in the inbox re-imports deliberately

- GIVEN an open `watch_refusal` row for inbox file `note.md`
- WHEN the user renames it to `note-v2.md` and it settles
- THEN `note-v2.md` is imported as a new Source and the row moves to
  `applied`

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
