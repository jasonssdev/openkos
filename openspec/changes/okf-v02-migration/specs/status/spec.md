# Delta for Status

## RENAMED Requirements

### Requirement: Needs-Attention via §9 Conformance → Needs-Attention via §11 Conformance

(Reason: OKF v0.2 renumbers the conformance section from §9 to §11 (v0.2 §13.1), and the requirement name embeds the section number.)
(Migration: None — behavior is unchanged; `check_conformance` findings still surface under the "needs attention" section; only the section reference in the name and body updates.)

## MODIFIED Requirements

### Requirement: Needs-Attention via §11 Conformance

`openkos status` MUST surface OKF §11 conformance findings (unparseable
frontmatter, missing/empty `type`) by reusing `check_conformance`, under a
"needs attention" section. Findings MUST be informational: their presence
MUST NOT cause a non-zero exit.
(Previously: named "Needs-Attention via §9 Conformance" and cited OKF §9
conformance; OKF v0.2 renumbers the conformance section to §11 (v0.2
§13.1); the underlying `check_conformance` behavior is unchanged.)

#### Scenario: No conformance issues
- GIVEN a bundle where every non-reserved file passes `check_conformance`
- WHEN `openkos status` runs
- THEN it reports a "no issues" needs-attention line and exits 0

#### Scenario: Conformance violation is surfaced but non-fatal
- GIVEN a bundle containing a concept file with a missing `type` field
- WHEN `openkos status` runs
- THEN the violation is listed under "needs attention" and the command still
  exits 0

### Requirement: Needs-Attention Surfaces Dangling References

`openkos status` MUST fold `lint`'s dangling-reference findings
(`check_dangling_targets`) into its "needs attention" section, alongside
§11 conformance findings. Each surfaced entry MUST name the referring
document and the missing target id. Findings MUST be informational: their
presence MUST NOT cause a non-zero exit.
(Previously: cited "§9 conformance findings"; OKF v0.2 renumbers the
conformance section to §11 (v0.2 §13.1); the folding behavior is
unchanged.)

#### Scenario: Dangling reference is surfaced under needs attention
- GIVEN a bundle containing a concept document whose `relations:` target or
  body link resolves to a concept id absent from disk
- WHEN `openkos status` runs
- THEN the dangling reference is listed under "needs attention", naming the
  referring document and the missing target id, and the command still
  exits 0

#### Scenario: Purge-created dangling reference is detected by status
- GIVEN a concept document referencing concept `<id>`, and `<id>` is then
  removed by `openkos purge <id> --force` leaving the referring document's
  reference dangling
- WHEN `openkos status` runs afterward
- THEN the dangling reference is listed under "needs attention"

#### Scenario: No dangling references, no new needs-attention entries
- GIVEN a bundle where every `relations:` target and resolvable body link
  points to a concept id present on disk
- WHEN `openkos status` runs
- THEN no dangling-reference entry appears under "needs attention"
