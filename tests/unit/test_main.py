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
import typer
from typer.testing import CliRunner

import openkos.cli.main

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Drop SGR style codes so an assertion sees the words a user reads.

    Rich decides whether to emit color from the environment, so raw stdout is
    not stable across a local run and a CI run; the rendered TEXT is.
    """
    return _ANSI_RE.sub("", text)


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
    """`openkos --help` lists `--version` among the top-level options.

    The assertion runs against ANSI-stripped output on purpose. Typer renders
    help through Rich with `FORCE_TERMINAL = True` whenever `GITHUB_ACTIONS`,
    `FORCE_COLOR`, or `PY_COLORS` is set (`typer/rich_utils.py`) -- and every
    GitHub Actions runner sets `GITHUB_ACTIONS`, so CI always gets styled
    output even though nothing in this repo asks for color. Rich then emits
    style codes *inside* the option name, so the literal substring
    `--version` is absent from raw stdout even though the flag is plainly
    listed. Asserting on the styled bytes made this test pass locally and
    fail in CI (it did: run 30187024173).

    `COLUMNS` is pinned for the same reason: Rich reads it even when stdout is
    not a TTY, and below ~40 columns it truncates the option name to
    `--versi…`, which would fail this test for a rendering condition that says
    nothing about whether the flag exists.
    """
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--help"], env={"COLUMNS": "80"})

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    # The spec requires more than the flag NAME being present: the entry must
    # describe what it does ("a `--version` entry describing it as printing the
    # installed version"), so assert the help string too, on the same line.
    (version_line,) = [ln for ln in help_text.splitlines() if "--version" in ln]
    assert "Show the installed openkos version and exit." in version_line


# --- #389: the published help surface ---------------------------------------

_INTERNAL_HELP_MARKERS = (
    "design D",
    "ADR-",
    "MVP-",
    "spec:",
    "decision #",
    "issue #",
)
"""Traceability vocabulary that belongs in source, never in published help.

Each of these appears in real command docstrings today. Typer publishes the
raw `__doc__` unless a command sets `help=`, so before #389 an end user read
things like "Wires the pure `bundle.provenance.resolve_backfill_raises` sweep
core (design D4/D5) into Typer's confirm-gate"."""

_INTERNAL_HELP_PATTERNS = (
    re.compile(r"\bS\d+[a-z]?\b"),
    re.compile(r"\bSlice \d+[a-z-]*"),
    re.compile(r"\bD\d+/D\d+\b"),
    re.compile(r"\bgap #\d+\b"),
)
"""Traceability vocabulary that a fixed substring list cannot catch.

Added after the first version of this test passed while `forget --scope`
still published the token "S2a" (review finding): a slice reference does not
have to spell out the word "Slice" to be jargon. Patterns cover the
abbreviated slice/story forms, paired design references like "D4/D5", and
numbered gap references.

The slice pattern deliberately does NOT end on a word boundary after its
digits. It did at first, which meant it matched a bare "Slice 1" and missed
"Slice 2a" and "Slice 2b-ii" -- there is no boundary between a digit and the
letter after it, and the lettered forms are the ones this codebase actually
uses most. `[a-z-]*` absorbs them while still refusing "Sliced"."""


def _registered_command_names() -> list[str]:
    """Every command name the app publishes, taken from Typer's own registry
    so a newly added command is covered without editing this test."""
    names = []
    for command in openkos.cli.main.app.registered_commands:
        if command.name:
            names.append(command.name)
            continue
        # Typer types `callback` as optional; every command in this app has
        # one, and a registration without a name OR a callback would be a
        # bug worth failing on rather than skipping silently.
        assert command.callback is not None
        names.append(command.callback.__name__.replace("_", "-"))
    return names


def test_no_command_help_publishes_internal_references() -> None:
    """No published `--help` text carries developer traceability vocabulary
    (#389).

    Checked per COMMAND, not only on the top-level listing: the same
    docstrings feed MCP tool descriptions, and text that does not help a
    person will not help an agent either. `help=` decouples the two, so
    traceability stays in the docstring and a clean line gets published.

    The whole rendered page is scanned, OPTION and ARGUMENT help included --
    those are published too, and the first version of this test missed a
    slice token sitting in one of them."""
    runner = CliRunner()

    offenders: dict[str, list[str]] = {}
    for name in _registered_command_names():
        result = runner.invoke(
            openkos.cli.main.app, [name, "--help"], env={"COLUMNS": "200"}
        )
        assert result.exit_code == 0, name
        text = _strip_ansi(result.stdout)
        hits = [marker for marker in _INTERNAL_HELP_MARKERS if marker in text]
        hits += [
            match
            for pattern in _INTERNAL_HELP_PATTERNS
            for match in pattern.findall(text)
        ]
        if hits:
            offenders[name] = hits

    assert offenders == {}


def test_top_level_help_groups_commands_into_panels() -> None:
    """The 26 commands are grouped by what the reader is trying to do,
    instead of listed flat in declaration order (#389).

    Declaration order put `purge` -- irreversible and rare -- fourth, while
    `query`, the value moment, sat near the bottom."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    for panel in ("Get started", "Explore", "Curate", "Maintain", "Remove"):
        assert panel in help_text, panel


def test_top_level_help_prints_panels_in_reading_order() -> None:
    """Panels print in reading order -- start, ask, decide, maintain, delete
    -- not in the order their first command happens to be declared (#389).

    Grouping alone did not fix the ordering half of the issue. Rich prints a
    panel when it first meets a command belonging to it, and `forget`/`purge`
    are declared early, so "Remove" landed SECOND and made the irreversible
    verbs more prominent than the flat list had."""
    runner = CliRunner()

    result = runner.invoke(openkos.cli.main.app, ["--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    positions = [help_text.index(panel) for panel in openkos.cli.main.PANEL_ORDER]
    assert positions == sorted(positions), dict(
        zip(openkos.cli.main.PANEL_ORDER, positions, strict=True)
    )


def test_every_command_belongs_to_a_known_panel() -> None:
    """Every registered command declares a panel from `PANEL_ORDER` (#389).

    This does NOT prevent the import-time failure -- the sort runs when the
    module loads, long before pytest collects anything, so a bad panel value
    stops the whole CLI and this assertion never gets to run (review finding
    on the first version of this test, which claimed otherwise). What it does
    is state the requirement where a contributor will read it, and keep the
    invariant visible in the suite. `_panel_rank` carries the legible failure
    for the case this test cannot reach."""
    for command in openkos.cli.main.app.registered_commands:
        assert command.rich_help_panel in openkos.cli.main.PANEL_ORDER, command.name


def test_unknown_panel_fails_with_a_message_naming_the_command() -> None:
    """A command declaring an unknown panel fails with the command name and
    the allowed values, not a bare `ValueError` from a lambda (#389).

    This is the failure a contributor actually meets: it happens at import,
    so it takes the whole CLI down. Failing hard is right -- a misplaced
    command should not ship quietly -- but failing hard and mutely was not."""
    bad = typer.models.CommandInfo(name="pretend", rich_help_panel="Nope")

    with pytest.raises(RuntimeError) as caught:
        openkos.cli.main._panel_rank(bad)

    message = str(caught.value)
    assert "pretend" in message
    assert "Nope" in message
    assert "Get started" in message  # the allowed values are shown
