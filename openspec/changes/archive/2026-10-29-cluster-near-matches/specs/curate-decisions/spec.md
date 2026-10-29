# Curate Decisions Specification (delta)

## MODIFIED Requirements

### Requirement: Identity Items Are Mergeable Unless They Await A Judgment Or Break A Guard

The merge-group action MUST also be carried by an identity item of the LOW tier with more than two members (a near-match cluster) whose persisted judgment is DIFFERENT or UNCERTAIN, with merge withheld for `more_than_two_members`; a cluster not yet judged MUST withhold merge-group as `pending_evaluation`. An identity item of the HIGH or ACRONYM tier with more than two members MUST keep merge-group withheld against a DIFFERENT or UNCERTAIN verdict.
(Previously: merge-group was carried only by a judged-SAME item. A near-match cluster replaces the pairs that carried merge on any verdict, so withholding it would have taken that ability away.)

#### Scenario: A near-match cluster judged uncertain offers the group walk

- GIVEN an open LOW-tier identity item over three members judged UNCERTAIN or DIFFERENT
- WHEN a client lists it
- THEN its actions are merge-group, keep-distinct and skip, and merge is withheld as `more_than_two_members`

#### Scenario: An unjudged near-match cluster withholds the group walk

- GIVEN an open LOW-tier identity item over three members with no judgment
- WHEN a client lists it
- THEN merge and merge-group are withheld as `pending_evaluation`

#### Scenario: An exact-title group judged uncertain still withholds the group walk

- GIVEN an open HIGH-tier identity item over three members judged UNCERTAIN
- WHEN a client lists it
- THEN merge and merge-group are withheld as `judged_uncertain`
