# Delta for Workspace Autocommit

## MODIFIED Requirements

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
(Previously: enumerated only `ingest`, `forget`, `relate`, `merge`,
`unmerge`, `reconcile`; the paths clause universally appended
`bundle/index.md` and `bundle/log.md` to every verb's commit, which is
factually wrong for `set-sensitivity` (log only, no index) and
`set-volatility` (`openkos.yaml` only, neither log nor index).)

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
