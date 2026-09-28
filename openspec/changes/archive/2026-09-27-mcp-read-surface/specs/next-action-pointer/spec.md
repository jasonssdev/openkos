# Delta for Next Action Pointer

## ADDED Requirements

### Requirement: Each Recommendation And Declination Names Its Subjects

`NextAction` MUST carry an additive, structured `subjects` field —
`tuple[str, ...] | None`, defaulting to `None` — naming the concept id(s)
the finding is about, derived from the finding's own structured data, never
parsed or inferred from free-text `reason`/`detail` prose. `NextResult` MUST
carry an additive, structured `declination_subjects` field, a tuple
index-aligned one-to-one with `declinations`, holding the same
`tuple[str, ...] | None` shape per entry.

`None` MUST mean the tier's call site has not declared its subjects at all
— an undeclared state that any disclosure gate downstream MUST treat as
withholdable by construction, distinct from a declared, empty tuple. WHEN a
tier's own finding names one or more concepts, `subjects` MUST be populated
with their ids. WHEN a tier's finding does not itself resolve to a specific
concept id (a fixed-text tier naming no document), `subjects` MUST be an
explicit empty tuple `()` — a DECLARED absence of subjects, never the
unset `None` default. Every call site that produces a `NextAction` or
records a declination MUST declare `subjects` explicitly; none MUST rely on
the `None` default to mean "nothing to declare."

For the below-source-sensitivity and multi-source-uncovered tiers,
`subjects` MUST include both the finding's own concept id and every id
named by the finding's `related_ids` (see `LintFinding.related_ids` below),
since those tiers' reason text names more than one concept. For the open
contradictions tier, `subjects` MUST be the finding's own pair of concept
ids. Every other tier's `subjects` MUST be either the sole concept id its
finding is about, or `()` for a tier whose fixed text names no document.

This is purely additive: `openkos next`'s existing human-readable stdout
output MUST remain byte-identical to its output before these fields
existed.

`lint.LintFinding` MUST gain an additive `related_ids: tuple[str, ...]`
field, defaulting to `()` and excluded from equality comparison, populated
for the `below-source-sensitivity` finding with the cited Source's own id
and for the `multi-source-uncovered` finding with every id its detail
cites. No lint output rendering MUST change because of this field.

#### Scenario: A tier-2 recommendation names its Source's concept id

- GIVEN a bundle with a Source with `extraction_status: failed` and an
  intact `resource`
- WHEN `next_action` produces its tier-2 recommendation
- THEN the recommendation's `subjects` field contains that Source's own
  concept id

#### Scenario: A declination names its subject

- GIVEN a bundle with a Source with `extraction_status: failed` and no
  `resource`
- WHEN `next_action` produces the resulting declination
- THEN the matching entry in `declination_subjects` contains that Source's
  concept id, not derived from its free-text detail

#### Scenario: A tier with no resolvable subject declares an explicit empty tuple

- GIVEN a finding whose tier does not resolve to a specific concept id (for
  example a missing-index or duplicate-group tier)
- WHEN `next_action` produces that tier's result
- THEN its `subjects` field is the explicit empty tuple `()`, a declared
  absence, never the unset `None` default

#### Scenario: declination_subjects stays index-aligned with declinations

- GIVEN a bundle producing more than one declination
- WHEN `next_action` produces its result
- THEN `declination_subjects` has exactly as many entries as `declinations`,
  in the same order, one per declination

#### Scenario: A below-source-sensitivity finding's subjects include its related Source

- GIVEN a provenance descendant below its Source's sensitivity, where the
  finding's `related_ids` names that Source's concept id
- WHEN `next_action` produces the tier-3 recommendation
- THEN `subjects` contains both the descendant's own concept id and the
  Source's id from `related_ids`

#### Scenario: A multi-source-uncovered finding's subjects include every cited id

- GIVEN a multi-source-uncovered finding whose detail cites more than one
  Source
- WHEN `next_action` produces that tier's recommendation or declination
- THEN `subjects` contains the finding's own concept id together with
  every id `related_ids` names

#### Scenario: related_ids does not affect LintFinding equality

- GIVEN two `LintFinding` instances equal in every field except
  `related_ids`
- WHEN they are compared for equality
- THEN they compare equal

#### Scenario: openkos next's stdout is byte-identical

- GIVEN an unchanged bundle state, exercised before and after these fields
  exist
- WHEN `openkos next` runs
- THEN its stdout is byte-for-byte identical in both cases
