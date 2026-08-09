# Delta for Lint

## MODIFIED Requirements

### Requirement: Non-NFC Names Scan

`openkos lint` MUST flag every on-disk name (file OR directory) under
`bundle_dir` that is not NFC (Unicode Normalization Form C) as a
`non-nfc-name` finding, one finding per decomposed directory covering its
entire subtree rather than one per descendant. This scan MUST remain
read-only: it MUST NOT write, rename, or delete any bundle file, and MUST
NOT gate the command's exit code (informational only, per the Non-Gating
Exit Contract). The finding's detail MUST name both the raw on-disk
spelling and the NFC target spelling, and MUST remediate by naming
`openkos normalize-names` as the command that performs the rename — it
MUST NOT assert that openkos never renames.

#### Scenario: A decomposed on-disk name is flagged

- GIVEN an on-disk file or directory name under `bundle_dir` that is not
  NFC
- WHEN `openkos lint` runs
- THEN it reports a `non-nfc-name` finding naming the raw spelling and the
  NFC target spelling

#### Scenario: A decomposed directory produces one finding for its whole subtree

- GIVEN a decomposed directory containing offending descendant entries
- WHEN `openkos lint` runs
- THEN it reports exactly one `non-nfc-name` finding for the directory,
  not one per descendant

#### Scenario: The remediation names normalize-names, not "openkos never renames"

- GIVEN a `non-nfc-name` finding
- WHEN its detail text is inspected
- THEN it names `openkos normalize-names` as the command that performs
  the rename, and it does not assert that openkos never renames

#### Scenario: The scan stays read-only and non-gating

- GIVEN a bundle containing one or more `non-nfc-name` findings
- WHEN `openkos lint` runs
- THEN it reports the findings, still exits 0, and no bundle file is
  created, modified, renamed, or deleted
