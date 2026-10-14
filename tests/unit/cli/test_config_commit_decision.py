"""`_decide_config_commit` is the git-reading half of `_plan_config_commit`
(#1330): it returns the plan, the refusal or `None` and never prints or
exits, so the curation decision surface can reach it as a port. The wrapper
keeps `set-volatility`'s behavior (its output is pinned by
`test_set_volatility_output_golden.py`)."""

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from openkos import config
from openkos.application.curate_decisions import ConfigCommitRefusal
from openkos.cli import main as cli_main
from openkos.cli.main import app
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_identity(
        monkeypatch, tmp_path, name="OpenKOS Test", email="test@openkos.example"
    )
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _baseline(tmp_path: Path) -> bytes:
    return (tmp_path / "openkos.yaml").read_bytes()


def _refusing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> bytes:
    """Commit `Person: volatile`, then hand-edit the same entry."""
    _workspace(tmp_path, monkeypatch)
    assert (
        runner.invoke(app, ["set-volatility", "Person", "volatile", "--auto"]).exit_code
        == 0
    )
    path = tmp_path / "openkos.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Person: volatile", "Person: static"),
        encoding="utf-8",
    )
    return _baseline(tmp_path)


def test_a_clean_config_decides_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)

    decision = cli_main._decide_config_commit(
        tmp_path, "curate", "Person", "volatile", _baseline(tmp_path)
    )

    assert decision is None


def test_an_unrelated_uncommitted_edit_decides_a_plan_that_applies_to_the_committed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    path = tmp_path / "openkos.yaml"
    committed = path.read_text(encoding="utf-8")
    path.write_text(committed + "\nunattended:\n  quiet_seconds: 5\n", encoding="utf-8")

    decision = cli_main._decide_config_commit(
        tmp_path, "curate", "Person", "volatile", _baseline(tmp_path)
    )

    assert isinstance(decision, cli_main._ConfigCommitPlan)
    assert decision.apply(committed.encode("utf-8")) == config.set_type_tier(
        committed, "Person", "volatile"
    ).encode("utf-8")


def test_a_dirty_entry_decides_a_refusal_without_printing_or_exiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _refusing_workspace(tmp_path, monkeypatch)

    decision = cli_main._decide_config_commit(
        tmp_path, "curate", "Person", "slow", baseline
    )

    assert isinstance(decision, ConfigCommitRefusal)
    assert decision.message == (
        "openkos curate: refusing to write -- openkos.yaml has uncommitted "
        "changes to the 'Person' tier entry (or a shape this command cannot "
        "edit in the committed version), so the change cannot be committed on "
        "its own; commit or stash them, then try again."
    )
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_the_refusal_names_the_verb_it_was_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _refusing_workspace(tmp_path, monkeypatch)

    decision = cli_main._decide_config_commit(
        tmp_path, "set-volatility", "Person", "slow", baseline
    )

    assert isinstance(decision, ConfigCommitRefusal)
    assert decision.message.startswith("openkos set-volatility: refusing to write")


def test_the_wrapper_still_echoes_the_refusal_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _refusing_workspace(tmp_path, monkeypatch)
    expected = cli_main._decide_config_commit(
        tmp_path, "set-volatility", "Person", "slow", baseline
    )
    assert isinstance(expected, ConfigCommitRefusal)

    with pytest.raises(typer.Exit) as caught:
        cli_main._plan_config_commit(
            tmp_path, "set-volatility", "Person", "slow", baseline
        )

    assert caught.value.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == expected.message + "\n"


def test_the_wrapper_returns_the_plan_or_none_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)

    assert (
        cli_main._plan_config_commit(
            tmp_path, "set-volatility", "Person", "volatile", _baseline(tmp_path)
        )
        is None
    )
