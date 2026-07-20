# Delta for Entity-Resolution Merge

## ADDED Requirements

### Requirement: Non-Silent Guard For Edge-Bearing Merge

Before Phase B writes, `merge` MUST check whether the absorbed object bears
outbound `relations:` entries OR is the target of any inbound typed
relation from another object in the bundle. WHEN either holds, `merge` MUST
NOT proceed silently: it MUST warn or refuse (the exact choice is fixed by
design) so the user is made aware before any write. Full reversible
rewiring of typed edges through merge/unmerge is OUT OF SCOPE for this
slice (Non-Goal — deferred to a later slice).

#### Scenario: Merge of an object with outbound relations surfaces a guard

- GIVEN the absorbed object has at least one outbound `relations:` entry
- WHEN `merge <survivor> <absorbed>` runs
- THEN the guard is triggered (warn or refuse) before any write occurs,
  never silently

#### Scenario: Merge of an inbound relation target surfaces a guard

- GIVEN another object in the bundle has a `relations:` entry whose
  `target` is the absorbed object
- WHEN `merge <survivor> <absorbed>` runs
- THEN the guard is triggered (warn or refuse) before any write occurs,
  never silently

#### Scenario: Merge of an object with no typed relations proceeds unaffected

- GIVEN the absorbed object has no outbound `relations:` entries and is not
  the target of any inbound typed relation
- WHEN `merge <survivor> <absorbed>` runs
- THEN merge proceeds exactly as specified in the existing merge
  requirements, with no guard triggered
