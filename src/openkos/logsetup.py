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
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkos import config, userstate
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


def workspace_record_path_for(root: Path) -> Path:
    """The sidecar naming the workspace a daemon log belongs to (#1334). The log
    is named by a hash that cannot be reversed, so without this record nothing
    can say whether a log's workspace still exists."""
    return Path(userstate.log_dir()) / f"{workspace_digest(root)}.workspace"


def _write_workspace_record(root: Path) -> None:
    """Atomically record the workspace's resolved path beside its log: the path
    and nothing else, owner-only, written to a temporary name and renamed."""
    record = workspace_record_path_for(root)
    temporary = record.with_name(record.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(os.path.realpath(root))
    temporary.replace(record)


_GROUP_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.(?:log(?:\.[0-9]+)?|workspace)$")
"""The ONLY names cleanup ever touches: what `configure_logging` writes -- the
current log, its numbered rotations and the workspace record. Anything else in
the directory is somebody else's."""

_MAX_RECORD_BYTES = 4096


@dataclass(frozen=True)
class StaleLog:
    """One workspace's log files whose recorded workspace no longer exists."""

    digest: str
    workspace: Path
    files: tuple[Path, ...]
    bytes: int


@dataclass(frozen=True)
class LogScan:
    stale: tuple[StaleLog, ...]
    """Groups cleanup would remove."""
    legacy_files: int
    """Log files nothing attributes to a workspace (no valid record). Never
    removed automatically."""
    legacy_bytes: int


def _read_record(path: Path, digest: str) -> Path | None:
    """The workspace a record names, or `None` when it cannot be trusted: not a
    regular file, unreadable, malformed, not absolute, or a path that does not
    hash to the name it sits under."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(_MAX_RECORD_BYTES + 1)
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(raw) > _MAX_RECORD_BYTES or not text or "\x00" in text:
        return None
    if not Path(text).is_absolute():
        return None
    workspace = Path(text)
    if workspace_digest(workspace) != digest:
        return None
    return workspace


def scan_logs(current_root: Path) -> LogScan:
    """Classify the per-user log directory without changing it. A group is STALE
    only when it carries a trustworthy record naming a workspace that is gone;
    the current workspace, a workspace that still exists, a group holding a
    symlink and every name that is not ours are never stale. A log with no
    trustworthy record is LEGACY: counted, never removed."""
    directory = Path(userstate.log_dir())
    groups: dict[str, list[Path]] = {}
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return LogScan((), 0, 0)
    for entry in entries:
        match = _GROUP_NAME.match(entry.name)
        if match is not None:
            groups.setdefault(match["digest"], []).append(Path(entry.path))
    current = workspace_digest(current_root)
    stale: list[StaleLog] = []
    legacy_files = 0
    legacy_bytes = 0
    for digest, paths in sorted(groups.items()):
        if digest == current:
            continue
        logs = [p for p in paths if not p.name.endswith(".workspace")]
        if any(p.is_symlink() or not p.is_file() for p in paths):
            continue  # never follow, never half-delete a group
        record = _read_record(directory / f"{digest}.workspace", digest)
        if record is None:
            legacy_files += len(logs)
            legacy_bytes += sum(p.stat().st_size for p in logs)
            continue
        if not config.workspace_absent(record):
            continue
        stale.append(
            StaleLog(
                digest=digest,
                workspace=record,
                files=tuple(paths),
                bytes=sum(p.stat().st_size for p in paths),
            )
        )
    return LogScan(tuple(stale), legacy_files, legacy_bytes)


def remove_stale_logs(current_root: Path, report: Callable[[str], None]) -> int:
    """Delete every stale group `scan_logs` finds, reporting each file removed
    through `report` -- a deletion is never silent. Returns the files removed. A
    file that cannot be removed is reported and left; cleanup never raises."""
    removed = 0
    for group in scan_logs(current_root).stale:
        for path in group.files:
            try:
                path.unlink()
            except OSError as exc:
                report(
                    f"openkos daemon: could not remove '{path.name}' "
                    f"({type(exc).__name__}); leaving it."
                )
                continue
            removed += 1
            report(
                f"openkos daemon: removed log '{path.name}' -- its workspace "
                f"'{group.workspace}' no longer exists."
            )
    return removed


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
    _write_workspace_record(root)
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
