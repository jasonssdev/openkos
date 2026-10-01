"""The workspace lock's CLI wiring (#925).

`lock.py`'s own tests prove the primitive serializes two processes. These prove
the CLI actually USES it, on every command that can write, and that the roster
saying which commands those are cannot rot silently.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import lock
from openkos.cli.main import _READ_ONLY_COMMANDS, _SELF_LOCKING_COMMANDS, app
from tests.unit.conftest import make_locked_error, make_non_lock_operational_error

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
        and name not in _SELF_LOCKING_COMMANDS
        and getattr(callback, "__openkos_locked_command__", None) is None
    ]

    assert unclassified == [], (
        "these commands are neither locked nor declared read-only; add "
        "@_guard_workspace_lock, or name them in _READ_ONLY_COMMANDS if they "
        "provably never write to the workspace"
    )


def test_a_command_belongs_to_exactly_one_class() -> None:
    """Read-only, locked and self-locking are disjoint: a command in two
    classes would be both exempt from the lock and wrapped in it."""
    commands = _registered_commands()
    locked = {
        name
        for name, callback in commands.items()
        if getattr(callback, "__openkos_locked_command__", None) is not None
    }

    assert frozenset() == _READ_ONLY_COMMANDS & _SELF_LOCKING_COMMANDS
    assert locked & _READ_ONLY_COMMANDS == set()
    assert locked & _SELF_LOCKING_COMMANDS == set()
    assert set(commands) >= _SELF_LOCKING_COMMANDS


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


@pytest.mark.skipif(
    sys.platform == "win32", reason="lock directory modes are POSIX-only"
)
def test_an_untrusted_lock_directory_is_a_refusal_with_exit_1_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1, not 3: a plain re-run refuses again until the directory is
    fixed, so this must not advertise the retry-safe code (#1134)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_tmp = tmp_path.parent / f"{tmp_path.name}-faketmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))
    directory = fake_tmp / f"{lock.LEGACY_LOCK_DIR_PREFIX}-{os.geteuid()}"
    directory.mkdir()
    directory.chmod(0o755)

    result = runner.invoke(app, ["relate", "a", "references", "b"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "openkos relate: refusing to run --" in result.stderr
    assert str(directory) in result.stderr
    assert "chmod 700" in result.stderr


_DERIVED_STORE_MESSAGE = (
    "openkos relate: failed -- another OpenKOS process is using the "
    "workspace's derived stores; re-running is safe, so try again once it "
    "finishes.\n"
)


@pytest.mark.parametrize(
    "errorcode", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED], ids=["busy", "locked"]
)
def test_derived_store_contention_in_a_locked_verb_exits_3_with_the_neutral_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, errorcode: int
) -> None:
    """A findings/derived-store write that hits a SQLite busy timeout in any
    locked verb no verb-specific handler took is the same retry-safe refusal
    as workspace-lock contention: exit 3, the verb's own name, and no guess at
    which other verb is running."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_locked(*args: object, **kwargs: object) -> None:
        raise make_locked_error(errorcode=errorcode)

    monkeypatch.setattr(
        "openkos.cli.main.application_lifecycle.resolve_concept_path", _raise_locked
    )

    result = runner.invoke(app, ["relate", "a", "references", "b"])

    assert result.exit_code == 3
    assert result.stderr == _DERIVED_STORE_MESSAGE
    assert "reindex" not in result.stderr
    assert "workspace lock" not in result.stderr


def test_a_non_contention_operational_error_in_a_locked_verb_is_not_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    def _raise(*args: object, **kwargs: object) -> None:
        raise make_non_lock_operational_error("disk I/O error")

    monkeypatch.setattr(
        "openkos.cli.main.application_lifecycle.resolve_concept_path", _raise
    )

    result = runner.invoke(app, ["relate", "a", "references", "b"])

    assert result.exit_code != 3
    assert isinstance(result.exception, sqlite3.OperationalError)


# --- `--wait <seconds>` (#1137) -------------------------------------------------

_TIMED_HOLDER = textwrap.dedent(
    """
    import sys
    import time
    from pathlib import Path
    from openkos import lock

    with lock.workspace_lock(Path(sys.argv[1])):
        print("ACQUIRED", flush=True)
        time.sleep(float(sys.argv[2]))
    """
)


def _locked_command_names() -> list[str]:
    return sorted(
        name
        for name, callback in _registered_commands().items()
        if getattr(callback, "__openkos_locked_command__", None) is not None
    )


def _option_names(command: str) -> set[str]:
    info = next(
        i
        for i in app.registered_commands
        if (i.name or (i.callback.__name__ if i.callback else ""))
        .replace("_", "-")
        .removesuffix("-cmd")
        == command
    )
    import typer.main

    click_command = typer.main.get_command_from_info(
        info,
        pretty_exceptions_short=True,
        rich_markup_mode=None,
    )
    return {opt for param in click_command.params for opt in param.opts}


def test_every_locked_verb_accepts_wait_and_no_other_command_does() -> None:
    """The guard adds the option, so the roster cannot drift from the lock."""
    locked = _locked_command_names()
    assert locked  # a vacuous pass would prove nothing

    with_wait = {c for c in _registered_commands() if "--wait" in _option_names(c)}

    assert with_wait == set(locked)


@pytest.mark.parametrize("value", ["abc", "-1", "1.5", "3601", ""])
def test_a_bad_wait_value_is_a_usage_error_before_any_work(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    # CI makes rich force colour and wrap at 80 columns, which splits the
    # message with escape sequences; pin the rendering so the exact text is
    # what is asserted, locally and in CI alike.
    for name in ("FORCE_COLOR", "GITHUB_ACTIONS", "TTY_COMPATIBLE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("COLUMNS", "200")

    result = runner.invoke(app, ["relate", "a", "references", "b", "--wait", value])

    assert result.exit_code == 2
    assert "Invalid value for '--wait'" in result.output
    assert "refusing to run" not in result.stderr


def test_wait_zero_equals_the_default_fail_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

        default = runner.invoke(app, ["relate", "a", "references", "b"])
        zero = runner.invoke(app, ["relate", "a", "references", "b", "--wait", "0"])

        assert default.exit_code == zero.exit_code == 3
        assert zero.stderr == default.stderr
        assert "waiting" not in zero.stderr
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_an_expired_wait_exits_3_with_one_waiting_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

        result = runner.invoke(app, ["relate", "a", "references", "b", "--wait", "1"])

        assert result.exit_code == 3
        assert "another OpenKOS process is modifying this workspace" in result.stderr
        waiting = [ln for ln in result.stderr.splitlines() if "waiting" in ln]
        assert waiting == [
            "openkos relate: the workspace is busy; waiting up to 1 s for it."
        ]
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_a_wait_succeeds_once_the_holder_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    free = runner.invoke(app, ["relate", "a", "references", "b"])
    assert free.exit_code != 3, free.stderr
    holder = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", _TIMED_HOLDER, str(tmp_path), "1"],
        stdout=subprocess.PIPE,
        text=True,
        cwd=_REPO_ROOT,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        result = runner.invoke(app, ["relate", "a", "references", "b", "--wait", "10"])

        # The body ran (not a lock refusal) and ended as it does when unheld.
        assert result.exit_code == free.exit_code
        assert "refusing to run" not in result.stderr
        assert "waiting up to 10 s" in result.stderr
    finally:
        holder.wait(timeout=60)
