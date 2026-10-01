# Delta for Pending Work

## ADDED Requirements

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
ids and the command that resolves it, followed by the most recent
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
