# Delta for Status

## ADDED Requirements

### Requirement: Needs-Attention Surfaces Dangling References

`openkos status` MUST fold `lint`'s dangling-reference findings
(`check_dangling_targets`) into its "needs attention" section, alongside
§9 conformance findings. Each surfaced entry MUST name the referring
document and the missing target id. Findings MUST be informational: their
presence MUST NOT cause a non-zero exit.

#### Scenario: Dangling reference is surfaced under needs attention

- GIVEN a bundle containing a concept document whose `relations:` target or
  body link resolves to a concept id absent from disk
- WHEN `openkos status` runs
- THEN the dangling reference is listed under "needs attention", naming the
  referring document and the missing target id, and the command still
  exits 0

#### Scenario: Purge-created dangling reference is detected by status

- GIVEN a concept document referencing concept `<id>`, and `<id>` is then
  removed by `openkos purge <id> --force` leaving the referring document's
  reference dangling
- WHEN `openkos status` runs afterward
- THEN the dangling reference is listed under "needs attention"

#### Scenario: No dangling references, no new needs-attention entries

- GIVEN a bundle where every `relations:` target and resolvable body link
  points to a concept id present on disk
- WHEN `openkos status` runs
- THEN no dangling-reference entry appears under "needs attention"

### Requirement: Needs-Attention Surfaces Missing Vector Index

`openkos status` MUST report, under "needs attention", whether the
workspace's `vectors.db` (`layout.vectors_db_path`) is absent from disk.
This check MUST be informational only: its presence MUST NOT cause a
non-zero exit, and `status` MUST NOT attempt to rebuild or re-embed the
index itself.

#### Scenario: Missing vectors.db is surfaced

- GIVEN a workspace whose `.openkos/vectors.db` file is absent (e.g. after
  `openkos purge`)
- WHEN `openkos status` runs
- THEN it lists the missing vector index under "needs attention" and still
  exits 0

#### Scenario: Present vectors.db produces no vector-index entry

- GIVEN a workspace whose `.openkos/vectors.db` file exists on disk
- WHEN `openkos status` runs
- THEN no missing-vector-index entry appears under "needs attention"
