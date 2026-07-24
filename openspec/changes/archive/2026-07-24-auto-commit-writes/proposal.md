# Proposal: Auto-Commit Writes — Git Lifecycle Slice 2

## Intent

North star (#145, maintainer-decided "Auto" model — not relitigated): a user runs only
`openkos` commands, the working tree stays clean, and full history accrues without them ever
touching git. Slice 1 (#151, #143 closed) made `init` leave a git-backed workspace. Today
every OTHER mutating verb writes canonical files and leaves them uncommitted, so the tree
drifts dirty and `purge`'s clean-tree rail can only pass by hand-committing. Slice 2 closes
that gap: auto-commit after every mutating verb's Phase B, reusing Slice 1's primitives.

## Scope

### In Scope
- Auto-commit after Phase B for `ingest`, `forget`, `relate`, `merge`, `unmerge`, `reconcile`.
- One shared CLI helper `_autocommit(root, paths, message)` in `cli/main.py`, mirroring the
  `init` git-setup block: best-effort, non-fatal, scoped `git add -- <paths>` (never `-A`).
- Reuse Slice 1's `repo_root`, `has_git_identity`, `commit_paths`. Commit ONLY when
  `repo_root(root) is not None` AND identity set; else WARN non-fatally, verb still succeeds.
- Per-verb structured commit messages (table below).
- One-time stderr transparency NOTICE when a commit includes confidential content (decision 1).

### Out of Scope (deferred)
- `purge` — its irreversible history-rewrite Phase B + #141/#142 fixes → **Slice 3
  `purge-transactional-cleanup`**.
- `reindex` (derived-only, `.openkos/*.db` gitignored — never commit), `init` (Slice 1 commits),
  read-only verbs.
- `autocommit: false` opt-out flag — noted as a possible FUTURE escape hatch, not now.

## Capabilities

### New Capabilities
- `workspace-autocommit`: after each mutating verb's Phase B, openkos commits the exact paths
  it wrote (scoped `git add`), degrading non-fatally when git/identity is unavailable, and
  emits a one-time NOTICE when the commit includes confidential content.

### Modified Capabilities
- None. (Per-verb specs — ingestion, forget-command, typed-relationships, entity-resolution-merge,
  reconcile-command — reference the shared capability; no per-verb requirement changes.)

## Approach

Add ONE helper `_autocommit(root, paths, message)` at the CLI orchestration layer, called by
each verb after its LAST Phase-B write, strictly on the success path — AFTER the `--auto`/`review`
confirm gate and after Phase B lands. Dependency direction stays `cli → vcs`; the canonical
layer never imports `vcs`. The helper mirrors `init`'s block exactly: skip (WARN) when not a
repo or identity unset; catch `GitError`/`OSError` → non-fatal stderr WARNING (`_run`'s
`UnicodeDecodeError→GitError` hardening already covers non-UTF-8 git output).

### Per-verb commit messages & staged paths

| Verb | Commit message | Staged paths (+ `index.md`, `log.md`) |
|---|---|---|
| `ingest` | `openkos: ingest <source> (+N concepts)` | new/updated `bundle/**` concept files, `raw/**` source copy |
| `forget` | `openkos: forget <id>` | removed concept file |
| `relate` | `openkos: relate <src> -> <dst> (<type>)` | edited concept file(s) |
| `merge` | `openkos: merge <src> into <dst>` | source + target concept files |
| `unmerge` | `openkos: unmerge <id>` | restored/edited concept files |
| `reconcile` | `openkos: reconcile (<summary>)` | reconciled `bundle/**` files |

Each verb passes its own Phase-B-written rel_paths PLUS `bundle/index.md` and `bundle/log.md`
to `_autocommit`; sdd-design/tasks pins the exact per-verb path set. Never `-A`.

### Resolved maintainer decisions
1. **Confidential content — COMMIT everything, NOTICE once.** All content (incl.
   `sensitivity: confidential`) is committed. Rationale: local git only, NEVER a remote
   (hard non-negotiable); content is already on disk regardless of sensitivity, so local git is
   no worse, and it preserves the always-clean-tree invariant `purge` needs. **Detection**:
   before committing, inspect the staged concept files' `sensitivity` frontmatter via
   `sensitivity.blocks_llm_send(value, threshold="confidential")`; if any staged path ranks
   confidential, emit ONE stderr NOTICE. **Scope of "one-time": per command invocation** —
   at most one NOTICE per verb run that stages confidential content (recommended: simplest, no
   persisted state). A per-workspace-once notice would require persisting a flag in
   `openkos.yaml` — deferred with the opt-out.
2. **Unconditional — NO opt-out flag.** Auto-commit is unconditional (matches "Auto" over
   manual/opt-in). `autocommit: false` noted as a FUTURE escape hatch, out of scope now.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | Modified | Add `_autocommit` helper; call from 6 verbs after Phase B |
| `openspec/specs/workspace-autocommit/spec.md` | New | Shared auto-commit capability + scenarios |
| `src/openkos/vcs/git.py` | Reused (no change expected) | `commit_paths`/`has_git_identity`/`repo_root` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Commit failure fails the verb (regression) | Med | Catch → non-fatal WARNING; verb keeps its success exit code |
| Sweeping unrelated host-repo dirt into a commit | Low | Scoped `git add -- <paths>`, never `-A` |
| Confidential content lands in git history surprisingly | Low | One-time NOTICE; local-only non-negotiable |
| 6 symmetric call sites exceed 800-line review budget | Med | sdd-tasks forecasts; chain per-verb PRs if needed |

## Rollback Plan

Auto-commit is purely additive and runs AFTER every canonical write, so a failed/partial commit
never corrupts the workspace — the verb's writes already landed on disk and are recoverable via
`git status`. On any git error: non-fatal stderr WARNING, verb exits its normal success code.
To revert the feature: remove `_autocommit` and its 6 call sites; existing workspaces are
unaffected (no schema or on-disk format change).

## Dependencies

- Slice 1 (#151) shipped: `commit_paths`, `has_git_identity`, `repo_root`, `_run`.
- `git` binary (probed via `git_available()`); absence degrades gracefully.

## Non-negotiables (AGENTS.md / #145)

- Local git ONLY — auto-commit NEVER pushes to any remote.
- Everything reconstructible from canonical files (markdown + git); derived `.openkos/*.db` stay
  gitignored and are never committed.

## Success Criteria

- [ ] Each of `ingest`, `forget`, `relate`, `merge`, `unmerge`, `reconcile` leaves a clean tree
      via one scoped commit with the per-verb message.
- [ ] Commit failure / not-a-repo / unset identity → WARNING, verb still exits success.
- [ ] Commit fires only on the success path, after the confirm gate and Phase B.
- [ ] Committing confidential content emits exactly one stderr NOTICE per invocation.
- [ ] No `-A` staging anywhere; canonical layer imports no `vcs`; `uv run pytest` + quality gate pass.
