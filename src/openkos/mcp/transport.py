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
import concurrent.futures
import json
import logging
import os
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import BinaryIO, Final, NamedTuple

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


MAX_LINE_BYTES: Final = 8 * 1024 * 1024
"""Longest stdin line (terminator included) the reader will buffer.

Stdin is a blocking `BufferedReader`, not an asyncio `StreamReader`, so no
default limit applies: a peer that never sends `\\n` would otherwise grow
memory without bound. 8 MiB is generous for JSON-RPC here -- the largest
legitimate inbound frame is a `tools/call` whose arguments are a query or a
concept id, i.e. kilobytes -- while keeping the worst case one bounded
buffer, not the whole address space."""

INBOUND_QUEUE_MAX: Final = 64
"""Capacity of the inbound line queue `serve_streams` builds. The reader
thread blocks when it is full (backpressure), so a client that floods
frames faster than the server dispatches them waits on the pipe instead of
growing memory: the worst case is this many lines of at most
`MAX_LINE_BYTES` each."""

OVERSIZED_FRAME: Final = b"oversized frame\n"
"""Stand-in posted on the queue for a line that exceeded `MAX_LINE_BYTES`.
Not valid JSON by construction, so `decode_line` rejects it with
`ParseError` and the server answers the dropped message with the same
`-32700` reply it gives any other unparseable line, without ever holding
the over-long bytes."""


def start_reader(
    reader: BinaryIO,
    loop: asyncio.AbstractEventLoop,
    queue: "asyncio.Queue[bytes | None]",
    *,
    max_line_bytes: int = MAX_LINE_BYTES,
) -> threading.Thread:
    """Start a daemon thread that reads lines from `reader` and posts them
    onto `queue`, one at a time, in order.

    Each read is `readline(max_line_bytes)`, so no more than that many
    bytes are ever buffered for one line. A chunk that fills the limit
    without ending in a newline is an over-long line: the rest of it is
    read and discarded (in bounded chunks), and `OVERSIZED_FRAME` is posted
    in its place. Posting blocks while `queue` is full, which is the
    backpressure that bounds the queue -- this is a dedicated thread, so
    blocking it stalls only the pipe read. On end of input (`readline()`
    returns `b""`) or a read error, the thread posts `None` as the
    end-of-input sentinel and returns. A closed or stopped `loop` -- which
    can happen if it is torn down while this thread is still running -- is
    swallowed rather than propagated, so a slow reader thread can never
    crash the process on its way out.
    """

    def _post(item: bytes | None) -> bool:
        put = queue.put(item)
        try:
            asyncio.run_coroutine_threadsafe(put, loop).result()
        except RuntimeError:  # the loop is closed: the coroutine never ran
            put.close()
            return False
        except concurrent.futures.CancelledError:  # the loop shut down mid-put
            return False
        return True

    def _discard_rest_of_line() -> None:
        while True:
            chunk = reader.readline(max_line_bytes)
            if not chunk or chunk.endswith(b"\n"):
                return

    def _run() -> None:
        try:
            while True:
                try:
                    line = reader.readline(max_line_bytes)
                    oversized = len(line) >= max_line_bytes and not line.endswith(b"\n")
                    if oversized:
                        _discard_rest_of_line()
                except OSError as exc:
                    logger.error("stdin read failed: %s", exc)
                    line = b""
                    oversized = False
                if not line:
                    return
                if oversized:
                    logger.warning(
                        "dropped a stdin line longer than %d bytes", max_line_bytes
                    )
                    line = OVERSIZED_FRAME
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
