# Delta for Folder Watch

## ADDED Requirements

### Requirement: An Import Reports Its Commit As An Automatic Action

A watch job MUST report, on its result, one automatic action for each import
whose commit produced a sha: the sha, the commit summary and the Concept IDs it
committed. An import whose commit was skipped or failed (no repository, identity
unset, a git error) MUST report none, since there is no commit to name. The
ingest service and the attended `ingest` verb MUST be unchanged by this: the
action is recorded where the `autocommit` port returns, not read back later.

#### Scenario: A committed import is reported

- GIVEN a settled file whose import commit returned a sha
- WHEN the watch job ends
- THEN its result carries one action holding that sha and the import's concept
  ids

#### Scenario: A skipped commit reports nothing

- GIVEN a settled file whose import could not be committed
- WHEN the watch job ends
- THEN its result carries no action
