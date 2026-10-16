"""Real-process lifecycle tests for `openkos serve` (local-http-api, S5b).

The server is launched as a child interpreter through the real CLI entry. The
HTTP probe runs in a SECOND child Python process, because the unit suite's
network guard (`tests/unit/conftest.py`) refuses loopback `connect` in this
process. POSIX only: signal and pipe semantics differ on Windows, and these
tests are not part of the cross-platform smoke subset.

Every read is bounded and every child is killed in `finally`; the ready line
is read rather than slept for.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-only: signals and stdin pipe semantics"
)

TOKEN = "t" * 40
TIMEOUT = 60.0
_ENTRY = "import sys; from openkos.cli.main import app; sys.exit(app())"

_PROBE = """
import sys, urllib.request, urllib.error
url, token = sys.argv[1], sys.argv[2]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request(url)
if token:
    request.add_header("Authorization", "Bearer " + token)
try:
    print(opener.open(request, timeout=20).status)
except urllib.error.HTTPError as exc:
    print(exc.code)
"""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("OPENKOS_API_TOKEN", "OPENKOS_TOKEN", "OPENKOS_BEARER_TOKEN"):
        env.pop(key, None)
    return env


def _spawn(
    args: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-I", "-c", _ENTRY, *args],
        cwd=cwd,
        env=_env() if env is None else env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _readline(stream: IO[bytes], timeout: float = TIMEOUT) -> bytes:
    """One bounded `readline`: a reader thread, so a hung child cannot hang us."""
    box: queue.Queue[bytes] = queue.Queue()
    threading.Thread(target=lambda: box.put(stream.readline()), daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError("timed out waiting for a line from the child") from None


@contextmanager
def _server(workspace: Path) -> Iterator[tuple[subprocess.Popen[bytes], int]]:
    proc = _spawn(["serve", "--workspace", str(workspace)], workspace)
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(TOKEN.encode() + b"\n")
        proc.stdin.flush()
        line = _readline(proc.stdout)
        ready = json.loads(line)
        assert ready["event"] == "ready"
        yield proc, int(ready["port"])
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=TIMEOUT)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def _probe(port: int, token: str) -> int:
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [
            sys.executable,
            "-I",
            "-c",
            _PROBE,
            f"http://127.0.0.1:{port}/v1/health",
            token,
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=_env(),
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip())


def _finish(proc: subprocess.Popen[bytes]) -> tuple[int, bytes, bytes]:
    assert proc.stdout is not None
    assert proc.stderr is not None
    code = proc.wait(timeout=TIMEOUT)
    return code, proc.stdout.read(), proc.stderr.read()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-I", "-c", _ENTRY, "init"],
        cwd=tmp_path,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    return tmp_path


def test_token_on_stdin_gives_one_ready_line_and_an_authenticated_server(
    workspace: Path,
) -> None:
    with _server(workspace) as (proc, port):
        assert port > 0
        assert _probe(port, TOKEN) == 200
        assert _probe(port, "") == 401
        assert proc.poll() is None


def test_closing_stdin_exits_zero_with_no_further_stdout(workspace: Path) -> None:
    with _server(workspace) as (proc, _port):
        assert proc.stdin is not None
        proc.stdin.close()
        code, out, _err = _finish(proc)
    assert code == 0
    assert out == b""


def test_sigterm_exits_zero(workspace: Path) -> None:
    with _server(workspace) as (proc, port):
        assert _probe(port, TOKEN) == 200
        proc.send_signal(signal.SIGTERM)
        code, out, _err = _finish(proc)
    assert code == 0, _err
    assert out == b""


def test_eof_before_a_token_exits_two_with_no_ready_line(workspace: Path) -> None:
    proc = _spawn(["serve", "--workspace", str(workspace)], workspace)
    try:
        assert proc.stdin is not None
        proc.stdin.close()
        code, out, err = _finish(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=TIMEOUT)
    assert code == 2
    assert out == b""
    assert b"token" in err


def test_a_bad_workspace_exits_one_with_empty_stdout(tmp_path: Path) -> None:
    proc = _spawn(["serve", "--workspace", str(tmp_path / "missing")], tmp_path)
    try:
        assert proc.stdin is not None
        proc.stdin.write(TOKEN.encode() + b"\n")
        proc.stdin.close()
        code, out, _err = _finish(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=TIMEOUT)
    assert code == 1
    assert out == b""


@pytest.mark.parametrize("channel", ["argv", "environment"])
def test_a_token_outside_stdin_is_never_accepted(workspace: Path, channel: str) -> None:
    env = _env()
    args = ["serve", "--workspace", str(workspace)]
    if channel == "argv":
        args += ["--token", TOKEN]
    else:
        for key in ("OPENKOS_API_TOKEN", "OPENKOS_TOKEN", "OPENKOS_BEARER_TOKEN"):
            env[key] = TOKEN
    proc = _spawn(args, workspace, env)
    try:
        assert proc.stdin is not None
        proc.stdin.close()
        code, out, _err = _finish(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=TIMEOUT)
    assert code == 2
    assert out == b""
