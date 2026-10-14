"""The ports `curate` hands the curation decision surface bind LATE.

The S0 goldens run the whole walk, where every patch is installed before the
ports are built, so an early binding (`autocommit=cli_main._autocommit`) would
pass them all. These tests build the ports FIRST and patch afterwards, which
is the only order that tells the two apart. About a hundred tests patch
`openkos.cli.main.*` and `openkos.cli.curate.*` names, and this is the
contract that keeps those patches live.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast

import pytest

from openkos import config
from openkos.application import commit_phase
from openkos.application.curate_decisions import DecisionPorts
from openkos.application.lock_wait import CommitSection
from openkos.cli import curate
from openkos.cli import main as cli_main


def _ports(tmp_path: Path) -> DecisionPorts:
    ctx = curate.CurateContext(
        root=tmp_path,
        layout=config.WorkspaceLayout(tmp_path),
        cfg=cast(config.Config, None),
    )
    return curate._decision_ports(ctx)


def test_autocommit_reads_the_cli_main_attribute_on_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ports = _ports(tmp_path)
    seen: list[tuple[Path, Sequence[str], str]] = []

    def _patched(root: Path, paths: Sequence[str], message: str) -> str:
        seen.append((root, paths, message))
        return "feed123"

    monkeypatch.setattr("openkos.cli.main._autocommit", _patched)

    assert ports.autocommit(tmp_path, ["a.md"], "msg") == "feed123"
    assert seen == [(tmp_path, ["a.md"], "msg")]


def test_the_commit_section_reads_the_cli_main_section_on_every_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ports = _ports(tmp_path)
    entered: list[Path] = []

    def _section_for(root: Path) -> CommitSection:
        def _section() -> AbstractContextManager[None]:
            entered.append(root)
            return commit_phase.unlocked_section()

        return _section

    monkeypatch.setattr("openkos.cli.main._commit_section_for", _section_for)

    with ports.commit_section():
        pass

    assert entered == [tmp_path]


def test_the_ruling_writer_reads_the_cli_main_adapter_on_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ports = _ports(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    seen: list[tuple[tuple[str, ...], str]] = []

    def _patched(
        layout_: config.WorkspaceLayout,
        member_ids: tuple[str, ...],
        *,
        target_state: str,
    ) -> str:
        seen.append((member_ids, target_state))
        return "bundle/.state/decisions/x.okf"

    monkeypatch.setattr("openkos.cli.main._apply_identity_decision", _patched)

    path = ports.record_identity_ruling(layout, ("a", "b"), lambda message: None)

    assert path == "bundle/.state/decisions/x.okf"
    assert seen == [(("a", "b"), "declined")]


def test_the_warning_sink_is_the_cli_warning_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ports = _ports(tmp_path)

    ports.on_warning("a note")

    captured = capsys.readouterr()
    assert captured.err == "a note\n"
    assert captured.out == ""
    assert callable(cli_main._echo_warning)
