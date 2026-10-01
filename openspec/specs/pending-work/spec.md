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
re-running the advisors MUST repopulate every open row.

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
`suggest-relations --apply`, or an ingest that lands a `watch_refusal`'s
bytes -- performs the write the row proposes, and to `declined` only when a
human declines it through a surface that records the decline. A run of the
job runner MUST NOT resolve a row. The MCP surface MUST NOT change a row's
status. A human-facing path MAY mark a row `claimed` while it presents it,
and a claim MUST lapse back to `pending` when its claimant exits without
resolving it.

#### Scenario: A merge resolves its identity row

- GIVEN an open identity row for A and B
- WHEN the operator merges A into B through `openkos merge`
- THEN the row is `applied`

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

#### Scenario: An absent queue is not an empty queue

- GIVEN a workspace with no queue table
- WHEN `openkos pending` runs
- THEN it says the queue has not been computed, names how to compute it,
  and does not say nothing is pending
