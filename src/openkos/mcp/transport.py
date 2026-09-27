"""Stdio framing and stdout ownership (design Decision 10, ADR-0027).

Two hazards this module owns structurally: any byte written to stdout that
is not a protocol message corrupts the JSON-RPC stream, and a naive
`\\n`-only line split leaves a trailing `\\r` on a Windows-style client's
frame. `claim_stdio` closes the first hazard by moving the real stdout to a
private descriptor and pointing file descriptor 1 -- and `sys.stdout` --
at stderr for the whole time the server is serving, so a stray `print`, a
`typer.echo`, or a fd-level `os.write(1, ...)` below this boundary all land
on stderr instead. `decode_line`/`encode_message` close the second: framing
is byte-exact newline-delimited JSON-RPC 2.0, with no embedded raw newline
and no non-finite float smuggled through as `NaN`/`Infinity`.

Everything here is stdlib only: `asyncio`, `json`, `logging`, `os`,
`threading` (ADR-0027 -- no dependency is added for this).
"""

import asyncio
import json
import logging
import os
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import BinaryIO, NamedTuple

logger = logging.getLogger(__name__)


class StdioStreams(NamedTuple):
    """The two streams the server reads from and writes to while serving.

    `reader` is `sys.stdin.buffer`; `writer` is the private descriptor that
    still refers to the process's real stdout, after `claim_stdio` has
    pointed file descriptor 1 (and `sys.stdout`) at stderr.
    """

    reader: BinaryIO
    writer: BinaryIO


class ParseError(Exception):
    """A line could not be decoded as one JSON-RPC frame."""


@contextmanager
def claim_stdio() -> Iterator[StdioStreams]:
    """Claim the process's stdout for protocol frames only.

    Flushes `sys.stdout`, duplicates file descriptor 1 into a private
    descriptor (the real stdout), points descriptor 1 at descriptor 2
    (stderr), and rebinds `sys.stdout = sys.stderr`. From this point on,
    anything below this boundary that writes to stdout -- a bare `print`,
    `sys.stdout.write`, a C-level write to descriptor 1, or a child process
    that inherits descriptor 1 -- lands on stderr instead, because the
    redirection happens at the file-descriptor level, not only through the
    `sys.stdout` object. On exit, descriptor 1 and `sys.stdout` are
    restored and the private handle is closed.
    """
    sys.stdout.flush()
    original_stdout = sys.stdout
    private_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    writer = os.fdopen(private_fd, "wb")
    try:
        yield StdioStreams(reader=sys.stdin.buffer, writer=writer)
    finally:
        writer.flush()
        os.dup2(private_fd, 1)
        writer.close()
        sys.stdout = original_stdout


def start_reader(
    reader: BinaryIO,
    loop: asyncio.AbstractEventLoop,
    queue: "asyncio.Queue[bytes | None]",
) -> threading.Thread:
    """Start a daemon thread that reads lines from `reader` and posts them
    onto `queue`, one at a time, in order.

    Each line is posted with `loop.call_soon_threadsafe(queue.put_nowait,
    line)`. On end of input (`readline()` returns `b""`) or a read error,
    the thread posts `None` as the end-of-input sentinel and returns. A
    `RuntimeError` raised because `loop` is already closed -- which can
    happen if the loop is torn down while this thread is still running --
    is swallowed rather than propagated, so a slow reader thread can never
    crash the process on its way out.
    """

    def _post(item: bytes | None) -> bool:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            return False
        return True

    def _run() -> None:
        try:
            while True:
                try:
                    line = reader.readline()
                except OSError as exc:
                    logger.error("stdin read failed: %s", exc)
                    line = b""
                if not line:
                    return
                if not _post(line):
                    return
        finally:
            _post(None)

    thread = threading.Thread(target=_run, daemon=True, name="mcp-stdin-reader")
    thread.start()
    return thread


def _reject_non_finite(constant: str) -> float:
    raise ValueError(f"unsupported non-finite constant in frame: {constant}")


def decode_line(raw: bytes) -> object | None:
    """Decode one line of NDJSON into a value, or `None` for a blank line.

    Strips a trailing `\\r\\n` or `\\n` (checked in that order, so a
    Windows-style client's `\\r\\n` terminator does not leave a stray
    `\\r` on the decoded text). A blank line -- nothing left after
    stripping the terminator -- returns `None`, meant to be ignored by the
    caller. Any other failure -- invalid UTF-8, invalid JSON, or a JSON
    body naming the non-finite constant `NaN`, `Infinity` or `-Infinity`
    -- raises `ParseError`.
    """
    if raw.endswith(b"\r\n"):
        body = raw[:-2]
    elif raw.endswith(b"\n"):
        body = raw[:-1]
    else:
        body = raw
    if not body:
        return None

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"invalid UTF-8: {exc}") from exc

    try:
        value: object = json.loads(text, parse_constant=_reject_non_finite)
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
    return value


def encode_message(message: Mapping[str, object]) -> bytes:
    """Encode one JSON-RPC message as one newline-terminated UTF-8 frame.

    `allow_nan=False` makes a non-finite float in the payload a `ValueError`
    instead of a silently-emitted `NaN`/`Infinity` token. JSON escapes every
    newline inside a string value as `\\n` (two characters), never a raw
    newline byte, so the only raw `\\n` in the output is the one this
    function appends at the end -- which is what makes one message one
    line.
    """
    text = json.dumps(
        message, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    return text.encode("utf-8") + b"\n"


class MessageWriter:
    """Writes one frame at a time to the claimed stdout stream.

    Must be called only from the event-loop thread: a single writer is
    what keeps two responses from interleaving mid-line.
    """

    def __init__(self, writer: BinaryIO) -> None:
        self._writer = writer

    def send(self, message: Mapping[str, object]) -> None:
        self._writer.write(encode_message(message))
        self._writer.flush()
