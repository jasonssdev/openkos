"""Byte-level pins for `set-volatility`'s output around the config-commit
decision (#1330): the clean write, the write over an unrelated uncommitted
edit, and the dirty-entry refusal. They exist so the `curate-decisions-as-data`
change can split `_plan_config_commit` into a pure decision plus the exit
wrapper without moving a byte of what this verb prints, exits with or commits."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_identity(
        monkeypatch, tmp_path, name="OpenKOS Test", email="test@openkos.example"
    )
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _subjects(tmp_path: Path) -> list[str]:
    out = vcs_git._run(["git", "log", "--format=%s"], cwd=tmp_path).stdout
    return out.splitlines()


def _edit_config(tmp_path: Path, old: str, new: str) -> None:
    config = tmp_path / "openkos.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )


def test_a_clean_write_prints_and_commits_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    before = _subjects(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert result.exit_code == 0
    assert result.stdout == (
        "openkos set-volatility: proposed changes:\n"
        "  Person: slow -> volatile\n"
        "openkos set-volatility: set Person -> volatile in openkos.yaml.\n"
    )
    assert result.stderr == ""
    assert _subjects(tmp_path) == [
        "openkos: set-volatility Person -> volatile",
        *before,
    ]


def test_a_write_over_an_unrelated_uncommitted_edit_prints_and_commits_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    before = _subjects(tmp_path)
    config = tmp_path / "openkos.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\nunattended:\n  quiet_seconds: 5\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"])

    assert result.exit_code == 0
    assert result.stdout == (
        "openkos set-volatility: proposed changes:\n"
        "  Person: slow -> volatile\n"
        "openkos set-volatility: set Person -> volatile in openkos.yaml.\n"
    )
    assert result.stderr == ""
    assert _subjects(tmp_path) == [
        "openkos: set-volatility Person -> volatile",
        *before,
    ]


def test_the_dirty_entry_refusal_prints_and_exits_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    assert (
        runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"]).exit_code
        == 0
    )
    _edit_config(tmp_path, "Person: volatile", "Person: static")
    before = _subjects(tmp_path)

    result = runner.invoke(app, ["set-volatility", "Person", "slow", "--auto"])

    assert result.exit_code == 1
    assert result.stdout == (
        "openkos set-volatility: proposed changes:\n  Person: static -> slow\n"
    )
    assert result.stderr == (
        "openkos set-volatility: refusing to write -- openkos.yaml has "
        "uncommitted changes to the 'Person' tier entry (or a shape this "
        "command cannot edit in the committed version), so the change cannot "
        "be committed on its own; commit or stash them, then try again.\n"
    )
    assert _subjects(tmp_path) == before
