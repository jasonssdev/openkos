# Sensitivity Config Specification (delta)

## ADDED Requirements

### Requirement: The Log Line Records The Pages A Change Raised

WHEN a sensitivity change raises provenance descendants, the `log.md` entry it
appends, on every surface that makes the change, MUST end with ` Also raised (<n>):`
followed by each of the n raised pages as a bundle-relative link and the level it had
before (`was '<level>'`), comma-separated and ending in a full stop. A change
that raises nothing MUST write the entry it always did. The entry is the only
record of the raised set.

#### Scenario: A Source raise records its descendants

- GIVEN a Source with one derived page at `private`
- WHEN the Source is set to `confidential`
- THEN the new `log.md` entry ends with `Also raised (1): [<id>](/<id>.md)
  (was 'private').`

#### Scenario: A change that raised nothing is unchanged

- WHEN a page that is not a Source is set to `confidential`
- THEN the entry ends with `(was 'private').`
