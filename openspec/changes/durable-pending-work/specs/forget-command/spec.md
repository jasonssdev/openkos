# Delta for Forget Command

## ADDED Requirements

### Requirement: Forget Sweeps Live Decision Entries Referencing The Purge Set

`openkos forget` MUST remove, from the LIVE working tree only (`forget`
performs no history rewrite), any `bundle/.state/**` decision entry
(contradiction decline/re-open) that references a purge-set member's
concept id, so a forgotten concept does not leave an orphaned decision
pointing at an id no longer present in the bundle.

#### Scenario: Forgetting a concept removes its live decision entry

- GIVEN a `bundle/.state/**` decision file references concept id `<id>`
- WHEN `openkos forget <id>` completes Phase B successfully
- THEN the live decision entry referencing `<id>` is removed, and no
  `bundle/.state/**` file in the working tree still references it

#### Scenario: An unrelated decision entry is preserved

- GIVEN a decision file references a concept unrelated to the forgotten
  concept-id
- WHEN `openkos forget <id>` completes
- THEN that unrelated decision entry is left unchanged
