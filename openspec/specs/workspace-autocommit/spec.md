# Workspace Autocommit Specification

## Purpose

After every mutating verb (`ingest`, `forget`, `relate`, `merge`, `unmerge`,
`reconcile`, `set-volatility`, `set-sensitivity`) completes its canonical Phase-B writes, `openkos` commits
exactly the paths that verb wrote, so the working tree stays clean without
the user ever touching git. This mirrors `init`'s git block and
reuses its primitives (`repo_root`, `has_git_identity`, `commit_paths`).

## Requirements

### Requirement: Post-Phase-B Commit Per Mutating Verb

After a successful Phase B, each of `ingest`, `forget`, `relate`, `merge`,
`unmerge`, `reconcile`, `set-volatility`, and `set-sensitivity` MUST make
exactly one commit via a shared `_autocommit(root, paths, message)` helper,
containing the verb's own Phase-B-written paths, including
`bundle/index.md` and/or `bundle/log.md` where that verb writes them,
leaving the working tree clean. The commit message MUST follow the per-verb
format: `openkos: ingest <source> (+N concepts)`, `openkos: forget <id>`,
`openkos: relate <src> -> <dst> (<type>)`, `openkos: merge <src> into
<dst>`, `openkos: unmerge <id>`, `openkos: reconcile (<summary>)`, `openkos:
set-volatility <ConceptType> -> <tier>`, `openkos: set-sensitivity <id> ->
<level>`.

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

#### Scenario: `set-volatility` commits only `openkos.yaml`

- GIVEN a git-backed workspace with configured identity
- WHEN `openkos set-volatility <ConceptType> <tier>` completes Phase B
  successfully
- THEN exactly one commit exists with message `openkos: set-volatility
  <ConceptType> -> <tier>`, containing only `openkos.yaml`, with no
  `bundle/index.md` or `bundle/log.md` change
- AND `git status` reports a clean tree

#### Scenario: `set-sensitivity` commits the concept file and the log, but not the index

- GIVEN a git-backed workspace with configured identity and an existing
  concept `<id>`
- WHEN `openkos set-sensitivity <id> <level>` completes Phase B successfully
- THEN exactly one commit exists with message `openkos: set-sensitivity
  <id> -> <level>`, containing the concept file and `bundle/log.md`, with no
  `bundle/index.md` change
- AND `git status` reports a clean tree

### Requirement: Scoped Staging Only

`_autocommit` MUST stage with `git add -- <paths>` and MUST NOT use `-A` or
`-a`, and MUST commit with the same pathspec (`git commit -- <paths>`), so
the commit contains only `<paths>`. A pre-existing unrelated dirty file
elsewhere in the workspace, whether unstaged or already staged by the user,
MUST NOT be swept into the commit, and MUST keep its staged or unstaged
state. Both git invocations MUST treat `<paths>` literally (a file name
containing `[`, `*` or `?` is a name, not a glob), MUST NOT be able to
prompt on a terminal, and MUST be abandoned after a bounded timeout; a
timeout is reported as the same non-fatal WARNING as any other commit
failure. A decline or re-open of a persisted
contradiction finding writes a `bundle/.state/**` decision path; that path
MUST be added explicitly to the caller's path list passed to `_autocommit`,
the same way `MergeResult.ledger_sidecar_path` is added for a merge. A
decision path not explicitly listed MUST NOT be picked up implicitly and
MUST NOT enter the commit.

#### Scenario: Unrelated dirty file is left untouched

- GIVEN a git-backed workspace with an unrelated pre-existing dirty
  (modified but uncommitted) file, and configured git identity
- WHEN a mutating verb completes successfully and `_autocommit` runs
- THEN the resulting commit contains only the verb's own written paths
- AND the unrelated dirty file remains modified and uncommitted after the
  command exits

#### Scenario: Content the user already staged is not committed

- GIVEN a git-backed workspace with configured identity and an unrelated
  file the user has already staged
- WHEN a mutating verb completes successfully and `_autocommit` runs
- THEN the resulting commit does not contain the staged file
- AND the file is still staged after the command exits

#### Scenario: A file name with glob characters is taken literally

- GIVEN a written path whose name contains `[a-z]*` and an untracked sibling
  that the name would match as a glob
- WHEN `_autocommit` runs
- THEN only the literally named file is committed and the sibling stays
  untracked

#### Scenario: A decline's decision path is staged explicitly

- GIVEN an operator declines a persisted contradiction finding, writing one
  `bundle/.state/**` decision path
- WHEN the decline command's `_autocommit` call runs
- THEN that decision path appears in the committed path set
- AND no other unrelated dirty path is swept into the commit

#### Scenario: An un-listed decision path never enters git

- GIVEN a decision path was written to `bundle/.state/**` but was NOT added
  to the caller's `_autocommit` path list
- WHEN `_autocommit` runs
- THEN that path is not staged and does not appear in the resulting commit

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
`sensitivity` value equals `confidential` (the top rank of
`okf.SENSITIVITY_ORDER`, tested as
`str(meta.get("sensitivity", "")).strip() == "confidential"` — a
transparency check, NOT the fail-closed `sensitivity.blocks_llm_send` LLM
gate), `openkos` MUST emit exactly ONE stderr NOTICE for that command
invocation, regardless of how many confidential files are staged. A commit
containing no confidential-ranked staged file — including files with a
missing, blank, or unparseable `sensitivity` — MUST NOT emit the notice.

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

### Requirement: Commit Disclosure For The Recovery-Critical Verbs

`forget`, `merge`, and `curate` MUST each print one line naming the commit
`_autocommit` just wrote and the `git revert` that undoes it. `curate` has
four commit points — the Identity automatic pass (one commit for the run's
automatic merges), Identity (per accepted merge), Structure (per accepted
edge), and Metadata (per accepted tier) — and each MUST disclose its own
commit, since each commits before the next item is considered. The wording
MUST come from one shared helper rather than a per-site string, so the call
sites cannot drift into several spellings of the same sentence.

The line MUST NOT advertise an unconditional undo. Every commit appends to
`bundle/log.md`, so `git revert` of any commit but the latest conflicts there
and leaves the bundle unparseable mid-revert; the line therefore states that
`git revert <sha>` undoes the commit only while it is the latest commit.

The scope is exactly those three verbs. Every other mutating verb —
`ingest`, `relate`, `unmerge`, `reconcile`, `set-volatility`,
`set-sensitivity`, `adjudicate`'s merge walks — MUST keep its output
unchanged: these are the verbs whose writes a human most often wants back,
and a line on all of them is noise that stops being read.

To name the commit, `commit_paths` and `_autocommit` MUST return the new
commit's abbreviated sha, and MUST return `None` on every degradation path
(not a git repository, git identity unset, the commit raising, or the sha
being unreadable). The disclosure MUST be printed ONLY when a sha came back:
a workspace with no git identity makes no commit, so telling its user to
`git revert <commit>` would name a commit that does not exist. The existing
non-fatal WARNING remains the whole report in that case.

#### Scenario: `forget` names the commit it wrote

- GIVEN a git-backed workspace with configured identity
- WHEN `openkos forget <id>` completes Phase B successfully
- THEN stdout carries one line naming the new commit's short sha and the
  `git revert <sha>` that undoes it, after the verb's own success line

#### Scenario: `merge` names the commit it wrote

- GIVEN a git-backed workspace with configured identity
- WHEN `openkos merge <survivor> <absorbed>` completes Phase B successfully
- THEN stdout carries one line naming the new commit's short sha and the
  `git revert <sha>` that undoes it

#### Scenario: Each `curate` stage names its own commit

- GIVEN a git-backed workspace with configured identity
- WHEN `curate`'s Identity, Structure, or Metadata stage applies one item
- THEN stdout carries one line for that item naming the commit's short sha
  and the `git revert <sha>` that undoes it

#### Scenario: The automatic pass names its one run commit

- GIVEN a git-backed workspace with configured identity and an automatic
  pass that applied two merges
- WHEN the pass completes
- THEN stdout carries one commit line naming the run commit's short sha and
  the `git revert <sha>` that undoes it only while it is the latest commit,
  alongside the per-merge disclosure lines

#### Scenario: A degraded auto-commit discloses nothing

- GIVEN a workspace where `_autocommit` degrades (no repository, identity
  unset, or the commit raising)
- WHEN `forget`, `merge`, or any writing `curate` stage completes
- THEN no commit line is printed on any stream, and only the existing
  non-fatal WARNING reports what happened
### Requirement: Exclusions and Unconditional Behavior

`reindex` output (`.openkos/*.db`) MUST NEVER be staged or committed by
`_autocommit` (it stays gitignored). `purge` is out of scope for this
capability. Auto-commit MUST be unconditional — there MUST be
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

### Requirement: Under The Job Runner, A Commit Failure Is A Recorded, Retryable Outcome

WHEN the auto-commit of a job started by the job runner degrades for any
reason `Non-Fatal Degradation` names, the job MUST record the outcome
`commit_failed` together with the workspace-relative paths it wrote and the
failure's class (not a repository, identity unset, git error, git timeout),
instead of only printing a warning. The next job MUST first attempt the
same scoped commit for those recorded paths that are still uncommitted, and
MUST record whether it succeeded. The interactive CLI's behavior is
unchanged: a degraded commit there remains a non-fatal warning with the
verb's normal exit code.

#### Scenario: The next job commits what the last one could not

- GIVEN a job whose commit failed with a git error, leaving its writes
  uncommitted
- WHEN the next job starts and git succeeds
- THEN it commits exactly the recorded paths still uncommitted, with the
  scoped staging this capability already requires, and records the retry as
  successful

#### Scenario: A permanent failure stays visible

- GIVEN a workspace that is not a git repository
- WHEN a job's commit is skipped for that reason
- THEN the job records `commit_failed` and `status` names the cause until it
  is fixed
### Requirement: One Commit For All Automatic Merges Of A Run

The automatic Identity pass of `curate --auto-merge` (`identity-auto-merge`)
MUST commit all the merges it applied in one run as a single commit through
the shared `_autocommit` helper, as an exception to Identity's one commit per
merge. The commit MUST contain exactly the union of the paths those merges
wrote (including `bundle/index.md` and `bundle/log.md`), staged and committed
with the scoped pathspec this specification requires, and MUST NOT sweep an
unrelated dirty file. Its message MUST follow the `openkos: ` prefix
convention and name that it is an auto-merge commit and the number of merges
it holds. When the pass applied no merge it MUST make no commit. Every Identity
merge made outside the pass, including those applied by accept-recommended,
MUST keep its own commit.

#### Scenario: Several automatic merges land in one commit

- GIVEN a git-backed workspace with identity and three automatic merges
- WHEN the pass completes
- THEN exactly one commit holds all three and `git status` is clean

#### Scenario: An unrelated dirty file is not swept in

- GIVEN a pre-existing unrelated modified file
- WHEN the pass commits its merges
- THEN that file is not in the commit and is still modified

#### Scenario: Interactive merges keep per-merge commits

- GIVEN `--auto-merge` merged one pair and the operator then answers `y` to
  two per-item prompts
- WHEN Identity completes
- THEN there is one commit for the pass and one for each answered merge

#### Scenario: No merge makes no commit

- GIVEN the pass applies nothing
- WHEN it completes
- THEN no commit is created by the pass

