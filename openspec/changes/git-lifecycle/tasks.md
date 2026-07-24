# Tasks: Git Lifecycle — Slice 1 (`init` sets up git)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~260-340 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | single PR |
| Delivery strategy | auto-forecast |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `vcs/git.py` primitives + `init` orchestration + `.gitignore` scaffolding, tested end-to-end against the 12 spec scenarios | PR 1 | `uv run pytest tests/unit/vcs/test_git_adapter.py tests/unit/cli/test_init.py` | `openkos init` in a real `tmp_path` (fresh dir, existing repo, no-git PATH) | Revert the `init` git-setup block + 3 new primitives in `git.py`; no schema/on-disk format change |

## Phase 1: `vcs/git.py` primitives (RED → GREEN)

- [ ] 1.1 RED — `tests/unit/vcs/test_git_adapter.py`: `init_repo(cwd)` runs `git init` in a real `tmp_path`, `.git` exists after.
- [ ] 1.2 GREEN — implement `init_repo(cwd: Path) -> None` in `src/openkos/vcs/git.py` via `_run(["git","init"], cwd=cwd)`; raise `GitError` on non-zero.
- [ ] 1.3 RED — test `init_repo` raises `GitUnavailable` when `_run` raises it (monkeypatch `_run`).
- [ ] 1.4 RED — `has_git_identity(cwd)`: returns `True` in a real temp repo with `user.name`/`user.email` set via `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at populated files.
- [ ] 1.5 RED — `has_git_identity(cwd)`: returns `False` when `user.name` and/or `user.email` are unset (env-isolated empty config files).
- [ ] 1.6 GREEN — implement `has_git_identity(cwd: Path) -> bool` probing `git config user.name` and `git config user.email`, both must resolve non-empty (returncode 0).
- [ ] 1.7 RED — `commit_paths(cwd, rel_paths, message)`: stages exactly the passed paths in a fresh repo, one commit with the given message, `git log` shows only those files.
- [ ] 1.8 RED — `commit_paths`: in a repo with a pre-existing unrelated untracked/modified file, that file is NOT staged or committed (threat-matrix "Commit state" row).
- [ ] 1.9 RED — `commit_paths`: raises `GitError` when `git add`/`git commit` exits non-zero.
- [ ] 1.10 GREEN — implement `commit_paths(cwd: Path, rel_paths: Sequence[str], message: str) -> None` in `src/openkos/vcs/git.py`: `git add -- <rel_paths>` then `git commit -m <message>`, both via `_run`, both raise `GitError` on non-zero. No `-A`/`-a`.

## Phase 2: `.gitignore` scaffolding

- [ ] 2.1 RED — test constant `_GITIGNORE_LINES = (".openkos/", ".DS_Store")` exists and is used to produce a 2-line `.gitignore` (add to `test_git_adapter.py` or a small helper test if the write helper lives in `git.py`).
- [ ] 2.2 GREEN — add `_GITIGNORE_LINES` to `src/openkos/vcs/git.py`; the write itself happens inline in the `init` CLI block (Phase 3) per design, unless a helper is more testable — keep it in `git.py` if introduced.

## Phase 3: `init` CLI orchestration (`src/openkos/cli/main.py`)

- [ ] 3.1 RED — `tests/unit/cli/test_init.py`: fresh empty dir → after `openkos init`, `.git` exists, `.gitignore` has 2 lines ignoring `.openkos/` and `.DS_Store`, one commit with message `chore(openkos): initialize workspace` containing `openkos.yaml`, `AGENTS.md`, `raw/**`, `bundle/**`, `.gitignore`, and `git status` is clean. (Spec: Fresh empty directory outside any repo / Fresh repo, full commit / Gitignore Scaffolding — no existing.)
- [ ] 3.2 RED — existing git working tree (parent or same dir), no `.gitignore`: `git init` does NOT run (no nested `.git`), `.gitignore` is written, commit contains only the openkos-created files, pre-existing unrelated dirty content stays untouched. (Spec: Directory already inside a git working tree / Existing repo, scoped commit excludes unrelated content.)
- [ ] 3.3 RED — existing git working tree with a pre-existing `.gitignore`: content unchanged byte-for-byte, `git init` does not run, commit excludes the pre-existing `.gitignore`. (Spec: Existing .gitignore is preserved / Existing repo with pre-existing .gitignore, scoped commit.)
- [ ] 3.4 RED — `git` unavailable (monkeypatch `vcs.git.git_available`/`_run` to simulate `GitUnavailable`): stderr WARNING emitted, exit code 0, all 5 pre-existing workspace artifacts present. (Spec: Git unavailable.)
- [ ] 3.5 RED — git identity unset (env-isolated empty config): stderr WARNING, exit 0, repo + `.gitignore` created, no commit made, no fallback bot identity used. (Spec: Git identity unset.)
- [ ] 3.6 RED — ordering assertion: `openkos.yaml` is written and readable before the git-setup step runs (e.g. via a monkeypatched `init_repo`/`commit_paths` spy asserting the marker file already exists when called). (Spec: Git step runs after the workspace marker.)
- [ ] 3.7 GREEN — implement the git-setup block in `init` (`src/openkos/cli/main.py`), inserted after the "created workspace" echo and before the Ollama preflight, inside one best-effort `try`/`except (GitError, GitUnavailable, OSError)`:
  1. `repo = vcs.git.repo_root(root)`; `if repo is None: vcs.git.init_repo(root)`.
  2. `if not (root / ".gitignore").exists(): write vcs.git._GITIGNORE_LINES`.
  3. `paths = [openkos.yaml, AGENTS.md, raw, bundle, .gitignore]` (only include `.gitignore` if this run wrote it, per spec's "plus any `.gitignore` written by this run").
  4. `if vcs.git.has_git_identity(root): vcs.git.commit_paths(root, paths, "chore(openkos): initialize workspace")` else emit stderr WARNING and skip commit.
  5. On any `GitError`/`GitUnavailable`/`OSError`, emit non-fatal stderr WARNING; do not raise `typer.Exit`.
- [ ] 3.8 GREEN — verify all Phase 3 RED tests (3.1–3.6) pass against the 3.7 implementation; fix gaps.

## Phase 4: Layering guard

- [ ] 4.1 RED — add/extend a layering test (e.g. `tests/unit/test_layering.py` or existing equivalent) asserting `src/openkos/model/`, `src/openkos/bundle/`, `src/openkos/state/` contain no `import openkos.vcs` / `from openkos.vcs` occurrences. (Spec: Canonical layer stays git-agnostic.) If no such test file/pattern exists yet in the repo, create it following the project's existing import-scan test conventions.
- [ ] 4.2 GREEN — confirm the test passes (Slice 1 only touches `vcs/git.py` and `cli/main.py`, so no violation expected).

## Phase 5: Docs + quality gate

- [ ] 5.1 Update `openspec/specs/workspace-init/spec.md` with the new git-setup requirements/scenarios per design's File Changes table (promote the delta from `openspec/changes/git-lifecycle/specs/workspace-init/spec.md`).
- [ ] 5.2 Check `.github/workflows/ci.yml`'s wheel-smoke-test step that runs `openkos init`; confirm it still exits 0 under a runner with no git identity (WARN-and-skip path, Phase 3.5) — adjust the step only if it currently asserts stdout/stderr content that would change.
- [ ] 5.3 Run full quality gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — fix any violations.
- [ ] 5.4 Run `uv run pytest` — full suite green.
