"""Unit tests for `userstate.py`: the per-user state, lock and log directories
(#1137, ADR-0036).

Each OS's layout is exercised through the injected `platform`, so a Linux CI
runner pins the macOS and Windows paths too. The two properties that carry
risk are the asymmetry the module exists for: the LOCK directory ignores the
environment on POSIX (a rendezvous must not depend on it), while the LOG
directory honours `XDG_STATE_HOME`.
"""

import os
import sys
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from openkos import userstate

pytestmark = pytest.mark.cross_platform_smoke

_HOME = PurePosixPath("/home/someone")
_WIN_HOME = PureWindowsPath("C:/Users/someone")


@pytest.fixture(autouse=True)
def _private_lock_directory() -> None:
    """Override the suite-wide autouse fixture of the same name: these tests
    pin the REAL `userstate.locks_dir`, which that fixture replaces."""


@pytest.fixture(autouse=True)
def _private_log_directory() -> None:
    """Override the suite-wide autouse fixture of the same name: these tests
    pin the REAL `userstate.log_dir`, which that fixture replaces."""


def test_linux_state_and_locks_dirs() -> None:
    assert userstate.state_dir("linux", home=_HOME) == _HOME / ".local/state/openkos"
    assert (
        userstate.locks_dir("linux", home=_HOME) == _HOME / ".local/state/openkos/locks"
    )


def test_other_posix_uses_the_linux_layout() -> None:
    assert userstate.state_dir("freebsd14", home=_HOME) == userstate.state_dir(
        "linux", home=_HOME
    )


def test_macos_state_and_locks_dirs() -> None:
    root = _HOME / "Library/Application Support/openkos"
    assert userstate.state_dir("darwin", home=_HOME) == root
    assert userstate.locks_dir("darwin", home=_HOME) == root / "locks"


def test_windows_state_and_locks_dirs_use_localappdata() -> None:
    env = {"LOCALAPPDATA": "C:\\Users\\someone\\AppData\\Local"}
    root = PureWindowsPath("C:/Users/someone/AppData/Local/openkos")
    assert userstate.state_dir("win32", home=_WIN_HOME, environ=env) == root
    assert userstate.locks_dir("win32", home=_WIN_HOME, environ=env) == root / "locks"


def test_windows_without_localappdata_falls_back_under_home() -> None:
    assert userstate.state_dir("win32", home=_WIN_HOME, environ={}) == (
        _WIN_HOME / "AppData" / "Local" / "openkos"
    )


@pytest.mark.parametrize(
    ("platform", "flavour"),
    [
        ("linux", PurePosixPath),
        ("darwin", PurePosixPath),
        ("win32", PureWindowsPath),
    ],
)
def test_every_result_uses_the_injected_platforms_path_semantics(
    platform: str, flavour: type[PurePath]
) -> None:
    """Whether `/xdg/state` is absolute is a property of the platform being
    described, not of the host running the test: results are pure paths in the
    injected platform's flavour, whatever the host is."""
    home = _WIN_HOME if platform == "win32" else _HOME
    env = {"LOCALAPPDATA": "C:/local", "XDG_STATE_HOME": "/xdg/state"}
    for function in (userstate.state_dir, userstate.locks_dir, userstate.log_dir):
        result = function(platform, home=home, environ=env)
        assert type(result) is flavour


def test_linux_logs_honour_xdg_state_home() -> None:
    env = {"XDG_STATE_HOME": "/xdg/state"}
    assert userstate.log_dir("linux", home=_HOME, environ=env) == PurePosixPath(
        "/xdg/state/openkos/logs"
    )


def test_linux_logs_default_without_xdg_state_home() -> None:
    assert userstate.log_dir("linux", home=_HOME, environ={}) == (
        _HOME / ".local/state/openkos/logs"
    )


@pytest.mark.parametrize("value", ["", "relative/state"])
def test_linux_logs_ignore_an_empty_or_relative_xdg_state_home(value: str) -> None:
    env = {"XDG_STATE_HOME": value}
    assert userstate.log_dir("linux", home=_HOME, environ=env) == (
        _HOME / ".local/state/openkos/logs"
    )


def test_macos_and_windows_log_dirs() -> None:
    env = {"LOCALAPPDATA": "C:/local", "XDG_STATE_HOME": "/xdg"}
    assert userstate.log_dir("darwin", home=_HOME, environ=env) == (
        _HOME / "Library/Logs/openkos"
    )
    assert userstate.log_dir("win32", home=_WIN_HOME, environ=env) == (
        PureWindowsPath("C:/local/openkos/Logs")
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX account database")
def test_the_lock_dir_ignores_the_environment_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two processes of one user with different `HOME` / `XDG_STATE_HOME` must
    resolve the SAME lock directory, or each holds a lock the other cannot see.
    The account database's home is a sentinel no environment variable contains."""
    sentinel = "/sentinel-account-home"
    monkeypatch.setattr("pwd.getpwuid", lambda uid: SimpleNamespace(pw_dir=sentinel))
    expected = PurePosixPath(sentinel) / ".local/state/openkos/locks"

    monkeypatch.setenv("HOME", "/env-one")
    monkeypatch.setenv("XDG_STATE_HOME", "/env-one/xdg")
    first = userstate.locks_dir("linux")
    monkeypatch.setenv("HOME", "/env-two")
    monkeypatch.setenv("XDG_STATE_HOME", "/env-two/xdg")
    second = userstate.locks_dir("linux")

    assert first == second == expected


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX account database")
def test_the_account_home_is_looked_up_for_the_effective_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def fake_getpwuid(uid: int) -> SimpleNamespace:
        seen.append(uid)
        return SimpleNamespace(pw_dir="/acct")

    monkeypatch.setattr("pwd.getpwuid", fake_getpwuid)
    monkeypatch.setattr(os, "geteuid", lambda: 4242)

    assert userstate.state_dir("linux") == PurePosixPath("/acct/.local/state/openkos")
    assert seen == [4242]
