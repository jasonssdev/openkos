# Delta for Pending Work

The existing requirements "An Identity Producer Carries A Servable Stored
Verdict" and "Writing A Verdict Through To An Open Row Does Not Count As A New
Or Stale Proposal" are confirmed unchanged: the refresh below applies them. The
`job-runtime` requirements "The Maintenance Pass Computes And Enqueues Only" and
"The Runner Uses The Application Services, Never The CLI" are confirmed
unchanged: lifting the identity stage into `application/` preserves the
daemon's behavior, so no `job-runtime` delta is written. `judging-application-service`,
`curate-decisions` and `status` are confirmed unchanged: the API wraps them.

## ADDED Requirements

### Requirement: The Decision Queue Can Be Refreshed Without A Model

A refresh operation MUST rebuild the open decision rows of the `identity`,
`relation_type` and `volatility` kinds from persisted state alone. It MUST make
no model call, MUST NOT resolve a backend client, and MUST NOT write to `bundle/`
or to the adjudications, edge-suggestions or volatility stores. It MUST write
only the pending-work queue. Every persisted verdict that a producer pass would
enqueue as an open proposal MUST become a listable open row for its kind after a
refresh, without a daemon pass: for `identity`, a group the identity advisor
reports whose stored verdict is servable; for `relation_type`, a stored
edge suggestion the existing serving rule serves; for `volatility`, a stored
cached answer the existing serving rule serves. A stored verdict that is not
servable (computed under another rubric, or about changed inputs) MUST NOT be
enqueued.

Because the effective confidentiality regime that keyed a cached volatility
answer is unknown without a backend client, the volatility pass MUST consider
both regimes. A type answered in one regime, or answered identically in both,
MUST be published once. A type answered in both regimes with different answers
MUST NOT be published (the engine never guesses between them) and MUST be
counted in the result as ambiguous. An absent queue with nothing to publish MUST
NOT be created by a refresh. With today's store (`state/volatility_suggestions.py`,
keyed by `type_name` as primary key, one row per type) this count is always 0:
both regimes can only resolve to the same single stored row, so at most one
distinct answer is ever held. The rule is a guard for a future store that keeps
one row per type and regime; it is kept, not deleted, and the behaviour above is
unchanged.

#### Scenario: A judged item appears with no daemon pass

- GIVEN judging persisted a servable identity, relation-type and volatility
  verdict and no producer pass has run
- WHEN a refresh runs
- THEN each judged item is a listable open row of its kind

#### Scenario: Refresh makes no model call

- GIVEN a backend whose every call fails the test
- WHEN a refresh runs
- THEN it completes and no backend client was resolved

#### Scenario: A non-servable verdict is not enqueued

- GIVEN a stored verdict computed under a rubric that no longer matches
- WHEN a refresh runs
- THEN no open row is created for it

#### Scenario: Refresh writes only the queue

- GIVEN a workspace with stores and a bundle
- WHEN a refresh runs
- THEN every file under `bundle/` and every verdict store is byte-identical
  afterwards

#### Scenario: Two regimes that disagree are skipped and counted

- GIVEN a volatility type whose cached answers differ between the two
  confidentiality regimes (reachable only through a store that holds more than
  one answer per type; today's store holds one row per type, so this cannot
  occur and the ambiguous count is always 0)
- WHEN a refresh runs
- THEN no row is published for that type and the volatility result counts it as
  ambiguous

#### Scenario: An absent queue with nothing to publish is not created

- GIVEN a workspace with no queue and no servable verdict of any kind
- WHEN a refresh runs
- THEN no queue is created and every per-kind count is zero

### Requirement: Refresh Holds The Workspace Lock Only Inside Commit Sections

A refresh MUST compute its outcomes without holding the workspace lock and MUST
enter the lock only inside the commit section that persists a row, through the
same commit-section port the producers use. A workspace lock held by another
process MUST be retried with the existing bounded backoff, and exhausting it
MUST surface the existing busy failure rather than a silent partial success.

#### Scenario: The lock is not held while computing

- GIVEN a refresh in its compute phase
- WHEN another process acquires the workspace lock
- THEN the acquisition is not blocked by the refresh

#### Scenario: Persisting a row enters a commit section

- GIVEN a refresh with one row to insert
- WHEN the row is persisted
- THEN the commit-section port was entered for that write

### Requirement: Refresh Amends Unchanged Inputs And Does Not Inflate The Queue Statistics

A refresh over inputs that have not changed MUST NOT add an enqueued row and
MUST NOT add a `stale` row for any of the three kinds: it MUST only refresh the
last-seen time of the existing open row, or amend its payload when the only
difference is a verdict now carried. `openkos pending --stats` MUST report the
same per-kind counts of enqueued, applied as proposed, applied modified,
declined, stale and still-open rows after a second refresh as after the first. A
digest difference caused by an input that really changed MUST still retire the
row as `stale` and insert a new `pending` row, under "Producers Upsert By Key
And Retire On A Changed Digest". A refresh MUST NOT move a row to `applied` or
`declined`.

#### Scenario: A second refresh leaves the statistics unchanged

- GIVEN a refresh has run over persisted verdicts of all three kinds
- WHEN a second refresh runs over unchanged inputs
- THEN `openkos pending --stats` reports identical counts for every kind

#### Scenario: A verdict written through to an existing row is an amendment

- GIVEN an open row for an item and a verdict persisted for it afterwards
- WHEN a refresh runs
- THEN the open row carries the verdict, is still the single open row for its
  key, and the enqueued and stale counts are unchanged

#### Scenario: A changed input still retires the row

- GIVEN an open row whose member was edited since
- WHEN a refresh runs
- THEN the old row is `stale` and one new `pending` row exists

#### Scenario: A refresh never resolves a row

- GIVEN any open row
- WHEN a refresh runs
- THEN the row is not `applied` or `declined`

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
Digest".) The result MUST state per kind whether the pass was complete, and a
row MUST NOT be retired for being absent for a kind reported as incomplete. An
event-driven row (a supersession, a watch refusal) MUST NOT be retired by a
refresh for being absent.

#### Scenario: An incomplete pass retires nothing

- GIVEN a kind whose refresh pass reports `complete` false and an open row of
  that kind that the pass did not see
- WHEN the refresh runs
- THEN the row stays open

#### Scenario: A complete identity pass retires an absent identity row

- GIVEN an untruncated identity report and an open identity row whose group is
  no longer reported
- WHEN the refresh runs
- THEN the row is `stale`

#### Scenario: A truncated identity pass retires nothing

- GIVEN a truncated identity report and an open identity row it did not reach
- WHEN the refresh runs
- THEN the row stays open and the identity result reports `complete` false

#### Scenario: Relation-type and volatility rows are never retired for absence

- GIVEN an open relation-type row and an open volatility row that no stored
  verdict produces any more
- WHEN the refresh runs
- THEN both rows stay open and both kinds report `complete` false

#### Scenario: Completeness is reported per kind

- GIVEN a refresh over a workspace
- WHEN the result is read
- THEN it states, for each of identity, relation_type and volatility, whether
  the pass was complete

#### Scenario: A supersession is not retired by refresh

- GIVEN an open `source-supersession/1` relation row
- WHEN a refresh runs
- THEN the row is not retired

### Requirement: The Identity Stage Is Shared Between The Daemon And Refresh

The identity stage that reports duplicate groups, drops groups judged different,
and enqueues them with their servable stored verdicts MUST be one shared
`application/` stage called by both the unattended maintenance pass and the
refresh. The maintenance pass's observable behavior MUST be unchanged by sharing
it: the same rows, payloads and counts result from the same inputs as before.

#### Scenario: The daemon produces the same queue as before

- GIVEN the existing daemon fixtures
- WHEN a maintenance pass runs
- THEN the identity rows, payloads and counts equal those it produced before
  the stage was shared

#### Scenario: Refresh and the daemon agree on identity

- GIVEN a workspace with an identity group carrying a servable verdict
- WHEN a refresh runs, then a maintenance pass runs over unchanged inputs
- THEN the maintenance pass only refreshes the row's last-seen time
