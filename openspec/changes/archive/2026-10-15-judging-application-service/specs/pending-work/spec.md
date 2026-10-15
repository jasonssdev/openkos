# Delta for Pending Work

## ADDED Requirements

### Requirement: An Identity Producer Carries A Servable Stored Verdict

The identity producer, including the unattended maintenance pass, MUST carry into
each identity proposal the stored verdict of its member set when that verdict is
servable, so a pass never replaces a judged row with an unjudged one. Servability
MUST be decided from the adjudications store, not from a queue row's payload: the
group's latest stored verdict MUST have been computed under the current judgment
rubric, MUST name exactly the group's members, and every stored member digest
MUST equal the member's current content digest. The pass MUST carry the latest such
verdict whatever confidential inclusion it was judged under: the queue payload is
owner-local and the pass sends nothing to a model. (Judging itself still binds the
confidential flag in its quote and run.) A verdict that is not servable MUST
NOT be carried, and the proposal then carries no verdict. Carrying a verdict MUST
make no model call and MUST NOT write to `bundle/`; the pass keeps computing and
enqueueing only. A pass MUST NOT erase a stored adjudication: it MUST NOT delete or
alter the adjudications store, and a row that carried a servable verdict before the
pass MUST carry the same verdict after it.

#### Scenario: A judged-SAME row survives a daemon pass

- GIVEN an open identity row carrying a SAME verdict that is still servable
- WHEN an unattended maintenance pass runs
- THEN the row still carries the SAME verdict, it is not retired, and no model call
  is made

#### Scenario: A servable verdict is carried into a row that has none

- GIVEN an open identity row with a null adjudication and a servable stored verdict
  for its members
- WHEN an unattended maintenance pass runs
- THEN the open row carries that verdict

#### Scenario: A verdict computed under an old rubric is not carried

- GIVEN a stored verdict computed under a rubric that no longer matches the current
  one
- WHEN an unattended maintenance pass runs
- THEN the proposal carries no verdict

#### Scenario: A verdict about edited members is not carried

- GIVEN a stored verdict and a member edited since
- WHEN an unattended maintenance pass runs
- THEN the proposal carries no verdict

#### Scenario: The pass does not compare the confidential inclusion

- GIVEN a servable stored verdict, whichever confidential inclusion it was judged
  under
- WHEN an unattended maintenance pass runs
- THEN the proposal carries that verdict and no model is called

#### Scenario: The adjudications store is untouched by a pass

- GIVEN an adjudications store with stored verdicts
- WHEN an unattended maintenance pass runs
- THEN the store holds the same verdicts afterwards

### Requirement: Writing A Verdict Through To An Open Row Does Not Count As A New Or Stale Proposal

Upgrading an open identity row with a persisted verdict, which changes its payload
digest, MUST NOT inflate the queue's lifetime counters. Judging an item MUST NOT add
an enqueued row and MUST NOT add a `stale` row: `openkos pending --stats` MUST
report the same per-kind counts of enqueued, applied as proposed, applied modified,
declined, stale and still-open rows after the write-through as before it, and the
upgraded item MUST remain the single open row for its key, keeping its creation
time. A later producer pass over the unchanged inputs MUST find that row's digest
equal to its proposal's and MUST only refresh its last-seen time. A digest
difference caused by an input that really changed, such as an edited member, MUST
still retire the row as `stale` under "Producers Upsert By Key And Retire On A
Changed Digest". A changed rubric is not an input: the row is amended to carry no
verdict, not retired.

#### Scenario: Judging does not change the stats

- GIVEN one open identity row and `openkos pending --stats` reporting its kind's
  counts
- WHEN a verdict is written through to that row
- THEN `openkos pending --stats` reports the same counts for the kind

#### Scenario: The next daemon pass finds the row unchanged

- GIVEN an open identity row upgraded with a servable verdict
- WHEN an unattended maintenance pass runs over unchanged inputs
- THEN only the row's last-seen time moves and the stats counts do not change

#### Scenario: A real input change still retires the row

- GIVEN an open identity row upgraded with a verdict and a member edited since
- WHEN a producer pass runs
- THEN the old row is `stale` and one new `pending` row exists
