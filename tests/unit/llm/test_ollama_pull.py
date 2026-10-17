"""`OllamaClient.pull`: the streaming `/api/pull`, read one line at a time.

Every test injects a fake `urlopen`; no live Ollama server is needed.
"""

import io
import json
import urllib.error
import urllib.request
from email.message import Message as Headers
from typing import Any

import pytest

from openkos.llm.base import PullEvent
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)


class _Stream:
    """A streamed reply: `readline` hands out one line per call."""

    def __init__(self, lines: list[bytes], fail_after: int | None = None) -> None:
        self._lines = list(lines)
        self.read_lines = 0
        self.closed = False
        self._fail_after = fail_after

    def readline(self) -> bytes:
        if self._fail_after is not None and self.read_lines >= self._fail_after:
            raise TimeoutError("stalled")
        self.read_lines += 1
        return self._lines.pop(0) if self._lines else b""

    def close(self) -> None:
        self.closed = True


def _lines(*objects: object) -> list[bytes]:
    return [json.dumps(o).encode() + b"\n" for o in objects]


def _client(
    stream: _Stream, sent: list[urllib.request.Request] | None = None
) -> OllamaClient:
    def urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        if sent is not None:
            sent.append(request)
        return stream

    return OllamaClient("qwen3:8b", host="http://127.0.0.1:11434", urlopen=urlopen)


_PROGRESS = (
    {"status": "pulling manifest"},
    {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 40},
    {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 100},
)


def test_a_stream_is_read_to_success() -> None:
    stream = _Stream(_lines(*_PROGRESS, {"status": "success"}))
    sent: list[urllib.request.Request] = []
    events: list[PullEvent] = []

    done = _client(stream, sent).pull(
        "bge-m3", on_event=events.append, should_stop=lambda: False
    )

    assert done is True
    assert [e.status for e in events] == [
        "pulling manifest",
        "pulling abc",
        "pulling abc",
        "success",
    ]
    assert events[1] == PullEvent(
        status="pulling abc", digest="sha256:abc", completed=40, total=100
    )
    assert events[0] == PullEvent(status="pulling manifest")
    request = sent[0]
    assert request.full_url == "http://127.0.0.1:11434/api/pull"
    assert request.get_method() == "POST"
    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == {"model": "bge-m3", "stream": True}
    assert stream.closed


def test_a_stop_closes_the_reply_and_reads_no_further_line() -> None:
    stream = _Stream(_lines(*_PROGRESS, {"status": "success"}))
    events: list[PullEvent] = []

    done = _client(stream).pull(
        "bge-m3", on_event=events.append, should_stop=lambda: True
    )

    assert done is False
    assert len(events) == 1
    assert stream.read_lines == 1
    assert stream.closed


def test_blank_lines_and_unknown_fields_are_ignored() -> None:
    lines = [b"\n", *_lines({"status": "success", "extra": [1, 2]})]
    events: list[PullEvent] = []

    assert _client(_Stream(lines)).pull(
        "m", on_event=events.append, should_stop=lambda: False
    )
    assert events == [PullEvent(status="success")]


@pytest.mark.parametrize(
    "text", ["pull model manifest: file does not exist", "model 'x' not found"]
)
def test_an_error_line_naming_a_missing_model_is_not_found(text: str) -> None:
    stream = _Stream(_lines({"error": text}))

    with pytest.raises(OllamaModelNotFound):
        _client(stream).pull("x", on_event=lambda e: None, should_stop=lambda: False)
    assert stream.closed


def test_any_other_error_line_is_an_ollama_error() -> None:
    stream = _Stream(_lines({"status": "pulling manifest"}, {"error": "disk full"}))

    with pytest.raises(OllamaError) as caught:
        _client(stream).pull("x", on_event=lambda e: None, should_stop=lambda: False)
    assert not isinstance(caught.value, OllamaModelNotFound)


def test_a_stream_that_ends_without_success_is_an_ollama_error() -> None:
    stream = _Stream(_lines(*_PROGRESS))

    with pytest.raises(OllamaError):
        _client(stream).pull("x", on_event=lambda e: None, should_stop=lambda: False)


@pytest.mark.parametrize(
    "line",
    [
        b"not json\n",
        b"[]\n",
        b'{"status": 3}\n',
        b'{"status": "pulling", "completed": "many"}\n',
        b'{"status": "pulling", "total": true}\n',
        b'{"status": "pulling", "digest": 7}\n',
    ],
)
def test_a_malformed_line_is_an_ollama_error(line: bytes) -> None:
    with pytest.raises(OllamaError):
        _client(_Stream([line])).pull(
            "x", on_event=lambda e: None, should_stop=lambda: False
        )


def test_a_refused_connection_is_unavailable() -> None:
    def urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))

    client = OllamaClient("m", urlopen=urlopen)

    with pytest.raises(OllamaUnavailable):
        client.pull("x", on_event=lambda e: None, should_stop=lambda: False)


def test_a_stalled_stream_is_unavailable() -> None:
    stream = _Stream(_lines(*_PROGRESS), fail_after=1)

    with pytest.raises(OllamaUnavailable):
        _client(stream).pull("x", on_event=lambda e: None, should_stop=lambda: False)
    assert stream.closed


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/pull", code, "error", Headers(), io.BytesIO(body)
    )


@pytest.mark.parametrize(
    ("code", "body", "kind"),
    [
        (500, b'{"error":"pull model manifest: file does not exist"}', "missing"),
        (404, b'{"error":"model not found"}', "missing"),
        (500, b'{"error":"disk full"}', "other"),
    ],
)
def test_an_http_error_is_mapped(code: int, body: bytes, kind: str) -> None:
    def urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise _http_error(code, body)

    client = OllamaClient("m", urlopen=urlopen)

    with pytest.raises(OllamaError) as caught:
        client.pull("x", on_event=lambda e: None, should_stop=lambda: False)
    assert isinstance(caught.value, OllamaModelNotFound) == (kind == "missing")
