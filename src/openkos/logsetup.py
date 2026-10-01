"""The one logging setup for every OpenKOS entry point (#1139).

Three modes, one place:

- `cli`: a bare-message handler on the root logger, stderr, level WARNING. This
  is what the stdlib's last-resort handler already did for an unconfigured
  process, so no verb's stdout or stderr changes.
- `mcp`: the `openkos.mcp` logger gets its own stderr handler at INFO and stops
  propagating (so the CLI root handler never prints a record twice). Nothing is
  ever written to stdout, which is the JSON-RPC channel.
- `daemon`: a rotating file (1 MiB x 5, UTF-8) per workspace, named by the
  sha256 of the workspace's real path, in the per-user log directory
  (`userstate.log_dir`) -- never inside the workspace, so never under `bundle/`.

Records carry ids, paths, counts, outcome codes and durations; never document
text, model output or rationale. Callers own the first part of that promise by
what they pass to the logger. This module owns the part a caller cannot: the
daemon's persisted file never carries a traceback's *message* (an exception
message can quote a document), only the exception type. The stderr modes keep
full tracebacks; they are the user's own terminal, not a stored artifact.

The stderr handler resolves `sys.stderr` at emit time, not at construction, so
a stream swapped after configuration (a test runner, a redirect) is honoured.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Literal

from openkos import userstate
from openkos.lock import workspace_digest

Mode = Literal["cli", "daemon", "mcp"]

MAX_LOG_BYTES = 1024 * 1024
LOG_BACKUPS = 5
_APP_LOGGER = "openkos"
_MCP_LOGGER = "openkos.mcp"

_installed: dict[str, tuple[logging.Logger, logging.Handler]] = {}
_saved_mcp: tuple[int, bool] | None = None
_saved_app: tuple[int, bool] | None = None


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """A stream handler bound to whatever `sys.stderr` is at emit time."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter("%(message)s"))

    @property
    def stream(self):  # type: ignore[no-untyped-def]
        return sys.stderr

    @stream.setter
    def stream(self, value: object) -> None:
        """Ignored: the stream is always the live `sys.stderr`."""


class _TypeOnlyFormatter(logging.Formatter):
    """Persisted-log formatter: an exception is its type, never its message."""

    def formatException(self, ei) -> str:  # type: ignore[no-untyped-def]
        exc_type = ei[0]
        return exc_type.__name__ if exc_type is not None else "Exception"


def log_path_for(root: Path) -> Path:
    """The daemon's log file for the workspace at `root` (a pure resolution)."""
    return Path(userstate.log_dir()) / f"{workspace_digest(root)}.log"


def is_configured(mode: Mode) -> bool:
    return mode in _installed


def _install(mode: str, logger: logging.Logger, handler: logging.Handler) -> None:
    _remove(mode)
    logger.addHandler(handler)
    _installed[mode] = (logger, handler)


def _remove(mode: str) -> None:
    entry = _installed.pop(mode, None)
    if entry is not None:
        logger, handler = entry
        logger.removeHandler(handler)
        handler.close()


def _private_dir(directory: Path) -> None:
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    if os.name == "posix":
        directory.chmod(0o700)


def configure_logging(
    mode: Mode, *, root: Path | None = None
) -> logging.handlers.RotatingFileHandler | None:
    """Install the handler for `mode`; idempotent per mode.

    Returns the rotating file handler in `daemon` mode (so its limits are
    inspectable), `None` otherwise. `daemon` requires the workspace `root`.
    """
    global _saved_mcp, _saved_app
    if mode == "cli":
        logger = logging.getLogger()
        handler = _StderrHandler()
        handler.setLevel(logging.WARNING)
        _install("cli", logger, handler)
        return None
    if mode == "mcp":
        mcp_logger = logging.getLogger(_MCP_LOGGER)
        if "mcp" not in _installed:
            _saved_mcp = (mcp_logger.level, mcp_logger.propagate)
        handler = _StderrHandler()
        mcp_logger.setLevel(logging.INFO)
        _install("mcp", mcp_logger, handler)
        mcp_logger.propagate = False
        return None
    if root is None:
        raise ValueError("daemon logging needs the workspace root")
    path = log_path_for(root)
    _private_dir(path.parent)
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(
        _TypeOnlyFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    file_handler.setLevel(logging.INFO)
    app_logger = logging.getLogger(_APP_LOGGER)
    if "daemon" not in _installed:
        _saved_app = (app_logger.level, app_logger.propagate)
    app_logger.setLevel(logging.INFO)
    _install("daemon", app_logger, file_handler)
    app_logger.propagate = False
    return file_handler


def reset_logging() -> None:
    """Remove every handler this module installed and restore the loggers it
    touched. Closes the daemon file so the log is complete on disk."""
    global _saved_mcp, _saved_app
    had_mcp = "mcp" in _installed
    had_daemon = "daemon" in _installed
    for mode in list(_installed):
        _remove(mode)
    if had_mcp and _saved_mcp is not None:
        mcp_logger = logging.getLogger(_MCP_LOGGER)
        mcp_logger.setLevel(_saved_mcp[0])
        mcp_logger.propagate = _saved_mcp[1]
    if had_daemon and _saved_app is not None:
        app_logger = logging.getLogger(_APP_LOGGER)
        app_logger.setLevel(_saved_app[0])
        app_logger.propagate = _saved_app[1]
    _saved_mcp = None
    _saved_app = None
