# Delta for Workspace Init

## ADDED Requirements

### Requirement: Conditional Git Repository Initialization

`init` MUST run `git init` in the workspace root ONLY when `vcs.git.repo_root(cwd)` returns `None` (the workspace is not already inside any git working tree). It MUST NOT run `git init` when `cwd` already resolves to a git working tree, whether the workspace root itself is that tree's root or a subdirectory of a parent repo.

#### Scenario: Fresh empty directory outside any repo

- GIVEN an empty current directory with no enclosing git working tree
- WHEN `openkos init` runs
- THEN `git init` runs in the workspace root and a `.git` directory exists

#### Scenario: Directory already inside a git working tree

- GIVEN the current directory is inside an existing git working tree (either its root or a subdirectory)
- WHEN `openkos init` runs
- THEN `git init` MUST NOT run and no nested `.git` directory is created

### Requirement: Gitignore Scaffolding

`init` MUST write a `.gitignore` at the workspace root that ignores `.openkos/` and `.DS_Store`, UNLESS a `.gitignore` already exists at that path, in which case `init` MUST NOT overwrite or modify it.

#### Scenario: No existing .gitignore

- GIVEN a workspace root with no `.gitignore`
- WHEN `openkos init` runs
- THEN a `.gitignore` is created that ignores `.openkos/` and `.DS_Store`

#### Scenario: Existing .gitignore is preserved

- GIVEN a workspace root that already contains a `.gitignore`
- WHEN `openkos init` runs
- THEN the existing `.gitignore` content is unchanged byte-for-byte

### Requirement: Scoped Initial Commit

When git identity (`user.name` and `user.email`) is configured and available, `init` MUST make exactly one commit whose message is `chore(openkos): initialize workspace`. The commit MUST include only the canonical files created by `init` (`openkos.yaml`, `AGENTS.md`, `raw/**`, `bundle/**`) plus any `.gitignore` written by this run — staged individually or by explicit path, never via a blanket `git add -A` — so pre-existing unrelated dirty content in a host working tree is never swept into the commit.

#### Scenario: Fresh repo, full commit

- GIVEN a fresh empty directory and a configured git identity
- WHEN `openkos init` runs
- THEN one commit exists with message `chore(openkos): initialize workspace`, and it contains `openkos.yaml`, `AGENTS.md`, `raw/**`, `bundle/**`, and `.gitignore`

#### Scenario: Existing repo, scoped commit excludes unrelated content

- GIVEN `cwd` is inside an existing git working tree containing unrelated untracked or modified files, and a configured git identity
- WHEN `openkos init` runs
- THEN the resulting commit contains only the files `init` itself created in this run, and the unrelated pre-existing dirty content remains untouched and uncommitted

#### Scenario: Existing repo with pre-existing .gitignore, scoped commit

- GIVEN `cwd` is inside an existing git working tree that already has a `.gitignore`, and a configured git identity
- WHEN `openkos init` runs
- THEN `git init` does not run, the existing `.gitignore` is unchanged, and the commit contains only the newly created openkos files, not the pre-existing `.gitignore`

### Requirement: Non-Fatal Git Degradation

WHEN `git` is unavailable on `PATH`, OR git identity (`user.name`/`user.email`) is unset, `init` MUST emit a non-fatal WARNING on stderr and MUST still exit 0 with a fully valid, complete workspace (all five pre-existing artifacts plus `.gitignore`, per the unmodified Workspace Creation requirement). WHEN identity specifically is unset, `init` MUST still create the repository (if applicable) and write `.gitignore`, but MUST SKIP the commit step entirely — it MUST NOT fall back to any injected bot identity.

#### Scenario: Git unavailable

- GIVEN `git` is not installed or not on `PATH`
- WHEN `openkos init` runs
- THEN a non-fatal WARNING is printed to stderr, `init` exits 0, and every workspace artifact required by the Workspace Creation requirement is present

#### Scenario: Git identity unset

- GIVEN `git` is available but `user.name` and/or `user.email` are unset
- WHEN `openkos init` runs
- THEN a non-fatal WARNING is printed to stderr, `init` exits 0, `.gitignore` and (when applicable) the repository are created, and no commit is made

### Requirement: Git Step Ordering and Layering

The git-setup step (conditional `git init`, `.gitignore` scaffolding, and the scoped commit) MUST run strictly AFTER Phase B's last canonical write (the `openkos.yaml` marker) and MUST NOT block or invalidate an otherwise-successful workspace on any git failure. The canonical layer (`model`, `bundle`, `state`) MUST NOT import `vcs`; git orchestration MUST live in the `init` CLI command, calling `vcs.git` write primitives.

#### Scenario: Git step runs after the workspace marker

- GIVEN a fresh empty directory
- WHEN `openkos init` runs
- THEN `openkos.yaml` exists before the git-setup step executes, so any git failure occurs only after the workspace is already valid

#### Scenario: Canonical layer stays git-agnostic

- GIVEN the `openkos` source tree
- WHEN `src/openkos/model/`, `src/openkos/bundle/`, and `src/openkos/state/` are inspected for imports
- THEN none of them imports `openkos.vcs`
