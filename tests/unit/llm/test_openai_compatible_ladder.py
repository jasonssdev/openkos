"""Unit tests for `OpenAICompatibleClient.chat`'s error ladder: transport
failures, HTTP error classification, malformed responses, and the bearer
key (issue #1057 Phase 4, tasks 4.12-4.22).
"""

import http.client
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleError,
    OpenAICompatibleModelNotFound,
    OpenAICompatibleUnavailable,
)


class _FakeResponse:
    """Minimal `urlopen`-shaped stand-in for a 200 JSON response."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        """Return the canned response body."""
        return self._body


class _RaisingReadResponse:
    """`urlopen`-shaped response whose `.read()` raises, simulating a
    transport failure while streaming the body (not while connecting)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def read(self) -> bytes:
        """Raise the configured transport exception instead of returning
        bytes."""
        raise self._exc


def _ok_body(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


def _raising_urlopen(exc: Exception) -> Any:
    """Return an `urlopen` stand-in that always raises `exc`."""

    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise exc

    return _urlopen


def _fake_urlopen_returning(response: Any) -> Any:
    """Return an `urlopen` stand-in that always replies with `response`."""

    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        return response

    return _urlopen


def _fake_urlopen(body: bytes, captured: list[urllib.request.Request]) -> Any:
    """Return an `urlopen` stand-in that records the request and replies
    with `body`."""

    def _urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse(body)

    return _urlopen


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    """Build a real `HTTPError` with a readable `body`, like a genuine
    server reply."""
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/chat/completions",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def _client(urlopen: Any, api_key: str | None = None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=urlopen,
        api_key=api_key,
    )


# --- transport failures -------------------------------------------------


def test_connection_refused_raises_unavailable() -> None:
    """A `URLError` (server not reachable) raises
    `OpenAICompatibleUnavailable`; no `URLError` escapes (spec: "Server
    Unavailable Raises A Typed Error").

    **RED today**: `chat` not implemented."""
    client = _client(_raising_urlopen(urllib.error.URLError("Connection refused")))

    with pytest.raises(OpenAICompatibleUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_timeout_raises_unavailable() -> None:
    """A `TimeoutError` (no response in time) raises
    `OpenAICompatibleUnavailable`, not a raw `TimeoutError`."""
    client = _client(_raising_urlopen(TimeoutError("timed out")))

    with pytest.raises(OpenAICompatibleUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_incomplete_read_raises_unavailable() -> None:
    """An `http.client.IncompleteRead` while streaming the body maps the
    same way as a connect-phase failure."""
    client = _client(
        _fake_urlopen_returning(_RaisingReadResponse(http.client.IncompleteRead(b"")))
    )

    with pytest.raises(OpenAICompatibleUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


# --- HTTP error classification -------------------------------------------


def test_404_with_model_body_raises_model_not_found() -> None:
    """A 404 whose body names the model and says "not found" raises
    `OpenAICompatibleModelNotFound` (spec: "Unknown Model Raises A Typed
    Not-Found Error")."""
    body = b'{"error": "model \\"ghost\\" not found"}'
    client = _client(_raising_urlopen(_http_error(404, body)))

    with pytest.raises(OpenAICompatibleModelNotFound):
        client.chat([{"role": "user", "content": "hi"}])


def test_400_with_model_not_found_body_raises_model_not_found() -> None:
    """A 400 whose body carries a `model_not_found` marker raises
    `OpenAICompatibleModelNotFound` too."""
    body = b'{"error": {"code": "model_not_found", "message": "no such model"}}'
    client = _client(_raising_urlopen(_http_error(400, body)))

    with pytest.raises(OpenAICompatibleModelNotFound):
        client.chat([{"role": "user", "content": "hi"}])


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_other_4xx_5xx_raises_generic_error(status: int) -> None:
    """401 (no key configured), 403, 500, 503 each raise
    `OpenAICompatibleError`, NOT `OpenAICompatibleModelNotFound` (spec:
    "Other Failures Raise A Generic Typed Error")."""
    client = _client(_raising_urlopen(_http_error(status, b"server error")))

    with pytest.raises(OpenAICompatibleError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])
    assert not isinstance(excinfo.value, OpenAICompatibleModelNotFound)


def test_401_403_message_names_env_var_never_key_value() -> None:
    """With an API key configured, a 401/403 raises `OpenAICompatibleError`
    whose message says authentication failed and names
    `OPENKOS_OPENAI_API_KEY`, and the configured key's value never appears
    (sentinel-key assertion)."""
    sentinel = "sk-super-secret-sentinel-value"
    client = _client(
        _raising_urlopen(_http_error(401, b"unauthorized")), api_key=sentinel
    )

    with pytest.raises(OpenAICompatibleError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])
    message = str(excinfo.value)
    assert "OPENKOS_OPENAI_API_KEY" in message
    assert "authentication failed" in message.lower()
    assert sentinel not in message


def test_malformed_json_raises_generic_error() -> None:
    """A non-JSON body raises `OpenAICompatibleError`, no unhandled
    `JSONDecodeError` escaping."""
    captured: list[urllib.request.Request] = []
    client = _client(_fake_urlopen(b"not json at all {{{", captured))

    with pytest.raises(OpenAICompatibleError):
        client.chat([{"role": "user", "content": "hi"}])


def test_missing_choices_content_shape_raises_generic_error() -> None:
    """A valid-JSON body missing `choices[0].message.content` raises
    `OpenAICompatibleError`, no unhandled `KeyError`/`IndexError`
    escaping."""
    captured: list[urllib.request.Request] = []
    body = json.dumps({"choices": []}).encode("utf-8")
    client = _client(_fake_urlopen(body, captured))

    with pytest.raises(OpenAICompatibleError):
        client.chat([{"role": "user", "content": "hi"}])


def test_null_content_raises_generic_error() -> None:
    """A `null` `content` value raises `OpenAICompatibleError` rather than
    returning `None` (spec: response content "must be a `str`")."""
    captured: list[urllib.request.Request] = []
    body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": None}}]}
    ).encode("utf-8")
    client = _client(_fake_urlopen(body, captured))

    with pytest.raises(OpenAICompatibleError):
        client.chat([{"role": "user", "content": "hi"}])


def _rung_factories() -> list[Callable[[], Any]]:
    """Zero-arg factories, one per ladder rung -- built fresh on each call
    so a single-use `HTTPError`/fake response is never shared across
    parametrized invocations."""
    return [
        lambda: _raising_urlopen(urllib.error.URLError("refused")),
        lambda: _raising_urlopen(TimeoutError("timed out")),
        lambda: _raising_urlopen(
            _http_error(404, b'{"error": "model \\"ghost\\" not found"}')
        ),
        lambda: _raising_urlopen(
            _http_error(400, b'{"error": {"code": "model_not_found"}}')
        ),
        lambda: _raising_urlopen(_http_error(401, b"unauthorized")),
        lambda: _raising_urlopen(_http_error(403, b"forbidden")),
        lambda: _raising_urlopen(_http_error(500, b"server error")),
        lambda: _fake_urlopen(b"not json at all {{{", []),
    ]


@pytest.mark.parametrize("rung_index", range(len(_rung_factories())))
def test_key_never_appears_in_any_ladder_rung_exception_message(
    rung_index: int,
) -> None:
    """The sentinel API key never appears in `str(exc)` for any rung
    (mutation-proof: mutating `_map_http_error`'s auth-failure branch to
    interpolate the key directly makes this fail; see task 4.20's own
    mutation-proof note)."""
    sentinel = "sk-mutation-proof-sentinel"
    urlopen = _rung_factories()[rung_index]()
    client = _client(urlopen, api_key=sentinel)

    with pytest.raises(OpenAICompatibleError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])
    assert sentinel not in str(excinfo.value)


# --- bearer key -----------------------------------------------------------


def test_key_sent_as_bearer_header_when_present() -> None:
    """A client with a key sends `Authorization: Bearer <key>` on every
    request (spec: "An Optional Bearer Key Is Applied Per Request And
    Never Logged")."""
    captured: list[urllib.request.Request] = []
    client = _client(_fake_urlopen(_ok_body("hi"), captured), api_key="sk-real-key")

    client.chat([{"role": "user", "content": "hi"}])

    assert captured[0].get_header("Authorization") == "Bearer sk-real-key"


def test_no_authorization_header_when_absent() -> None:
    """A client without a key sends no `Authorization` header at all."""
    captured: list[urllib.request.Request] = []
    client = _client(_fake_urlopen(_ok_body("hi"), captured))

    client.chat([{"role": "user", "content": "hi"}])

    assert captured[0].get_header("Authorization") is None
