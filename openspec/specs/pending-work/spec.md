# Pending Work Specification

## Purpose

Durable persistence for machine-computed findings and operator decisions
produced by the advisors, and the one queue every consequential proposal
waits in until a human resolves it. The first half covers the
Contradictions advisor: a `CONTRADICTS`/`CONSISTENT`/`UNCERTAIN` verdict
from `find_contradictions`, and an operator's decline of one. The second
half covers the queue that also holds identity, relation-type, volatility,
revision and watch-refusal proposals, how producers keep it current, and
how only human-facing write paths resolve a row.

Findings and decisions have opposite natures and are stored separately.
Findings are recomputable machine inference, kept in `.openkos/` (already
swept by `purge`'s delete-and-rebuild). Decisions are irreplaceable human
judgment, kept in `bundle/.state/`, committed, reusing ADR-0013's
frontmatter-sidecar mechanism.

The two hazards inherited from ADR-0013 — `_autocommit`'s scoped staging
and `purge`/`forget`'s sweep coverage of the `bundle/.state/**`
subtree — are specified in the capabilities that
already own those contracts (`workspace-autocommit`, `privacy-purge`,
`forget-command`), not here, so the requirement lives where the next author
reading `_autocommit`/`purge`/`forget` will find it.

## Requirements

### Requirement: Contradiction Findings Are Persisted With Provenance

Each verdict a `curate` Contradictions stage run produces MUST be persisted
under `.openkos/`, keyed by its candidate pair identity, and MUST retain the
verdict, confidence, rationale, and a content digest of the inputs it was
computed from.

#### Scenario: A finding survives the process

- GIVEN one `curate` run completes the Contradictions stage
- WHEN a later, unrelated process reads the pending-work store
- THEN the same verdict, confidence, and rationale are readable without a
  new LLM call

### Requirement: Persisted Findings Are Rankable, And The Honesty Guard Is Preserved

`next` and `status` MUST be able to read the persisted finding set. A `next`
tier reading persisted findings MUST rank an open, non-stale, non-declined
contradiction as a candidate action. `next`'s `None`-action result MUST
continue to mean only "no ranked tier fired" and MUST NOT be read, stated,
or implied to mean the bundle is clean — this tier makes more findings
rankable; it MUST NOT license the inverse inference for findings that
remain unranked.

#### Scenario: An open contradiction is ranked

- GIVEN one open, non-stale, non-declined contradiction finding is persisted
- WHEN `next` runs and no higher-ranked tier fires
- THEN `next` returns that finding as its action

#### Scenario: An unranked finding does not become a false all-clear

- GIVEN a persisted finding exists but every `next` tier declines to fire
- WHEN `next` returns a `None` action
- THEN the printed result states only that no ranked tier produced a
  finding, and does not state or imply the bundle is clean

### Requirement: Declining Is A Non-Interactive Verb Keyed On Proposal Identity

An operator MUST be able to decline a specific contradiction finding through
a non-interactive command surface addressing it by a stable identity. That
identity MUST be derived from the candidate proposal — sorted `pair_ids`
and `merged_absorbed_id` — and MUST NOT be derived from a finding's storage
row id. `merged_absorbed_id` MUST be part of the identity: it is the sole
discriminator between a typed-edge candidate and a merged-body candidate;
`pair_ids` shape alone is not a safe substitute.

#### Scenario: A declination survives recomputation

- GIVEN an operator declined a contradiction finding
- WHEN the Contradictions stage recomputes the same candidate pair later
- THEN the recomputed finding is recognized as already declined and does
  not reappear as open

#### Scenario: A typed-edge and a merged-body candidate over the same pair stay distinct

- GIVEN a typed-edge candidate and a merged-body candidate share the same
  `pair_ids`
- WHEN an operator declines one of them
- THEN only that candidate's decision is recorded, keyed by its own
  `merged_absorbed_id`, and the other candidate is unaffected

### Requirement: Declined Findings Are Hidden By Default, With An Explicit Listing View

A declined finding MUST NOT appear in ordinary `curate`, `status`, or `next`
output. An explicit command or flag MUST exist to list declined findings.

#### Scenario: A declined finding stays out of ordinary output

- GIVEN one contradiction finding was declined
- WHEN `curate`, `status`, or `next` runs without the declined-listing view
- THEN that finding does not appear in the output

#### Scenario: The declined-listing view surfaces it

- GIVEN one contradiction finding was declined
- WHEN the operator invokes the declined-listing view
- THEN that finding appears, identified and marked declined

### Requirement: Re-Opening A Declined Finding Requires Explicit Operator Action

A declined finding MUST NOT be reinstated automatically by any content
change to the concepts it was computed from. Reinstatement MUST require an
explicit operator action naming the finding.

#### Scenario: Content change does not silently reopen a decline

- GIVEN a declined finding, and one of its concepts is subsequently edited
- WHEN the finding is recomputed
- THEN it is marked stale, not reopened, and stays hidden from ordinary
  output

#### Scenario: Explicit re-open reinstates it

- GIVEN a declined contradiction finding
- WHEN the operator issues the explicit re-open action naming it
- THEN the finding is no longer treated as declined and is eligible to
  rank again

### Requirement: A Finding Is Invalidated Honestly When Its Inputs Change

A persisted finding MUST carry a content digest of the objects it was
computed from. When that digest no longer matches current bundle state, the
finding MUST be marked stale. A stale finding MUST NOT be presented as
current and MUST NOT be silently dropped.

#### Scenario: A changed concept marks its finding stale

- GIVEN a persisted finding computed over concept A's current content
- WHEN concept A's content changes
- THEN the finding is marked stale on next read, rather than shown as
  current

#### Scenario: A stale finding remains visible as stale

- GIVEN a finding marked stale
- WHEN `status` or the declined-listing view runs
- THEN the stale finding is shown labeled stale, not silently omitted

### Requirement: A Human Identity Ruling Is Durable And Outranks The Model

An operator MUST be able to record that the members of a duplicate-candidate
group are NOT the same entity, through a non-interactive command surface,
and that ruling MUST survive the session that produced it.

The ruling's identity MUST be derived from the group's MEMBER SET, sorted so
it is independent of the order the members were supplied in, and MUST NOT be
derived from a candidate group's position or row id — a group is recomputed
on every run, so a position-derived key would evaporate the ruling on the
next one.

The identity MUST occupy a namespace disjoint from the contradiction
decision key. "These two do not contradict each other" and "these two are
not the same entity" are opposite rulings that can both be made about the
same pair, and a shared key would let one silently answer for the other.

Recording a ruling MUST NOT require a matching adjudication row: the human
may be overruling a verdict the model has not produced yet, or one that was
recomputed away. Requiring one would make the human's answer depend on the
machine's, which is the dependency this requirement removes.

A ruled group MUST NOT appear in ordinary `duplicates`, `status`, or `next`
output, MUST NOT be adjudicated by `curate`'s Identity stage, and MUST be
excluded before that stage's cost gate so it costs no model call. An
explicit listing view MUST exist, and the ruling MUST be reversible.

The ruling surface MUST be `duplicates --keep-distinct <id>` (repeated once
per member, at least two distinct members), `duplicates --reopen <id>`
(repeated the same way) to reverse it, and `duplicates --kept-distinct` to
list every ruled group. Each MUST short-circuit before the whole-bundle
candidate walk, and MUST NOT make a model call. Member ids MUST be
path-safety canonicalized, deduplicated and sorted, so either typing order
addresses the same record; existence of the named concepts MUST NOT be
required. Fewer than two distinct members MUST be refused with exit `2`.
A recorded or reversed ruling MUST be autocommitted.

Declining a per-item merge prompt MUST record the ruling, on EVERY
interactive walk that offers one — a decline persisted on one surface and
forgotten on another is drift between two paths that share a prompt.

#### Scenario: The re-offer loop terminates

- GIVEN a candidate group whose merge an operator declined
- WHEN `next`, `status`, and `duplicates` run afterwards
- THEN none of them reports the group as pending, and reaching a clean
  status no longer requires performing the refused merge

#### Scenario: The ruling is order-independent

- GIVEN an operator rules a two-member group distinct
- WHEN the same two members are supplied in the opposite order
- THEN the same ruling is addressed, not a second one that suppresses
  nothing

#### Scenario: A neighbouring group is unaffected

- GIVEN a ruling over members A and B
- WHEN a candidate group pairs A with C
- THEN that group is still offered for review

#### Scenario: The ruling costs no model call

- GIVEN a ruled group
- WHEN `curate`'s Identity stage runs
- THEN the group is absent from the stage's cost line and no adjudication
  call is issued for it

#### Scenario: A ruled group is reversible and visible

- GIVEN a ruled group
- WHEN the operator invokes the listing view, then reopens it
- THEN the ruling is shown before the reopen and the group is offered again
  after it

#### Scenario: The privacy sweep covers identity rulings

- GIVEN a ruling naming a concept that is later forgotten or purged
- WHEN the sweep runs
- THEN the ruling is removed, and for `purge` its sidecar is included in the
  whole-history expunge

### Requirement: Consequential Proposals Live In One Pending-Work Queue

`.openkos/findings.db` MUST hold a pending-work queue table, one row per
consequential proposal, with at least: a decision key, a kind, a payload
digest, a status, the producer, the target ids, a created time, a
last-seen time, and a resolution time. The kinds MUST include `identity`
(a duplicate group or adjudicated pair proposed for merge), `relation_type`,
`volatility`, `contradiction`, `revision`, and `watch_refusal`. The status
MUST be one of `pending`, `claimed`, `applied`, `declined`, `stale`. The
queue MUST be derived: deleting it MUST lose no human decision, and
re-running the advisors MUST repopulate every open row, except rows an
event-driven producer recorded (a source imported as a new version,
ADR-0041), which the next watch pass over the inbox recomputes only for a
change it sees.

A `relation_type` row MAY be produced by an event rather than an advisor
(`source-supersession/1`); a complete advisor pass over `relation_type`
MUST NOT retire it as `stale` for being absent. Its resolving command is
`openkos relate <new> supersedes <old>`, and `relate` of that pair MUST
resolve it as `applied`.

#### Scenario: The queue rebuilds from the advisors

- GIVEN a queue with open rows and a declined contradiction recorded in
  `bundle/.state/decisions/`
- WHEN `findings.db` is deleted and a maintenance pass runs
- THEN the same open rows exist again and the declined contradiction is not
  open

### Requirement: The Decision Key Is Stable And Kind-Scoped

A row's decision key MUST be derived from the proposal, never from a row
id or a position: the producing advisor, the proposal's target ids sorted,
and the advisor-specific discriminators the existing decision keys already
require -- a contradiction's `merged_absorbed_id`, and an identity
proposal's full member set. Keys of different kinds MUST NOT collide, so a
contradiction key and an identity key over the same two concepts are
distinct. For the contradiction and identity kinds the key MUST address the
same proposal the existing decline and kept-distinct records address, so a
recorded human decision applies to the row.

#### Scenario: A typed-edge and a merged-body contradiction stay distinct

- GIVEN a typed-edge and a merged-body contradiction over the same pair
- WHEN both are enqueued
- THEN two rows with different keys exist

#### Scenario: A kept-distinct ruling closes the identity row

- GIVEN an open identity row for members A and B
- WHEN the operator runs `openkos duplicates --keep-distinct A --keep-distinct B`
- THEN the row is `declined`

### Requirement: Producers Upsert By Key And Retire On A Changed Digest

A producer enqueueing a proposal MUST: refresh the last-seen time of an
open (`pending` or `claimed`) row with the same key and digest; mark an
open row with the same key and a different digest `stale` and insert a new
`pending` row; insert nothing when the key's latest human decision is a
decline still in force; and otherwise insert a new `pending` row. At most
one open row MUST exist per key. A producer MUST NOT move a row to
`applied` or `declined`.

#### Scenario: A re-run does not duplicate

- GIVEN an open contradiction row
- WHEN the contradictions advisor runs again over unchanged inputs
- THEN one open row exists for that key and its last-seen time moved

#### Scenario: A changed input retires the row

- GIVEN an open relation-type row computed over concepts A and B
- WHEN concept A is edited and the advisor runs again
- THEN the old row is `stale` and one new `pending` row exists

### Requirement: Only Human-Facing Write Paths Resolve A Row

A row MUST move to `applied` only when a human-facing write path --
`curate`, `adjudicate`, `merge`, `relate`, `set-volatility`, `reconcile`,
`suggest-relations --apply`, an ingest that lands a `watch_refusal`'s
bytes, or a decision applied through the curation decision surface (including a
merge-group) -- performs the write the row proposes, and to `declined` only when a
human declines it through a surface that records the decline. A merge-group
performs the write its group's row proposes once its first pairwise merge is
written, and resolves that row `applied` then, as proposed when every member was
marked and modified otherwise, before rows naming the absorbed member are retired.
A run of the job runner MUST NOT resolve a row. The MCP surface MUST NOT change a row's
status. A human-facing path MAY mark a row `claimed` while it presents it,
and a claim MUST lapse back to `pending` when its claimant exits without
resolving it.
(Previously: the paths were named by verb only, and a pairwise merge's retirement of rows naming the absorbed concept left a larger group's row `stale` even when a person had decided that group.)

#### Scenario: A merge resolves its identity row

- GIVEN an open identity row for A and B
- WHEN the operator merges A into B through `openkos merge`
- THEN the row is `applied`

#### Scenario: A merge retires every open row naming the absorbed concept

- GIVEN open rows over A, B and C (an identity row over all three, and
  relation-type rows over A/B and B/C)
- WHEN the operator merges B into A
- THEN every open row that names B is `stale`, so none offers a resolving
  command over a concept that no longer exists
- AND an open row that does not name B is untouched
- AND a row already `applied`, `declined` or `stale` keeps its state

#### Scenario: A merge-group applies its own row, not stale

- GIVEN an open identity row over A, B and C, and a relation-type row over B/C
- WHEN a merge-group marking A, B and C with survivor A is applied through the
  decision surface
- THEN the identity row over A, B and C is `applied` as proposed
- AND the relation-type row over B/C is `stale`

#### Scenario: A different verdict closes the identity row

- GIVEN an open identity row for A and B
- WHEN `openkos adjudicate` (or `curate`) persists a DIFFERENT verdict for
  exactly A and B
- THEN the row is `declined`
- AND no identity decision sidecar is written, so the group is offered again
  if a member is edited or the judgment rubric changes

#### Scenario: A servable different verdict keeps the group out of the queue

- GIVEN a persisted DIFFERENT verdict for A and B that is still servable
- WHEN the identity advisor runs, and when `status` counts identical-title
  groups
- THEN neither offers the group as identity work

#### Scenario: The runner never resolves a row

- GIVEN any open row
- WHEN a maintenance pass completes
- THEN the row is not `applied` or `declined`

### Requirement: Every Resolution Records How It Was Resolved

When a row leaves the open states, the queue MUST record the resolution as
one of: applied as proposed, applied modified (the write differed from the
proposal, such as a different relation type or merge direction), declined,
or stale. `openkos pending --stats` MUST report, per kind, the counts of
enqueued, applied as proposed, applied modified, declined, stale, and
still-open rows, and the fraction applied as proposed among resolved rows,
and MUST state that the counts cover only the queue's current lifetime.

#### Scenario: The mechanical fraction is reported per kind

- GIVEN three resolved identity rows, two applied as proposed and one
  declined
- WHEN `openkos pending --stats` runs
- THEN it reports the identity kind's fraction applied as proposed as
  2 of 3, with the lifetime caveat

### Requirement: `openkos pending` Lists The Queue Read-Only

`openkos pending` MUST list open rows grouped by kind, each with its target
ids and the command that resolves it, and MUST NOT render a row's payload
(proposal text can carry a person's words); it applies no confidentiality
gate, because it runs in the owner's own terminal and shows only ids,
kinds and commands. The list is followed by the most recent
unattended job outcomes that need attention (`budget_exhausted`,
`timed_out`, `commit_failed`, `failed`). `--all` MUST also list `applied`,
`declined`, and `stale` rows. It MUST take no lock, make no model call, and
write nothing. When the queue is absent it MUST say so and point at
`openkos daemon --once`, never report the base as having nothing pending.
Each row MUST name a subject and a resolving command that can actually
resolve THAT row: a volatility row (about a concept type, so it has no
target) names its type; a watch refusal's command names the refused file, not
a placeholder; an identity row names the merge walk when its group was judged
the same, and otherwise the judgment walk (`adjudicate --apply`, whose prompt
answers are `y` merge, `s` skip and `d` keep-distinct; for a group of more than
two members, which that walk does not merge, it says the walk prints the
pairwise `merge` commands) with the `duplicates --keep-distinct` ruling over
its members as an alternative on its own line, since `duplicates` alone only
lists groups. The only payload fields read for
this are a type name, an inbox path and an adjudication verdict -- names and
a verdict, never proposal prose.

When relation suggestions wait (open `relation_type` rows other than
supersessions), `openkos pending` MUST say so on the line under its heading,
with the count and `openkos curate --structure`, the command that reviews
them, because `curate` does not present its Structure stage by default. A
relation row's resolving command MUST be that command, except a supersession's,
which is `openkos relate <newer> supersedes <older>`. The only extra payload
field read for this is a relation row's suggested type, to tell a supersession
from a suggestion.

The identity and relation-type advisors keep at most a fixed number of
candidates per run, and the queue records no truncation. A kind whose open
rows reach that cap MUST be listed with a note that the cap is the per-run
candidate cap, so more may exist, naming the verb that reports the total
(`duplicates`, `suggest-relations`); a kind under the cap carries no note.

#### Scenario: An absent queue is not an empty queue

- GIVEN a workspace with no queue table
- WHEN `openkos pending` runs
- THEN it says the queue has not been computed, names how to compute it,
  and does not say nothing is pending

#### Scenario: Waiting relation suggestions are counted and the command named

- GIVEN one open relation suggestion and one open supersession
- WHEN `openkos pending` runs
- THEN the line under the heading reads `1 relation suggestion(s) waiting --
  review them with `openkos curate --structure`.`
- AND the suggestion's resolving command is `openkos curate --structure`
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
