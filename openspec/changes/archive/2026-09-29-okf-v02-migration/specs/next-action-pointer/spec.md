# Delta for Next-Action Pointer

## MODIFIED Requirements

### Requirement: No-Runnable-Action Output Never Claims Cleanliness

WHEN none of the four ranked tiers produces a finding, `openkos next` MUST
print a line naming `openkos status` as the place to see the full report,
and MUST NOT state or imply that the bundle is clean, free of issues, or has
nothing needing attention. This output MUST be the same regardless of
whether commandless findings (conformance, dangling,
multi-source-uncovered) exist in the bundle, because `next`'s short-circuit
means it never proves their absence.

Declinations and skip notices are NOT commandless findings and are exempt
from that sameness rule: both name specific documents this run actually
observed, so withholding them to keep the output uniform would trade an
honest report for a tidy one.
(Previously: the example commandless finding cited "a §9 conformance
violation"; OKF v0.2 renumbers the conformance section to §11 (v0.2
§13.1); the requirement's behavior is unchanged.)

#### Scenario: No ranked tier fires on a truly empty bundle

- GIVEN a freshly initialized workspace with no sources ingested and a
  present, populated vector index
- WHEN `openkos next` runs
- THEN it prints a line naming `openkos status`, and no wording claims the
  bundle is clean or issue-free

#### Scenario: No ranked tier fires despite commandless findings existing

- GIVEN a bundle with a present vector index, no unextracted sources, no
  below-source-sensitivity descendants, no exact-title duplicate groups, and
  at least one commandless finding (a §11 conformance violation, a dangling
  reference, or a multi-source-uncovered descendant)
- WHEN `openkos next` runs
- THEN it still prints the same no-runnable-action line naming `openkos
  status`, and does not claim the bundle is clean
