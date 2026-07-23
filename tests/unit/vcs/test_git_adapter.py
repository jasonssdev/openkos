"""Unit tests for `openkos.vcs.git`: the sole `subprocess` adapter over
`git`/`git-filter-repo`. Most tests shell out to a REAL git repository via
the `tmp_git_repo` fixture (`tests/unit/vcs/conftest.py`) rather than
mocking `subprocess` -- the adapter's whole job is to invoke real `git`
correctly, so its tests must prove that against the real binary."""

import shutil
import subprocess
from pathlib import Path

import pytest

from openkos.vcs import git
from tests.unit.vcs.conftest import TmpGitRepo, _git

# --- availability probes ----------------------------------------------------


def test_git_available_true_when_git_on_path() -> None:
    assert git.git_available() is True


def test_git_available_false_when_git_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert git.git_available() is False


def test_filter_repo_available_true_when_installed() -> None:
    assert git.filter_repo_available() is True


def test_filter_repo_available_false_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_which(name: str) -> str | None:
        return None if name == "git-filter-repo" else "/usr/bin/git"

    monkeypatch.setattr(shutil, "which", _fake_which)
    assert git.filter_repo_available() is False


# --- expunge_paths argv shape (spec req 3 / threat matrix: argv safety) ----


def test_expunge_paths_argv_shape(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`expunge_paths` invokes the EXACT fixed argv
    `["git","filter-repo","--force","--invert-paths","--paths-from-file",<tmp>]`,
    with the target paths written as `literal:<path>` lines in the temp
    file -- never interpolated into argv itself, so a path with shell
    metacharacters or a leading `-` cannot be re-parsed as a flag/token."""
    captured: dict[str, object] = {}
    real_run = git._run

    def _spy_run(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["git", "filter-repo"]:
            captured["argv"] = list(argv)
            # Read the paths file NOW, before `expunge_paths`'s `finally`
            # deletes it once this call returns.
            captured["paths_file_contents"] = Path(argv[5]).read_text(encoding="utf-8")
        return real_run(argv, cwd, env)

    monkeypatch.setattr(git, "_run", _spy_run)

    weird_path = "-weird; rm -rf --no-preserve-root $(echo x).txt"
    git.expunge_paths(tmp_git_repo.root, [weird_path])

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "git"
    assert argv[1] == "filter-repo"
    assert argv[2] == "--force"
    assert argv[3] == "--invert-paths"
    assert argv[4] == "--paths-from-file"
    assert captured["paths_file_contents"] == f"literal:{weird_path}\n"
    assert weird_path not in argv  # never interpolated into argv itself


# --- repo_root (threat matrix: git repository selection) -------------------


def test_repo_root_matches_workspace(tmp_git_repo: TmpGitRepo) -> None:
    root = git.repo_root(tmp_git_repo.root)
    assert root == tmp_git_repo.root.resolve()


def test_repo_root_returns_none_outside_any_git_repo(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert git.repo_root(outside) is None


# --- is_clean (threat matrix: commit state) ---------------------------------


def test_is_clean_true_on_freshly_committed_repo(tmp_git_repo: TmpGitRepo) -> None:
    assert git.is_clean(tmp_git_repo.root) is True


def test_is_clean_false_on_dirty_working_tree(tmp_git_repo: TmpGitRepo) -> None:
    (tmp_git_repo.root / "notes.txt").write_text("changed", encoding="utf-8")
    assert git.is_clean(tmp_git_repo.root) is False


def test_is_clean_false_on_staged_change(tmp_git_repo: TmpGitRepo) -> None:
    new_file = tmp_git_repo.root / "untracked.txt"
    new_file.write_text("new", encoding="utf-8")
    _git(["add", "untracked.txt"], cwd=tmp_git_repo.root)
    assert git.is_clean(tmp_git_repo.root) is False


# --- has_published_commits (threat matrix: push state) ----------------------


def test_has_published_commits_false_with_no_remote(tmp_git_repo: TmpGitRepo) -> None:
    assert git.has_published_commits(tmp_git_repo.root) is False


def test_has_published_commits_true_after_push_to_bare_remote(
    tmp_git_repo: TmpGitRepo, tmp_path_factory: pytest.TempPathFactory
) -> None:
    bare_dir = tmp_path_factory.mktemp("bare-remote")
    bare_result = git._run(["git", "init", "--bare"], cwd=bare_dir)
    assert bare_result.returncode == 0

    _git(["remote", "add", "origin", str(bare_dir)], cwd=tmp_git_repo.root)
    push_result = git._run(
        ["git", "push", "origin", "HEAD:refs/heads/main"], cwd=tmp_git_repo.root
    )
    assert push_result.returncode == 0

    assert git.has_published_commits(tmp_git_repo.root) is True


# --- expunge_paths removes blobs from ALL history (spec req 3, req 6) ------


def test_expunge_paths_removes_blobs_from_history(tmp_git_repo: TmpGitRepo) -> None:
    concept_rel_path = f"bundle/{tmp_git_repo.source_id}.md"
    concept_path = tmp_git_repo.root / concept_rel_path
    assert concept_path.exists()

    blob_sha = git._run(
        ["git", "rev-parse", f"HEAD:{concept_rel_path}"], cwd=tmp_git_repo.root
    ).stdout.strip()
    assert blob_sha

    git.expunge_paths(tmp_git_repo.root, [concept_rel_path])

    rev_list = git._run(
        ["git", "rev-list", "--objects", "--all"], cwd=tmp_git_repo.root
    )
    assert concept_rel_path not in rev_list.stdout

    reflog = git._run(["git", "reflog"], cwd=tmp_git_repo.root)
    assert reflog.stdout.strip() == ""

    cat_file = git._run(["git", "cat-file", "-e", blob_sha], cwd=tmp_git_repo.root)
    assert cat_file.returncode != 0

    assert not concept_path.exists()


# --- exit-code mapping -------------------------------------------------------


def test_run_maps_file_not_found_to_git_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_not_found(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("no such file or directory: 'git'")

    monkeypatch.setattr(subprocess, "run", _raise_not_found)

    with pytest.raises(git.GitUnavailable):
        git._run(["git", "status"], cwd=tmp_path)


def test_expunge_paths_maps_nonzero_exit_to_git_error(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_run(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=1, stdout="", stderr="filter-repo exploded"
        )

    monkeypatch.setattr(git, "_run", _fake_run)

    with pytest.raises(git.GitError, match="filter-repo exploded"):
        git.expunge_paths(tmp_git_repo.root, ["bundle/sources/notes.md"])
