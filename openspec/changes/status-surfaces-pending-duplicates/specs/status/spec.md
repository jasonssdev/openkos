# Delta for Status

## ADDED Requirements

### Requirement: Needs-Attention Surfaces Pending Duplicate Groups

`openkos status` MUST consult `find_candidates` over the bundle (with the
default `include_deprecated=False`, offering no `--include-deprecated` flag)
and fold exact-title-match groups into "needs attention". Only exact-title
matches count toward this requirement; near-match groups MUST NOT cause
`status` to depart from `Nothing needs attention.` The surfaced line MUST
report the exact-title group count with correct singular/plural wording,
MUST name `openkos duplicates` as the next step, and MUST NOT use the words
`HIGH`, `LOW`, `exact`, or `near`, nor phrase the count as a total (the
count intentionally differs from — and is normally lower than — the count
`openkos duplicates` itself reports, once near-match groups exist). This
check MUST remain read-only and informational: its presence MUST NOT cause
a non-zero exit.

#### Scenario: No duplicate groups

- GIVEN a bundle with no candidate duplicate groups of any tier
- WHEN `openkos status` runs
- THEN no duplicate-groups entry appears under "needs attention" and the
  command still exits 0

#### Scenario: Exact-title duplicate groups are surfaced

- GIVEN a bundle containing two same-type documents sharing a normalized
  title (an exact-title-match group)
- WHEN `openkos status` runs
- THEN the group is listed under "needs attention" with the correct
  singular/plural count wording, naming `openkos duplicates` as the next
  step, without printing `HIGH`, `LOW`, `exact`, or `near`, and the command
  still exits 0 without printing `Nothing needs attention.`

#### Scenario: Only near-match groups still means nothing needs attention

- GIVEN a bundle whose only candidate duplicate groups are near-title
  matches (no exact-title-match group exists)
- WHEN `openkos status` runs
- THEN no duplicate-groups entry appears under "needs attention", the
  command still prints `Nothing needs attention.`, and it exits 0

#### Scenario: Deprecated-only duplicate group is excluded by default

- GIVEN a bundle whose only exact-title-match duplicate group consists
  entirely of deprecated concepts
- WHEN `openkos status` runs
- THEN no duplicate-groups entry appears under "needs attention" for that
  group, and the command still exits 0
