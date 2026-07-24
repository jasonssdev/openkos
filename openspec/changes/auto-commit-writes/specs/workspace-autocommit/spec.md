# Workspace Autocommit Specification

## Purpose

After every mutating verb (`ingest`, `forget`, `relate`, `merge`, `unmerge`,
`reconcile`) completes its canonical Phase-B writes, `openkos` commits
exactly the paths that verb wrote, so the working tree stays clean without
the user ever touching git. This mirrors `init`'s Slice 1 git block and
reuses its primitives (`repo_root`, `has_git_identity`, `commit_paths`).

## Requirements

### Requirement: Post-Phase-B Commit Per Mutating Verb

After a successful Phase B, each of `ingest`, `forget`, `relate`, `merge`,
`unmerge`, and `reconcile` MUST make exactly one commit via a shared
`_autocommit(root, paths, message)` helper, containing the verb's own
Phase-B-written paths plus `bundle/index.md` and `bundle/log.md`, leaving
the working tree clean. The commit message MUST follow the per-verb format:
`openkos: ingest <source> (+N concepts)`, `openkos: forget <id>`,
`openkos: relate <src> -> <dst> (<type>)`, `openkos: merge <src> into
<dst>`, `openkos: unmerge <id>`, `openkos: reconcile (<summary>)`.

#### Scenario: Ingest commits new concept files and log/index

- GIVEN a git-backed workspace with configured identity
- WHEN `openkos ingest <source>` completes Phase B successfully
- THEN exactly one commit exists with message `openkos: ingest <source>
  (+N concepts)`, containing the new/updated `bundle/**` concept files, the
  `raw/**` source copy, `bundle/index.md`, and `bundle/log.md`
- AND `git status` reports a clean tree

#### Scenario: Forget commits the removed concept file

- GIVEN a git-backed workspace with configured identity and an existing
  concept `<id>`
- WHEN `openkos forget <id>` completes Phase B successfully
- THEN exactly one commit exists with message `openkos: forget <id>`,
  containing the concept file's removal, `bundle/index.md`, and
  `bundle/log.md`
- AND `git status` reports a clean tree

#### Scenario: Remaining mutating verbs each produce one scoped commit

- GIVEN a git-backed workspace with configured identity
- WHEN `openkos relate`, `openkos merge`, `openkos unmerge`, or `openkos
  reconcile` completes Phase B successfully
- THEN exactly one commit exists with that verb's message format from the
  table above, containing only the paths that verb's Phase B wrote plus
  `bundle/index.md` and `bundle/log.md`
- AND `git status` reports a clean tree

### Requirement: Scoped Staging Only

`_autocommit` MUST stage with `git add -- <paths>` and MUST NOT use `-A` or
`-a`. A pre-existing unrelated dirty file elsewhere in the workspace MUST
NOT be swept into the commit.

#### Scenario: Unrelated dirty file is left untouched

- GIVEN a git-backed workspace with an unrelated pre-existing dirty
  (modified but uncommitted) file, and configured git identity
- WHEN a mutating verb completes successfully and `_autocommit` runs
- THEN the resulting commit contains only the verb's own written paths
- AND the unrelated dirty file remains modified and uncommitted after the
  command exits

### Requirement: Commit Fires Only on the Success Path

`_autocommit` MUST run strictly AFTER the verb's `--auto`/`review` confirm
gate and AFTER Phase B has landed. WHEN a verb's confirm gate is declined or
refused (Phase B does not run), no commit MUST be attempted.

#### Scenario: Declined confirm gate makes no commit

- GIVEN a git-backed workspace with configured identity and a verb that
  requires interactive confirmation
- WHEN the user declines the confirm prompt
- THEN Phase B does not run, `_autocommit` is not invoked, and no new
  commit exists after the command exits

### Requirement: Non-Fatal Degradation

WHEN the workspace is not inside a git working tree (`repo_root(root) is
None`), OR git identity (`user.name`/`user.email`) is unset, OR
`commit_paths` raises a git error (`GitError`/`OSError`), `openkos` MUST
emit a non-fatal WARNING to stderr and the verb MUST still complete with
its normal success exit code, because the canonical writes already landed
before `_autocommit` ran.

#### Scenario: Not a git repository

- GIVEN a workspace where `repo_root(root)` returns `None`
- WHEN a mutating verb completes Phase B successfully
- THEN a non-fatal WARNING is printed to stderr, and the verb exits its
  normal success code with all canonical writes present on disk

#### Scenario: Git identity unset

- GIVEN a git-backed workspace where `user.name` and/or `user.email` are
  unset
- WHEN a mutating verb completes Phase B successfully
- THEN a non-fatal WARNING is printed to stderr, no commit is made, and the
  verb exits its normal success code

#### Scenario: Commit step raises a git error

- GIVEN a git-backed workspace with configured identity, where
  `commit_paths` raises `GitError` or `OSError` (e.g. a hook rejection or
  disk pressure)
- WHEN a mutating verb completes Phase B successfully and `_autocommit`
  attempts the commit
- THEN the exception is caught, a non-fatal WARNING is printed to stderr,
  and the verb exits its normal success code with the canonical writes
  intact

### Requirement: One-Time Confidential Transparency Notice

WHEN a commit includes any staged concept file whose frontmatter
`sensitivity` ranks confidential per
`sensitivity.blocks_llm_send(value, threshold="confidential")`, `openkos`
MUST emit exactly ONE stderr NOTICE for that command invocation, regardless
of how many confidential files are staged. A commit containing no
confidential-ranked staged file MUST NOT emit the notice.

#### Scenario: Single confidential file triggers exactly one notice

- GIVEN a mutating verb's Phase B writes one concept file with
  `sensitivity: confidential`
- WHEN `_autocommit` stages and commits that file
- THEN exactly one NOTICE is printed to stderr for the invocation

#### Scenario: Multiple confidential files still emit only one notice

- GIVEN a mutating verb's Phase B writes several concept files, more than
  one of which ranks confidential
- WHEN `_autocommit` stages and commits them in a single commit
- THEN exactly one NOTICE is printed to stderr, not one per file

#### Scenario: No confidential content, no notice

- GIVEN a mutating verb's Phase B writes only concept files ranked below
  confidential
- WHEN `_autocommit` stages and commits them
- THEN no NOTICE is printed to stderr

### Requirement: Exclusions and Unconditional Behavior

`reindex` output (`.openkos/*.db`) MUST NEVER be staged or committed by
`_autocommit` (it stays gitignored). `purge` is out of scope for this
capability. Auto-commit MUST be unconditional in this slice — there MUST be
no configuration flag or CLI option to disable it.

#### Scenario: Derived index database is never committed

- GIVEN a workspace with `.openkos/*.db` present after a mutating verb runs
- WHEN `_autocommit` runs for that verb
- THEN the resulting commit does not include any path under `.openkos/`

#### Scenario: No opt-out exists

- GIVEN a git-backed workspace with configured identity
- WHEN any mutating verb runs to success, with no flag or config setting
  requesting that auto-commit be skipped
- THEN `_autocommit` still runs and produces its commit
