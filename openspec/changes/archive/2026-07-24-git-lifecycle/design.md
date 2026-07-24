# Design: Git Lifecycle — Slice 1 (`init` sets up git)

## Technical Approach

Extend `src/openkos/vcs/git.py` with three write/probe primitives reusing the sole
`_run()` subprocess seam, then orchestrate them from the `init` CLI verb after Phase B's
last write (`openkos.yaml`). Git setup is a best-effort, non-fatal post-workspace step —
mirroring the existing Ollama preflight — so `init` always exits 0 with a valid workspace.
Dependency direction stays `cli → vcs`; the canonical layer (`model`/`bundle`/`state`) is
untouched. Maps to proposal scope (#143) and the existing-workspace decision matrix.

## Architecture Decisions

### Decision: No ADR
**Choice**: Do not create an ADR. **Alternatives**: record an ADR for "openkos owns git".
**Rationale**: The Auto model was already decided (exploration, maintainer sign-off) and is
not this design's call. Slice 1 is additive and trivially reversible (delete the `init`
call + the two primitives; no schema or on-disk format change). ADR gate requires BOTH a
hard-to-reverse decision AND a technology/interface tradeoff — neither holds. Confirms the
proposal's NO-ADR judgment.

### Decision: `commit_paths` (rename `commit_all`), scoped `git add -- <paths>`
**Choice**: `commit_paths(cwd, rel_paths, message)` stages exactly the passed paths via
`git add -- <p1> <p2> …` then `git commit -m <message>`. **Alternatives**: `git add -A` /
`git commit -a`. **Rationale**: In an existing host repo, `-A`/`-a` would sweep unrelated
dirty content into openkos's commit (risk row 4). The `--` end-of-options guard keeps a
leading-dash path from parsing as a flag. Paths are init-controlled constants
(`openkos.yaml`, `AGENTS.md`, `raw`, `bundle`, `.gitignore`), not user data, so fixed-argv
(not `--paths-from-file`) is acceptable and simpler. The name `commit_all` is misleading
and is dropped.

### Decision: WARN-and-skip on unset identity (probe, don't inject)
**Choice**: `has_git_identity(cwd)` probes `git config user.name` AND `user.email`; if
either is unset/empty, skip the commit with a stderr WARNING (repo + `.gitignore` stay).
**Alternatives**: inject a fallback bot identity. **Rationale**: A surprise author in the
user's history is worse than a skipped commit; `git status` makes recovery trivial.

## Interfaces / Contracts

```python
def init_repo(cwd: Path) -> None: ...
    # runs ["git", "init"]; raises GitError on non-zero, GitUnavailable if git absent.
def has_git_identity(cwd: Path) -> bool: ...
    # True iff both user.name and user.email resolve non-empty (returncode 0).
def commit_paths(cwd: Path, rel_paths: Sequence[str], message: str) -> None: ...
    # git add -- <rel_paths>; git commit -m <message>; GitError on failure.
```

Error convention matches existing probes: `GitUnavailable`/`GitError`/`GitFinalizeError`
via `_run`. `_GITIGNORE_TEMPLATE` is a module-level string constant: an openkos header
ignoring `.openkos/` (derived stores), followed verbatim by the standard toptal
windows/linux/macos/python template (which already includes `.DS_Store`, `Thumbs.db`,
`__pycache__/`, `.venv/`, caches, etc.). The exact bytes to emit are the source-of-truth
reference `openspec/changes/git-lifecycle/gitignore.reference` — apply copies that content
into the code constant verbatim. Verified safe: none of the template's broad ignores
(`build/`, `var/`, `lib/`, `dist/`, `db.sqlite3`, `*.log`) collide with openkos canonical
paths (`bundle/**`, `raw/**`, `openkos.yaml`, `AGENTS.md`; the bundle uses `log.md`, not
`.log`), so no canonical content is ever ignored.

## Data Flow / Orchestration Sequence

Slotted after the created-workspace echo, before the Ollama preflight, inside one
best-effort try/except:

```
1. repo = repo_root(root)                     # None ⇒ not inside any repo
2. if repo is None: init_repo(root)           # git init only when repo_root is None
3. if not .gitignore exists: write _GITIGNORE_TEMPLATE  # respect existing, never overwrite
4. paths = [openkos.yaml, AGENTS.md, raw, bundle, .gitignore]
5. if has_git_identity(root): commit_paths(root, paths, "chore(openkos): initialize workspace")
   else: WARN "git identity unset; skipped initial commit" (stderr)
6. except (GitError, GitUnavailable, OSError): WARN non-fatal; exit stays 0
```

Control-flow branches map 1:1 to the proposal's decision matrix (repo exists? / gitignore
exists? / identity set?).

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/vcs/git.py` | Modify | Add `init_repo`, `has_git_identity`, `commit_paths`, `_GITIGNORE_TEMPLATE` |
| `src/openkos/cli/main.py` (`init`) | Modify | Best-effort git-setup block after Phase B, before Ollama preflight |
| `openspec/specs/workspace-init/spec.md` | Modify | New git-setup requirements + scenarios |

## Testing Strategy (Strict TDD)

Prefer **real temp git repos** (`tmp_path`) over mocking — git is already a probed CI
dependency and `_run` is the only subprocess site, so real repos are deterministic and
exercise true git semantics. Isolate identity via env (`GIT_CONFIG_GLOBAL`/`_SYSTEM` →
empty files) so "unset" is real. Simulate missing git by monkeypatching `_run` to raise
`GitUnavailable`.

| Layer | What | Approach |
|-------|------|----------|
| Unit | `init_repo`, `has_git_identity`, `commit_paths` | Real temp repo; assert repo/commit/scoping |
| Integration | `init` orchestration | Run verb in tmp_path; assert exit 0 + side effects |

RED cases mirror spec scenarios: (1) fresh dir → repo + `.gitignore` (asserts it ignores
`.openkos/` and `.DS_Store`; do NOT byte-lock the whole template in the assertion — check
key lines) + one commit, clean tree; (2) existing repo, no gitignore → no re-init, gitignore
written, only openkos
files committed; (3) existing repo + gitignore → existing content preserved; (4) missing
git → WARNING, exit 0; (5) unset identity → repo + gitignore, commit skipped, WARNING, exit
0; (6) ordering → commit contains `openkos.yaml` marker; (7) scoped-add → a pre-existing
unrelated dirty file is NOT staged/committed. The CI build job's wheel smoke-test runs
`openkos init`; the WARN-and-skip paths (4)(5) keep exit 0 there even if the runner has no
git identity.

## Threat Matrix

| Boundary | Applicability | Design response | RED test |
|---|---|---|---|
| Documentation-like paths | N/A — no file classification/execution | — | — |
| Git repository selection | Applicable | `git init` only when `repo_root(cwd) is None`; all ops via `_run(cwd=…)`, no `git -C`, no `..` | init inside existing parent repo does not re-init/nest |
| Commit state | Applicable | Scoped `git add -- <paths>`, never `-A`/`-a`; nothing-to-commit tolerated | unrelated dirty file not swept in |
| Push state | N/A — Slice 1 never pushes | — | — |
| PR commands | N/A — no PR automation | — | — |

## Migration / Rollout

No migration. Purely additive; existing workspaces unaffected. Rollback: remove the `init`
git-setup call and the new primitives (proposal rollback plan).

## Open Questions

- None blocking. Confidential-vs-git-history exposure is moot for a fresh workspace and
  deferred to Slice 2.
