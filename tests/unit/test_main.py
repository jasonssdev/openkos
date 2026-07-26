"""Unit tests for the `openkos` package surface and its console entry point.

These tests double as proof that the harness is wired end to end: the package
is importable from the installed distribution, the console script resolves to
the Typer `app` object, the app responds to `--help`, and the PEP 561 marker
ships with the package.
"""

import re
from importlib import metadata, resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

import openkos.cli.main


def test_console_script_entry_point_resolves_to_app() -> None:
    """The `openkos` console script is declared once and loads the Typer `app`."""
    (entry_point,) = metadata.entry_points(group="console_scripts", name="openkos")

    assert entry_point.load() is openkos.cli.main.app


def test_app_help_exits_zero() -> None:
    """Invoking the Typer app with `--help` succeeds without a subcommand."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--help"])

    assert result.exit_code == 0


def test_package_ships_py_typed_marker() -> None:
    """The package is distributed as typed, per PEP 561."""
    marker = resources.files("openkos") / "py.typed"

    assert marker.is_file()


def test_version_flag_prints_version_and_exits_zero() -> None:
    """`--version` prints `openkos {version}` and exits 0."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--version"])

    assert result.exit_code == 0
    assert re.match(r"^openkos \d+\.\d+\.\d+", result.stdout.strip())


def test_version_flag_matches_installed_distribution_metadata() -> None:
    """The printed version matches `importlib.metadata.version("openkos")`."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--version"])

    assert result.stdout.strip() == f"openkos {metadata.version('openkos')}"


def test_version_flag_works_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--version` succeeds outside an initialized workspace, without any
    workspace resolution."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"openkos {metadata.version('openkos')}"


def test_version_flag_short_circuits_combined_subcommand_invocation() -> None:
    """`--version doctor` prints only the version; `doctor` never runs."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--version", "doctor"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"openkos {metadata.version('openkos')}"
    assert "checking environment at" not in result.stdout


def test_version_flag_prints_exactly_one_line() -> None:
    """`--version` writes a single stdout line and nothing to stderr."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--version"])

    assert len(result.stdout.strip().splitlines()) == 1
    assert result.stderr == ""


def test_version_flag_falls_back_when_distribution_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `_pkg_version` raises `PackageNotFoundError`, `--version` prints
    `openkos unknown` and exits 0 -- the module-local alias is monkeypatched,
    never the shared stdlib `importlib.metadata.version` origin."""

    def _raise_not_found(_name: str) -> str:
        raise metadata.PackageNotFoundError("openkos")

    monkeypatch.setattr(openkos.cli.main, "_pkg_version", _raise_not_found)
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "openkos unknown"
    assert openkos.cli.main._version_line() == "openkos unknown"


def test_version_flag_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--version` performs no filesystem write and constructs no
    `OllamaClient`."""
    monkeypatch.chdir(tmp_path)

    def _fail_if_constructed(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OllamaClient must not be constructed by --version")

    monkeypatch.setattr("openkos.cli.main.OllamaClient", _fail_if_constructed)
    before = set(tmp_path.rglob("*"))
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--version"])

    assert result.exit_code == 0
    assert set(tmp_path.rglob("*")) == before


def test_version_flag_is_discoverable_in_help_text() -> None:
    """`openkos --help` lists `--version` among the top-level options."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--help"])

    assert result.exit_code == 0
    assert "--version" in result.stdout
