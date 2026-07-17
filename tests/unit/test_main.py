"""Unit tests for the `openkos` package surface and its console entry point.

These tests double as proof that the harness is wired end to end: the package
is importable from the installed distribution, the console script resolves to
the Typer `app` object, the app responds to `--help`, and the PEP 561 marker
ships with the package.
"""

from importlib import metadata, resources

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
