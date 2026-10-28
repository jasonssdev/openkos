# Stable Python API Specification (delta)

## ADDED Requirements

### Requirement: The Inbox Watch Folder Is Read And Changed Against Its Version

`openkos.api` MUST offer `inbox_settings(workspace)`,
`preview_inbox_settings(workspace, change)` and
`apply_inbox_settings(workspace, version, wait_seconds=...)`. The read MUST take
no lock and write nothing. The preview MUST return the settings now and after
and a version, or an `InboxSettingsRefused` with a typed reason; the apply MUST
write only the `unattended.inbox` line of `openkos.yaml`, commit only it, and
return `InboxSettingsStale` when the file or version changed. None of them MUST
start or stop a process.

#### Scenario: A change is applied against its version

- GIVEN a preview of a folder
- WHEN its version is applied unchanged
- THEN only `unattended.inbox` is written and committed, and the settings read
  back name the folder

### Requirement: The Latest Unattended Outcome Is Read Without A Lock

`openkos.api` MUST offer `unattended_activity(workspace)` returning an
`UnattendedActivity` with `last_job` (the newest job of any kind, or `None`),
`budget_exhausted` and `jobs_unreadable`. `budget_exhausted` MUST be true only
when the newest watch or maintenance job ended `budget_exhausted`; a commit retry
MUST NOT end it and a pass still running MUST NOT count. The read MUST take no
lock, build no model client, write nothing and never create the job record; a
record that exists but cannot be read MUST be reported through
`jobs_unreadable`, never read as no history.

#### Scenario: A spent budget is reported with its job

- GIVEN the newest watch job ended `budget_exhausted`
- WHEN `unattended_activity(workspace)` is called
- THEN `budget_exhausted` is true and `last_job` names that job

#### Scenario: A later clean pass ends the stop

- GIVEN a watch job that ended `budget_exhausted` and a later maintenance job
  that completed
- WHEN `unattended_activity(workspace)` is called
- THEN `budget_exhausted` is false
