# Delta for Ingestion

## ADDED Requirements

### Requirement: Extraction Runs Without The Workspace Lock

`ingest` MUST perform source validation, staging, extraction, and the
confirmation question without holding the workspace lock, and MUST take it
only for its commit phase (`workspace-lock`). In a batch, each file MUST be
its own commit phase, so the lock is never held across files. The commit
phase MUST re-validate the drift-guarded targets and the documents whose
sensitivity decided the Source's or a derived object's level, and MUST
re-compose `index.md` and `log.md` from their current bytes rather than
refuse when only those changed.

#### Scenario: A batch does not hold the lock between files

- GIVEN `openkos ingest notes/ --auto` over three files
- WHEN it is extracting the second file
- THEN another process's locked verb is not refused for contention

#### Scenario: A concurrent append does not refuse an ingest

- GIVEN an ingest whose extraction finished
- WHEN another process appended to `index.md` before the ingest's commit
  phase
- THEN the ingest writes and both entries are in `index.md`

### Requirement: Batch `--auto` Is Bounded By The Unattended Budget

A batch `ingest --auto` MUST admit files in its existing order while each
file's call estimate fits the calls remaining and `max_sources_per_pass` is
not reached (`unattended-budget`), MUST NOT start a file it cannot admit,
and MUST print one stderr line naming the limit reached and the number of
files deferred. The batch exit ladder is unchanged for the files it
started; deferred files MUST NOT turn a successful batch into a failure.

#### Scenario: Deferred files do not fail the batch

- GIVEN `max_sources_per_pass: 2` and five new files
- WHEN `openkos ingest notes/ --auto` runs and the two admitted files
  succeed
- THEN it exits `0` and reports three deferred
