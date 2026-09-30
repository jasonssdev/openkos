"""Unit tests for `mcp/transport.py`: framing and stdio ownership (design
Decision 10, ADR-0027).

These tests exercise the pure framing functions (`decode_line`,
`encode_message`), the reader thread (`start_reader`) and the writer
(`MessageWriter`) in-process. The one test that needs a real second process
-- proving `claim_stdio` redirects a stray fd-level write, not only
`sys.stdout` -- lives in `test_stdio_subprocess.py`.
"""

import asyncio
import io
import os
import threading

import pytest

from openkos.mcp import transport

_RAISES = object()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A line ending in `\n`.
        (b'{"a": 1}\n', {"a": 1}),
        # A line ending in `\r\n` (a Windows-style client).
        (b'{"a": 1}\r\n', {"a": 1}),
        # A blank line, CRLF-terminated -- this is the case that catches a
        # naive `rstrip(b"\n")`/`raw[:-1]` implementation: stripping only
        # `\n` would leave a trailing `\r`, which is neither empty (so the
        # blank-line check would miss it) nor valid JSON on its own (so
        # `json.loads` would raise instead of this returning `None`).
        (b"\r\n", None),
        # Invalid UTF-8 bytes.
        (b"\xff\xfe not valid utf-8\n", _RAISES),
        # Invalid JSON text.
        (b"not json at all\n", _RAISES),
        # A JSON body containing the literal token `NaN`. Python's `json`
        # accepts `NaN`/`Infinity`/`-Infinity` by default (a JSON dialect
        # extension); `parse_constant` must reject it, so a hand-rolled
        # frame cannot smuggle a non-finite float value past the boundary.
        (b'{"a": NaN}\n', _RAISES),
    ],
)
def test_decode_line_all_cases(raw: bytes, expected: object) -> None:
    if expected is _RAISES:
        with pytest.raises(transport.ParseError):
            transport.decode_line(raw)
    else:
        assert transport.decode_line(raw) == expected


def test_encode_message_framing() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"text": "line one\nline two"}}

    encoded = transport.encode_message(payload)

    # Round-trips to the same value -- the embedded `\n` inside the string
    # value is JSON-escaped, not a raw newline byte.
    assert transport.decode_line(encoded) == payload

    # Exactly one raw `\n`, at the very end, and no raw `\r` anywhere.
    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1
    assert b"\r" not in encoded


def test_encode_message_rejects_nan() -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        transport.encode_message({"value": float("nan")})


def test_start_reader_posts_lines_then_sentinel() -> None:
    """Over a real `os.pipe` with a running event loop, `start_reader`
    posts each written line onto the queue in order and then `None` on end
    of input. Kills omitting the `None` sentinel on end of input."""

    async def _scenario() -> list[bytes | None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        thread = transport.start_reader(reader, loop, queue)
        try:
            os.write(write_fd, b"one\n")
            os.write(write_fd, b"two\n")
        finally:
            os.close(write_fd)

        # Bounded, not `await queue.get()` outright: a mutation that omits
        # the end-of-input sentinel must fail this test promptly instead of
        # hanging the whole suite.
        items = [
            await asyncio.wait_for(queue.get(), timeout=5),
            await asyncio.wait_for(queue.get(), timeout=5),
            await asyncio.wait_for(queue.get(), timeout=5),
        ]
        # Off the loop thread: the reader hands its last item over through
        # the loop, so joining from the loop itself would deadlock.
        await loop.run_in_executor(None, thread.join, 5)
        assert not thread.is_alive()
        return items

    assert asyncio.run(_scenario()) == [b"one\n", b"two\n", None]


def test_start_reader_drops_an_oversized_line_and_serves_the_next() -> None:
    """A line longer than `max_line_bytes` is never posted as-is: the reader
    posts `OVERSIZED_FRAME` (invalid JSON, so it decodes to a parse error),
    discards the rest of that line, and the next line arrives intact."""

    async def _scenario() -> list[bytes | None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        thread = transport.start_reader(reader, loop, queue, max_line_bytes=16)
        try:
            os.write(write_fd, b"x" * 100 + b"\n")
            os.write(write_fd, b'{"ok":1}\n')
        finally:
            os.close(write_fd)
        items = [await asyncio.wait_for(queue.get(), timeout=5) for _ in range(3)]
        await loop.run_in_executor(None, thread.join, 5)
        return items

    items = asyncio.run(_scenario())

    assert items == [transport.OVERSIZED_FRAME, b'{"ok":1}\n', None]
    with pytest.raises(transport.ParseError):
        transport.decode_line(transport.OVERSIZED_FRAME)


def test_start_reader_accepts_a_line_of_exactly_the_limit() -> None:
    """The bound includes the newline: a line that is exactly
    `max_line_bytes` long, terminator included, is a normal frame."""

    async def _scenario() -> list[bytes | None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        transport.start_reader(reader, loop, queue, max_line_bytes=8)
        try:
            os.write(write_fd, b"1234567\n")
        finally:
            os.close(write_fd)
        return [await asyncio.wait_for(queue.get(), timeout=5) for _ in range(2)]

    assert asyncio.run(_scenario()) == [b"1234567\n", None]


def test_start_reader_oversized_line_at_end_of_input() -> None:
    """An over-long line cut off by end of input (no newline ever) is still
    dropped with one marker, and the end sentinel follows."""

    async def _scenario() -> list[bytes | None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        transport.start_reader(reader, loop, queue, max_line_bytes=8)
        try:
            os.write(write_fd, b"y" * 50)
        finally:
            os.close(write_fd)
        return [await asyncio.wait_for(queue.get(), timeout=5) for _ in range(2)]

    assert asyncio.run(_scenario()) == [transport.OVERSIZED_FRAME, None]


def test_start_reader_backpressures_on_a_bounded_queue() -> None:
    """With a full bounded queue the reader thread blocks instead of
    growing the queue or dropping lines: every line arrives, in order, and
    the queue never holds more than its bound."""

    async def _scenario() -> tuple[list[bytes | None], int]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        transport.start_reader(reader, loop, queue)
        try:
            os.write(write_fd, b"a\nb\nc\nd\n")
        finally:
            os.close(write_fd)
        await asyncio.sleep(0.1)  # let the reader run into the full queue
        peak = queue.qsize()
        items: list[bytes | None] = []
        for _ in range(5):
            items.append(await asyncio.wait_for(queue.get(), timeout=5))
            peak = max(peak, queue.qsize())
        return items, peak

    items, peak = asyncio.run(_scenario())

    assert items == [b"a\n", b"b\n", b"c\n", b"d\n", None]
    assert peak <= 1


def test_start_reader_swallows_closed_loop_error() -> None:
    """Closing the loop before the reader thread finishes does not raise
    inside the thread -- the closed-loop `RuntimeError` from
    `call_soon_threadsafe` is swallowed."""
    errors: list[BaseException | None] = []
    original_hook = threading.excepthook

    def _record(args: threading.ExceptHookArgs) -> None:
        errors.append(args.exc_value)

    threading.excepthook = _record
    try:
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        loop.close()  # closed BEFORE the thread ever tries to post

        thread = transport.start_reader(reader, loop, queue)
        try:
            os.write(write_fd, b"line\n")
        finally:
            os.close(write_fd)
        thread.join(timeout=5)
    finally:
        threading.excepthook = original_hook

    assert not thread.is_alive()
    assert errors == []


def test_message_writer_send_writes_bytes() -> None:
    buffer = io.BytesIO()
    writer = transport.MessageWriter(buffer)
    message = {"jsonrpc": "2.0", "id": 1, "result": {}}

    writer.send(message)

    assert buffer.getvalue() == transport.encode_message(message)
