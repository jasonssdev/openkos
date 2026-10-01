---
type: Decision
title: "ADR-0036: The workspace lock covers a short commit phase, not the whole verb, and lives in a per-user state directory"
description: Locked verbs compute without the lock and take it only for a commit phase that re-validates every input the plan depends on, writes, commits and refreshes the cheap derived stores; purge keeps a whole-verb lock; plain query takes no lock; the lock file moves from the OS temp directory to a per-user state directory; the CLI stays fail-fast with an opt-in bounded --wait, and derived-store contention becomes the same retry-safe exit 3. Amends ADR-0020 Decisions One, Two and Six.
status: Accepted
date: 2026-09-30
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-30T00:00:00Z
sensitivity: public
---

# ADR-0036: The workspace lock covers a short commit phase, not the whole verb, and lives in a per-user state directory

- **Status:** Accepted
- **Date:** 2026-09-30
- **Amends:** [ADR-0020](0020-concurrency-across-three-writers.md) (Decisions One, Two and Six)

## Context

ADR-0020 scoped the interprocess lock to operations and defined the
operation as "the span from Phase-A snapshot to final write". For the CLI
that span is the whole verb body: `_guard_workspace_lock` in
`src/openkos/cli/main.py` wraps everything a locked verb does, including
every model call and every confirmation prompt. Two consequences were
tolerable while the only writer was a person at a terminal, and are not
once MVP 4 adds a background runtime (#1137):

- **One verb can hold the lock for minutes.** A batch `ingest` holds it
  across every file; `chat_timeout` is 600 seconds per call; a measured
  single transcript ingest took 4m 28s. A daemon ingesting a dropped
  folder would make every locked verb a person types -- including plain
  `query`, which writes nothing and is locked anyway -- refuse with exit 3
  for the whole pass. The reverse also holds: a person sitting at a
  `Proceed?` prompt holds the lock and starves the daemon.
- **ADR-0020 deferred exactly this.** Decision Two said "MVP 4's batch work
  is the first candidate and must return here before taking one", and its
  "Explicitly not decided" section left open whether the daemon may hold
  the lock across a batch.

ADR-0020 Decision Two also answered the temp-directory hazard "by scope
rather than by relocation": the lock file lives in
`<tempdir>/openkos-locks[-<uid>]/`, and a temp reaper that deletes it while
it is held lets a second process lock a fresh inode. Scope kept holds short
for a CLI. A daemon is a long-lived process on a machine whose temp
directory is reaped on a timer, and it is the process that depends on the
lock most. `lock.py`'s own docstring says a long-lived adapter "should
revisit it".

Three facts in the code constrain the answer:

1. **The drift guard covers write targets only.** `application/drift.py`
   `describe_drift` compares the bytes of every file a verb will WRITE or
   UNLINK. It does not cover files a plan READ but will not write. Under a
   whole-verb lock no other OpenKOS writer could change those; once the
   lock is released during compute, one can. The dangerous case is
   sensitivity: `query --save` and `ingest` compute a high-water mark from
   documents they do not rewrite, and a concurrent raise of one of those
   documents to `confidential` would be silently lost in the new object.
2. **Derived-store writes can carry bundle content.** `fts.db` holds
   document text and `findings.db` holds quoted claims. `purge` deletes and
   rebuilds them under its lock, and `forget` sweeps them. A lock-free
   writer that read a document before a purge and writes its projection
   after would resurrect purged text.
3. **Derived-store contention already has two exit codes.** Workspace-lock
   contention exits 3 (retry-safe); SQLite `busy_timeout` contention on a
   derived store is mapped centrally in `_guard_workspace_lock` and in
   `reindex` to exit 1 with a message that blames "the workspace lock (a
   concurrent reindex?)". Both mean "another OpenKOS process is busy; a
   re-run is equivalent".

## Decision

**One. A locked verb holds the lock only for its commit phase.** A verb runs
as a lock-free compute phase followed by a locked commit phase. Model
calls, embedding calls, candidate walks and every human prompt happen in
compute. The commit phase takes the lock and, in order: re-validates its
inputs, performs the Phase-B write burst, runs the scoped auto-commit, and
refreshes the derived stores that cost no model call (FTS and graph). No
verb waits on a model backend or on human input while holding the lock.
The lock's unit in ADR-0020 Decision One becomes this commit phase.

**Two. The commit phase re-validates every input the plan's safety depends
on, not only its write targets.** The guarded set is the union of the write
and unlink targets (as today) and the plan's read dependencies whose
content decides a sensitivity level, a provenance list, or a catalog entry.
A changed write target refuses with exit 3 (nothing written), exactly as
the drift guard does today. The catalog files (`index.md`, `log.md`) are
the one exception: their new bytes are re-composed under the lock from the
staged plan and the current file, because every verb appends to them and a
refusal on every concurrent append would waste the compute of a whole
unattended pass. A changed read dependency that feeds a sensitivity or
provenance decision refuses rather than re-composing, because re-deciding
it silently is the failure the refusal exists to prevent.

**Three. Writes to a store that can hold bundle content happen inside a
commit phase.** Persisting findings, upserting pending-work rows, and
writing vectors each take the lock for their write and drop any row whose
inputs no longer exist or no longer match their recorded digest. The
embedding calls themselves stay outside the lock.

**Four. `purge` keeps a whole-verb lock.** It rewrites git history and
deletes every derived store; nothing may interleave with it.

**Five. Plain `query` takes no lock.** Only `query --save` does, and only
for its commit phase. The read path joins the lock-free read verbs.

**Six. Acquisition policy stays with the adapter, and the primitive stays
non-blocking** (unchanged from ADR-0020 Decision Five). The interactive CLI
fails fast by default and gains `--wait <seconds>`: a bounded retry with
backoff around the non-blocking primitive, after which it refuses with
exit 3 as today. The unattended runner retries with bounded, jittered
exponential backoff capped by its own job deadline. No blocking acquire is
added to `lock.py`.

**Seven. Derived-store contention is the same retry-safe exit 3 as lock
contention**, with a message that names the store rather than guessing at
a concurrent `reindex`.

**Eight. The lock file moves to a per-user state directory.** It keeps the
realpath-keyed file name (`<sha256(realpath(root))>.lock`) and the
owner-only checks (not a symlink, a directory, owned by the effective uid,
no group or other access), and moves to a `locks/` directory under the
account's OpenKOS state directory: `~/.local/state/openkos/` on Linux and
other POSIX systems, `~/Library/Application Support/openkos/` on macOS,
`%LOCALAPPDATA%\openkos\` on Windows. On POSIX the home directory is read
from the account database, not from `$HOME` or `$XDG_STATE_HOME`, because a
lock is a rendezvous: two processes of one user that resolved different
directories from different environments would each hold a lock the other
cannot see. Logs, which are not a rendezvous, honour `$XDG_STATE_HOME`.
For a transition period a locked run also takes the legacy temp-directory
lock, after the new one, so an older `openkos` running beside a newer one
still excludes it.

## Consequences

**Easier.** A daemon ingesting or computing findings no longer blocks a
person, and a person thinking at a prompt no longer blocks the daemon.
`query` never refuses because of an unrelated writer. The lock file is
out of the reaper's reach, which closes the hazard ADR-0020 accepted for
short holds and deferred for long-lived ones. Scripts get one retry-safe
code for "another OpenKOS process is busy", whichever store noticed.

**Harder.** Every locked verb needs its compute and commit split, which is
most of the work of this decision and cannot be done by a decorator: the
adapter must hand the service a commit-phase callback. A plan computed
lock-free can be invalidated by a concurrent writer, so a verb can now
spend its model calls and then refuse at commit; the catalog re-composition
in Decision Two keeps that rare for the common case of two appends, and
the unattended runner records it as a retryable outcome rather than a
failure. The read-dependency set is new analysis per verb, and a verb that
under-declares it reintroduces the sensitivity race silently -- the tests
that pin it are mandatory, not optional. The transitional double lock adds
one open per locked run until the legacy path is removed.

**Accepted risk.** ABA -- a concurrent writer changing a file and changing
it back between compute and commit -- still passes re-validation, as it did
under ADR-0020; identical bytes are identical content. A human editor
remains unsynchronised (ADR-0020 Decision Four is unchanged). A process
that resolves its home directory differently from another of the same
user's processes (a service launched with a different account database
view is the only realistic case) still escapes mutual exclusion; the
account-database rule makes that the exception rather than an environment
variable away.

**Follow-on commitment.** The legacy temp-directory lock is removed in a
later release, after one release in which both are taken; the tasks of the
change that adopts this ADR name the follow-up.

## Alternatives considered

**Keep the whole-verb lock and let the daemon wait.** Zero new analysis.
Rejected because the daemon's waits are not the problem; the person's
refusals are. A background ingest would still make `query` exit 3 for
minutes.

**Lock only the write burst, with the drift guard as it is.** The shape
#1137 proposed. Rejected as insufficient on the evidence above: the drift
guard does not cover read dependencies, so a concurrent sensitivity raise
could be lost without any refusal. Decision Two is the minimum addition that
keeps the guarantee the whole-verb lock gave implicitly.

**Refuse on any catalog change.** The purest drift rule. Rejected because
every verb appends to `index.md` and `log.md`; under a running daemon a
person's ingest would refuse whenever the daemon committed anything during
its extraction, discarding minutes of model work for a conflict that has a
mechanical resolution.

**Move the lock into `.openkos/`.** Removes the reaper hazard without a new
directory. Rejected for the reason ADR-0020 recorded: a refusing command
must leave the workspace byte- and structure-identical, and creating
`.openkos/` to then refuse breaks the tests that pin that guarantee.

**Honour `$XDG_STATE_HOME` for the lock.** The conventional reading of the
XDG spec. Rejected for the lock only: a daemon started by a service
manager commonly sees a different environment than a login shell, and a
lock resolved from the environment would silently stop excluding.

**Wait by default in the CLI when the holder is the daemon.** Would hide
contention from a user who cannot "wait for that command to finish" on a
process they did not start. Rejected for now because the lock file carries
no holder identity by design (ADR-0020, `lock.py`), so the CLI cannot tell
who holds it without reintroducing pidfile heuristics; with the commit
phase short, `--wait` is enough.
