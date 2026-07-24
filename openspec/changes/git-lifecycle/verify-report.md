# Verify Report: git-lifecycle (Slice 1)

**Change**: git-lifecycle | **Branch**: feat/git-lifecycle-init | **Commits**: ac12747 (planning), 74ad02f (implementation)
**Verdict**: PASS WITH WARNINGS

## 1. Quality Gate (ground-truth, run independently)

| Check | Result |
|---|---|
| `uv run pytest` | 1874 passed, 0 failed (78.13s) |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 131 files already formatted |
| `uv run mypy .` | Success: no issues found in 131 source files |

All four commands match the apply-progress claims exactly.

## 2. Coverage Gate — investigated in depth (flagged risk)

**Local reproduction**: `uv run pytest --cov` in `/Users/jasonssdev/Dev/Projects/openkos` reproduces "No data was collected" / 0.00% / gate FAILS, confirming the apply-phase report.

**Root-cause isolation** (git worktrees used to control for branch vs. directory):
- Checked out base commit `7c41971` (pre-Slice-1) in a **fresh worktree** at a different path, ran `uv sync --locked` + `uv run pytest --cov`: coverage worked correctly — 97.71% total, gate PASSES.
- Checked out the **feature-branch tip `74ad02f`** (same code as the PR) in a second **fresh worktree**, ran `uv sync --locked` + `uv run pytest --cov`: coverage worked correctly — **97.73% total, 1874 passed, gate PASSES.**
- Bisected within `/Users/jasonssdev/Dev/Projects/openkos` itself: even a single, pre-existing, unrelated test (`test_repo_root_matches_workspace`) reproduces "No data collected" when run from that specific directory, on any commit. Ruled out: shell env pollution (`env -i` clean run still fails), stale `__pycache__` bytecode, `COVERAGE_CORE=ctrace` override, symlinked paths, `uv.lock`/installed-package drift (verified identical package sets and identical `tracer.cpython-313-darwin.so` MD5 between the failing directory and the working worktree).

**Conclusion**: the coverage-collection failure is **not caused by the git-lifecycle change** and is **not a general project/base-branch defect** either — the exact same code (both `7c41971` and `74ad02f`) collects coverage correctly in a clean checkout. It is an artifact specific to this one local working directory/session (most likely tied to this session's sandboxed command-execution wrapper around the recognized project root, which appears to suppress Python's trace hooks only when the cwd is that specific directory). This will not reproduce in CI, which always runs from a fresh checkout.

**Ground-truth coverage numbers for the new code** (from the clean feature-branch worktree run, real data, not asserted):
- Full suite: **97.73%** total (well above the 90% `fail_under` gate) — CI would pass this.
- `src/openkos/vcs/git.py`: 131 stmts, 2 missed, 64 branches, 3 partial → **97%**. The 2 missed lines (388, 415) and the 1 partial branch (787→789) are all in **pre-existing** functions (`is_clean`, `has_published_commits`, `expunge_paths` cleanup) — none in the new Slice-1 primitives.
- The three new primitives (`init_repo`, `has_git_identity`, `commit_paths`, `_GITIGNORE_TEMPLATE`): **0 missed lines** in the coverage report — fully covered.
- `src/openkos/cli/main.py` git-setup block (lines ~190–232): **0 missed lines** — fully covered. (The file's 70 total misses are all pre-existing, elsewhere in the 1507-line file.)

**Verdict for this check**: PASS on the merits (new code is fully covered, CI's `fail_under=90` will be satisfied), but flagged WARNING because the coverage gate is **currently unverifiable from this local machine/session** — the apply phase's "pre-existing environment issue" framing is directionally correct but imprecise (it is a directory/session artifact, not a "pre-existing" property of the base branch's code). Recommend the next `sdd-apply`/maintainer re-run `uv run pytest --cov` from a clean checkout (or rely on CI) before merge, rather than trusting either this session's or the apply phase's local run.

## 3. Spec-Scenario Conformance (12/12 scenarios covered by a passing test)

| # | Scenario | Covering test |
|---|---|---|
| 1 | Fresh empty directory outside any repo | `test_git_fresh_empty_directory_full_commit` |
| 2 | Directory already inside a git working tree | `test_git_existing_repo_no_gitignore_scoped_commit` |
| 3 | No existing .gitignore | `test_git_fresh_empty_directory_full_commit` |
| 4 | Existing .gitignore is preserved | `test_git_existing_repo_with_gitignore_preserved` |
| 5 | Fresh repo, full commit | `test_git_fresh_empty_directory_full_commit` |
| 6 | Existing repo, scoped commit excludes unrelated content | `test_git_existing_repo_no_gitignore_scoped_commit` |
| 7 | Existing repo with pre-existing .gitignore, scoped commit | `test_git_existing_repo_with_gitignore_preserved` |
| 8 | Git unavailable | `test_git_unavailable_warns_and_exits_zero` |
| 9 | Git identity unset | `test_git_identity_unset_warns_no_commit_exits_zero` |
| 10 | Git step runs after the workspace marker | `test_git_setup_runs_after_workspace_marker_exists` |
| 11 | Canonical layer stays git-agnostic | `test_canonical_layer_does_not_import_vcs` (parametrized model/bundle/state) + `test_cli_main_imports_vcs_git` (non-vacuous positive guard) |
| 12 | (ordering primitive coverage: `init_repo`/`has_git_identity`/`commit_paths` unit-level RED cases) | `tests/unit/vcs/test_git_adapter.py` (17 new primitive tests) |

All 12 scenarios have a directly-mapped, passing runtime test. No uncovered scenario.

## 4. Deviation Scrutiny — modified pre-existing tests (all judged LEGITIMATE)

Reviewed via `git show 74ad02f -- tests/`.

- **`tests/unit/cli/test_purge.py`**: `git add -A` → `git add -f -A`. Confirmed this is solely because `init`'s new `.gitignore` now legitimately ignores `.openkos/`, and this test's fixture deliberately stages a "stale committed vectors.db" under `.openkos/` to reproduce its own scenario. The assertion under test (purge deletes/rebuilds the index) is unchanged; only the fixture setup needed `-f` to force-add an ignored path. Not a regression.
- **`tests/unit/vcs/conftest.py`**: `isolate_git_identity` helper added; `tmp_git_repo`/`tmp_git_repo_with_history_residual` fixtures now isolate identity to unset around their internal `openkos init` call. Confirmed necessary: without isolation, Slice 1's own best-effort commit step would fire using the host's real git identity, non-deterministically breaking these fixtures' "exactly one commit" invariant (used by ~600 downstream tests). Does not hide any behavior — it deterministically forces the *no-commit* branch of the new git-setup step so the fixture's own explicit, pinned-identity commit remains the sole commit.
- **`test_preflight_never_pulls_or_spawns_a_server`**: stubs `vcs_git.repo_root`/`has_git_identity` so the new (legitimate) git-setup subprocess calls never fire in this specific test, whose actual concern is a blanket ban on the Ollama preflight spawning subprocesses. The forbidden-subprocess assertion (`subprocess.run`/`Popen` raise if called) remains fully armed and the test still asserts `exit_code == 0`. Intent preserved.
- **`test_preflight_outcome_never_changes_written_files`**: isolates identity to unset so no commit fires across the 4 snapshotted workspaces (a commit's object SHA is wall-clock-dependent, which would make byte-snapshot comparison flaky for a reason unrelated to what the test checks). `git init` alone (no commit) is verified byte-identical across invocations. Test still performs its full snapshot-equality assertion across all 4 outcomes.

No deviation weakens an assertion; all are narrowly scoped, well-documented adaptations to the new git-setup step's legitimate side effects.

## 5. Design & Layering Conformance

- **Primitive signatures** match `design.md`'s Interfaces/Contracts exactly: `init_repo(cwd: Path) -> None`, `has_git_identity(cwd: Path) -> bool`, `commit_paths(cwd: Path, rel_paths: Sequence[str], message: str) -> None` (verified via `grep` against `src/openkos/vcs/git.py`).
- **Subprocess seam discipline**: confirmed only one `# noqa: S603` in the entire `src/` tree, on the sole `subprocess.run` call inside `_run()`; the three new primitives call only `_run()`, no new subprocess sites.
- **`commit_paths` scoping**: confirmed no `-A`/`-a` flag anywhere in the function; stages via `git add -- <rel_paths>` exactly as designed.
- **Layering guard**: `tests/unit/vcs/test_layering.py` (AST-based import scan) passed at runtime — `model`/`bundle`/`state` do not import `openkos.vcs`; a non-vacuous positive check confirms `cli/main.py` does.
- **`.gitignore` fidelity**: `test_gitignore_template_matches_reference_file_verbatim` asserts byte-for-byte equality between the `_GITIGNORE_TEMPLATE` constant and `openspec/changes/git-lifecycle/gitignore.reference`, and passed at runtime.

## 6. Early Spec Promotion — recommendation

The apply phase promoted the delta spec into `openspec/specs/workspace-init/spec.md` (normally an archive-phase action). Diffed the promoted section against the delta spec content: verbatim match, no drift or paraphrase. This is a harmless early merge — content-identical, does not pre-empt or conflict with anything archive would otherwise do, and reduces archive-phase risk of forgetting to promote. **Recommendation**: acceptable as-is; no revert needed. (Only note for process hygiene: future slices should let archive do this by default unless there's a reason for early promotion, to keep phase responsibilities crisp — but this instance caused no harm.)

## Tasks vs. Code State

All 26 tasks in `openspec/changes/git-lifecycle/tasks.md` are marked `[x]` and every claim in the apply-progress record (Engram id 1815) was independently verified against real test/lint/type-check runs and source inspection — no discrepancy found except the coverage-gate framing noted in section 2.

## Issues

- **CRITICAL**: None.
- **WARNING**: Local coverage-gate verification is currently unreliable from this machine/session (directory-specific artifact, not code-related — see section 2). Recommend confirming via CI or a clean checkout before merge, rather than relying on any local `--cov` run in this particular working directory.
- **SUGGESTION**: None blocking.

## Final Verdict

**PASS WITH WARNINGS** — implementation matches spec, design, and tasks; full quality gate is green; all 12 spec scenarios have passing covering tests; all test deviations are legitimate; new code is fully covered per ground-truth (clean-worktree) coverage data. The only open item is a local-environment coverage-tooling quirk unrelated to this change, which does not block merge but should be confirmed once via CI.
