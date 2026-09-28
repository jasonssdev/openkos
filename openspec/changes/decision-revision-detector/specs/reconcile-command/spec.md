# Delta for Reconcile Command

## ADDED Requirements

### Requirement: Revision Findings Walk

`reconcile --from-findings` MUST also walk fresh, high-confidence revision
findings (from the `decision-revision-detection` capability), per item and
TTY-only, using the same confirm-gate precedence the existing findings walk
uses. A finding is "fresh" for this walk only while its pair carries no
`supersedes`, `reconciled_with`, or `revises` edge in either direction; once
such an edge exists (written by this walk or by hand), that finding MUST
NOT be offered again. For each such finding:

- A `REVERSES` verdict, WHEN direction is known, MUST be offered as a
  directional `supersedes` edge, written by `_reconcile_pair`'s `--winner`
  mode, held by the LATER Decision (the loser is the earlier Decision).
- A `REFINES` verdict, WHEN direction is known, MUST be offered as a
  directional `revises` edge, written by `_reconcile_pair`'s `--revision`
  mode, held by the LATER Decision.
- WHEN the finding's direction is unknown (an untyped change, per the
  `decision-revision-detection` capability's own requirement), the walk
  MUST ask the human ONCE, in a single combined choice, both which of the
  two Decisions is later AND which relation type to write (`supersedes` or
  `revises`) — or to skip the item. This MUST NOT be split into a
  "which is later" question followed by a separate confirm-the-inferred-type
  step: for an undirected pair the system infers no relation type at all
  (the `decision-revision-detection` capability's untyped-change
  requirement), so there is nothing to confirm — the human's single answer
  supplies both the holder and the edge type used for the write.
- `REAFFIRMS` and `UNRELATED` findings MUST NEVER be offered by this walk.

For a DIRECTED finding, this walk MUST NOT let the human override the
verdict kind (`REVERSES` vs `REFINES`): a human who disagrees with the
detected kind MUST skip the item and use `reconcile --winner`/`--revision`
directly. This override restriction does not apply to an UNDIRECTED
(untyped-change) finding, because the system infers no kind for one in the
first place — there, the human's combined answer supplies the relation
type the system never detected, which is choosing, not overriding. The
existing contradiction-findings walk MUST be unaffected by this addition.

#### Scenario: A REVERSES finding is offered as supersedes held by the later Decision

- GIVEN a fresh, high-confidence `REVERSES` finding between Decisions
  `alpha` (earlier) and `beta` (later), with a known direction
- WHEN `reconcile --from-findings` walks this finding and the operator
  accepts
- THEN `beta` gains a single outbound `supersedes` edge targeting `alpha`

#### Scenario: A REFINES finding is offered as revises held by the later Decision

- GIVEN a fresh, high-confidence `REFINES` finding between Decisions
  `alpha` (earlier) and `beta` (later), with a known direction
- WHEN `reconcile --from-findings` walks this finding and the operator
  accepts
- THEN `beta` gains a single outbound `revises` edge targeting `alpha`

#### Scenario: Unknown direction asks once for both the later Decision and the relation type

- GIVEN a fresh, high-confidence `REVERSES` (or `REFINES`) finding between
  Decisions `alpha` and `beta` whose direction is unknown (an untyped
  change)
- WHEN `reconcile --from-findings` walks this finding
- THEN the operator is asked, in ONE combined question, which of `alpha` or
  `beta` is the later Decision AND which relation type to record
  (`supersedes` or `revises`) — or to skip — and the single answer supplies
  both the holder and the edge type used for the write, with no separate
  confirm step afterward

#### Scenario: Skipping an unknown-direction item writes nothing

- GIVEN a fresh, high-confidence finding whose direction is unknown
- WHEN the operator is asked the combined later-Decision-and-type question
  and chooses to skip
- THEN no edge is written for that pair, and the walk continues to the next
  item

#### Scenario: REAFFIRMS and UNRELATED are never offered

- GIVEN persisted findings including a `REAFFIRMS` verdict and an
  `UNRELATED` verdict
- WHEN `reconcile --from-findings` walks revision findings
- THEN neither the `REAFFIRMS` nor the `UNRELATED` finding is offered as an
  item

#### Scenario: A resolved pair's finding is not offered again

- GIVEN a revision finding whose pair already carries a `supersedes`,
  `reconciled_with`, or `revises` edge (written by a prior walk or by hand)
- WHEN `reconcile --from-findings` walks revision findings again
- THEN that finding is not offered

#### Scenario: The walk cannot override a directed finding's detected kind

- GIVEN a fresh, high-confidence `REVERSES` finding with a KNOWN direction
- WHEN the operator disagrees with the detected kind
- THEN the walk offers no way to write `revises` instead of `supersedes`
  for that item; the operator must skip it and run
  `reconcile --winner`/`--revision` by hand

#### Scenario: Contradiction findings walk is unaffected

- GIVEN a bundle with both pending contradiction findings and pending
  revision findings
- WHEN `reconcile --from-findings` runs
- THEN the existing contradiction-findings walk behaves exactly as before
  this addition
