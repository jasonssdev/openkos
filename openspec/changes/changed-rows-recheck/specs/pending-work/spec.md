# Delta for pending-work

## ADDED Requirements

### Requirement: Refresh Retires Every Row Naming A Concept That No Longer Exists

A refresh MUST retire as `stale` every open `identity` and `relation_type` row
that names, as a target, a Concept ID whose concept file does not exist in the
bundle, whether or not the pass for its kind is complete, and MUST report the
count per kind. The absence MUST be checked inside the commit section that writes
the retirement, so a concept restored before that section is not taken for
missing. A target that cannot be read for another reason MUST NOT count as
missing, and input refs MUST NOT be consulted. A `contradiction`, `volatility`,
`revision` or `watch_refusal` row MUST NOT be retired by this rule. The rule MUST
call no model, write only the queue, and leave resolved rows as they are; an
absent queue MUST NOT be created by it.

#### Scenario: A row naming an absorbed page is retired on a truncated pass

- GIVEN a truncated identity report and an open identity row one of whose
  members has no concept file
- WHEN a refresh runs
- THEN the row is `stale` and the identity result counts it as retired for a
  missing page

#### Scenario: A relation row naming a missing endpoint is retired

- GIVEN an open relation-type row whose target concept file is gone
- WHEN a refresh runs
- THEN the row is `stale` and the relation_type result counts it

#### Scenario: A contradiction naming an absorbed page stays open

- GIVEN an open contradiction row that names a concept absorbed by a merge
- WHEN a refresh runs
- THEN the row stays open

#### Scenario: Rows over existing pages are untouched

- GIVEN an open identity row whose members all exist
- WHEN a refresh runs
- THEN the row is not retired for a missing page

## MODIFIED Requirements

### Requirement: Refresh Retires A Row Only After A Complete Pass For Its Kind

A refresh MUST retire an open row of a kind as `stale` for being absent only
when the pass for that kind is `complete` under the same contract the producers
use ("absent" is distinguished from "not reached"). Only the `identity` kind can
prove completeness from persisted state alone: its pass is complete exactly when
the identity report is not truncated. The `relation_type` and `volatility`
passes MUST always report `complete` as false and MUST insert and amend only;
a refresh MUST NOT retire an open row of either kind for being absent, and that
retirement remains the job of the unattended maintenance pass. (A row of those
kinds whose inputs really changed is still retired as `stale` when its
replacement is inserted, under "Producers Upsert By Key And Retire On A Changed
Digest".) A row that names a concept no longer in the bundle is not absent from
a pass; its retirement is governed by "Refresh Retires Every Row Naming A
Concept That No Longer Exists" and is reported apart from this one. The result
MUST state per kind whether the pass was complete, and a row MUST NOT be retired
for being absent for a kind reported as incomplete. An event-driven row (a
supersession, a watch refusal) MUST NOT be retired by a refresh for being absent.

#### Scenario: An incomplete pass retires nothing

- GIVEN a kind whose refresh pass reports `complete` false and an open row of
  that kind that the pass did not see, naming only concepts that exist
- WHEN the refresh runs
- THEN the row stays open

#### Scenario: A complete identity pass retires an absent identity row

- GIVEN an untruncated identity report and an open identity row whose group is
  no longer reported
- WHEN the refresh runs
- THEN the row is `stale`

#### Scenario: A truncated identity pass retires nothing

- GIVEN a truncated identity report and an open identity row it did not reach,
  whose members all exist
- WHEN the refresh runs
- THEN the row stays open and the identity result reports `complete` false

#### Scenario: Relation-type and volatility rows are never retired for absence

- GIVEN an open relation-type row and an open volatility row that no stored
  verdict produces any more, naming only concepts that exist
- WHEN the refresh runs
- THEN both rows stay open and both kinds report `complete` false

#### Scenario: Completeness is reported per kind

- GIVEN a refresh over a workspace
- WHEN the result is read
- THEN it states, for each of identity, relation_type and volatility, whether
  the pass was complete

#### Scenario: A supersession is not retired by refresh

- GIVEN an open `source-supersession/1` relation row over sources that exist
- WHEN a refresh runs
- THEN the row is not retired
