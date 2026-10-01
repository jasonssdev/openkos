# Delta for Privacy Purge

## ADDED Requirements

### Requirement: Purge Removes The Workspace's Unattended Records And Logs

`openkos purge` MUST delete `.openkos/jobs.db` with the other stores it
deletes and name it among them, and MUST delete the workspace's daemon log
files in the per-user log directory (the files named by the sha256 of the
workspace's real path, including rotated ones). A log file it cannot delete
MUST be named in the incomplete-erasure report with its path and the manual
remedy, as for a store.

#### Scenario: A purge leaves no daemon log naming the purged concept

- GIVEN a daemon log for the workspace that names `<id>`
- WHEN `openkos purge <id>` completes
- THEN no daemon log file for the workspace remains

#### Scenario: An undeletable log is reported

- GIVEN a daemon log file that cannot be deleted
- WHEN `openkos purge <id>` completes
- THEN the report names that file and the manual remedy
