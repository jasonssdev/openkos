"""Unit tests for the `openkos` package surface and its console entry point.

These tests exercise the only production code that exists today, and double as
proof that the harness is wired end to end: the package is importable from the
installed distribution, the console script resolves to a real callable, and the
PEP 561 marker ships with the package.
"""

from importlib import metadata, resources

import pytest

import openkos


def test_main_prints_greeting_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """`main` writes the greeting to stdout and leaves stderr untouched."""
    openkos.main()

    captured = capsys.readouterr()
    assert captured.out == "Hello from openkos!\n"
    assert captured.err == ""


def test_console_script_entry_point_resolves_to_main() -> None:
    """The `openkos` console script is declared once and loads `openkos.main`."""
    (entry_point,) = metadata.entry_points(group="console_scripts", name="openkos")

    assert entry_point.load() is openkos.main


def test_package_ships_py_typed_marker() -> None:
    """The package is distributed as typed, per PEP 561."""
    marker = resources.files("openkos") / "py.typed"

    assert marker.is_file()
