# Curate Decisions Specification (delta)

## MODIFIED Requirements

### Requirement: Identity Items Are Mergeable Unless They Await A Judgment Or Break A Guard

An identity item MUST carry the merge action when it holds a persisted judgment (SAME, DIFFERENT or UNCERTAIN) over exactly two members that pass the cross-type check. An item with more than two members and a cross-type pair carry no merge action and name the reason. An item whose adjudication is null MUST be listed as pending evaluation and MUST carry no merge action. A merge against a DIFFERENT or UNCERTAIN verdict is applied and undone exactly as one against SAME.
(Previously: only a judged-SAME pair carried the merge action.)

#### Scenario: An uncertain item is judged uncertain, not pending

- GIVEN an open identity item judged UNCERTAIN over two members of one OKF type
- WHEN a client lists it
- THEN its actions are merge, keep-distinct and skip, and nothing is withheld

#### Scenario: A different item still offers the merge

- GIVEN an open identity item judged DIFFERENT over two members of one OKF type
- WHEN a client lists it
- THEN its actions are merge, keep-distinct and skip

#### Scenario: A merge against an uncertain or different item is applied

- GIVEN an open identity item judged UNCERTAIN or DIFFERENT over a passing pair
- WHEN a client previews and applies merge
- THEN the merge is applied as for SAME, and can be undone

#### Scenario: An uncertain group still withholds the merge

- GIVEN an open identity item judged UNCERTAIN over three members
- WHEN a client lists it
- THEN merge and merge-group are withheld with the reason `judged_uncertain`
