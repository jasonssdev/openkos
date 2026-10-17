# Delta for pending-work

## ADDED Requirements

### Requirement: A Reconcile Resolves The Revision Row Over Its Pair

The reconcile write core MUST resolve, besides the typed-edge contradiction row
over the pair, the open `revision` row over the same pair, as `applied`: as
proposed when the edge written is the one the row's verdict and dates name (a
`supersedes` for a reversal, a `revises` for a refinement, held by the later
Decision, or either holder when the dates do not order the pair), and modified
otherwise (a symmetric `reconciled_with`, the other edge type or the other
holder). Like every resolution it is best-effort: an absent or unreadable queue
leaves the write in place.

#### Scenario: A directed reconcile applies the revision row as proposed

- GIVEN an open revision row whose verdict is a reversal and whose later Decision
  is A
- WHEN `openkos reconcile A B --winner A` runs
- THEN the revision row is `applied` as proposed

#### Scenario: A symmetric reconcile applies the revision row modified

- GIVEN an open revision row over A and B
- WHEN `openkos reconcile A B` runs
- THEN the revision row is `applied` modified
