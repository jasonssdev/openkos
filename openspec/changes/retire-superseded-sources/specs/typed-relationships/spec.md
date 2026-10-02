# Delta for Typed Relationships

## MODIFIED Requirements

### Requirement: Seeded-But-Extensible Relation Vocabulary

The known-default vocabulary is `{references, depends_on, derived_from,
related_to, caused_by, part_of, member_of, produced_by}`. The lifecycle
relation types `supersedes`, `revises` and `reconciled_with` (the resolution
types, which an LLM suggester MUST NOT propose) are recognized by the write
path and MUST NOT produce the unknown-type warning, but they are not part of
the seeded default set. Any non-empty, non-whitespace-only `rel` string MUST
be accepted for write. WHEN `rel` is neither in the known set nor a
lifecycle relation type, `relate` MUST emit a WARN to stderr but MUST NOT
reject. WHEN `rel` is empty or whitespace-only, `relate` MUST reject with no
write.

#### Scenario: Known type accepted silently

- GIVEN `rel` is `depends_on`
- WHEN `relate` runs
- THEN it writes the relation with no WARN emitted

#### Scenario: A lifecycle type is accepted silently

- GIVEN `rel` is `supersedes`
- WHEN `relate sources/v2 supersedes sources/v1` runs
- THEN it writes the relation and no "not a seeded relation type" note is
  emitted

#### Scenario: Unknown type accepted with WARN

- GIVEN `rel` is `inspired_by` (not in the known set)
- WHEN `relate` runs
- THEN it writes the relation AND emits a WARN to stderr naming the unknown
  type

#### Scenario: Empty or whitespace type rejected

- GIVEN `rel` is empty or `"   "`
- WHEN `relate` runs
- THEN it exits non-zero with a clear error and writes nothing

#### Scenario: A lifecycle type is still never suggested

- GIVEN the edge-type suggester's allowed vocabulary
- WHEN it is listed
- THEN it contains none of `supersedes`, `revises`, `reconciled_with`
