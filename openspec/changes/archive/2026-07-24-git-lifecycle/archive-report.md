# Archive Report: git-lifecycle (Slice 1)

**Date**: 2026-07-24
**Change**: `git-lifecycle` — Slice 1 (`init` sets up git)
**Status**: ARCHIVED

## Executive Summary

Slice 1 of the maintainer-approved git-lifecycle "Auto" arc: `openkos init` now
sets up git for a workspace — a conditional `git init`, a scaffolded `.gitignore`,
and a scoped initial commit — degrading non-fatally when git is unavailable or the
git identity is unset. This closes the prerequisite gap that made `openkos purge`
unusable out of the box (a fresh workspace previously failed purge's "must be a git
repo" rail until the user manually ran `git init` + a commit).

**Resolves**: issue #143. Part of the git-lifecycle arc (#145 decided — "Auto" model).
**Merged**: PR #151 (squash).
**All tests pass**: 1877 passed (up from 1874 baseline before the resilience correction).
**Verification verdict**: PASS (0 CRITICAL).
**Resilience review**: 2 WARNINGs found and fixed before merge (see below).

## Change Scope

### Summary
- **Capability**: `workspace-init` (the `openkos init` command).
- **Behavior**: after Phase B's last canonical write, `init` runs one best-effort,
  non-fatal git-setup step:
  - `git init` — only when `repo_root(cwd) is None` (never hijacks a parent repo).
  - Scaffolds `.gitignore` — an openkos header ignoring `.openkos/` (derived stores)
    followed by the standard toptal windows/linux/macos/python template. Respects an
    existing `.gitignore` (never overwrites).
  - One scoped initial commit — `git add -- <init-created paths>` (never `-A`),
    message `chore(openkos): initialize workspace`.
  - Degrades non-fatally: git unavailable or identity unset → stderr WARNING, exit 0,
    valid workspace. A mid-setup git error → honest, actionable WARNING (points at
    `git status`), exit 0.
- **Layering**: git primitives live in `src/openkos/vcs/git.py` (reusing the sole
  `_run()` subprocess seam); the canonical layer (`model`/`bundle`/`state`) never
  imports `vcs`; orchestration is in the `init` CLI command.

### Files Modified
1. `src/openkos/vcs/git.py` — added `init_repo`, `has_git_identity`, `commit_paths`
   write primitives and the `_GITIGNORE_TEMPLATE` constant; hardened `_run()` to map
   `UnicodeDecodeError` (non-UTF-8 git output) → `GitError` (resilience fix 1).
2. `src/openkos/cli/main.py` — best-effort git-setup block in `init` after Phase B,
   with the decision-matrix branching and an honest, actionable degradation WARNING
   (resilience fix 2).
3. `openspec/specs/workspace-init/spec.md` — promoted delta: 5 new requirements +
   13 Given/When/Then scenarios (including the mid-setup-error non-fatal scenario).

### Test Coverage
- Unit tests for `init_repo`, `has_git_identity`, `commit_paths`, `_GITIGNORE_TEMPLATE`
  (real temp git repos over the sole `_run()` seam; identity isolated via
  `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`).
- Integration tests for the `init` orchestration covering all 13 spec scenarios
  (fresh dir; existing repo with/without `.gitignore`; git unavailable; identity unset;
  ordering; scoped-add; non-UTF-8 git output; commit-step failure).
- Layering guard test (canonical layer must not import `openkos.vcs`).
- Resilience-correction tests: `UnicodeDecodeError` → `GitError` mapping; actionable
  degradation warning on a commit-step failure.
- Full suite: 1877 passed; ruff (check + format) and mypy clean over the whole repo;
  CI coverage gate (`fail_under=90`) green on PR #151.

## Deviations
- Three pre-existing tests were adapted to the new git-setup behavior (all confirmed
  legitimate by the verify phase, not regression-masking): `test_purge.py`
  (`git add -f -A` to force-add the now-ignored `.openkos/`), `conftest.py` fixtures
  (git-identity isolation to keep the "exactly one commit" invariant deterministic),
  and two `test_preflight_*` tests (narrow stubbing to neutralize the new legitimate
  git subprocess calls; original assertions intact).
- The delta spec was promoted into `openspec/specs/workspace-init/spec.md` during the
  apply phase (verbatim match verified) rather than at archive; this archive only moves
  the change folder.

## Deferred (follow-on changes)
- **Slice 2 `auto-commit-writes`**: auto-commit after every mutating verb's Phase B
  (ingest/forget/relate/merge/unmerge/reconcile), including the confidential-vs-git-history
  exposure decision. Depends on Slice 1.
- **Slice 3 `purge-transactional-cleanup`**: fix #141 (dangling references after
  `--force` purge) and #142 (silent `vectors.db` drop / no reindex prompt). Depends on
  Slice 1.
