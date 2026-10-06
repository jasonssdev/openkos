# Delta for Workspace Autocommit

## MODIFIED Requirements

### Requirement: Post-Phase-B Commit Per Mutating Verb

After a successful Phase B, each of `ingest`, `import`, `forget`, `relate`, `merge`,
`unmerge`, `reconcile`, `set-volatility`, and `set-sensitivity` MUST make
exactly one commit via a shared `_autocommit(root, paths, message)` helper,
containing the verb's own Phase-B-written paths, including
`bundle/index.md` and/or `bundle/log.md` where that verb writes them,
leaving the working tree clean. The commit message MUST follow the per-verb
format: `openkos: ingest <source> (+N concepts)`, `openkos: import
<namespace> (+N concepts)`, `openkos: forget <id>`,
`openkos: relate <src> -> <dst> (<type>)`, `openkos: merge <src> into
<dst>`, `openkos: unmerge <id>`, `openkos: reconcile (<summary>)`, `openkos:
set-volatility <ConceptType> -> <tier>`, `openkos: set-sensitivity <id> ->
<level>`.
(Previously: `import` was not a mutating verb with a commit.)

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

#### Scenario: Import commits the namespace, anchor, index and log

- GIVEN a git-backed workspace with configured identity
- WHEN `openkos import <dir> --namespace acme` completes Phase B
  successfully, adopting N concepts
- THEN exactly one commit exists with message `openkos: import acme
  (+N concepts)`, containing the adopted files under
  `bundle/imports/acme/`, the import anchor Source(s), `bundle/index.md`
  and `bundle/log.md`, and nothing else
- AND `git status` reports a clean tree

### Requirement: Commit Disclosure For The Recovery-Critical Verbs

`forget`, `merge`, `import`, and `curate` MUST each print one line naming the commit
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
`git revert <sha>` undoes the commit only while it is the latest commit. For
`import`, whose re-import is refused, that revert followed by a new import is
the documented way to start over.

The scope is exactly those four verbs. Every other mutating verb —
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
(Previously: the scope was `forget`, `merge` and `curate`; `import` is added.)

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

#### Scenario: `import` names the commit it wrote

- GIVEN a git-backed workspace with configured identity
- WHEN `openkos import <dir> --namespace acme` completes Phase B
  successfully
- THEN stdout carries one line naming the new commit's short sha and the
  `git revert <sha>` that undoes it only while it is the latest commit

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
- WHEN `forget`, `merge`, `import`, or any writing `curate` stage completes
- THEN no commit line is printed on any stream, and only the existing
  non-fatal WARNING reports what happened
