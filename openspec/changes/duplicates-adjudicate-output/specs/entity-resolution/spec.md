# Delta for Entity-Resolution Candidates

## ADDED Requirements

### Requirement: Leading Candidate-Group Tally Line

When `openkos duplicates` finds one or more candidate groups, the FIRST line
of stdout MUST be `N candidate group(s) (X exact, Y near)`, where `N` is the
total group count, `X` is the count of HIGH-tier groups, `Y` is the count of
LOW-tier groups, and `group(s)` MUST pluralize correctly for `N`. This line
is additive only; existing per-group detail lines are unchanged.

#### Scenario: Single group

- GIVEN a bundle with exactly one HIGH-tier candidate group
- WHEN `duplicates` runs
- THEN the first stdout line is `1 candidate group (1 exact, 0 near)`

#### Scenario: Multiple mixed exact/near groups

- GIVEN a bundle with two HIGH-tier and three LOW-tier candidate groups
- WHEN `duplicates` runs
- THEN the first stdout line is `5 candidate groups (2 exact, 3 near)`

#### Scenario: All-exact groups

- GIVEN a bundle with three HIGH-tier candidate groups and no LOW-tier groups
- WHEN `duplicates` runs
- THEN the first stdout line is `3 candidate groups (3 exact, 0 near)`

#### Scenario: All-near groups

- GIVEN a bundle with two LOW-tier candidate groups and no HIGH-tier groups
- WHEN `duplicates` runs
- THEN the first stdout line is `2 candidate groups (0 exact, 2 near)`

### Requirement: One-Time Trigger-Column Legend Line

When at least one candidate group is printed, `duplicates` MUST print
exactly one legend line explaining the `[tier] type -- trigger` columns
(trigger = normalized key for HIGH, similarity ratio for LOW), placed after
the tally and BEFORE the group loop. The legend MUST NOT repeat per group.

#### Scenario: Legend appears once regardless of group count

- GIVEN a bundle with four candidate groups
- WHEN `duplicates` runs
- THEN the legend line appears exactly once in stdout, before the first
  group's detail lines

### Requirement: Trailing Next-Action Hint

When at least one candidate group is printed, the LAST line of `duplicates`
stdout MUST be `Next: openkos merge <survivor> <absorbed>`.

#### Scenario: Hint is the final line

- GIVEN a bundle with at least one candidate group
- WHEN `duplicates` runs
- THEN the last stdout line is `Next: openkos merge <survivor> <absorbed>`

### Requirement: Empty State Stays Single-Line

WHEN `duplicates` finds zero candidate groups, stdout MUST contain ONLY the
existing `"No candidates found."` line — no tally, no legend, and no
`Next:` hint.

#### Scenario: Zero groups print only the existing message

- GIVEN a bundle with no candidate groups
- WHEN `duplicates` runs
- THEN stdout is exactly `"No candidates found."` with no additional lines

### Requirement: Reusable Group-Tally Formatting Helper

The system MUST provide a pure formatting helper, sibling to
`_format_type_tally`, that renders the tally requirement's line from the
per-tier counts (HIGH/exact and LOW/near), reusing `_plural`. Given all-zero
counts it MUST return `""`. Its argument shape is an implementation detail;
only the returned string and the empty-on-zero contract are observable. This
helper MUST NOT be `_format_type_tally` itself (that helper stays
extraction-specific).

#### Scenario: Zero counts yield empty string

- GIVEN the helper is called with all-zero tier counts
- WHEN it runs
- THEN it returns `""`

#### Scenario: Populated counts yield the tally line

- GIVEN the helper is called with counts for HIGH and LOW tiers
- WHEN it runs
- THEN it returns the `N candidate group(s) (X exact, Y near)` line matching
  those counts

### Requirement: Existing Detail Lines Stay Byte-Identical

All per-group detail lines emitted by `duplicates` before this change MUST
remain byte-identical after adding the tally, legend, and hint lines.

#### Scenario: Pre-existing substring assertions still pass

- GIVEN any pre-existing CliRunner test asserting a per-group detail
  substring on `duplicates` output
- WHEN `duplicates` runs after this change
- THEN that substring is still present, unchanged
