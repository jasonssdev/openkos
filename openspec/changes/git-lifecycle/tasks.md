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

- [x] 1.1 RED — `tests/unit/vcs/test_git_adapter.py`: `init_repo(cwd)` runs `git init` in a real `tmp_path`, `.git` exists after.
- [x] 1.2 GREEN — implement `init_repo(cwd: Path) -> None` in `src/openkos/vcs/git.py` via `_run(["git","init"], cwd=cwd)`; raise `GitError` on non-zero.
- [x] 1.3 RED — test `init_repo` raises `GitUnavailable` when `_run` raises it (monkeypatch `_run`).
- [x] 1.4 RED — `has_git_identity(cwd)`: returns `True` in a real temp repo with `user.name`/`user.email` set via `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at populated files.
- [x] 1.5 RED — `has_git_identity(cwd)`: returns `False` when `user.name` and/or `user.email` are unset (env-isolated empty config files).
- [x] 1.6 GREEN — implement `has_git_identity(cwd: Path) -> bool` probing `git config user.name` and `git config user.email`, both must resolve non-empty (returncode 0).
- [x] 1.7 RED — `commit_paths(cwd, rel_paths, message)`: stages exactly the passed paths in a fresh repo, one commit with the given message, `git log` shows only those files.
- [x] 1.8 RED — `commit_paths`: in a repo with a pre-existing unrelated untracked/modified file, that file is NOT staged or committed (threat-matrix "Commit state" row).
- [x] 1.9 RED — `commit_paths`: raises `GitError` when `git add`/`git commit` exits non-zero.
- [x] 1.10 GREEN — implement `commit_paths(cwd: Path, rel_paths: Sequence[str], message: str) -> None` in `src/openkos/vcs/git.py`: `git add -- <rel_paths>` then `git commit -m <message>`, both via `_run`, both raise `GitError` on non-zero. No `-A`/`-a`.

## Phase 2: `.gitignore` scaffolding

- [x] 2.1 RED — test constant `_GITIGNORE_TEMPLATE` exists and its content includes `.openkos/`/`.DS_Store`/`__pycache__/`/`.venv` key lines, and matches the reference file verbatim (`test_git_adapter.py`). (Deviation: design's Interfaces/Contracts section supersedes this task's literal `_GITIGNORE_LINES` 2-tuple name with the module-level `_GITIGNORE_TEMPLATE` full-template string constant — implemented per design, not per this task's original wording.)
- [x] 2.2 GREEN — added `_GITIGNORE_TEMPLATE` to `src/openkos/vcs/git.py` (verbatim copy of `openspec/changes/git-lifecycle/gitignore.reference`); the write itself happens inline in the `init` CLI block (Phase 3).

## Phase 3: `init` CLI orchestration (`src/openkos/cli/main.py`)

- [x] 3.1 RED — `tests/unit/cli/test_init.py`: fresh empty dir → after `openkos init`, `.git` exists, `.gitignore` ignores `.openkos/` and `.DS_Store`, one commit with message `chore(openkos): initialize workspace` containing `openkos.yaml`, `AGENTS.md`, `raw/**`, `bundle/**`, `.gitignore`, and `git status` is clean. (Spec: Fresh empty directory outside any repo / Fresh repo, full commit / Gitignore Scaffolding — no existing.)
- [x] 3.2 RED — existing git working tree (parent or same dir), no `.gitignore`: `git init` does NOT run (no nested `.git`), `.gitignore` is written, commit contains only the openkos-created files, pre-existing unrelated dirty content stays untouched. (Spec: Directory already inside a git working tree / Existing repo, scoped commit excludes unrelated content.)
- [x] 3.3 RED — existing git working tree with a pre-existing `.gitignore`: content unchanged byte-for-byte, `git init` does not run, commit excludes the pre-existing `.gitignore`. (Spec: Existing .gitignore is preserved / Existing repo with pre-existing .gitignore, scoped commit.)
- [x] 3.4 RED — `git` unavailable (monkeypatch `vcs.git._run` to simulate `GitUnavailable`): stderr WARNING emitted, exit code 0, all 5 pre-existing workspace artifacts present. (Spec: Git unavailable.)
- [x] 3.5 RED — git identity unset (env-isolated empty config): stderr WARNING, exit 0, repo + `.gitignore` created, no commit made, no fallback bot identity used. (Spec: Git identity unset.)
- [x] 3.6 RED — ordering assertion: `openkos.yaml` is written and readable before the git-setup step runs (via a monkeypatched `init_repo` spy asserting the marker file already exists when called). (Spec: Git step runs after the workspace marker.)
- [x] 3.7 GREEN — implemented the git-setup block in `init` (`src/openkos/cli/main.py`), inserted after the "created workspace"/next-step echoes and before the Ollama preflight, inside one best-effort `try`/`except (vcs_git.GitError, OSError)` (`GitUnavailable` is a `GitError` subclass, so it is caught too):
  1. `repo = vcs_git.repo_root(root)`; `if repo is None: vcs_git.init_repo(root)`.
  2. `if not (root / ".gitignore").exists(): write vcs_git._GITIGNORE_TEMPLATE`.
  3. `git_paths = [openkos.yaml, AGENTS.md, raw, bundle]` plus `.gitignore` only if this run wrote it.
  4. `if vcs_git.has_git_identity(root): vcs_git.commit_paths(root, git_paths, "chore(openkos): initialize workspace")` else emit stderr WARNING and skip commit.
  5. On any `GitError`/`OSError`, emit non-fatal stderr WARNING; never raise `typer.Exit`.
- [x] 3.8 GREEN — verified all Phase 3 RED tests (3.1–3.6) pass against the 3.7 implementation.
  - Deviation/fix: the pre-existing shared `tmp_git_repo`/`tmp_git_repo_with_history_residual` fixtures (`tests/unit/vcs/conftest.py`, used by ~600 tests) called `openkos init` without isolating git identity — Slice 1's own commit step would otherwise fire non-deterministically on a host/CI machine with a real global git identity, breaking those fixtures' "exactly one commit" invariant. Fixed by isolating identity to unset (`isolate_git_identity`, no name/email — writes no file, so it cannot pollute the tree) around the fixtures' `init` calls.
  - Deviation/fix: `tests/unit/cli/test_purge.py::test_purge_deletes_and_rebuilds_index_no_tombstone` used `git add -A` to stage a deliberately-added `.openkos/vectors.db` fixture file; since `init` now writes a `.gitignore` ignoring `.openkos/`, this silently staged nothing. Fixed with `git add -f -A`.
  - Deviation/fix: `test_init.py::test_preflight_never_pulls_or_spawns_a_server` patches `subprocess.run`/`Popen` process-wide to assert the Ollama preflight never spawns a subprocess; the new git-setup step legitimately does spawn `subprocess.run` (real `git`, via `vcs.git._run`), unrelated to that test's concern. Neutralized by stubbing `vcs_git.repo_root`/`has_git_identity` so the git-setup step makes no subprocess call of its own in that one test.
  - Deviation/fix: `test_init.py::test_preflight_outcome_never_changes_written_files` does a full-tree byte-snapshot comparison across 4 workspaces; a real git commit's object SHA is wall-clock-timestamp-dependent, which would make the `.git/` trees byte-different for a reason unrelated to what the test checks. Fixed by isolating git identity to unset for that test (no commit fires; `git init` alone is byte-identical across separate invocations, verified empirically).

## Phase 4: Layering guard

- [x] 4.1 RED — added `tests/unit/vcs/test_layering.py` (new file, following `tests/unit/graph/test_base.py`/`tests/unit/resolution/test_layering.py`'s existing AST-based import-scan convention) asserting `src/openkos/model/`, `src/openkos/bundle/`, `src/openkos/state/` contain no `import openkos.vcs` / `from openkos.vcs import ...` occurrences, plus a positive non-vacuous guard that `cli/main.py` does import `openkos.vcs`. (Spec: Canonical layer stays git-agnostic.) Triangulation skipped: purely structural AST-scan test, single possible correct output, mirrors the existing established pattern verbatim.
- [x] 4.2 GREEN — confirmed the test passes; Slice 1 only touches `vcs/git.py` and `cli/main.py`, no violation.

## Phase 5: Docs + quality gate

- [x] 5.1 Updated `openspec/specs/workspace-init/spec.md` with the new git-setup requirements/scenarios, promoted verbatim from `openspec/changes/git-lifecycle/specs/workspace-init/spec.md`.
- [x] 5.2 Checked `.github/workflows/ci.yml`'s "Init smoke test" step: it only asserts artifact existence (`test -d raw`, `test -f ...`), never stdout/stderr content — the WARN-and-skip paths (no git identity on the runner) keep exit 0 there. No change needed.
- [x] 5.3 Full quality gate green: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` — all clean.
- [x] 5.4 `uv run pytest` — full suite green: 1874 passed (1870 pre-existing/adjusted + 4 new layering tests; 67 vcs-adapter tests include 17 new Slice-1 primitive tests; 49 CLI init tests include 6 new git-setup tests).

## Phase 6: Bounded correction — resilience review (2 WARNING-level fixes)

- [x] 6.1 RED — `tests/unit/vcs/test_git_adapter.py::test_run_maps_unicode_decode_error_to_git_error`: monkeypatch `subprocess.run` to raise `UnicodeDecodeError`, assert `vcs.git._run` raises `GitError`, not the raw `UnicodeDecodeError` (confirmed failing before the fix). GREEN — `_run` now catches `UnicodeDecodeError` and maps it to `GitError`, alongside the existing `FileNotFoundError`→`GitUnavailable` and `OSError`→`GitError` mappings, so non-UTF-8 git output can never escape the adapter's typed-error contract.
- [x] 6.2 RED — `tests/unit/cli/test_init.py::test_git_unicode_decode_error_warns_and_exits_zero`: monkeypatch `subprocess.run` to raise `UnicodeDecodeError` during `init`'s git-setup step, assert `openkos init` still exits 0 with a stderr WARNING and a complete, valid workspace (confirmed failing before the fix — a `1` exit code with the raw exception). GREEN — with 6.1's `_run` fix, the error is now caught by `init`'s existing `except (vcs_git.GitError, OSError)` handler.
- [x] 6.3 RED — `tests/unit/cli/test_init.py::test_git_commit_failure_warns_actionably_not_misleadingly_skipped`: monkeypatch `commit_paths` to raise `GitError` after the repo/`.gitignore`/staged files already exist, assert exit 0 AND the stderr warning contains `git status` (a concrete recovery hint) AND does not contain the misleading word "skipped" (confirmed failing before the fix — the pre-existing message read `"git setup skipped (...)"`. GREEN — reworded `init`'s degradation warning in `src/openkos/cli/main.py` to `"git setup did not complete cleanly (...). The workspace itself is still valid; run \`git status\` in it to inspect and finish git setup manually if needed."` — honest for every failure mode (a repo/gitignore/staged files may already exist) and actionable, while the identity-unset branch's separate, legitimately-accurate "skipped the initial commit" message is unchanged.
- [x] 6.4 Updated `openspec/changes/git-lifecycle/specs/workspace-init/spec.md` (delta) and `openspec/specs/workspace-init/spec.md` (promoted, mirrored) — "Non-Fatal Git Degradation" requirement now covers ANY git-setup failure (not just unavailable/identity-unset), with a new "Git error mid-setup leaves a partial but honestly-reported state" scenario.
- [x] 6.5 Full quality gate green after correction: `uv run pytest` (1877 passed), `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` — all clean.
