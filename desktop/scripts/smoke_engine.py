"""Smoke-check one frozen engine: the engine contract, one process, sqlite-vec.

Usage: python desktop/scripts/smoke_engine.py <path/to/openkos-engine>

Against a fresh temporary workspace made by the engine's own `init`, this
checks that the frozen engine:

1. starts with a token on stdin, writes one ready line and answers
   `GET /v1/status` with that token;
2. runs as one process: its process group holds only itself and it has no
   child, so no launcher sits between the shell and the engine;
3. exits with status 0 when its stdin closes;
4. exits when the process holding its stdin is killed with SIGKILL, which is
   how a force-quit shell leaves no orphan;
5. loads the sqlite-vec extension, as `doctor` reports it.

Standard library only, so any Python runs it, and no model is needed: `doctor`
runs with `OLLAMA_HOST` pointed at a closed port and only its vector-extension
line is read. Exits 0 when every check passes, 1 naming the first that fails.
"""

import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import IO

READY_TIMEOUT_SECONDS = 30.0
EXIT_TIMEOUT_SECONDS = 10.0
DOCTOR_TIMEOUT_SECONDS = 120.0
VECTOR_EXTENSION_LINE = "[PASS] Vector extension loadable"

# A closed port: `doctor`'s model checks fail fast instead of reaching a model.
CLOSED_OLLAMA_HOST = "127.0.0.1:9"

# `git config` identity for the engine's `init` commit. GIT_AUTHOR_* would not
# satisfy git's identity check; GIT_CONFIG_* does, without touching any file.
GIT_IDENTITY = {
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "user.name",
    "GIT_CONFIG_VALUE_0": "openkos-smoke",
    "GIT_CONFIG_KEY_1": "user.email",
    "GIT_CONFIG_VALUE_1": "smoke@example.invalid",
}

# The body of the holder process of check 4: it starts the engine with its
# stdin piped from itself, hands over the token, waits for the ready line,
# reports the engine's PID and then waits to be killed.
HOLDER = """
import subprocess, sys, time
engine = subprocess.Popen(
    [sys.argv[1], "serve", "--workspace", sys.argv[2]],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    start_new_session=True,
)
engine.stdin.write((sys.argv[3] + "\\n").encode())
engine.stdin.flush()
engine.stdout.readline()
print(engine.pid, flush=True)
while True:
    time.sleep(1)
"""


class SmokeFailure(Exception):
    """One check failed; the message says which and why."""


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    env.update(GIT_IDENTITY)
    env["OLLAMA_HOST"] = CLOSED_OLLAMA_HOST
    return env


def _read_line(stream: IO[bytes], timeout: float) -> bytes | None:
    """One line from `stream`, or None when none arrives within `timeout`."""
    lines: queue.Queue[bytes] = queue.Queue()
    threading.Thread(target=lambda: lines.put(stream.readline()), daemon=True).start()
    try:
        return lines.get(timeout=timeout)
    except queue.Empty:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _gone_within(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _processes() -> list[tuple[int, int, int]]:
    """Every process as (pid, ppid, pgid)."""
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,pgid="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = []
    for line in listing.splitlines():
        pid, ppid, pgid = (int(field) for field in line.split())
        rows.append((pid, ppid, pgid))
    return rows


def make_workspace(engine: Path, parent: Path) -> Path:
    # Resolved: the engine refuses a workspace path with a symlinked segment,
    # and the temporary directory is one on macOS (`/var` -> `/private/var`).
    workspace = (parent / "workspace").resolve()
    workspace.mkdir()
    result = subprocess.run(
        [str(engine), "init"],
        cwd=workspace,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=DOCTOR_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise SmokeFailure(f"init exited {result.returncode}: {result.stderr}")
    return workspace


def check_serve(engine: Path, workspace: Path) -> None:
    """Checks 1 to 3: ready line, /v1/status, one process, exit on EOF."""
    token = secrets.token_hex(32)
    started = time.monotonic()
    process = subprocess.Popen(
        [str(engine), "serve", "--workspace", str(workspace)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_environment(),
        start_new_session=True,
    )
    if process.stdin is None or process.stdout is None:
        raise SmokeFailure("the engine's stdin and stdout were not piped")
    try:
        process.stdin.write(f"{token}\n".encode("ascii"))
        process.stdin.flush()
        line = _read_line(process.stdout, READY_TIMEOUT_SECONDS)
        ready_seconds = time.monotonic() - started
        if not line:
            raise SmokeFailure(
                f"no ready line within {READY_TIMEOUT_SECONDS:.0f} s"
                f" (exit status {process.poll()})"
            )
        ready = json.loads(line)
        if ready.get("event") != "ready":
            raise SmokeFailure(f"the first stdout line is not a ready event: {line!r}")
        url = f"http://{ready['host']}:{ready['port']}{ready['base_path']}/status"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            status_code = response.status
            body = json.loads(response.read())
        status_seconds = time.monotonic() - started
        if status_code != 200 or "concepts" not in body:
            raise SmokeFailure(f"/v1/status answered {status_code}: {body!r}")
        print(
            f"ok   serve: ready in {ready_seconds:.3f} s,"
            f" /v1/status 200 in {status_seconds:.3f} s"
        )

        rows = _processes()
        group = sorted(pid for pid, _, pgid in rows if pgid == process.pid)
        children = sorted(pid for pid, ppid, _ in rows if ppid == process.pid)
        if group != [process.pid] or children:
            raise SmokeFailure(
                f"the engine is not one process: group {group}, children {children}"
            )
        print(f"ok   one process: pid {process.pid}, no child, alone in its group")

        process.stdin.close()
        try:
            code = process.wait(timeout=EXIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise SmokeFailure(
                f"the engine outlived stdin EOF by {EXIT_TIMEOUT_SECONDS:.0f} s"
            ) from None
        if code != 0:
            raise SmokeFailure(f"the engine exited {code} on stdin EOF")
        print("ok   stdin EOF: exit 0")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def check_killed_parent(engine: Path, workspace: Path) -> None:
    """Check 4: SIGKILL the process holding stdin; the engine must exit."""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            HOLDER,
            str(engine),
            str(workspace),
            secrets.token_hex(32),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_environment(),
    )
    if holder.stdout is None:
        raise SmokeFailure("the holder's stdout was not piped")
    engine_pid: int | None = None
    try:
        line = _read_line(holder.stdout, READY_TIMEOUT_SECONDS)
        if not line:
            raise SmokeFailure("the holder never reported a ready engine")
        engine_pid = int(line)
        holder.kill()
        holder.wait()
        if not _gone_within(engine_pid, EXIT_TIMEOUT_SECONDS):
            raise SmokeFailure(
                f"orphaned engine {engine_pid}: alive {EXIT_TIMEOUT_SECONDS:.0f} s"
                " after its parent was killed"
            )
        print("ok   killed parent: the engine exited")
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
        if engine_pid is not None and _alive(engine_pid):
            os.killpg(engine_pid, signal.SIGKILL)


def check_vector_extension(engine: Path, workspace: Path) -> None:
    """Check 5: `doctor` reports sqlite-vec as loadable."""
    result = subprocess.run(
        [str(engine), "doctor"],
        cwd=workspace,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=DOCTOR_TIMEOUT_SECONDS,
        check=False,
    )
    if VECTOR_EXTENSION_LINE not in result.stdout.splitlines():
        raise SmokeFailure(
            f"doctor did not report {VECTOR_EXTENSION_LINE!r}:\n{result.stdout}"
        )
    print("ok   sqlite-vec: loadable")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    engine = Path(argv[1]).absolute()
    if not engine.is_file():
        print(f"smoke: {engine} is not a file", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="openkos-smoke-") as scratch:
        try:
            workspace = make_workspace(engine, Path(scratch))
            check_serve(engine, workspace)
            check_killed_parent(engine, workspace)
            check_vector_extension(engine, workspace)
        except SmokeFailure as failure:
            print(f"FAIL {failure}", file=sys.stderr)
            return 1
    print(f"smoke: {engine} passed every check")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
