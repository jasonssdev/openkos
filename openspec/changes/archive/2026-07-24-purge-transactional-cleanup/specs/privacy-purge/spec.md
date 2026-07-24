# Delta for Privacy Purge

## ADDED Requirements

### Requirement: Deferred-Reembed Warning On Success

Because a successful purge deletes `.openkos/vectors.db` without rebuilding
it (per the Index Cleanup requirement), `purge`'s success output MUST
include a warning stating that dense retrieval is degraded until the index
is rebuilt, and MUST instruct the user to run `openkos reindex`. This MUST
be message-only: `purge` MUST NOT prompt interactively and MUST NOT
auto-run `reindex` itself.

#### Scenario: Successful purge warns about degraded dense retrieval

- GIVEN a successful `openkos purge <concept-id>` run
- WHEN the command prints its success output
- THEN the output includes a warning that dense retrieval is degraded and
  an instruction to run `openkos reindex`

#### Scenario: No interactive prompt or auto-reindex occurs

- GIVEN a successful purge that deleted `.openkos/vectors.db`
- WHEN the command completes
- THEN `purge` does not prompt for confirmation to reindex and does not
  invoke `reindex` itself

### Requirement: Post-Rewrite Live-Tree Auto-Commit

After a successful purge completes its live-tree cleanup (removal of the
live `index.md` catalog bullet and any live `log.md` tombstone for every
purge-set member), `purge` MUST commit the resulting live-tree state via
the shared `_autocommit(root, paths, message)` helper, staging
`bundle/index.md` and `bundle/log.md`, with commit message `openkos: purge
<id>` (or `openkos: purge <id> (+N)` when the purge set contains additional
cascaded members). This commit MUST run strictly after the live-tree
cleanup and MUST leave the working tree clean.

The commit step MUST be non-fatal: a git failure (workspace not a git
working tree, missing identity, or `commit_paths` raising `GitError`/
`OSError`) MUST emit a non-fatal WARNING to stderr and MUST NOT change
`purge`'s exit code, because the already-irreversible history rewrite and
index cleanup have already landed. The commit step MUST also tolerate an
empty diff: when `git-filter-repo`'s rewrite already left the live tree
identical to what `_autocommit` would stage (no pending changes to
`index.md`/`log.md`), `purge` MUST still succeed with no error.

#### Scenario: Successful purge leaves a clean working tree via commit

- GIVEN a purge that has passed all six safety rails, completed the
  history rewrite, and finished live-tree cleanup
- WHEN the commit step runs
- THEN exactly one commit exists with message `openkos: purge <id>`
  (or the `(+N)` cascaded form), containing `bundle/index.md` and
  `bundle/log.md`, and `git status` reports a clean tree

#### Scenario: Commit failure does not fail the already-irreversible purge

- GIVEN a purge that has completed its history rewrite and index cleanup,
  where the commit step raises `GitError` or `OSError`
- WHEN the commit step runs
- THEN a non-fatal WARNING is printed to stderr, and `purge` still exits
  with its normal success code

#### Scenario: Empty diff after filter-repo's own rewrite still succeeds

- GIVEN a purge where `git-filter-repo`'s rewrite already left
  `index.md`/`log.md` identical to the state `_autocommit` would stage (no
  pending changes to commit)
- WHEN the commit step runs
- THEN `purge` completes successfully with no error, whether or not a new
  commit was created
