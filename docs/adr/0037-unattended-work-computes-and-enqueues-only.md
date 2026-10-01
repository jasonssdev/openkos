---
type: Decision
title: "ADR-0037: Unattended work computes and enqueues; only a human-facing path applies a proposal"
description: A run no human started performs only non-consequential work and records every consequential proposal as a row in a derived pending-work queue in findings.db, keyed by a stable decision key and retired as stale when its inputs change; only human-facing write paths move a row to applied, declines stay in bundle/.state/decisions/ under git, and the queue is the instrument for measuring how much of the curation queue is mechanical.
status: Proposed
date: 2026-09-30
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-30T00:00:00Z
sensitivity: public
---

# ADR-0037: Unattended work computes and enqueues; only a human-facing path applies a proposal

- **Status:** Proposed
- **Date:** 2026-09-30

## Context

The roadmap's MVP 4 rule is that the engine performs the non-consequential
(`ingest`, `reindex`, `lint`, computing findings) and enqueues the
consequential (merges, forgets, relation confirmations) as durable pending
work, "which `.openkos/findings.db` and `bundle/.state/decisions/` already
exist to hold". The pre-MVP-4 audit (#1141) found that neither has queue
semantics:

- `state/findings.py` `record_findings` appends rows with no status, no
  claim and no identity key; a re-run appends duplicates, and staleness is
  decided per row at read time from input digests. Four tenants persist
  (contradiction, adjudication, edge-suggestion and revision verdicts).
  A duplicate group -- the merge candidate the roadmap's own measurement
  is about -- has nowhere to live.
- `bundle/.state/decisions/` holds human verdicts (ADR-0014), not
  proposals.
- Nothing structural enforces the rule. `curate --auto --accept structure`
  writes relations without a TTY, so an unattended caller that passed the
  flag would apply consequential work.

Without a queue a daemon can either recompute every advisor whenever
someone asks what is pending (what ADR-0014 introduced durable findings to
avoid) or apply consequential changes itself (what "human curates, engine
maintains" forbids). ADR-0034 already settled the one narrow exception the
project would consider -- an opt-in auto-merge class admitted only by a
pre-registered measurement -- and its first measurement failed, so no class
exists.

The roadmap also asks for a number before anything is automated on top of
the queue: how much of the curation queue is mechanical. That number needs
an instrument that sees every proposal and how it was resolved.

## Decision

**One. "Unattended" is defined by who started the run, not by a flag.** A
run started by the job runner (the watcher, a scheduled maintenance pass)
is unattended. A verb a person invoked is human-facing, including a
non-TTY invocation that carries the verb's explicit consent flags. The
runner never passes a consent flag (`--accept`, `--apply`,
`--confirm-count`, `--force`) and calls no write core that applies a
consequential change.

**Two. Unattended stages compute and enqueue, and do nothing else
consequential.** The non-consequential work -- importing a settled source
under raw immutability, extracting, refreshing derived stores, linting,
computing findings -- runs. Every consequential proposal becomes a row in
the pending-work queue: identity (merge candidates, including the
duplicate groups `duplicates` produces), relation types, volatility
tiers, contradiction and revision findings, and the watcher's refusals.

**Three. The queue is one derived table in `.openkos/findings.db`.** Each
row carries a stable decision key (the advisor plus the sorted target ids,
with the advisor-specific discriminators the existing decision keys
already require, such as a contradiction's `merged_absorbed_id`), a kind, a
payload digest over the proposal and its inputs, a status
(`pending | claimed | applied | declined | stale`), the producer, and
timestamps. Producers upsert by key: the same digest refreshes the open
row, a changed digest retires the open row as `stale` and opens a new one.
The table is rebuildable by re-running the advisors, like every other
`.openkos/` store; it joins the store `purge` deletes and `forget` sweeps.

**Four. Only human-facing paths resolve a row.** `curate`, `adjudicate`,
`reconcile`, `merge`, `relate` and the other write verbs move a row to
`applied` when they perform the write it proposed, and to `declined` when
the human declines it. The decline itself stays where ADR-0014 put it, in
`bundle/.state/decisions/` under git, and remains the authority: the queue
row mirrors it and a rebuilt queue re-derives it. The MCP surface lists the
queue and never changes a row's status.

**Five. The queue is the measurement instrument.** Every resolution records
whether the human applied the proposal exactly as proposed, applied a
modified form, declined it, or let it go stale. The fraction applied as
proposed, per kind, is the roadmap's "how much of the queue is mechanical"
number, and no automation is built on top of the queue until it has been
measured and a successor ADR, under ADR-0034's conditions, admits a class.

## Consequences

**Easier.** "What is the base waiting on?" becomes a read: `next`, `status`,
a new read-only `pending` verb and the MCP pending tool read rows instead
of recomputing advisors. A re-run of an advisor no longer duplicates work,
and a duplicate group gets a durable identity. The boundary between
maintenance and curation becomes a data structure a test can inspect
instead of a flag a caller can pass.

**Harder.** Every advisor gains a producer adapter and every human write
path gains a resolution call; a write path that forgets to resolve its row
leaves a proposal visible after the human acted on it, which is the drift
this ADR exists to prevent, so a test enumerates the paths. The table is a
fifth tenant of `findings.db` and must join the `forget` sweep by every
field that names a concept, including target ids and any quoted text in
the payload. Rebuilding the queue loses the `applied` history (declines
survive, in git), so the mechanical-fraction measurement is only as long as
the store's life, and the measurement must say so.

**Accepted risk.** A human-invoked non-TTY run with consent flags -- a cron
line someone wrote -- is indistinguishable from a person and may still
apply consequential work. That is consent given in advance by a person, not
engine autonomy, and it is how the CLI already behaves.

## Alternatives considered

**A separate `queue.db`.** A cleaner name. Rejected because a new file is a
new privacy surface: `findings.db` is already deleted by `purge` and swept
by `forget`, and a second tenant inheriting both is the precedent ADR-0014
set on purpose.

**Store proposals in `bundle/.state/`.** Durable and reviewable. Rejected:
proposals are recomputable machine inference whose payloads quote concept
text; ADR-0014 put exactly that class in `.openkos/` for that reason.

**Reuse the merge ledger's identity for merge candidates.** A ledger entry
exists only after a merge; a candidate needs an identity before one, and
the member-set key ADR-0014's kept-distinct rulings already use is the
right one.

**Let the daemon apply "safe" consequential work.** Rejected here and left
to ADR-0034's process: a class qualifies only through its own
pre-registered measurement, and the queue is what makes that measurement
possible.
