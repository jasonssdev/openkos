# Delta for sensitivity-config

## MODIFIED Requirements

### Requirement: Raise-Only Propagation to Provenance Descendants

WHEN `<concept-id>` names a Source-typed concept AND the assignment raises
its `sensitivity`, the command MUST resolve every provenance descendant of
that Source and, for each, MUST compute the new value via
`okf.combine_sensitivity(existing, new)` (ADR-0003) and stage a write only
when that computation is a strict raise over the descendant's current value.
The command MUST NOT lower any descendant by this or any other path. Every
staged descendant raise MUST appear in the confirmation preview and in the
post-write success message. `--auto` MUST still perform propagation; it
MUST only skip the confirmation prompt. A provenance reference that cannot
be resolved to an existing concept MUST emit a warning, MUST be excluded
from propagation, and MUST NOT abort or block the write to the named
Source concept itself. WHEN a partial write failure occurs after one or
more descendant raises have already landed, the command MUST NOT roll back
any already-written file and its failure message MUST name every path that
already landed before the failure.
(Previously: the partial-write-failure message named none of the paths that
already landed.)

#### Scenario: Raising a Source raises every derived object in the same run

- GIVEN a Source concept with two derived concepts below its current
  `sensitivity`
- WHEN `set-sensitivity <source-id> <higher-level>` runs and is confirmed
- THEN the Source and both derived concepts are written in the same run,
  each descendant's new value equals `combine_sensitivity(old, new)`, and
  both appear in the preview and success message

#### Scenario: Lowering a Source leaves derived objects untouched

- GIVEN a Source concept and a derived concept at a lower `sensitivity`
- WHEN `set-sensitivity <source-id> <lower-level>` runs with the
  downgrade-permitting flag and is confirmed
- THEN the Source's value changes but every derived concept's frontmatter
  is byte-identical to before the run

#### Scenario: A derived object already at a higher level is not lowered

- GIVEN a derived concept whose `sensitivity` already exceeds the Source's
  new target level
- WHEN `set-sensitivity <source-id> <level>` raises the Source
- THEN that derived concept is not staged for write and its frontmatter is
  unchanged

#### Scenario: Unresolvable provenance warns, is excluded, and does not abort

- GIVEN a derived concept whose `provenance` reference does not resolve to
  any existing concept
- WHEN `set-sensitivity <source-id> <higher-level>` runs
- THEN a warning naming the dangling reference is emitted, that concept is
  excluded from propagation, and the Source concept's own write still
  succeeds

#### Scenario: A Source with zero derived objects behaves exactly as today

- GIVEN a Source concept with no provenance descendants
- WHEN `set-sensitivity <source-id> <level>` runs and is confirmed
- THEN only the Source concept's `sensitivity` changes, matching prior
  single-concept behavior

#### Scenario: `--auto` propagates without prompting

- GIVEN a Source concept with one derived concept eligible for a raise
- WHEN `set-sensitivity <source-id> <higher-level> --auto` runs
- THEN both concepts are written without any confirmation prompt, and both
  appear in the success message

#### Scenario: Partial write failure names every path that already landed (#233)

- GIVEN a Source raise staging writes for three derived concepts, where the
  write fails after the first two land but before the third
- WHEN `set-sensitivity <source-id> <higher-level>` runs and the failure
  occurs
- THEN the command exits non-zero, the first two derived concept files
  remain raised on disk, and the failure message names both of their paths
  explicitly
