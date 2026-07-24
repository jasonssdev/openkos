# Delta for Lint

## ADDED Requirements

### Requirement: Dangling-Reference Scan

`openkos lint` MUST flag any concept document whose outbound reference names
a concept id absent from disk as a dangling-reference finding. An outbound
reference is either (a) a `relations:` frontmatter target id, or (b) a body
markdown bundle link resolved via the same `normalize_link` resolution
`lint`'s orphan-page scan already uses. The check MUST run beside
`check_orphans` (a new `check_dangling_targets(docs)`), scan every
non-reserved bundle document, and report each finding as the referring
document's id/path plus the missing target id. The scan MUST NOT write,
modify, or delete any bundle file, and MUST NOT gate the command's exit
code (informational only, per the Non-Gating Exit Contract).

#### Scenario: relations: target absent from disk is flagged

- GIVEN a concept document with a `relations:` entry naming a target concept
  id that has no corresponding file on disk
- WHEN `openkos lint` runs
- THEN it reports a dangling-reference finding naming the referring document
  and the missing target id

#### Scenario: Body markdown bundle link to an absent id is flagged

- GIVEN a concept document whose body contains a markdown link that
  `normalize_link` resolves to a concept id absent from disk
- WHEN `openkos lint` runs
- THEN it reports a dangling-reference finding naming the referring document
  and the missing target id

#### Scenario: Reference to an existing concept is not flagged

- GIVEN a concept document whose `relations:` target and body links all
  resolve to concept ids present on disk
- WHEN `openkos lint` runs
- THEN no dangling-reference finding is reported for that document

#### Scenario: Purge leaves a referring document detectably dangling

- GIVEN a concept document referencing concept `<id>` via `relations:` or a
  body link, and `<id>` is then removed by `openkos purge <id> --force`
- WHEN `openkos lint` runs afterward
- THEN it reports a dangling-reference finding for the referring document
  naming `<id>` as the missing target

#### Scenario: Dangling-reference findings do not change the exit contract

- GIVEN a bundle containing one or more dangling-reference findings
- WHEN `openkos lint` runs
- THEN it reports the findings and still exits 0, and no bundle file is
  created, modified, or deleted
