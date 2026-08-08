# Delta for Entity-Resolution Merge

## MODIFIED Requirements

### Requirement: Frontmatter-Conflict Resolution

| Field kind | Rule |
|---|---|
| Scalar | Survivor's value wins |
| List | Union, deduped, order-preserving |
| Freshness/`as of` | Most recent of the two |

Sensitivity is excluded (see next requirement). All conflicts MUST appear
in the Phase A preview. The `type` scalar follows the same survivor-wins
scalar rule as any other scalar field, including when survivor and
absorbed declare DIFFERENT OKF types (a cross-type merge): the merged
document's `type` MUST be the survivor's declared type, and the absorbed
object's `type` MUST be discarded without being surfaced as a "conflict"
requiring resolution — this is explicit, tested behavior, not an
incidental side effect of generic scalar-merge logic.
(Previously: the scalar-wins rule was stated generically; `type`'s
behavior on a cross-type merge was an implicit consequence never named or
pinned by a dedicated test.)

#### Scenario: Conflicting fields resolved and surfaced

- GIVEN differing scalar and list-field values on both sides
- WHEN `merge` runs
- THEN the merged scalar is the survivor's, the list is the union, and
  both conflicts were shown in the preview

#### Scenario: Survivor's type wins on a cross-type merge

- GIVEN a survivor declared `type: Concept` and an absorbed object declared
  `type: Entity`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document's `type` is `Concept`, and the absorbed object's
  `Entity` type is discarded
