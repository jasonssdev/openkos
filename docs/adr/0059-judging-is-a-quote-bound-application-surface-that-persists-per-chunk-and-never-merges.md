---
type: Decision
title: "ADR-0059: Judging is a quote-bound application surface that persists per chunk and never merges"
description: Judging runs as an application surface on a quote, consent and run protocol; the token binds the model, backend, locality, confidential inclusion, rubric and work set, a run never exceeds its quote, verdicts persist per chunk, a stop takes effect at a chunk boundary, the app and the daemon coexist without a lease, and the surface ends at persisted verdicts.
status: Accepted
date: 2026-10-15
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-15T00:00:00Z
sensitivity: public
---

# ADR-0059: Judging is a quote-bound application surface that persists per chunk and never merges

- **Status:** Accepted
- **Date:** 2026-10-15

## Context

The curation decision surface ([ADR-0053](0053-curation-decisions-are-a-two-phase-application-surface.md)) lists open decisions and applies one action to one item, but it computes no judgment. An identity item whose row carries no verdict is listed as pending evaluation, with merge withheld. The only ways to judge it are the interactive `curate` Identity stage and the `adjudicate` verb, and both live in `cli/`, print, prompt and read a TTY. The desktop app, the first client of the stable Python API ([ADR-0039](0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md)), reaches the engine through a loopback API ([ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md)) and cannot use either.

The forces:

- **Spend needs consent at human timescales.** A judge call costs tens of seconds on the default judge ([ADR-0047](0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md)). The CLI's cost gate quotes and then rebuilds its partition after consent, which is harmless across the seconds a terminal prompt takes. An app's human may answer minutes or days later, after ingest or the daemon has changed the work. A rebuilt partition can then spend more than was quoted, or spend on a different model than the one named.
- **No lock across human time.** [ADR-0036](0036-lock-a-short-commit-phase-in-a-per-user-state-directory.md) holds the workspace lock only for a short commit phase. Holding it, or a lease, from quote to run would block the daemon and every other writer while a person decides.
- **No preemption.** [ADR-0021](0021-sync-async-boundary.md) keeps the core synchronous with no cancellation below the worker thread, so a running chat call cannot be interrupted. A run that persists only when its batch returns loses every paid verdict when it is stopped or killed, and today a busy workspace at persist time loses them too.
- **The daemon and the app share one queue.** Both may run at once. The daemon's identity stage is model-free and re-proposes every group each pass; today it proposes them without verdicts, so it erases any verdict the app paid for.
- **Not every verdict decides.** An UNCERTAIN verdict is persisted, yet it leaves the item exactly as undecided as no verdict, and a client that re-offered it would pay again for an answer the judge declined to give.
- **Never merge.** Unattended work computes and enqueues only ([ADR-0037](0037-unattended-work-computes-and-enqueues-only.md)), and the only merges without per-item consent are the measured class ([ADR-0034](0034-identity-auto-merge-only-for-a-measured-class.md), [ADR-0049](0049-structural-identity-class-merges-under-measured-constants.md)).
- **The API is not frozen yet.** The surface must be real enough for a client to shape it, and stays internal until the MVP 6 API stage.

## Decision

We make judging an `application/` surface, `application/judging.py`, that runs a **quote, consent and run** protocol. It never prompts, prints, reads a TTY, holds the workspace lock across a model call, merges, or calls a write core. Identity sets the protocol; relation typing and volatility adopt it over their existing services. Contradiction and revision judging are not covered: their rows carry no app actions.

1. **The quote is free and kind-tagged.** It is computed without a model call and states how many items there are, how many are served from persisted work, how many need a call, and the upper bound on calls. It names the resolved model, the backend and whether it is local, the effective confidential inclusion, and, for identity, the rubric digest. It carries the notices the CLI discloses before spending, and a notice when the unattended engine has recently been active.
2. **Consent is a token, and the token is a ceiling.** The token binds the fields above and the work set, each item identified with the digests of the content it covers. A run judges only items that are both in the token and still needed. Work that appeared since the quote, including an item whose content changed, is reported and not judged. A change of model, backend, locality, confidential inclusion or rubric returns a typed stale result before any call, and new consent is required. The token has no time-to-live; staleness is decided by digests and resolved values.
3. **UNCERTAIN is its own state.** An item whose servable verdict is UNCERTAIN is listed as judged uncertain, with keep-distinct and skip and no merge. It is excluded from a quote's calls unless the caller opts in, and then its cost is in the quote before consent. An edit to a member makes the verdict unservable, so the item returns to the work to judge without any reset.
4. **Verdicts persist per chunk; a stop takes effect at a chunk boundary.** The run chunks its work, judges each chunk outside the lock, and persists it in one short commit section before the next chunk starts. A stop request is honored between chunks, so a stopped or killed run keeps every completed chunk, and a new quote shows them as served. The cancel latency is the in-flight chunk; it is stated, not hidden. A busy workspace is retried a bounded number of times, and on exhaustion the run stops spending and reports how many verdicts were left unpersisted.
5. **The surface ends at persisted verdicts and queue rows.** A persisted verdict is written through to the item's open queue row, so the item becomes decidable in the curation decision surface. Write-through only amends an open row whose inputs are unchanged, in place: it never creates a row and never reopens a resolved one, and an amended row is not retired and replaced, so the queue's stale count keeps meaning that inputs changed. A judged group with no open row gets its row, verdict included, from the next producer pass.
6. **The app and the daemon coexist without a lease.** Safety comes from idempotent persists and digest pins, not mutual exclusion. The daemon carries every stored verdict that is still servable (this build's rubric, the group's exact members, their current content) into what it proposes, so a daemon pass leaves a judged row as it found it and never makes a model call to do so. The adjudications store, not the queue row, decides what is servable, because only the store records the rubric. The daemon deliberately does not compare the confidential inclusion a stored verdict was judged under: it makes no model call, has no chat client and sends nothing off the device, so it only preserves an owner-local judgment, while the quote and the run, which do send content to a model, still require the inclusion to match.
7. **The surface is internal and owner-local.** Its outcomes carry work keys and verdicts that may derive from confidential concepts, so no disclosing adapter imports it without its own gate.

This applies ADR-0018, ADR-0021, ADR-0036, ADR-0037, ADR-0039, ADR-0047, ADR-0049 and ADR-0053, and supersedes none of them.

## Consequences

**What becomes easier:**

- A desktop client can price judging, ask the person, judge, show progress, stop, and resume without importing the CLI or holding a lock while a person decides.
- Consent means something at human timescales: the run cannot spend on work or a model the person did not see.
- Paid work survives a stop, a crash and a busy workspace, up to the chunk in flight.
- A verdict the app pays for survives the daemon, and the next client lists the item as decidable.
- UNCERTAIN no longer loops through paid re-judging.

**What becomes harder, and what we accept:**

- **Two identity orchestrations.** curate keeps its inline Identity judging, so its output stays byte-identical and the tests that patch it keep working. The duplicated sequencing is accepted, guarded by shared serve and persist helpers and an equivalence test.
- **Cancel latency is a chat call.** With one group per chunk a stop can still wait up to the chat timeout. A smaller unit would need a stop hook inside the model call, which the synchronous core does not have.
- **Smaller chunks cost more LLM-free work.** Each chunk repeats a candidate-side walk the batch used to pay once. For identity that is a sensitivity scan per group, small against a judge call; for the other kinds the chunk size is set by measurement.
- **A stale result asks the person again.** A model or backend change between quote and run costs a new consent, even when the price is unchanged. That is the point.
- **Model swaps.** When the app and the daemon alternate between the judge and the smaller model, the single-model runtime (ADR-0047) swaps more often. The unattended notice discloses that the engine is active; it does not prevent it.
- **The daemon's identity rows follow the rubric.** After a judgment fix ships, the next daemon pass returns rows judged under the old rubric to pending evaluation, which is correct, and their items are offered for judging again.
- **App spend is not counted against the daemon's daily budget.** That budget bounds the runner only.
- **This is not the stable API.** The token's serialization and the observer's shape are frozen in the MVP 6 API stage.

## Alternatives considered

- **Rebuild the partition at run time with no token**, as the CLI does. Simple, but at human timescales it spends more than was quoted or on another model. Rejected.
- **A token with a time-to-live.** A day-old quote over unchanged content is still exact, and a minute-old one over a changed model is not. Time is the wrong measure. Rejected.
- **Refuse the whole run when any quoted item moved.** The daemon and ingest touch the work constantly, so most runs would be refused. Judging the intersection and reporting the rest is safe and useful. Rejected.
- **A lease or lock between the app and the daemon.** It contradicts ADR-0036 and would stall the daemon while a person decides. The harm it would prevent is wasted spend, not corruption, and idempotent persists already bound it. Rejected.
- **Persist only at the end of the run, and stop only between runner stages.** That is today's behavior, and it loses a whole batch of paid work on a stop or a busy workspace. Rejected.
- **A stop hook inside the judging leaf.** It changes every leaf, and a raising progress callback already discards the leaf's batch by contract. Chunking in the orchestrator needs no leaf change. Rejected.
- **Re-offer UNCERTAIN by default.** It pays again for an answer the judge declined to give. Rejected.
- **Let the daemon preserve whatever verdict the open row holds.** Queue rows carry no rubric digest, so a verdict from before a judgment fix would stay mergeable. Rejected in favor of serving from the store.
- **Rewire curate onto the new surface.** One implementation, but it moves the code that hundreds of test patches resolve, and silently inert patches are the likely result. Rejected for this change.
- **A generic quote and run for every kind first.** That designs the abstraction before its hardest case, the "designed in a vacuum" risk ADR-0039 warns about. Identity sets the protocol, and the other kinds adopt it. Rejected.
