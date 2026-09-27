"""Real-subprocess proof that `claim_stdio` owns stdout at the file
descriptor level, not only through `sys.stdout` (design Decision 10,
ADR-0027).

A same-process test cannot prove this: patching `sys.stdout` in-process
would not catch a raw `os.write(1, ...)`, which bypasses `sys.stdout`
entirely and writes straight to the file descriptor. Only a real second
process, with its own fd table, proves the redirection holds at the level
that matters. `cross_platform_smoke` (#929): this is exactly the kind of
platform-sensitive fd/console behavior that job exists to catch on macOS
and Windows, not only Linux.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.cross_platform_smoke

_CHILD = textwrap.dedent(
    """
    import os
    import sys

    from openkos.mcp import transport

    with transport.claim_stdio() as streams:
        print("stray print text")
        sys.stdout.write("stray write text\\n")
        os.write(1, b"stray os.write text\\n")
        writer = transport.MessageWriter(streams.writer)
        writer.send({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    """
)


def test_claim_stdio_redirects_stray_writes() -> None:
    # `S603` is suppressed: the argv is a fixed list -- `sys.executable`,
    # `-c`, a module-level literal script -- with no shell and no
    # caller-supplied input, matching `tests/unit/test_lock.py`'s pattern. A
    # real second process is not incidental here: fd-level redirection is
    # exactly what an in-process test cannot prove (see the module docstring).
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")

    stdout_lines = result.stdout.splitlines()
    # Stdout carries exactly one line: the one protocol frame written
    # through the claimed writer. No bare `print`, no `sys.stdout.write`,
    # and no fd-level `os.write(1, ...)` reaches it.
    assert len(stdout_lines) == 1
    assert json.loads(stdout_lines[0]) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }
    for stray in (b"stray print text", b"stray write text", b"stray os.write text"):
        assert stray not in result.stdout
        # Every stray write lands on stderr instead -- this is the fd-level
        # proof: `os.write(1, ...)` would still reach the real stdout if
        # `claim_stdio` only rebound `sys.stdout` without also
        # `os.dup2(2, 1)`.
        assert stray in result.stderr


# -- 4.10: the openkos mcp verb, end to end over a real subprocess ---------

_CHILD_ENTRYPOINT = "from openkos.cli.main import app; app()"


def _build_fixture_workspace(root: Path) -> None:
    """The minimal set `config.require_workspace`/`config.read_config`
    accept -- no `openkos init` subprocess needed for this harness."""
    bundle = root / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "index.md").write_text("# Index\n", encoding="utf-8")
    (bundle / "log.md").write_text("# Log\n", encoding="utf-8")
    (root / "openkos.yaml").write_text("{}\n", encoding="utf-8")


def _frame(payload: dict[str, object], *, terminator: bytes = b"\n") -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8") + terminator


def test_full_handshake_over_stdio(tmp_path: Path) -> None:
    """A real subprocess of `openkos mcp --workspace <fixture>`, run with a
    poisoned `OLLAMA_HOST` (so "model-free" stays checkable): `initialize`
    (framed with `\\r\\n`, per Decision 10's accepted-on-input terminator)
    -> `notifications/initialized` -> `tools/list` -> `ping` -> close
    stdin. Every captured stdout line parses as a well-formed JSON-RPC 2.0
    message, no line contains `\\r`, and the process exits 0. Covers "A
    subprocess run emits only JSON-RPC lines on stdout" and "Closing
    stdin abandons in-flight requests and exits 0" at the real-process
    level."""
    _build_fixture_workspace(tmp_path)

    stdin_bytes = (
        _frame(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            },
            terminator=b"\r\n",
        )
        + _frame({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + _frame({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    )

    env = dict(os.environ)
    env["OLLAMA_HOST"] = "http://127.0.0.1:9"

    # `S603`: fixed argv, no shell, no caller-supplied input -- matches
    # this module's other subprocess test above.
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            _CHILD_ENTRYPOINT,
            "mcp",
            "--workspace",
            str(tmp_path),
        ],
        input=stdin_bytes,
        capture_output=True,
        timeout=20,
        env=env,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"\r" not in result.stdout

    lines = result.stdout.splitlines()
    assert lines, "expected at least the initialize response"
    messages = [json.loads(line) for line in lines]
    for message in messages:
        assert message["jsonrpc"] == "2.0"

    ids = {message["id"] for message in messages if "id" in message}
    assert ids == {1, 2, 3}
