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
import subprocess
import sys
import textwrap

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
