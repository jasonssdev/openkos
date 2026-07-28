# Delta for sensitivity-config

## MODIFIED Requirements

### Requirement: Scope Is Exactly One Named Concept

The verb MUST affect only the frontmatter of the one concept named by
`<concept-id>`, PLUS, when `<concept-id>` resolves to a Source-typed concept,
raise-only propagation to its provenance descendants as defined by the
"Raise-Only Propagation to Provenance Descendants" requirement below. It MUST
NOT modify, read for the purpose of propagation, or otherwise touch any
sibling concept's `sensitivity` value, and MUST NOT touch a non-Source
target's derived concepts. The `--help` text and the post-write success
message MUST each state this bounded scope honestly (one concept, plus
Source-to-descendant raises when applicable).
(Previously: scope was exactly one concept with no propagation of any kind.)

#### Scenario: Sibling concepts and a non-Source target's derived concepts are untouched

- GIVEN a workspace containing a source concept and its derived concepts,
  where `<concept-id>` targets a derived (non-Source) concept
- WHEN `set-sensitivity <concept-id> <level>` runs and is confirmed
- THEN only the target concept's `sensitivity` changes; every sibling and
  every other concept's frontmatter is byte-identical to before the run

## ADDED Requirements

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
Source concept itself.

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
