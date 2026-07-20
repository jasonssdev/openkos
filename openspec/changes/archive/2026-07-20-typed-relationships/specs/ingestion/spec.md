# Delta for Ingestion

## ADDED Requirements

### Requirement: OKF §9 Conformance — `relations:` Field Shape

`check_conformance` MUST validate the `relations:` frontmatter field when
present on any document: it MUST be a list of mappings, each containing a
non-empty `target` and a non-empty `type` string. A malformed shape (not a
list, an entry missing `target`/`type`, or an entry with an empty value)
MUST be reported as a violation in the existing `f"{path}: {message}"`
shape, appended to the existing rules 1-3 violation list. For any document
without a `relations:` key, the existing rules 1-3 output MUST remain
byte-identical to before this rule was added.

#### Scenario: Malformed relations entry reported as violation

- GIVEN a document whose `relations:` list contains an entry missing
  `target` or `type`
- WHEN `check_conformance` runs
- THEN a violation naming that document's path is appended to the result

#### Scenario: Byte-identical output when relations is absent

- GIVEN a bundle with no document containing a `relations:` key
- WHEN `check_conformance` runs before and after this rule is added
- THEN the violation list is byte-identical

#### Scenario: Well-formed relations passes

- GIVEN a document with a well-formed `relations:` list
- WHEN `check_conformance` runs
- THEN no violation is reported for that document's `relations:` field
