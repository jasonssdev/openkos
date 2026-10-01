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

### Requirement: A CLI Ingest Is Never Limited By The Unattended Budget

An `ingest` a person launches from the CLI -- single file or batch, attended
or `--auto`, TTY or not -- MUST NOT be limited or counted by the
`unattended:` budget, which applies only to jobs the runner starts
(`unattended-budget`). Its cost gate, `--auto` behaviour, and exit ladder
MUST be unchanged.

#### Scenario: --auto ingests more sources than max_sources_per_pass

- GIVEN `unattended: {max_sources_per_pass: 2}` and five new files
- WHEN `openkos ingest notes/ --auto` runs
- THEN all five files are ingested, no deferral is reported, and the exit
  code is what it would be without the `unattended:` section
