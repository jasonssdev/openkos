# Delta for Curate Decisions

## ADDED Requirements

### Requirement: A Persisted Verdict Written Through To An Open Row Is Reflected In The List

When a verdict is persisted for the member set of an open identity row, the row
MUST be updated in place so the listed item reflects that verdict, without a new
`decision_key`. A SAME verdict MUST make the item mergeable under the existing
checks; a DIFFERENT verdict keeps resolving the row as `declined` as it does today;
an UNCERTAIN verdict MUST list the item as `judged_uncertain`. The write-through
MUST update only an open row: it MUST NOT create a row where none is open, MUST NOT
reopen a resolved row and MUST NOT resolve a row. A verdict whose members no longer
hash to what was judged MUST NOT be written through. The effect on the queue's
lifetime counters is defined by `pending-work`.

#### Scenario: A SAME verdict makes the item mergeable

- GIVEN an open identity item pending evaluation over two members that pass the
  cross-type check
- WHEN a SAME verdict is persisted for exactly those members
- THEN a later list shows the same `decision_key` with the merge action present

#### Scenario: An UNCERTAIN verdict lists as judged uncertain

- GIVEN an open identity item pending evaluation
- WHEN an UNCERTAIN verdict is persisted for exactly its members
- THEN a later list shows the item as `judged_uncertain`

#### Scenario: Nothing is created for an item with no open row

- GIVEN a persisted verdict for a member set that holds no open row
- WHEN the write-through runs
- THEN no row is created

#### Scenario: A resolved row is not reopened

- GIVEN an identity row already `applied` or `declined`
- WHEN a verdict is persisted for its members
- THEN the row keeps its status

## MODIFIED Requirements

### Requirement: Only Judged-SAME Identity Items Are Mergeable

An identity item MUST carry the merge action only when it holds a persisted SAME judgment over exactly two members that pass the cross-type check. A judged-DIFFERENT item, a judged-SAME item with more than two members and a cross-type pair carry no merge action and name the reason; every identity item keeps keep-distinct and skip. An identity item whose adjudication is null MUST be listed as
pending evaluation and MUST carry no merge action. An identity item whose persisted judgment is UNCERTAIN, including one produced by a malformed model reply, MUST be listed as `judged_uncertain`, MUST carry keep-distinct and skip, MUST carry no merge action naming the reason `judged_uncertain`, and MUST NOT be listed as pending evaluation. The surface MUST NOT merge
an unjudged item on a caller's decision alone, and MUST NOT call a model to
judge it.
(Previously: the evaluation field distinguished judged SAME, judged DIFFERENT and pending evaluation only, so an UNCERTAIN verdict was listed as pending evaluation.)

#### Scenario: A judged SAME item is mergeable

- GIVEN an open identity item with a SAME judgment
- WHEN a client lists it
- THEN it carries the merge action

#### Scenario: An unjudged item is pending evaluation

- GIVEN an open identity item with a null adjudication, as the unattended
  daemon enqueues
- WHEN a client lists it
- THEN it is marked pending evaluation and carries no merge action

#### Scenario: A merge against an unjudged item is not applied

- GIVEN an open identity item with a null adjudication
- WHEN a client requests a merge for its `decision_key`
- THEN the typed not-applied result with reason `not_mergeable` is returned
  (returned, not raised: it is not an error) and nothing is written

#### Scenario: An uncertain item is judged uncertain, not pending

- GIVEN an open identity item whose persisted judgment is UNCERTAIN
- WHEN a client lists it
- THEN its evaluation is `judged_uncertain`, its actions are keep-distinct and
  skip, its withheld merge names the reason `judged_uncertain`, and it is not
  marked pending evaluation

#### Scenario: A merge against an uncertain item is not applied

- GIVEN an open identity item judged UNCERTAIN
- WHEN a client requests a merge for its `decision_key`
- THEN the typed not-applied result with reason `not_mergeable` is returned and
  nothing is written

### Requirement: The Surface Computes No Judgment

The surface MUST NOT call a model to decide whether, or how, an item should be
resolved. Judging, the cost gate and the candidate probes MUST remain outside
the surface; judging is provided by the `judging-application-service` capability,
through which an item pending evaluation becomes decidable. The only model-backed
step reachable from an apply is the injected reconciliation port of an identity
merge.
(Previously: judging was named only as out of scope and a separate follow-up, with no capability named as the place a pending-evaluation item becomes decidable.)

#### Scenario: List and skip need no model

- GIVEN no model backend configured
- WHEN a client lists the open decisions and applies skip
- THEN both succeed

#### Scenario: No judge is started by the surface

- GIVEN an identity item pending evaluation
- WHEN a client lists it
- THEN it stays pending evaluation and no judging runs
