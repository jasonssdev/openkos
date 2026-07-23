"""Unit tests for `openkos.vcs.git`: the sole `subprocess` adapter over
`git`/`git-filter-repo`. Most tests shell out to a REAL git repository via
the `tmp_git_repo` fixture (`tests/unit/vcs/conftest.py`) rather than
mocking `subprocess` -- the adapter's whole job is to invoke real `git`
correctly, so its tests must prove that against the real binary."""

import shutil
import subprocess
import tempfile
from collections.abc import Callable
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


def _spy_run_that_succeeds(
    calls: list[list[str]],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Returns a fake `_run` replacement that records every argv it is
    called with (proving whether the production code invoked a subprocess
    at all) and always reports success -- used to assert "never called"."""

    def _fake_run(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    return _fake_run


# --- FIX 1 (CRITICAL): newline/control-char injection via --paths-from-file -


def test_expunge_paths_rejects_newline_in_rel_path(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rel_path containing `\\n` would inject a SECOND
    `--paths-from-file` directive (e.g. `glob:**`), which combined with the
    always-passed `--invert-paths` escalates a single-path purge into a mass
    deletion. Must reject BEFORE any subprocess call, and must not leave a
    paths file behind."""
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run", _spy_run_that_succeeds(calls))

    before = set(Path(tempfile.gettempdir()).glob("*.txt"))
    with pytest.raises(ValueError, match="newline"):
        git.expunge_paths(tmp_git_repo.root, ["bundle/target.md\nglob:**"])
    after = set(Path(tempfile.gettempdir()).glob("*.txt"))

    assert calls == []
    assert after - before == set()


def test_expunge_paths_rejects_carriage_return_in_rel_path(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run", _spy_run_that_succeeds(calls))

    with pytest.raises(ValueError, match="carriage"):
        git.expunge_paths(tmp_git_repo.root, ["bundle/target.md\rglob:**"])

    assert calls == []


def test_expunge_paths_rejects_control_character_in_rel_path(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run", _spy_run_that_succeeds(calls))

    with pytest.raises(ValueError, match="control character"):
        git.expunge_paths(tmp_git_repo.root, ["bundle/target\x00.md"])

    assert calls == []


def test_expunge_paths_rejects_absolute_rel_path(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run", _spy_run_that_succeeds(calls))

    with pytest.raises(ValueError, match="absolute"):
        git.expunge_paths(tmp_git_repo.root, ["/etc/passwd"])

    assert calls == []


def test_expunge_paths_rejects_dotdot_segment_in_rel_path(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run", _spy_run_that_succeeds(calls))

    with pytest.raises(ValueError, match=r"\.\."):
        git.expunge_paths(tmp_git_repo.root, ["bundle/../../etc/passwd"])

    assert calls == []


def test_expunge_paths_rejects_empty_or_whitespace_rel_path(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run", _spy_run_that_succeeds(calls))

    with pytest.raises(ValueError, match="empty"):
        git.expunge_paths(tmp_git_repo.root, ["   "])

    assert calls == []


# --- FIX 3 (WARNING): empty rel_paths is a destructive no-op-filter --------


def test_expunge_paths_rejects_empty_rel_paths_list(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty `rel_paths` list makes git-filter-repo's `sanity_check_args`
    set `inclusive=False`, which rewrites EVERY commit in history while
    filtering nothing out -- a full destructive rewrite that purges nothing.
    Must reject before any subprocess call."""
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run", _spy_run_that_succeeds(calls))

    with pytest.raises(ValueError, match="empty"):
        git.expunge_paths(tmp_git_repo.root, [])

    assert calls == []


# --- FIX 2 (CRITICAL): finalize partial-failure must be distinguishable ----


def test_expunge_paths_reflog_expire_failure_raises_git_finalize_error(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `git filter-repo` SUCCEEDS but `git reflog expire` fails afterward,
    history has already been rewritten -- the purged blobs may still be
    recoverable via the un-expired reflog. The caller MUST be able to
    distinguish this from an ordinary pre-rewrite failure via a distinct
    exception type, with a remediation command in the message."""
    real_run = git._run

    def _fake_run(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["git", "filter-repo"]:
            return real_run(argv, cwd, env)
        if argv[:3] == ["git", "reflog", "expire"]:
            return subprocess.CompletedProcess(
                argv, returncode=1, stdout="", stderr="reflog expire exploded"
            )
        return real_run(argv, cwd, env)

    monkeypatch.setattr(git, "_run", _fake_run)

    concept_rel_path = f"bundle/{tmp_git_repo.source_id}.md"
    with pytest.raises(git.GitFinalizeError, match="reflog expire") as exc_info:
        git.expunge_paths(tmp_git_repo.root, [concept_rel_path])

    message = str(exc_info.value)
    assert "may still be recoverable" in message.lower()
    assert "git reflog expire --expire=now --all" in message
    assert "git gc --prune=now" in message


def test_expunge_paths_gc_failure_raises_git_finalize_error(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If filter-repo AND reflog expire succeed but `git gc --prune=now`
    fails, purged blobs are unreferenced but not yet pruned -- still
    recoverable. Must raise `GitFinalizeError`, not a generic `GitError`."""
    real_run = git._run

    def _fake_run(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["git", "gc"]:
            return subprocess.CompletedProcess(
                argv, returncode=1, stdout="", stderr="gc exploded"
            )
        return real_run(argv, cwd, env)

    monkeypatch.setattr(git, "_run", _fake_run)

    concept_rel_path = f"bundle/{tmp_git_repo.source_id}.md"
    with pytest.raises(git.GitFinalizeError, match="gc") as exc_info:
        git.expunge_paths(tmp_git_repo.root, [concept_rel_path])

    message = str(exc_info.value)
    assert "may still be recoverable" in message.lower()
    assert "git reflog expire --expire=now --all" in message
    assert "git gc --prune=now" in message


def test_expunge_paths_pre_rewrite_failure_is_plain_git_error_not_finalize_error(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-rewrite filter-repo failure means the rewrite did NOT happen --
    this stays an ordinary `GitError`, never the more alarming
    `GitFinalizeError` (which implies data may already be at risk)."""

    def _fake_run(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=1, stdout="", stderr="filter-repo exploded"
        )

    monkeypatch.setattr(git, "_run", _fake_run)

    with pytest.raises(git.GitError) as exc_info:
        git.expunge_paths(tmp_git_repo.root, ["bundle/sources/notes.md"])

    assert not isinstance(exc_info.value, git.GitFinalizeError)


# --- FIX 4 (WARNING): _run must map OSError broadly, not just FileNotFound -


def test_run_maps_permission_error_to_git_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present-but-unexecutable binary (e.g. after a TOCTOU race following
    `shutil.which`) raises `PermissionError` (an `OSError` subclass, but NOT
    `FileNotFoundError`). `_run` must map this to a typed error too, never
    let a raw `OSError` escape the adapter's error contract."""

    def _raise_permission_error(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("permission denied: 'git'")

    monkeypatch.setattr(subprocess, "run", _raise_permission_error)

    with pytest.raises(git.GitError):
        git._run(["git", "status"], cwd=tmp_path)


# --- FIX 5 (WARNING): repo_root must not swallow non-"not a repo" errors --


def test_repo_root_raises_git_error_on_unexpected_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`repo_root` returning `None` must mean "not inside a git repo" --
    NOT "git failed for some other reason". A non-"not a git repository"
    failure must surface as `GitError` with stderr, not silently become
    `None`."""

    def _fake_run(
        argv: list[str], cwd: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=128, stdout="", stderr="fatal: unable to read config file"
        )

    monkeypatch.setattr(git, "_run", _fake_run)

    with pytest.raises(git.GitError, match="unable to read config"):
        git.repo_root(tmp_path)


def test_repo_root_still_returns_none_for_genuine_not_a_repo(
    tmp_path: Path,
) -> None:
    """Regression guard: the benign "not inside a git repo" case must still
    return `None`, not raise, after FIX 5."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert git.repo_root(outside) is None


# --- FIX 6 (test-only): multi-ref rewrite proof --------------------------


def test_expunge_paths_removes_blobs_from_all_refs(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """`expunge_paths` must remove the target blob from EVERY ref, not just
    the current branch. Creates a second branch containing the same target
    path, then asserts it is gone from `git rev-list --objects --all`."""
    concept_rel_path = f"bundle/{tmp_git_repo.source_id}.md"

    original_branch = git._run(
        ["git", "branch", "--show-current"], cwd=tmp_git_repo.root
    ).stdout.strip()
    assert original_branch

    _git(["branch", "second-branch"], cwd=tmp_git_repo.root)
    _git(["checkout", "second-branch"], cwd=tmp_git_repo.root)
    extra_path = tmp_git_repo.root / "extra.txt"
    extra_path.write_text("extra content on second branch", encoding="utf-8")
    _git(["add", "extra.txt"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Second branch commit"], cwd=tmp_git_repo.root)
    _git(["checkout", original_branch], cwd=tmp_git_repo.root)

    all_branches_before = git._run(
        ["git", "rev-list", "--objects", "--all"], cwd=tmp_git_repo.root
    )
    assert concept_rel_path in all_branches_before.stdout

    git.expunge_paths(tmp_git_repo.root, [concept_rel_path])

    all_branches_after = git._run(
        ["git", "rev-list", "--objects", "--all"], cwd=tmp_git_repo.root
    )
    assert concept_rel_path not in all_branches_after.stdout
