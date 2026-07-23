# Privacy Purge Specification

## Purpose

`openkos purge <concept-id>` is the irreversible, true-erasure counterpart to
`forget`: it whole-file-expunges a concept's source `raw/<name>` and bundle
file from ALL git history (not just the working tree) via `git-filter-repo`.
Slice 1 is honest whole-file erasure with a named residual; it does not claim
complete right-to-be-forgotten.

## Non-Goals

Content-scrub of `index.md`/`log.md` HISTORY blobs (Slice 2); scrub of any
prior `forget` tombstone text (Slice 2); the committed-`.openkos` (`fts.db`)
leak vector (Slice 2); a `forget --hard` alias.

## Requirements

### Requirement: Purge Set Resolution Reuses Forget Phase A

`purge <concept-id>` MUST accept `--scope {self,source}` (default `self`) and
MUST resolve the purge set using `forget`'s existing pure Phase A: concept-id
path-safety/resolution, `--scope source` Provenance Descendant Resolution
(orphan-after-delete fixed point), and reference-aware detection, unchanged.

#### Scenario: Self scope purge set is one concept
- GIVEN `openkos purge <concept-id>` with no `--scope` flag
- WHEN Phase A resolves the purge set
- THEN it contains exactly `<concept-id>`

#### Scenario: Source scope cascades to orphaned descendants
- GIVEN Source X and a concept C with `provenance: [X]` only
- WHEN `openkos purge X --scope source` runs
- THEN the purge set contains X and C, and both their `raw/<name>` and
  `bundle/<id>.md` paths are targeted for history expunge

### Requirement: Fail-Closed Safety Rails Run In Fixed Order Before Any Write

`purge` MUST evaluate the following rails in this exact order and refuse
(exit non-zero, write nothing, no partial rewrite) at the FIRST rail that
fails: (1) reference-aware refusal — any surviving inbound reference or
unverifiable referrer outside the purge set, unless `--force`; (2) `git` or
`git-filter-repo` is not available; (3) workspace is not a git repository, or
the workspace root is not the git repository root; (4) the working tree is
dirty (uncommitted changes); (5) the local repo has commits present on ANY
configured remote; (6) the typed confirmation phrase does not match exactly.
No rail after the first failing one MUST be evaluated, and no history rewrite
or index deletion MUST begin until ALL six rails pass. (Tool availability is
checked at rail 2 — immediately after the reference-aware gate — because it
is the cheapest, most deterministic remaining check and carries no repo-state
assumption; the git-root/dirty-tree/remote-state rails run after it.)

#### Scenario: Reference-aware refusal blocks first
- GIVEN a concept outside the purge set holds a reference to a purge-set
  member, and the workspace is otherwise git-clean
- WHEN `openkos purge <concept-id>` runs without `--force`
- THEN it refuses at rail 1, exits non-zero, and writes nothing

#### Scenario: Missing git-filter-repo refuses with an install message
- GIVEN `git-filter-repo` is not installed (or `git` is unavailable)
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 2, exits non-zero, and prints a clear install
  remediation, writing nothing

#### Scenario: Not a git repository refuses
- GIVEN the workspace root is not itself a git repository root
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 3, exits non-zero, and writes nothing

#### Scenario: Dirty working tree refuses
- GIVEN the git working tree has uncommitted changes
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 4, exits non-zero, and writes nothing

#### Scenario: Commits present on a remote refuse
- GIVEN the local branch has commits already present on a configured remote
- WHEN `openkos purge <concept-id>` runs
- THEN it refuses at rail 5, exits non-zero, and writes nothing, citing
  published history as the reason

#### Scenario: Typed confirmation mismatch aborts with no write
- GIVEN all prior rails pass and the user is prompted for a typed
  confirmation phrase
- WHEN the entered text does not match the required phrase exactly (a bare
  `y`/`yes` MUST NOT satisfy it)
- THEN `purge` aborts, exits non-zero, and no rewrite or index deletion
  occurs

#### Scenario: All rails pass, rewrite proceeds
- GIVEN no external references, a clean tree, a local-only repo (no
  matching remote commits), `git`/`git-filter-repo` available, and an
  exact typed-phrase match
- WHEN `openkos purge <concept-id>` runs
- THEN the history rewrite begins

### Requirement: Whole-History Expunge Via git-filter-repo

Once all rails pass, `purge` MUST invoke `git-filter-repo` to remove, from
ALL git history and the working tree, every purge-set member's source
`raw/<name>` (resolved from that source's `resource: raw/<name>` frontmatter)
and every purge-set member's `bundle/<id>.md`.

#### Scenario: Self-scope purge removes raw and bundle files from history
- GIVEN a successful self-scope purge of concept-id `<id>` sourced from
  `raw/<name>`
- WHEN the rewrite completes
- THEN neither `raw/<name>` nor `bundle/<id>.md` appears in `git rev-list
  --objects --all`, reflog, or `git cat-file` output

#### Scenario: Source-scope cascade removes all purge-set files from history
- GIVEN a successful `--scope source` purge whose set contains a source and
  two descendant concepts
- WHEN the rewrite completes
- THEN the source's `raw/<name>` and all three `bundle/<id>.md` files are
  absent from git history

### Requirement: Index Cleanup Is Delete-And-Rebuild, No Tombstone

After a successful rewrite, `purge` MUST delete
`.openkos/{fts,vectors,graph}.db` (not row-level `DELETE`, since SQLite's
freelist can retain deleted content) and MUST rebuild `fts.db` and `graph.db`
from the post-rewrite bundle state. `purge` MUST NOT rebuild `vectors.db` —
re-embedding requires a running Ollama embedder, a dependency `purge` must
never require; `vectors.db` stays deleted for the next `openkos reindex` to
lazily re-embed. `purge` MUST NOT write any `log.md` tombstone entry.

#### Scenario: Index files are deleted; fts.db and graph.db are rebuilt
- GIVEN a successful purge
- WHEN index cleanup runs
- THEN `.openkos/fts.db`, `.openkos/vectors.db`, and `.openkos/graph.db` are
  each deleted, `.openkos/fts.db` and `.openkos/graph.db` are replaced with
  freshly rebuilt files, and `.openkos/vectors.db` stays deleted (no Ollama
  dependency introduced)

#### Scenario: No tombstone is written
- GIVEN a successful purge of any scope
- WHEN `log.md` is inspected afterward
- THEN it contains no new tombstone entry for the purged concept(s)

### Requirement: Whole-History Content-Scrub Of index.md And log.md

After a successful rewrite, `purge` MUST content-scrub `bundle/index.md` and
`bundle/log.md` across ALL git history (every past commit's blob of exactly
these two files, and no other path) by removing, as FULL LINE removals, each
purge-set member's catalog bullet, log entries, and any `forget` tombstone
referencing it. Matching MUST use markdown link-identity (the same
`_link_identity` used elsewhere), never a bare id-substring match. A line
whose link-identity does NOT match a purge-set member — including a
surviving sibling concept's catalog bullet or an unrelated log entry that
merely mentions the purged id in prose — MUST be left byte-identical in every
commit. The scrub MUST run in the SAME single `git-filter-repo` pass as the
whole-file expunge (no second rewrite). Content outside `index.md`/`log.md`
(e.g. a surviving concept's bundle body) MUST NOT be scrubbed even if it
contains the purged id or title.

#### Scenario: Purged concept is gone from index.md and log.md history
- GIVEN a successful purge of concept `<id>` with title `<title>`
- WHEN every commit's `bundle/index.md` and `bundle/log.md` blobs are
  inspected after the rewrite
- THEN neither `<id>` nor `<title>` appears in any commit's blob of either
  file

#### Scenario: Surviving sibling and prose mention round-trip unchanged
- GIVEN a purge-set member's catalog bullet exists alongside a surviving
  sibling concept's catalog bullet in `index.md`, and a `log.md` entry that
  mentions the purge-set member's id only in prose (not as its own link)
- WHEN the history scrub runs
- THEN the sibling's catalog bullet and the prose-mention log entry are
  byte-identical, in every historical commit, to their pre-purge content

#### Scenario: Scrub is scoped to index.md and log.md only
- GIVEN a surviving concept's bundle body contains the purged id or title in
  its own text
- WHEN the history scrub runs
- THEN that bundle body's content is unchanged in every commit; only
  `bundle/index.md` and `bundle/log.md` are rewritten

### Requirement: Live log.md Tombstone Cleanup

After a successful rewrite, `purge` MUST remove any LIVE `bundle/log.md`
`forget` tombstone entry referencing a purge-set member, via a new
`remove_log_entry` function mirroring `remove_index_entry`'s live-index
cleanup, matched by the same link-identity rule.

#### Scenario: Prior forget tombstone removed from live log.md
- GIVEN a concept was previously `forget`-ed (leaving a tombstone in the
  live `log.md`) and is now purged
- WHEN the purge completes
- THEN the live `log.md` no longer contains a tombstone entry for that
  concept's id

### Requirement: Live Index Cleanup After Successful Purge

After a successful rewrite, `purge` MUST remove the LIVE `index.md` catalog
bullet for every purge-set member (reusing `forget`'s own
`remove_index_entry`/write path), so the live catalog never keeps a bullet
pointing at a concept absent from every commit. `purge` MUST NOT print any
warning stating that purged content remains in `index.md`/`log.md` history,
because the whole-history content-scrub requirement (above) removes it: after
a successful purge, the purged id/title MUST NOT appear anywhere in
`index.md` or `log.md`, in any commit, live or historical.

#### Scenario: Live index bullet is removed
- GIVEN a successful purge of any scope
- WHEN the command completes
- THEN `index.md` no longer contains a catalog bullet for any purge-set
  member

#### Scenario: No residual warning is printed
- GIVEN a successful purge of any scope
- WHEN the command completes
- THEN stdout does NOT contain any warning stating that purged content
  remains in `index.md`/`log.md` history, because no such residual exists

### Requirement: Irreversibility — No Rollback After Rewrite Begins

`purge` MUST NOT create a pre-purge backup, and MUST NOT provide any
rollback mechanism once the `git-filter-repo` rewrite has started. The
entirety of the safety model MUST occur before the rewrite begins: all six
rails from the ordered-rails requirement MUST pass before ANY write (rewrite
or index deletion) occurs, and once started, the operation runs to
completion or leaves a state requiring manual git-level recovery, not an
automated undo.

#### Scenario: No backup file is created
- GIVEN any purge invocation, successful or refused
- WHEN the command completes
- THEN no backup of the purged content is written anywhere by `purge`

#### Scenario: No rail evaluation occurs after the rewrite starts
- GIVEN a purge that has passed all six rails and begun the rewrite
- WHEN the rewrite is in progress
- THEN `purge` performs no further refusal check and offers no abort path
