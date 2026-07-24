# Proposal: Git Lifecycle — Slice 1 (`init` sets up git)

## Intent

Running openkos commands should be the only thing a user executes. Today `openkos init`
never touches git, so `purge` is unusable out of the box: a fresh workspace fails purge's
rail 3 (`repo_root` returns `None`) until the user manually runs `git init` + a commit.
This resolves issue **#143**: `init` must leave a git-backed workspace ready for the rest
of the lifecycle. Part of the maintainer-approved "Auto" arc (#145, decided — not
relitigated here).

## Scope

### In Scope (Slice 1 — resolves #143)
- `git init` in the workspace **only when** `repo_root(cwd)` is `None` (never hijack a parent repo).
- Scaffold `.gitignore`: an openkos header ignoring the derived stores (`.openkos/` → `fts.db`/`vectors.db`/`graph.db`) followed by the standard toptal windows/linux/macos/python template (OS cruft + Python caches/venv/build). Exact bytes in `gitignore.reference`. Commit canonical files (`openkos.yaml`, `AGENTS.md`, `raw/**`, `bundle/**`). Verified: no template rule ignores openkos canonical content.
- One initial commit of the fresh workspace.
- Graceful non-fatal degradation (WARNING, not failure) when git is unavailable or identity unset; `init` still succeeds with a valid workspace.
- New git primitives (`init_repo`, `commit_all`) in `src/openkos/vcs/git.py`; orchestrated from the `init` CLI command.

### Out of Scope (deferred follow-on changes)
- **Slice 2 `auto-commit-writes`**: auto-commit after every mutating verb's Phase B. Depends on Slice 1.
- **Slice 3 `purge-transactional-cleanup`**: fix #141 (dangling refs after `--force`) and #142 (silent `vectors.db` drop / reindex prompt). Depends on Slice 1.
- Auto-commit opt-out flag, reindex-after-purge behavior, #141 remedy — decided in their slices.
- Confidential-vs-git-history exposure: **moot for Slice 1** (new workspace has no confidential content); deferred to Slice 2 where it becomes real.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `workspace-init`: `init` additionally initializes git (conditional `git init`), scaffolds `.gitignore`, and makes one initial commit, degrading non-fatally when git/identity is unavailable.

## Approach

Extend `src/openkos/vcs/git.py` with two write primitives (`init_repo`, `commit_all`)
alongside the existing probe-only surface, reusing the single `_run()` subprocess site
(sole `# noqa: S603`). The `init` command (`cli/main.py`) calls them **after** Phase B's
last write (`openkos.yaml` marker). Dependency direction stays `cli → vcs`; the canonical
layer (`model`/`bundle`/`state`) never imports `vcs`. Never named `lifecycle.py`
(taken by the status-predicate module).

### Existing-workspace decision matrix

| Repo exists | `.gitignore` exists | Tree | Behavior |
|---|---|---|---|
| No | — | — (new) | `git init`, write `.gitignore`, initial commit of the new workspace |
| Yes | No | any | Skip `git init`; write `.gitignore`; commit only the newly-created openkos files (`git add` the created paths, not `-A`) |
| Yes | Yes | any | Skip `git init`; **do not overwrite** `.gitignore`; commit only newly-created files |

Rationale: `git init` fires only when `repo_root(cwd) is None`; an existing `.gitignore` is
respected (#145 guardrail); commits are scoped to init's own files so unrelated dirty content
in a host repo is never swept in.

### Commit authorship & message
- Rely on the user's existing `git config user.name/email`.
- If identity is unset: **warn and skip the commit** (repo + `.gitignore` still created). Do not inject a fallback bot identity — a surprise author in the user's history is worse than a skipped commit, and `git status` makes recovery trivial.
- Message: `chore(openkos): initialize workspace`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/vcs/git.py` | Modified | Add `init_repo`, `commit_all` write primitives |
| `src/openkos/cli/main.py` (`init`) | Modified | Scaffold `.gitignore`; call git primitives after Phase B |
| `openspec/specs/workspace-init/spec.md` | Modified | New git-setup requirements + scenarios |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `git init` in a parent repo creates nested/hijacked history | Low | Fire only when `repo_root(cwd) is None` (purge rail-3 template) |
| Commit fails (no identity, disk) fails whole `init` | Med | Catch and WARN non-fatally; workspace stays valid |
| Overwriting a user's existing `.gitignore` | Low | Detect and skip if present |
| Sweeping unrelated host-repo files into the commit | Low | Scope `git add` to init-created paths, never `-A` |

## Rollback Plan

Git setup is purely additive and runs **after** all canonical files are written, so a
failed/partial git step never corrupts the workspace. On any git error: emit a non-fatal
WARNING and exit 0 with a valid workspace — the user recovers via `git status` / manual
`git init`. To revert the feature entirely: remove the git-setup call in `init` and the
new primitives; existing workspaces are unaffected (no schema or on-disk format change).

## Dependencies

- `git` binary (already probed via `git_available()`); absence degrades gracefully.

## Success Criteria

- [ ] `openkos init` in an empty dir produces a git repo, a `.gitignore` ignoring `.openkos/` + `.DS_Store`, and one initial commit.
- [ ] `openkos purge` works on a freshly-init'd workspace with no manual git steps (#143 closed).
- [ ] `init` inside an existing repo does not run `git init` and does not overwrite `.gitignore`.
- [ ] Missing git / unset identity yields a WARNING and a still-valid workspace (exit 0).
- [ ] Canonical layer contains no `vcs` import; `uv run pytest` and quality gate pass.
