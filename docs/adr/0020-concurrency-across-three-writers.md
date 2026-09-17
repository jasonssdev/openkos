---
type: Decision
title: "ADR-0020: Concurrency across three writers -- per-operation locking, detection for the rest"
description: The interprocess lock synchronises OpenKOS operations, not processes or sessions; writers it cannot reach are handled by detection, and reads stay lock-free.
status: Proposed
date: 2026-09-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-16T00:00:00Z
sensitivity: public
---

# ADR-0020: Concurrency across three writers -- per-operation locking, detection for the rest

- **Status:** Proposed
- **Date:** 2026-09-16

## Context

`docs/roadmap.md` gates MVP 3 on two ADRs before code, and states the
problem this one settles: the interprocess lock shipped for #925 "makes
those processes safe with respect to each other; it says nothing about
those three writers" -- a human in an editor, an agent over MCP, and
(from MVP 4) a daemon.

**What the lock actually does today.** `src/openkos/lock.py` takes an
OS-level *advisory exclusive* lock -- `fcntl.flock(LOCK_EX|LOCK_NB)` on
POSIX, `msvcrt.locking(LK_NBLCK, 1)` on Windows (`lock.py:97-103`) -- on
a file outside the workspace, at
`<tempdir>/openkos-locks[-<uid>]/<sha256(realpath(root))>.lock`
(`lock.py:128-141`). It is non-blocking with no timeout and no retry
queue: contention raises `WorkspaceBusyError` immediately
(`lock.py:30-34`), which `_guard_workspace_lock`
(`src/openkos/cli/main.py:290-334`) maps to a refusal and exit code 3.
Release is delegated to the kernel, so a crashed holder leaves an inert
file rather than a stale claim, and no pidfile staleness heuristic
exists (`lock.py:22-28`).

Three facts about that design force this decision.

**1. The lock reaches 22 of 27 commands. The five it does not reach are
exactly the five MVP 3 exposes.** `status` (`main.py:10179`), `next`
(`10405`), `list` (`10498`), `lint` (`10708`) and `doctor` (`14916`)
never call `workspace_lock`; #925 proposed that shape deliberately
("read-only verbs stay lock-free"). Those same five verbs are the ones
prerequisite zero (#995) just moved behind application services, and
they are the material the MCP read tools are built from. Whatever
posture reads have today, MCP inherits.

**2. The drift guard is a single-process guard, and it says so.**
`_reject_drifted_targets` (`main.py:1084-1230`) compares Phase-A bytes to
current bytes immediately before writing and refuses on mismatch. Its own
docstring states the limits: it cannot detect a second process's write
landing between two byte-identical states, nor a write arriving between
its check and the following `fsio.write_atomic`. It also names the widest
window -- "`--auto` and `review: false` skip the prompt but not the
window... makes those runs the likeliest to race a second writer." An
MCP write tool is `--auto` by construction: there is no human at a
prompt. The riskiest existing shape is the default shape for the new
adapter.

**3. The lock's placement was accepted on a premise MVP 3 breaks.**
Putting the lock at `.openkos/workspace.lock` turned 96 tests red,
because `tests/unit/cli/conftest.py` records directories as well as
files so that a refusal which created a stray path is caught: a
refusing command must leave the workspace byte- and structure-identical.
The lock moved outside the workspace instead of weakening that
guarantee, and `docs/architecture.md:263-268` records the result. The
accepted residual risk was a temp reaper deleting the lock file while it
is held -- on POSIX the lock survives on the open descriptor, but a
second process creating a fresh file at that path gets a different
inode and a lock that conflicts with nothing, so two writers both
believe they hold it. That was judged unreachable for a CLI process
measured in seconds, and explicitly deferred: it "must be revisited for
a long-lived MVP 3 adapter."

A fourth fact bounds how much any lock can achieve. `flock` is
**advisory**: nothing stops Obsidian, `git`, or a text editor from
writing `bundle/*.md` while a lock is held (`lock.py:19-21`). The human
in the editor -- the writer MVP 3 exists to serve, since "read and edit
the answer in Obsidian" is its stated outcome -- is unsynchronisable by
construction. Any decision that treats all three writers as lockable is
describing a system we do not have.

## Decision

We scope the lock to **operations, not processes or sessions**, and we
treat the three writers as two distinct classes rather than three peers.

**One. The lock synchronises OpenKOS operations. An adapter never holds
it for its own lifetime.** An MCP server acquires `workspace_lock` around
each individual mutating operation and releases it before returning; a
daemon does the same per unit of work. A session-scoped or
connection-scoped hold is forbidden. This is the whole invariant: the
lock's unit is the span from Phase-A snapshot to final write, which is
what `_guard_workspace_lock` already wraps for the CLI, and it must not
grow to match a process that now outlives the operation.

**Two. This is also what keeps the temp-directory placement valid, so it
is not merely a preference.** The reaper hazard is a function of *hold
duration*, not process lifetime. Per-operation scoping keeps holds in the
range the #925 tradeoff was accepted for, and the deferred revisit is
therefore answered by scope rather than by relocation. A holder that ever
needs to exceed that range must re-validate that it still holds the lock
it thinks it holds, rather than assume acquisition is permanent. No
caller has that need today; MVP 4's batch work is the first candidate and
must return here before taking one.

**Three. Reads stay lock-free, and the resulting weakness is stated
rather than closed.** The five read verbs, and every MCP read tool built
on them, take no lock. `fsio.write_atomic` (`fsio.py:77-107`) guarantees
a reader never observes a half-written *file*, but it guarantees nothing
about a half-written *operation*: a merge that has replaced the survivor
and not yet rewritten `index.md` is a state a concurrent reader can
legitimately observe. We accept that, because the alternative starves the
CLI (see Alternatives), and we require instead that a read which can
observe an in-flight multi-file operation be able to **say so**. The
existing machinery is the right shape for this and is already load-
bearing elsewhere: `bundle_ledger.scan_torn_writes` (`ledger.py:301-320`)
detects a pending marker with no committed state, and derived staleness
(`state/derived.py:191-241`) fails safe by treating unreadable or
unstamped stores as stale rather than raising. Reporting an inconsistent
read is in scope for MVP 3; preventing one is not.

**Four. The human in the editor is an unsynchronised writer, handled by
detection after the fact -- not by a lock, ever.** This is a boundary,
not a gap to be closed later. OpenKOS synchronises its own operations
with each other; every other writer is reconciled by the mechanisms that
already exist for exactly this: `_reject_drifted_targets`' byte
comparison, the per-row input digests findings rows carry
(`state/findings.py:99-108`, `256-269`), and whole-bundle manifest
hashing for `fts.db` and `graph.db`. No future ADR should propose making
Obsidian participate in the lock protocol.

**Five. Acquisition stays non-blocking in `lock.py`; retry policy belongs
to the adapter.** The module keeps its single non-blocking primitive and
its `WorkspaceBusyError`. The CLI continues to map that to exit 3, which
is correct for a human who can re-run a command. An MCP adapter maps it
to a retryable protocol error and MAY retry with bounded backoff, because
an agent has no operator to re-run anything and a bare refusal converts
ordinary contention into a failed task. This split follows ADR-0018: the
service composes and decides, the adapter owns protocol error shapes.
Blocking acquisition is not added to `lock.py`, so no caller can acquire
an unbounded wait by accident.

**Six. MCP write tools hold the lock across Phase A, not only across the
write.** Because they are `--auto` by construction they lose the
confirmation prompt but keep the read-then-write window that `forget`
(`main.py:5586`), `merge` (`main.py:8543-8552`) and `reconcile`
(`main.py:9584-9600`) each open with a whole-bundle snapshot. The lock
must span that window, matching what `_guard_workspace_lock` does for
the CLI body today.

## Consequences

**Easier.** The MCP adapter has one rule to follow rather than a
judgement call per tool, and it is the same rule the CLI already obeys,
so the two adapters cannot drift into different concurrency models. The
#925 deferred revisit is answered without moving the lock file and
without reopening the 96 tests that put it where it is.

**Harder.** "Read tools may return a state no single operation ever
produced" is a property MVP 3 must document for its users, not a bug
report to be filed later. Someone will read an inconsistent bundle
through MCP and it will look like corruption; the detection required by
Decision Three is what makes that distinguishable, and it is work this
ADR creates rather than describes. Concretely, nothing today tells a
reader that a multi-file operation is in flight: `scan_torn_writes`
covers the merge ledger's two-phase marker, not an interrupted
`index.md` rewrite.

**Accepted risk.** A human editing in Obsidian during an MCP write can
still lose their edit to a last-writer-wins overwrite, exactly as #925
described for two CLI processes, because the drift guard's window is
real and Obsidian holds no lock. We accept it for MVP 3 rather than
pretend otherwise. The mitigation available today is that the guard
refuses on a *detected* mismatch, so the common case is a refusal rather
than silent loss; the uncommon case -- a write landing inside the guard's
own check-to-write gap -- remains silent, and no mechanism in this ADR
changes that.

**Explicitly not decided here.** The sync/async boundary, which the
roadmap names as the other required ADR; ADR-0018 already fixes the
application layer as synchronous with async confined to the MVP 3 edge,
and the remaining question is how that edge is shaped. Also undecided:
whether MVP 4's daemon may ever hold a lock across a batch, which
Decision Two requires it to come back for.

## Alternatives considered

**The MCP server holds the lock for its lifetime.** The simplest possible
rule, and it makes every server write trivially safe. Rejected because it
inverts the product: a running server would make every CLI invocation of
the 22 mutating verbs fail with exit 3, so using the MCP adapter would
mean giving up the terminal. It also maximises exactly the hold duration
the temp-reaper tradeoff was accepted on the assumption of minimising.

**Shared locks for reads (`LOCK_SH`).** Would close Decision Three's
weakness directly and is a small change to `lock.py`. Rejected on the
interaction with non-blocking acquisition: a read-heavy agent would hold
shared locks often, and every CLI writer that met one would fail fast at
exit 3 rather than wait, converting a consistency improvement into a
availability regression for the writer we already have. Revisiting this
requires deciding blocking acquisition first, which Decision Five
declines.

**Move the lock inside the workspace now that a server needs it.** Would
remove the reaper hazard entirely. Rejected on evidence: it is the
placement that turned 96 tests red, and those tests protect a real
product guarantee -- a refusing command leaves the workspace byte- and
structure-identical. Decision Two removes the motivation anyway.

**Blocking acquisition with a timeout in `lock.py`.** Tempting because it
would let the adapter simply wait. Rejected because a timeout is policy,
and placing it in the leaf module fixes one number for a CLI, an MCP
server and a daemon, whose tolerances are not the same. Decision Five
puts the policy where the tolerance is known.

**A pidfile with staleness heuristics.** Would survive a reaper by making
the lock's identity independent of the inode. Rejected for the reason
`lock.py:22-28` already records: kernel-delegated release is correct
without a heuristic, and every pidfile scheme has to guess about a pid it
cannot prove is the same process.

**Treating the editor as a third lockable writer.** Rejected as
unimplementable rather than undesirable: `flock` is advisory, and the
editor is a third-party program we do not control. Naming this in
Decision Four is meant to stop it being re-proposed.
