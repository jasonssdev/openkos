"""Unit tests for `logsetup.py`: the one logging setup for the CLI, the daemon
and the MCP server (#1139, job-runtime "One Logging Setup")."""

import hashlib
import logging
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path, PurePath

import pytest
from typer.testing import CliRunner

from openkos import logsetup, userstate
from openkos.cli.main import app

SENTINEL = "SENTINEL-BODY-7f3a91c2-do-not-log"


@pytest.fixture(autouse=True)
def _clean_logging() -> Iterator[None]:
    """Every test starts and ends with no OpenKOS handler installed."""
    logsetup.reset_logging()
    yield
    logsetup.reset_logging()


@pytest.fixture
def log_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state" / "logs"
    monkeypatch.setattr(userstate, "log_dir", lambda *a, **k: directory)
    return directory


def test_daemon_log_file_is_named_by_workspace_digest(
    tmp_path: Path, log_directory: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    logsetup.configure_logging("daemon", root=root)
    logging.getLogger("openkos.jobs").info("job started calls=%d", 3)
    logsetup.reset_logging()

    digest = hashlib.sha256(os.path.realpath(root).encode()).hexdigest()
    path = log_directory / f"{digest}.log"
    assert path.read_text(encoding="utf-8").count("job started calls=3") == 1
    assert logsetup.log_path_for(root) == path


def test_daemon_log_dir_is_owner_only_and_never_in_the_bundle(
    tmp_path: Path, log_directory: Path
) -> None:
    root = tmp_path / "ws"
    (root / "bundle").mkdir(parents=True)
    before = sorted(root.rglob("*"))
    logsetup.configure_logging("daemon", root=root)
    logging.getLogger("openkos.jobs").info("x")
    logsetup.reset_logging()

    assert sorted(root.rglob("*")) == before
    assert not list((root / "bundle").rglob("*"))
    if sys.platform != "win32":
        assert stat.S_IMODE(log_directory.stat().st_mode) == 0o700


def test_daemon_rotates_at_one_mebibyte_keeping_five(
    tmp_path: Path, log_directory: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    handler = logsetup.configure_logging("daemon", root=root)
    assert handler is not None
    assert handler.maxBytes == 1024 * 1024
    assert handler.backupCount == 5
    assert handler.encoding == "utf-8"


def test_daemon_log_never_carries_exception_text(
    tmp_path: Path, log_directory: Path
) -> None:
    """A traceback's message can quote a document; the persisted log keeps the
    exception TYPE only."""
    root = tmp_path / "ws"
    root.mkdir()
    logsetup.configure_logging("daemon", root=root)
    try:
        raise RuntimeError(SENTINEL)
    except RuntimeError:
        logging.getLogger("openkos.mcp").exception("internal error for request 1")
    logsetup.reset_logging()

    text = logsetup.log_path_for(root).read_text(encoding="utf-8")
    assert "internal error for request 1" in text
    assert "RuntimeError" in text
    assert SENTINEL not in text


def test_daemon_log_is_not_echoed_to_stderr(
    tmp_path: Path, log_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    logsetup.configure_logging("daemon", root=root)
    logging.getLogger("openkos.jobs").warning("only in the file")
    assert "only in the file" not in capsys.readouterr().err


def test_daemon_requires_a_root() -> None:
    with pytest.raises(ValueError, match="root"):
        logsetup.configure_logging("daemon")


def test_cli_mode_writes_warnings_to_current_stderr_bare(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logsetup.configure_logging("cli")
    log = logging.getLogger("openkos.somewhere")
    log.info("hidden")
    log.warning("shown %s", "now")
    captured = capsys.readouterr()
    assert captured.err == "shown now\n"
    assert captured.out == ""


def test_cli_mode_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    logsetup.configure_logging("cli")
    logsetup.configure_logging("cli")
    logging.getLogger("openkos.x").warning("once")
    assert capsys.readouterr().err == "once\n"


def test_cli_callback_configures_logging_without_changing_output() -> None:
    runner = CliRunner()
    version = runner.invoke(app, ["--version"])
    assert version.stdout.count("\n") == 1
    logsetup.reset_logging()
    result = runner.invoke(app, ["status", "--help"])
    assert result.exit_code == 0
    assert logsetup.is_configured("cli")


def test_mcp_mode_keeps_stdout_clean(capsys: pytest.CaptureFixture[str]) -> None:
    logsetup.configure_logging("mcp")
    logging.getLogger("openkos.mcp").warning("advisory")
    logging.getLogger("openkos.mcp").info("request cancelled")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "advisory\nrequest cancelled\n"


def test_mcp_mode_does_not_double_print_with_the_cli_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logsetup.configure_logging("cli")
    logsetup.configure_logging("mcp")
    logging.getLogger("openkos.mcp").warning("one line")
    assert capsys.readouterr().err == "one line\n"


def test_reset_restores_mcp_logger_state() -> None:
    logger = logging.getLogger("openkos.mcp")
    level, propagate, handlers = logger.level, logger.propagate, list(logger.handlers)
    logsetup.configure_logging("mcp")
    logsetup.reset_logging()
    assert (logger.level, logger.propagate, list(logger.handlers)) == (
        level,
        propagate,
        handlers,
    )


def test_log_directory_resolves_through_userstate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "from-userstate"

    def fake_log_dir(*a: object, **k: object) -> PurePath:
        return directory

    monkeypatch.setattr(userstate, "log_dir", fake_log_dir)
    root = tmp_path / "ws"
    root.mkdir()
    assert logsetup.log_path_for(root).parent == directory
