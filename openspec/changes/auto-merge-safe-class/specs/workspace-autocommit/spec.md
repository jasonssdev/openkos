# Delta for Workspace Autocommit

> **Conditional delta.** Built ONLY if the pre-registered decision rule in
> `auto-merge-safe-class/design.md` passes. On FAIL this file is deleted
> from the change before archive.

## MODIFIED Requirements

### Requirement: Commit Disclosure For The Recovery-Critical Verbs

`forget`, `merge`, and `curate` MUST each print one line naming the commit
`_autocommit` just wrote and the `git revert` that undoes it. `curate` has
four commit points — Identity (per accepted merge), Identity's automatic
pass under `--auto-merge` (ONE commit per run covering every automatic
merge of that run and its `**Auto-merge**` `log.md` bullet), Structure (per
accepted edge), and Metadata (per accepted tier) — and each MUST disclose
its own commit, since each commits before the next item is considered. The
wording MUST come from one shared helper rather than a per-site string, so
the call sites cannot drift into different spellings of the same sentence.
(Previously: `curate` had three commit points, and every Identity merge was
committed individually.)

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

#### Scenario: An automatic merge run is one commit

- GIVEN a git-backed workspace with configured identity and two eligible,
  disjoint pairs
- WHEN `curate --auto-merge` applies both automatically
- THEN exactly one new commit contains both merges and the run's
  `**Auto-merge**` bullet, and stdout carries exactly one commit line for
  the automatic pass

#### Scenario: A degraded auto-commit discloses nothing

- GIVEN a workspace where `_autocommit` degrades (no repository, identity
  unset, or the commit raising)
- WHEN `forget`, `merge`, or any writing `curate` stage completes
- THEN no commit line is printed on any stream, and only the existing
  non-fatal WARNING reports what happened
