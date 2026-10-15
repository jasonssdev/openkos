"""The CLI's commit helpers are delegators over `openkos.vcs.autocommit`; this
pins the exact bytes they print (characterization: green before and after the
lift), because `test_main_autocommit.py` only matches substrings."""

from pathlib import Path

import pytest

from openkos.cli import main
from openkos.vcs import autocommit
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import isolate_git_identity

_NOT_A_REPO = (
    "openkos: WARNING -- not a git repository; skipped auto-commit "
    "(writes are on disk).\n"
)
_NO_IDENTITY = (
    "openkos: WARNING -- git identity unset; skipped auto-commit "
    "(writes are on disk).\n"
)
_NOTICE = (
    "openkos: NOTICE -- this commit includes content marked "
    "'sensitivity: confidential'. openkos commits to LOCAL git "
    "only and never pushes to a remote.\n"
)


def _identity(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    isolate_git_identity(monkeypatch, config_dir, name="T", email="t@example.invalid")


def test_not_a_repository_prints_the_exact_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")

    sha = main._autocommit(tmp_path, ["f.txt"], "m")

    captured = capsys.readouterr()
    assert (sha, captured.out, captured.err) == (None, "", _NOT_A_REPO)


def test_unset_identity_prints_the_exact_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(monkeypatch, tmp_path_factory.mktemp("cfg"))
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")

    sha = main._autocommit(tmp_path, ["f.txt"], "m")

    captured = capsys.readouterr()
    assert (sha, captured.out, captured.err) == (None, "", _NO_IDENTITY)


def test_a_git_failure_prints_the_exact_warning_with_its_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vcs_git.init_repo(tmp_path)
    _identity(monkeypatch, tmp_path_factory.mktemp("cfg"))

    def boom(*_args: object) -> str:
        raise vcs_git.GitError("add exploded")

    monkeypatch.setattr(vcs_git, "commit_paths", boom)

    sha = main._autocommit(tmp_path, ["f.txt"], "m")

    captured = capsys.readouterr()
    assert (sha, captured.out, captured.err) == (
        None,
        "",
        "openkos: WARNING -- auto-commit did not complete (add exploded); "
        "run `git status` to inspect.\n",
    )


def test_a_confidential_commit_prints_the_exact_notice_and_returns_the_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vcs_git.init_repo(tmp_path)
    _identity(monkeypatch, tmp_path_factory.mktemp("cfg"))
    secret = tmp_path / "bundle" / "secret.md"
    secret.parent.mkdir()
    secret.write_text("---\ntype: Concept\nsensitivity: confidential\n---\n", "utf-8")

    sha = main._autocommit(tmp_path, ["bundle/secret.md"], "m")

    captured = capsys.readouterr()
    assert sha is not None
    assert (captured.out, captured.err) == ("", _NOTICE)


def test_a_patched_confidential_predicate_still_drives_the_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vcs_git.init_repo(tmp_path)
    _identity(monkeypatch, tmp_path_factory.mktemp("cfg"))
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(main, "_commit_has_confidential", lambda root, paths: True)

    main._autocommit(tmp_path, ["f.txt"], "m")

    assert capsys.readouterr().err == _NOTICE


def test_a_config_edit_with_a_plan_and_no_identity_prints_the_exact_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vcs_git.init_repo(tmp_path)
    isolate_git_identity(monkeypatch, tmp_path_factory.mktemp("cfg"))
    plan = autocommit.ConfigCommitPlan(apply=lambda content: content)

    sha = main._autocommit_config_edit(tmp_path, "m", plan)

    captured = capsys.readouterr()
    assert (sha, captured.out, captured.err) == (None, "", _NO_IDENTITY)


def test_a_config_edit_with_nothing_to_commit_is_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vcs_git.init_repo(tmp_path)
    _identity(monkeypatch, tmp_path_factory.mktemp("cfg"))
    (tmp_path / "openkos.yaml").write_text("backend: ollama\n", encoding="utf-8")
    vcs_git.commit_paths(tmp_path, ["openkos.yaml"], "seed")

    sha = main._autocommit_config_edit(tmp_path, "m")

    captured = capsys.readouterr()
    assert (sha, captured.out, captured.err) == (None, "", "")
