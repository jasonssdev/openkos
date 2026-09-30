# Delta for OKF Format Migration

## MODIFIED Requirements

### Requirement: Legacy Lifecycle Values Are Not Deprecated

Every consumer that reads a concept's lifecycle `status` MUST treat
`"stable"`, a legacy `"active"`, `"draft"`, and an absent `status` key
identically as NOT deprecated. Only the literal value `"deprecated"`
WITHOUT a valid `status_derived_from` export marker, and the existing
inbound-`supersedes`-edge rule (`status-aware-retrieval`), MUST mark a
concept deprecated. A `"deprecated"` carrying a valid export marker is the
engine's deprecated-status export (`deprecated-status-export`), derived
from that edge rule and never read back as a declaration of its own.
(Previously: the literal `"deprecated"` always counted; no engine path wrote it, so no export marker existed.)

#### Scenario: A legacy `active` concept is not deprecated

- GIVEN a concept with `status: active` and no inbound `supersedes` edge
- WHEN its effective status is resolved by any consumer
  (`lifecycle.deprecated_concept_ids`, `list`, `concept_read`, the MCP
  concept payload)
- THEN it is reported as not deprecated

#### Scenario: A `stable` concept is not deprecated

- GIVEN a concept with `status: stable` and no inbound `supersedes` edge
- WHEN its effective status is resolved
- THEN it is reported as not deprecated

#### Scenario: An absent status is not deprecated

- GIVEN a concept with no `status` key at all
- WHEN its effective status is resolved
- THEN it is reported as not deprecated

#### Scenario: An exported `deprecated` is not a declaration

- GIVEN a concept with `status: deprecated` and `status_derived_from:
  supersedes`, and no inbound `supersedes` edge
- WHEN its effective status is resolved by any consumer
- THEN it is reported as not deprecated
