# Delta for Workspace Autocommit

## ADDED Requirements

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

## MODIFIED Requirements

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
(Previously: `curate` had three commit points; the Identity automatic pass's
single run commit was not one of them.)

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
