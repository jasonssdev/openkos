"""Unit tests for `openkos.llm`: `OllamaClient` and the `LLMBackend` seam.

Every test injects a fake `urlopen` callable -- no live Ollama server is
ever contacted. Fakes either return a fake response object exposing
`.read() -> bytes`, or raise the real `urllib.error.HTTPError`/`URLError`/
`TimeoutError` that the production `urlopen` would raise, so the error
mapping in `openkos.llm.ollama.OllamaClient.chat` is exercised exactly as
it would be against a real transport.
"""

import ast
import http.client
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from openkos.llm.base import Message
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _FakeResponse:
    """Minimal `urlopen`-shaped stand-in for a 200 JSON response."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        """Return the canned response body, mirroring `http.client.HTTPResponse`."""
        return self._body


class _RaisingReadResponse:
    """`urlopen`-shaped response whose `.read()` raises, simulating a transport
    failure that occurs while streaming the body (not while connecting)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def read(self) -> bytes:
        """Raise the configured transport exception instead of returning bytes."""
        raise self._exc


def _ok_body(content: str) -> bytes:
    """Build a well-formed `/api/chat` success body containing `content`."""
    return json.dumps(
        {"message": {"role": "assistant", "content": content}, "done": True}
    ).encode("utf-8")


def _sent_body(request: urllib.request.Request) -> dict[str, Any]:
    """Decode the JSON body of a captured `Request` for assertion (mypy-narrowed)."""
    data = request.data
    assert isinstance(data, bytes)
    parsed: dict[str, Any] = json.loads(data)
    return parsed


def _fake_urlopen(body: bytes, captured: list[urllib.request.Request]) -> Any:
    """Return an `urlopen` stand-in that records the request and replies with `body`."""

    def _urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse(body)

    return _urlopen


def _fake_urlopen_returning(response: Any) -> Any:
    """Return an `urlopen` stand-in that replies with a pre-built response object,
    e.g. one whose `.read()` raises to simulate a mid-body transport failure."""

    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        return response

    return _urlopen


def _fake_urlopen_capturing_timeout(
    body: bytes, captured_timeouts: list[float | None]
) -> Any:
    """Return an `urlopen` stand-in that records the `timeout` kwarg it receives."""

    def _urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        captured_timeouts.append(timeout)
        return _FakeResponse(body)

    return _urlopen


# --- Phase 1: Foundation -----------------------------------------------------


def test_message_typed_dict_holds_role_and_content() -> None:
    """`Message` is a plain `{"role", "content"}` mapping (no conversion needed)."""
    message: Message = {"role": "user", "content": "hi"}

    assert message["role"] == "user"
    assert message["content"] == "hi"
    # Triangulation skipped: purely structural TypedDict, single possible shape.


# --- Phase 2/3: success + request shape --------------------------------------


def test_chat_success_returns_assistant_content() -> None:
    """`chat()` returns the server's `message.content` string verbatim."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3", urlopen=_fake_urlopen(_ok_body("Stoicism is a school of..."), captured)
    )

    result = client.chat([{"role": "user", "content": "What is stoicism?"}])

    assert result == "Stoicism is a school of..."


def test_chat_request_body_disables_stream_and_think() -> None:
    """The POSTed JSON body always carries `stream: false` and `think: false`."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(_ok_body("hi"), captured))

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["stream"] is False
    assert sent["think"] is False


def test_chat_preserves_system_and_user_messages_in_order() -> None:
    """Both `system` and `user` entries are forwarded, each with its own role, in order."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(_ok_body("ack"), captured))
    messages: list[Message] = [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Define apatheia."},
    ]

    client.chat(messages)

    sent = _sent_body(captured[0])
    assert sent["messages"] == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Define apatheia."},
    ]


def test_chat_request_targets_host_api_chat_with_model_and_messages() -> None:
    """The request URL is `{host}/api/chat` and the body carries the model tag."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3:8b",
        host="http://example.internal:11434",
        urlopen=_fake_urlopen(_ok_body("ok"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert captured[0].full_url == "http://example.internal:11434/api/chat"
    sent = _sent_body(captured[0])
    assert sent["model"] == "qwen3:8b"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


# --- Phase 4/5: error mapping -------------------------------------------------


def _raising_urlopen(exc: Exception) -> Any:
    """Return an `urlopen` stand-in that raises `exc` instead of responding."""

    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise exc

    return _urlopen


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    """Build a real `HTTPError` with a readable `body`, like a genuine Ollama reply."""
    return urllib.error.HTTPError(
        url="http://localhost:11434/api/chat",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


class _RaisingReadBytesIO(io.BytesIO):
    """A `BytesIO` whose `read()` raises, to simulate a transport failure while
    reading an HTTP *error* response body inside `_map_http_error`."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(b"")
        self._exc = exc

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        """Raise the configured transport exception instead of returning bytes."""
        raise self._exc


def _http_error_raising_read(code: int, exc: Exception) -> urllib.error.HTTPError:
    """Build an `HTTPError` whose body `.read()` raises `exc`."""
    return urllib.error.HTTPError(
        url="http://localhost:11434/api/chat",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=_RaisingReadBytesIO(exc),
    )


def test_connection_refused_raises_ollama_unavailable() -> None:
    """A `URLError` (server not reachable) is mapped to `OllamaUnavailable`."""
    client = OllamaClient(
        "qwen3", urlopen=_raising_urlopen(urllib.error.URLError("Connection refused"))
    )

    with pytest.raises(OllamaUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_request_timeout_raises_ollama_unavailable() -> None:
    """A `TimeoutError` (no response in time) is mapped to `OllamaUnavailable`, not a hang."""
    client = OllamaClient("qwen3", urlopen=_raising_urlopen(TimeoutError("timed out")))

    with pytest.raises(OllamaUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_body_read_timeout_raises_ollama_unavailable() -> None:
    """A `TimeoutError` while reading the response body (not while connecting) still
    maps to `OllamaUnavailable`, not a raw `TimeoutError` leaking to the caller."""
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen_returning(
            _RaisingReadResponse(TimeoutError("timed out mid-read"))
        ),
    )

    with pytest.raises(OllamaUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_body_read_connection_reset_raises_ollama_unavailable() -> None:
    """A `ConnectionResetError` while reading the response body maps to
    `OllamaUnavailable`, triangulating body-read failures beyond `TimeoutError`."""
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen_returning(
            _RaisingReadResponse(ConnectionResetError("connection reset by peer"))
        ),
    )

    with pytest.raises(OllamaUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_body_read_incomplete_read_raises_ollama_unavailable() -> None:
    """An `http.client.IncompleteRead` while reading the response body (a
    Content-Length/body-length mismatch) maps to `OllamaUnavailable`, not a
    raw `IncompleteRead` leaking to the caller. `IncompleteRead` subclasses
    `http.client.HTTPException`, not `OSError`, so it needs its own catch."""
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen_returning(
            _RaisingReadResponse(http.client.IncompleteRead(partial=b""))
        ),
    )

    with pytest.raises(OllamaUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_404_model_not_found_body_raises_ollama_model_not_found() -> None:
    """A 404 whose body says the model tag was not found raises `OllamaModelNotFound`."""
    body = json.dumps({"error": "model 'ghost:latest' not found"}).encode("utf-8")
    client = OllamaClient(
        "ghost:latest", urlopen=_raising_urlopen(_http_error(404, body))
    )

    with pytest.raises(OllamaModelNotFound):
        client.chat([{"role": "user", "content": "hi"}])


def test_404_without_not_found_text_raises_ollama_error() -> None:
    """A 404 whose body does NOT mention "not found" falls through to the
    generic `OllamaError`, not `OllamaModelNotFound` (D5's narrower 404 branch)."""
    body = json.dumps({"error": "something else"}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_raising_urlopen(_http_error(404, body)))

    with pytest.raises(OllamaError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    assert not isinstance(excinfo.value, OllamaModelNotFound)


def test_error_body_read_failure_still_raises_ollama_error() -> None:
    """A transport failure while reading a non-2xx *error* response body degrades
    to a typed `OllamaError` (empty detail) rather than leaking a raw exception --
    symmetric with the guarded success-path body read in `chat`."""
    err = _http_error_raising_read(500, TimeoutError("reset while reading error body"))
    client = OllamaClient("qwen3", urlopen=_raising_urlopen(err))

    with pytest.raises(OllamaError):
        client.chat([{"role": "user", "content": "hi"}])


def test_non_404_http_error_raises_ollama_error_with_detail() -> None:
    """A non-404 HTTP error is mapped to `OllamaError`, carrying the server's detail."""
    body = json.dumps({"error": "internal server error"}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_raising_urlopen(_http_error(500, body)))

    with pytest.raises(OllamaError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    assert not isinstance(excinfo.value, OllamaModelNotFound)
    assert "internal server error" in str(excinfo.value)


def test_malformed_json_response_raises_ollama_error() -> None:
    """A 200 body that is not valid JSON raises `OllamaError`, not a raw parse exception."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3", urlopen=_fake_urlopen(b"not json at all {{{", captured)
    )

    with pytest.raises(OllamaError):
        client.chat([{"role": "user", "content": "hi"}])


def test_missing_message_key_raises_ollama_error() -> None:
    """A 200 body missing the top-level `message` key entirely raises `OllamaError`."""
    captured: list[urllib.request.Request] = []
    body = json.dumps({"done": True}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, captured))

    with pytest.raises(OllamaError):
        client.chat([{"role": "user", "content": "hi"}])


def test_message_present_without_content_key_raises_ollama_error() -> None:
    """A 200 body where `message` is present but its `content` key is absent
    raises `OllamaError` (narrower shape than a missing `message` key)."""
    captured: list[urllib.request.Request] = []
    body = json.dumps({"message": {"role": "assistant"}}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, captured))

    with pytest.raises(OllamaError):
        client.chat([{"role": "user", "content": "hi"}])


def test_non_string_message_content_raises_ollama_error() -> None:
    """A 200 body whose `message.content` is not a string raises `OllamaError`."""
    captured: list[urllib.request.Request] = []
    body = json.dumps({"message": {"role": "assistant", "content": 42}}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, captured))

    with pytest.raises(OllamaError):
        client.chat([{"role": "user", "content": "hi"}])


def test_default_timeout_is_forwarded_to_urlopen() -> None:
    """The default `120.0` timeout (D6) is forwarded to `urlopen` unchanged."""
    captured_timeouts: list[float | None] = []
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen_capturing_timeout(_ok_body("hi"), captured_timeouts),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert captured_timeouts == [120.0]


def test_custom_timeout_is_forwarded_to_urlopen() -> None:
    """A custom `timeout` constructor arg is forwarded to `urlopen` unchanged."""
    captured_timeouts: list[float | None] = []
    client = OllamaClient(
        "qwen3",
        timeout=5.0,
        urlopen=_fake_urlopen_capturing_timeout(_ok_body("hi"), captured_timeouts),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert captured_timeouts == [5.0]


# --- Phase 6/7: host configuration --------------------------------------------


def test_no_override_targets_default_localhost_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no `host` arg and no `OLLAMA_HOST` env, the request targets the packaged default."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    captured: list[urllib.request.Request] = []
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(_ok_body("hi"), captured))

    client.chat([{"role": "user", "content": "hi"}])

    assert captured[0].full_url == "http://localhost:11434/api/chat"


def test_explicit_host_arg_overrides_env_and_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit `host` constructor arg wins over `OLLAMA_HOST` and the default."""
    monkeypatch.setenv("OLLAMA_HOST", "http://env-host:11434")
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        host="http://explicit-host:11434",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert captured[0].full_url == "http://explicit-host:11434/api/chat"


def test_ollama_host_env_overrides_default_when_no_arg_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OLLAMA_HOST` is honored when no explicit `host` argument is given."""
    monkeypatch.setenv("OLLAMA_HOST", "http://env-host:11434")
    captured: list[urllib.request.Request] = []
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(_ok_body("hi"), captured))

    client.chat([{"role": "user", "content": "hi"}])

    assert captured[0].full_url == "http://env-host:11434/api/chat"


def test_bare_host_port_is_normalized_with_http_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare `host:port` (no scheme) is normalized by prepending `http://` (D2)."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        host="example.internal:11434",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert captured[0].full_url == "http://example.internal:11434/api/chat"


# --- Phase 8: layering guard --------------------------------------------------


def test_llm_modules_do_not_import_config() -> None:
    """No module under `src/openkos/llm/` imports `openkos.config` (leaf discipline)."""
    llm_dir = _REPO_ROOT / "src" / "openkos" / "llm"
    modules = sorted(llm_dir.glob("*.py"))
    assert modules, "expected llm/ modules to exist"

    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert not any("config" in name for name in imported), (
            f"{path} imports config: {imported}"
        )
