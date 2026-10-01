"""Per-user state, lock and log directories (#1137, ADR-0036).

A leaf module: it imports only the standard library, so `lock` (itself a leaf)
and any later adapter can use it without a layering dependency.

Everything here is a PURE resolution: no directory is created, nothing is
read from the workspace. Creating and verifying a directory is the caller's
job, because only the caller knows what trust it needs (the lock refuses a
directory that is not owner-only; a log directory merely needs to exist).

**The lock directory ignores the environment on POSIX.** A lock is a
rendezvous: two processes of one user that resolved different directories from
different environments would each hold a lock the other cannot see. A daemon
launched by a service manager commonly sees a different `HOME` and
`XDG_STATE_HOME` than a login shell, so the home directory is read from the
account database for the effective uid, never from `$HOME`, and
`$XDG_STATE_HOME` plays no part. **Logs are not a rendezvous**, so they honour
`$XDG_STATE_HOME` as the XDG specification asks.

Every function takes the platform (and, where the environment legitimately
matters, the environment and home) as an optional argument so each OS's
layout is testable from any OS; the defaults read the running process.

The path logic runs in the injected platform's own path semantics
(`PurePosixPath` for POSIX, `PureWindowsPath` for `win32`), never the host's,
and the functions return those pure paths: whether `/xdg/state` is absolute is
a property of the platform being described, not of the machine running the
test. A caller that touches the real filesystem wraps the result in `Path`.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import PurePath, PurePosixPath, PureWindowsPath

APP_DIR_NAME = "openkos"
LOCKS_DIR_NAME = "locks"


def _platform(platform: str | None) -> str:
    return sys.platform if platform is None else platform


def _flavour(platform: str) -> type[PurePath]:
    return PureWindowsPath if platform == "win32" else PurePosixPath


def _account_home(platform: str) -> PurePath:
    """The effective uid's home directory from the account database (POSIX),
    never `$HOME`. Windows has no account database home; the user profile is
    used there, and only as the fallback when `%LOCALAPPDATA%` is unset."""
    if sys.platform == "win32":  # pragma: no cover - not exercised by Linux CI
        from pathlib import Path

        return _flavour(platform)(str(Path.home()))
    import pwd

    return _flavour(platform)(pwd.getpwuid(os.geteuid()).pw_dir)


def _resolve_home(platform: str, home: PurePath | None) -> PurePath:
    if home is None:
        return _account_home(platform)
    return _flavour(platform)(home)


def _windows_local_app_data(environ: Mapping[str, str], home: PurePath) -> PurePath:
    configured = environ.get("LOCALAPPDATA")
    if configured:
        return PureWindowsPath(configured)
    return home / "AppData" / "Local"


def state_dir(
    platform: str | None = None,
    *,
    home: PurePath | None = None,
    environ: Mapping[str, str] | None = None,
) -> PurePath:
    """The account's OpenKOS state directory.

    `~/.local/state/openkos` on Linux and other POSIX systems,
    `~/Library/Application Support/openkos` on macOS, `%LOCALAPPDATA%\\openkos`
    on Windows. On POSIX `environ` is never consulted and `home` defaults to
    the account database's, so `$HOME` and `$XDG_STATE_HOME` cannot move it.
    """
    name = _platform(platform)
    resolved_home = _resolve_home(name, home)
    if name == "win32":
        env = os.environ if environ is None else environ
        return _windows_local_app_data(env, resolved_home) / APP_DIR_NAME
    if name == "darwin":
        return resolved_home / "Library" / "Application Support" / APP_DIR_NAME
    return resolved_home / ".local" / "state" / APP_DIR_NAME


def locks_dir(
    platform: str | None = None,
    *,
    home: PurePath | None = None,
    environ: Mapping[str, str] | None = None,
) -> PurePath:
    """The directory holding one lock file per workspace."""
    return state_dir(platform, home=home, environ=environ) / LOCKS_DIR_NAME


def log_dir(
    platform: str | None = None,
    *,
    home: PurePath | None = None,
    environ: Mapping[str, str] | None = None,
) -> PurePath:
    """The directory holding one log file per workspace.

    `${XDG_STATE_HOME:-~/.local/state}/openkos/logs` on Linux and other POSIX
    systems (a relative `$XDG_STATE_HOME` is ignored, as the XDG specification
    requires), `~/Library/Logs/openkos` on macOS, `%LOCALAPPDATA%\\openkos\\Logs`
    on Windows.
    """
    name = _platform(platform)
    env = os.environ if environ is None else environ
    resolved_home = _resolve_home(name, home)
    if name == "win32":
        return _windows_local_app_data(env, resolved_home) / APP_DIR_NAME / "Logs"
    if name == "darwin":
        return resolved_home / "Library" / "Logs" / APP_DIR_NAME
    xdg = env.get("XDG_STATE_HOME", "")
    xdg_base = PurePosixPath(xdg) if xdg else None
    if xdg_base is not None and xdg_base.is_absolute():
        base: PurePath = xdg_base
    else:
        base = resolved_home / ".local" / "state"
    return base / APP_DIR_NAME / "logs"
