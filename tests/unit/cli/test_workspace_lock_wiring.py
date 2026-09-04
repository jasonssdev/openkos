"""The workspace lock's CLI wiring (#925).

`lock.py`'s own tests prove the primitive serializes two processes. These prove
the CLI actually USES it, on every command that can write, and that the roster
saying which commands those are cannot rot silently.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import lock
from openkos.cli.main import _READ_ONLY_COMMANDS, app

runner = CliRunner()

_REPO_ROOT = Path(__file__).resolve().parents[3]

# `S603` is suppressed on every spawn below. The argv is a fixed list --
# `sys.executable`, `-c`, the module-level literal script, and pytest's own
# `tmp_path` -- with no shell and no caller-supplied input. Contention between
# two real processes is the whole property under test and cannot be faked
# in-process.

_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from openkos import lock

    with lock.workspace_lock(Path(sys.argv[1])):
        print("ACQUIRED", flush=True)
        sys.stdin.read()
    """
)


def _registered_commands() -> dict[str, object]:
    """Every command Typer has registered, keyed by its published CLI name."""
    found: dict[str, object] = {}
    for info in app.registered_commands:
        callback = info.callback
        assert callback is not None
        name = info.name or callback.__name__.replace("_", "-").removesuffix("-cmd")
        found[name] = callback
    return found


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def test_every_command_is_classified() -> None:
    """No command may be silently unclassified.

    This is the test that makes the fail-safe direction real. A new mutating
    verb that nobody remembers to think about is locked by default -- and if
    someone adds one to neither side, or misspells its name in the decorator,
    this fails rather than letting it race in production.
    """
    commands = _registered_commands()
    unclassified = [
        name
        for name, callback in commands.items()
        if name not in _READ_ONLY_COMMANDS
        and getattr(callback, "__openkos_locked_command__", None) is None
    ]

    assert unclassified == [], (
        "these commands are neither locked nor declared read-only; add "
        "@_guard_workspace_lock, or name them in _READ_ONLY_COMMANDS if they "
        "provably never write to the workspace"
    )


def test_the_declared_lock_name_matches_the_registered_name() -> None:
    """The decorator's string is what the refusal prints, so a copy-paste slip
    would report the wrong verb to the operator. Cross-check it against the
    name Typer actually publishes."""
    mismatched = {
        name: declared
        for name, callback in _registered_commands().items()
        if (declared := getattr(callback, "__openkos_locked_command__", None))
        is not None
        and declared != name
    }

    assert mismatched == {}


def test_read_only_names_are_real_commands() -> None:
    """The exemption list must not name a command that no longer exists --
    otherwise it silently stops exempting anything and nobody notices."""
    assert set(_registered_commands()) >= _READ_ONLY_COMMANDS


def test_read_only_commands_are_not_locked() -> None:
    """The exemption has to actually exempt: a read-only command carrying the
    decorator would make `openkos status` fail during any long ingest."""
    commands = _registered_commands()
    wrongly_locked = [
        name
        for name in _READ_ONLY_COMMANDS
        if getattr(commands[name], "__openkos_locked_command__", None) is not None
    ]

    assert wrongly_locked == []


def test_a_mutating_command_refuses_while_another_process_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end property: a second mutator refuses instead of racing.

    Exit 3 is this CLI's documented retry-safe refusal -- nothing written, a
    plain re-run is equivalent -- which is exactly a busy workspace's contract.
    """
    _init_workspace(tmp_path, monkeypatch)
    holder = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", _HOLDER, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=_REPO_ROOT,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        result = runner.invoke(app, ["relate", "a", "references", "b"])

        assert result.exit_code == 3
        assert "another OpenKOS process is modifying this workspace" in result.stderr
        assert "openkos relate:" in result.stderr
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_a_read_only_command_still_runs_while_the_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openkos status` during a long `ingest` must still answer.

    Without this, `test_a_mutating_command_refuses...` would pass just as well
    against a lock applied to every command -- which would make the tool
    unusable while any write is in flight.
    """
    _init_workspace(tmp_path, monkeypatch)
    holder = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", _HOLDER, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=_REPO_ROOT,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0, result.output
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_help_does_not_take_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openkos forget --help` must answer during another process's write.

    The decorator wraps the command BODY, which `--help` never reaches. A
    Typer group callback would have been the tidier seam and gets this wrong:
    it fires before the subcommand's help is rendered, so asking for help
    against a busy workspace would fail.
    """
    _init_workspace(tmp_path, monkeypatch)
    holder = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", _HOLDER, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=_REPO_ROOT,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        result = runner.invoke(app, ["forget", "--help"])

        assert result.exit_code == 0
        assert "concept_id" in result.output
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_no_workspace_reports_its_own_refusal_not_a_lock_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Error precedence is unchanged: outside a workspace the operator still
    gets "no OpenKOS workspace found", and no `.openkos/` is created by the
    lock in a directory that is not a workspace."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["forget", "anything", "--auto"])

    assert result.exit_code == 1
    assert "no OpenKOS workspace found" in result.stderr
    assert not lock.lock_path_for(tmp_path).exists()
    assert not (tmp_path / ".openkos").exists()
