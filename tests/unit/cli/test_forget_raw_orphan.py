"""Issue #1262: `forget` never touches `raw/`, so a forgotten Source's raw
copy is orphaned on disk AND in git history, and `purge` refuses once the
concept is gone. `forget` must say so and name the sequence that really
erases it, and `lint` must report a raw file no Source references."""

import re
import shlex
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()

_COMMAND = re.compile(r"`([^`]+)`")


def _workspace_with_a_source(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """An initialized, committing workspace at `root` holding one ingested
    Source. The git config and the original file live OUTSIDE `root` (its
    parent), so the tree stays clean for `purge`'s dirty-tree rail."""
    root.mkdir()
    monkeypatch.chdir(root)
    isolate_git_identity(monkeypatch, root.parent, name="T", email="t@openkos.invalid")
    assert runner.invoke(app, ["init"]).exit_code == 0
    original = root.parent / "notes.txt"
    original.write_text("private content", encoding="utf-8")
    assert runner.invoke(app, ["ingest", str(original), "--auto"]).exit_code == 0
    return "sources/notes"


def _in_history(root: Path, rel_path: str) -> bool:
    result = vcs_git._run(["git", "rev-list", "--objects", "--all"], cwd=root)
    assert result.returncode == 0
    return any(line.endswith(rel_path) for line in result.stdout.splitlines())


def _commands(text: str) -> list[list[str]]:
    return [shlex.split(match) for match in _COMMAND.findall(text)]


def test_forget_names_the_orphaned_raw_copy_and_a_sequence_that_erases_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    source_id = _workspace_with_a_source(root, monkeypatch)

    forget = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert forget.exit_code == 0, forget.output
    assert (root / "raw" / "notes.txt").exists()
    assert "raw/notes.txt remains on disk and in git history" in forget.output
    # The disclosure's own commands, executed verbatim, must erase the blob.
    steps = _commands(forget.output)
    revert = next(c for c in steps if c[:2] == ["git", "revert"])
    purge = next(c for c in steps if c[:2] == ["openkos", "purge"])
    assert purge[2] == source_id
    assert "--scope" in purge

    reverted = vcs_git._run(["git", *revert[1:], "--no-edit"], cwd=root)
    assert reverted.returncode == 0, reverted.stderr
    erased = runner.invoke(
        app,
        [*purge[1:], "--force", "--confirm-phrase", f"purge {source_id} (1 concepts)"],
    )
    assert erased.exit_code == 0, erased.output
    assert not (root / "raw" / "notes.txt").exists()
    assert not _in_history(root, "raw/notes.txt")


def test_forget_of_a_concept_without_a_raw_copy_says_nothing_about_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    _workspace_with_a_source(root, monkeypatch)
    concept = root / "bundle" / "concepts" / "plain.md"
    concept.parent.mkdir(parents=True, exist_ok=True)
    concept.write_text("---\ntype: Concept\ntitle: Plain\n---\n\nBody.\n", "utf-8")
    vcs_git._run(["git", "add", "-A"], cwd=root)
    vcs_git._run(["git", "commit", "-m", "plain"], cwd=root)

    forget = runner.invoke(app, ["forget", "concepts/plain", "--auto"])

    assert forget.exit_code == 0, forget.output
    assert "remains on disk" not in forget.output


def test_lint_reports_a_raw_file_no_source_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    source_id = _workspace_with_a_source(root, monkeypatch)
    assert runner.invoke(app, ["forget", source_id, "--auto"]).exit_code == 0

    lint = runner.invoke(app, ["lint"])

    assert lint.exit_code == 0, lint.output
    assert "Unreferenced raw files:" in lint.stdout
    assert "raw/notes.txt" in lint.stdout


def test_lint_reports_no_unreferenced_raw_file_for_a_referenced_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    _workspace_with_a_source(root, monkeypatch)

    lint = runner.invoke(app, ["lint"])

    assert lint.exit_code == 0, lint.output
    assert "Unreferenced raw files:" in lint.stdout
    assert "No unreferenced raw files." in lint.stdout


def test_forget_says_nothing_when_the_raw_copy_is_already_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    source_id = _workspace_with_a_source(root, monkeypatch)
    (root / "raw" / "notes.txt").unlink()
    vcs_git._run(["git", "add", "-A"], cwd=root)
    vcs_git._run(["git", "commit", "-m", "drop raw"], cwd=root)

    forget = runner.invoke(app, ["forget", source_id, "--auto"])

    assert forget.exit_code == 0, forget.output
    assert "remains on disk" not in forget.output


def test_forget_without_a_commit_names_the_precondition_not_a_revert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No git identity -> no forget commit -> there is nothing to revert, so
    the disclosure must not name a `git revert` it cannot back."""
    root = tmp_path / "ws"
    source_id = _workspace_with_a_source(root, monkeypatch)
    isolate_git_identity(monkeypatch, tmp_path / "ws")  # identity now unset

    forget = runner.invoke(app, ["forget", source_id, "--auto"])

    assert forget.exit_code == 0, forget.output
    assert "raw/notes.txt remains on disk" in forget.output
    assert "git revert" not in forget.output
    assert "restore the concept from git first" in forget.output
    assert f"`openkos purge {source_id}`" in forget.output
