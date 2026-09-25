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

`type_alternative` is EXCLUDED from the generic fill-the-gap branch: the
absorbed side's value MUST NEVER be imported into the merged document
(issue #803). It records ONE extraction's uncertainty about ONE document's
classification, not a property of the entity, so importing it manufactures
doubt no extraction ever expressed about the survivor — the reported case
came out of a merge newly flagged as possibly an `Organization`. The
exclusion also removes a latent hazard: the concept builder REFUSES
`type_alternative == type`, while the merge path has no such check, so
inheritance could leave a survivor carrying `type: X` plus
`type_alternative: X`, a state the builder will not produce. A survivor
carrying its OWN `type_alternative` MUST keep it; the absorbed document's
value MUST be restored to it unchanged by `unmerge`.

`event_date` is likewise EXCLUDED from the generic fill-the-gap branch:
the absorbed side's value MUST NEVER be imported onto a survivor that
lacks its own (issue #1014c). It records evidence about WHEN a single
Source's event happened, not a property that generalizes to a merged
entity, so importing it would stamp a date onto a survivor whose own
content carries no such evidence. A survivor carrying its OWN `event_date`
MUST keep it, unaffected by the absorbed side's value, whatever that value
is. A survivor with none MUST remain without one after the merge;
`unmerge` MUST restore the absorbed document's own `event_date`, if it had
one, unchanged.
(Previously: `event_date` did not exist; this exclusion did not apply to
it.)

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

#### Scenario: The absorbed `type_alternative` does not cross the merge

- GIVEN a survivor with no `type_alternative` and an absorbed object
  declaring one
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document carries no `type_alternative`, and `unmerge`
  restores the absorbed document's own value unchanged

#### Scenario: The survivor keeps its own `type_alternative`

- GIVEN both sides declaring a DIFFERENT `type_alternative`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document carries the survivor's value

#### Scenario: The absorbed event_date does not cross the merge

- GIVEN a survivor with no `event_date` and an absorbed object declaring
  `event_date: 2026-07-14`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document carries no `event_date`, and `unmerge` restores
  the absorbed document's `2026-07-14` value unchanged

#### Scenario: The survivor keeps its own event_date

- GIVEN a survivor declaring `event_date: 2026-07-14` and an absorbed
  object declaring a DIFFERENT `event_date: 2026-08-01`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document's `event_date` is `2026-07-14`, the survivor's
  own value
