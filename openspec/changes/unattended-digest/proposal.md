# Proposal: Unattended Passes End With A "What Changed" Digest (#1268 deliverable 5)

## Intent

ADR-0044's deliverable 5 turns review into an after-the-fact digest: "unattended
runs end with a 'what changed' summary listing each automatic action and its
undo". Prompts (#1264) and cap disclosure (#1265) shipped; this is the remaining
piece. Today a daemon pass that imports files and commits them prints one line
per job (`watch: completed -- calls=.. done=..`) and nothing that names the
commits, so a person who was not watching cannot tell what the engine wrote or
how to take it back. The pre-registration's bar B4 ("every automatic action
listed with its undo") reads exactly this output.

## Scope

### In Scope

- `openkos daemon` (every pass of the loop, and `--once`) ends a pass that made
  at least one automatic commit with a digest on stdout: a summary line, then one
  line per commit, newest first, then one ordering note.
- Item line shape: `<short sha> <what> [<concept ids>] -- undo: git revert <sha>`.
  The sha, the touched concepts and the undo are on the same line.
- Automatic commits that are listed today: the watch job's import commits and the
  commit-retry job's commit. Maintenance computes and enqueues only, so it makes
  none.
- The ledger is a value on `JobResult.actions`, recorded where the commit is made
  (the `autocommit` port's returned sha). No new store.
- TTY convention (ADR-0042): on a terminal the digest is its own section and
  wraps under a hanging indent; piped, the text is unwrapped and greppable. No
  ANSI.
- Living-spec deltas (`job-runtime`, `folder-watch`) and `docs/cli.md`.

### Out of Scope

- A persisted digest, a `digest` verb, or `pending` showing it. The commits are
  the record; `git log` reproduces the list (see Decisions 2 and 3).
- Actions that do not exist yet (the structural-class merges of deliverable 4).
  They join by returning `JobResult.actions`; the digest does not change shape.
- Imports whose commit was skipped or failed (no repository, identity unset, git
  error). They have no commit to name; the existing stderr warning and the
  commit-retry job cover them, and the retry's eventual commit is listed.
- `undo` commands other than `git revert` (`unmerge`, `forget`, `unrelate`): no
  current automatic action is one of those writes.

## Decisions

| # | Decision | Reason |
|---|---|---|
| 1 | The digest is per pass, printed after that pass's job reports | The daemon loop has no other end; `--once` is one pass. A pass with no commit prints nothing. |
| 2 | Printed only, nothing persisted | AGENTS.md: a derived artifact is never a source of truth. Each commit already carries its own message and sha. |
| 3 | `pending` is unchanged | `pending` lists work awaiting a decision; the digest lists work already done. Mixing them would blur the queue. |
| 4 | Undo is `git revert <sha>`, listed newest first, with a note | Every commit appends to `bundle/log.md`, so a revert is safe only while that commit is the latest (#1221). Newest-first order makes each printed command valid when its turn comes. |
| 5 | The ledger wraps the `autocommit` port instead of widening `IngestOutcome` | `ingest_service` and the attended `ingest` stay untouched; the sha is read where the commit is made. |
| 6 | Concept ids come from the committed paths | A concept id is its path minus `bundle/` and `.md`; reserved `index.md`/`log.md` and `raw/` files are not concepts. Capped at four per line; the sha is always present. |

## Capabilities

### Modified Capabilities

- `job-runtime`: a new requirement for the digest and a change to the failed
  auto-commit requirement (the retry's commit is an automatic action).
- `folder-watch`: a new requirement that an import's commit is reported.
