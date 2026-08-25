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
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from openkos.llm.base import EMBED_DIM, Embedder, Message
from openkos.llm.ollama import (
    DEFAULT_TIMEOUT,
    InstalledModel,
    OllamaClient,
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaGenerationCapped,
    OllamaModelNotFound,
    OllamaUnavailable,
    is_embedding_model,
    is_timeout_failure,
    model_tag_matches,
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


class _FakeEmbedder:
    """A structural `Embedder`: records every `embed` call, returns fixed
    vectors. Mirrors `_FakeLLM`'s injection pattern in
    `tests/unit/retrieval/test_answer.py`."""

    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self.vectors = vectors if vectors is not None else []
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return self.vectors


def test_embedder_protocol_is_satisfied_by_structural_fake() -> None:
    """A fake exposing `embed(texts) -> list[list[float]]` satisfies `Embedder`
    structurally (no explicit inheritance required), mirroring the fake
    `LLMBackend` injection pattern used for `chat()`."""
    fake = _FakeEmbedder(vectors=[[0.1] * EMBED_DIM])

    embedder: Embedder = fake
    result = embedder.embed(["hello"])

    assert result == [[0.1] * EMBED_DIM]
    assert fake.calls == [["hello"]]


def test_embed_dim_is_1024() -> None:
    """`EMBED_DIM` is the fixed contract dimension every embedding row uses."""
    assert EMBED_DIM == 1024


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


# --- Generation ceiling: `num_predict` and `done_reason == "length"` (#422) --


def test_chat_sends_num_predict_when_max_generation_tokens_configured() -> None:
    """`chat()` emits `options.num_predict` when a ceiling is configured."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["options"] == {"num_predict": 8192}


def test_chat_omits_options_entirely_when_max_generation_tokens_not_configured() -> (
    None
):
    """`chat()` omits `options` entirely when no ceiling is configured, so an
    opted-out workspace sends a byte-identical request to today's."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(_ok_body("hi"), captured))

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert "options" not in sent


def test_chat_sends_temperature_zero_when_configured() -> None:
    """`chat()` emits `options.temperature` even at 0.0 -- the deterministic
    setting is the whole point, so a falsy-check bug would defeat it."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        temperature=0.0,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["options"] == {"temperature": 0.0}


def test_chat_sends_seed_when_configured() -> None:
    """`chat()` emits `options.seed` when a sampling seed is configured."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        seed=7,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["options"] == {"seed": 7}


def test_chat_merges_all_configured_options() -> None:
    """Ceiling, temperature and seed share one `options` object."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        temperature=0.0,
        seed=7,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["options"] == {"num_predict": 8192, "temperature": 0.0, "seed": 7}


# --- Context window: `num_ctx` (#691) --------------------------------------


def test_chat_sends_num_ctx_when_context_window_configured() -> None:
    """`chat()` emits `options.num_ctx` when a context window is configured.

    Unpinned, the model reserves whatever its Modelfile ships -- 32768 tokens
    and a ~10 GB footprint on `qwen3:8b` (#691), which leaves no room for a
    second slot on a 16 GB machine.
    """
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        context_window=12288,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["options"] == {"num_ctx": 12288}


def test_chat_omits_num_ctx_when_context_window_not_configured() -> None:
    """`context_window=None` contributes nothing, so an opted-out workspace
    sends a byte-identical request to the pre-#691 one and keeps inheriting
    the model's own default window."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(_ok_body("hi"), captured))

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert "options" not in sent


def test_chat_merges_num_ctx_with_the_other_options() -> None:
    """The window shares the one `options` object with the ceiling and the
    sampling pins -- `num_ctx` and `num_predict` are different bounds and both
    must survive together."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        temperature=0.0,
        seed=7,
        context_window=12288,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["options"] == {
        "num_predict": 8192,
        "temperature": 0.0,
        "seed": 7,
        "num_ctx": 12288,
    }


def test_embed_never_sends_num_ctx() -> None:
    """`num_ctx` is CHAT-only, exactly like `num_predict`: the embedding
    endpoint takes no generation options, and adding one there would be a
    silent request-shape change on a seam #691 never measured."""
    captured: list[urllib.request.Request] = []
    body = json.dumps({"embeddings": [[0.0] * 1024]}).encode("utf-8")
    client = OllamaClient(
        "bge-m3",
        context_window=12288,
        urlopen=_fake_urlopen(body, captured),
    )

    client.embed(["hi"])

    assert "options" not in _sent_body(captured[0])


def _ok_body_with_done_reason(content: str, done_reason: str) -> bytes:
    """Build a well-formed `/api/chat` success body carrying `done_reason`."""
    return json.dumps(
        {
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": done_reason,
        }
    ).encode("utf-8")


def test_chat_raises_generation_capped_when_done_reason_is_length() -> None:
    """`done_reason == "length"` raises `OllamaGenerationCapped`: the model
    hit the configured ceiling and the reply is truncated."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        urlopen=_fake_urlopen(
            _ok_body_with_done_reason("truncated mid-", "length"), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped):
        client.chat([{"role": "user", "content": "hi"}])


def test_chat_generation_capped_is_an_ollama_error() -> None:
    """`OllamaGenerationCapped` is `OllamaError`-family, so it propagates
    through every existing bare `except OllamaError` handler unmodified."""
    assert issubclass(OllamaGenerationCapped, OllamaError)


def test_chat_does_not_raise_when_done_reason_is_stop() -> None:
    """`done_reason == "stop"` is a normal completion; no exception."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen(
            _ok_body_with_done_reason("a complete reply", "stop"), captured
        ),
    )

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result == "a complete reply"


def test_chat_does_not_raise_when_done_reason_is_absent() -> None:
    """A response missing `done_reason` entirely must NOT raise -- absent is
    treated as not-capped (fail-open on the signal; the bound itself is
    what protects us)."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3", urlopen=_fake_urlopen(_ok_body("no signal"), captured)
    )

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result == "no signal"


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
    """`DEFAULT_TIMEOUT` (D6) is forwarded to `urlopen` unchanged.

    Asserted against the constant rather than a literal: this test is about
    the value reaching the transport untouched, not about what the value
    happens to be. `DEFAULT_TIMEOUT` moved from 120s to 600s in #405, and a
    hardcoded literal here would fail for the wrong reason -- the number
    itself is pinned by `test_config.py`, next to the rationale.
    """
    captured_timeouts: list[float | None] = []
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen_capturing_timeout(_ok_body("hi"), captured_timeouts),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert captured_timeouts == [DEFAULT_TIMEOUT]


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


# --- issue #240: the resolved host is reportable, and classifies ---------------


def test_resolved_host_reports_the_packaged_default_when_nothing_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no `host` arg and no `OLLAMA_HOST`, `resolved_host` reports the
    same normalized default the request URL is built from (#240).

    The property exists so a caller can ask WHERE this client will actually
    send, instead of re-deriving it from the environment and being wrong."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    client = OllamaClient("qwen3")

    assert client.resolved_host == "http://localhost:11434"


def test_resolved_host_reports_the_env_host_when_no_argument_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OLLAMA_HOST` is what the send will use when no `host` arg is passed,
    so it is what `resolved_host` reports (#240)."""
    monkeypatch.setenv("OLLAMA_HOST", "http://remote.example:11434")
    client = OllamaClient("qwen3")

    assert client.resolved_host == "http://remote.example:11434"


def test_resolved_host_reports_the_explicit_argument_over_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit `host=` wins over `OLLAMA_HOST` for the actual send, so it
    must win here too (#240).

    This is the whole point of reading the CLIENT rather than the env: a
    caller that passes `host="http://remote.example:11434"` while
    `OLLAMA_HOST` happens to be loopback would otherwise be told it is
    sending locally when it is not."""
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    client = OllamaClient("qwen3", host="http://remote.example:11434")

    assert client.resolved_host == "http://remote.example:11434"


def test_resolved_host_normalizes_a_bare_host_port_exactly_like_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`resolved_host` is the SAME normalized string the request URL is built
    from -- scheme prepended, trailing slash stripped (#240)."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        host="example.internal:11434/",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert client.resolved_host == "http://example.internal:11434"
    assert captured[0].full_url == f"{client.resolved_host}/api/chat"


def test_locality_classifies_the_default_host_as_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packaged default is loopback by literal form, so `locality` is
    local -- the fact the confidential local exemption rests on (#240).

    `display_host` carries the port because the classifier is handed the
    RESOLVED host (`http://localhost:11434`), not an unset env var; the
    scheme is stripped and the host[:port] remainder is what may be shown."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    client = OllamaClient("qwen3")

    assert client.locality.is_local is True
    assert client.locality.display_host == "localhost:11434"


def test_locality_classifies_an_env_remote_host_as_non_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host inherited from `OLLAMA_HOST` that is not loopback classifies
    non-local, so the exemption is denied (#240)."""
    monkeypatch.setenv("OLLAMA_HOST", "http://remote.example:11434")
    client = OllamaClient("qwen3")

    assert client.locality.is_local is False


def test_locality_follows_the_explicit_argument_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit non-loopback `host=` classifies non-local even when
    `OLLAMA_HOST` is loopback (#240).

    Reading the env instead of the client would grant a local exemption to
    a client that is demonstrably sending off-machine -- the exact fail-open
    this property exists to close."""
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    client = OllamaClient("qwen3", host="http://remote.example:11434")

    assert client.locality.is_local is False


def test_locality_never_exposes_userinfo_in_display_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A credentialed host never leaks its password through `display_host`
    (#199/#353 contract, inherited unchanged by #240)."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    client = OllamaClient("qwen3", host="http://user:s3cret@remote.example:11434")

    assert "s3cret" not in client.locality.display_host
    assert "user" not in client.locality.display_host
    assert client.locality.is_local is False


def test_locality_never_raises_on_an_unparseable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmatched bracket degrades to non-local instead of raising (#240):
    the properties are read on fail-open paths, so they must never be the
    thing that crashes a command."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    client = OllamaClient("qwen3", host="[::1:11434")

    assert client.locality.is_local is False


def _tags_body(entries: list[dict[str, Any]]) -> bytes:
    """Build a well-formed `/api/tags` success body containing `entries`."""
    return json.dumps({"models": entries}).encode("utf-8")


# --- Phase 9: list_models() ---------------------------------------------------


def test_list_models_returns_installed_models_with_tag_and_family() -> None:
    """A reachable server's `model` entries are returned as `InstalledModel`
    items carrying both `tag` and `details.family` (Scenario: chat model
    family "qwen" and embedding family "bert" both returned with family)."""
    body = _tags_body(
        [
            {"model": "qwen3:8b", "details": {"family": "qwen"}},
            {"model": "bge-m3:latest", "details": {"family": "bert"}},
        ]
    )
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == [
        InstalledModel(tag="qwen3:8b", family="qwen"),
        InstalledModel(tag="bge-m3:latest", family="bert"),
    ]


def test_list_models_missing_details_yields_none_family_and_is_kept() -> None:
    """An entry missing the `details` key entirely still returns an
    `InstalledModel` with `family=None` -- never dropped (Scenario: entry
    missing details/family still returned)."""
    body = _tags_body([{"model": "qwen3:8b"}])
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == [InstalledModel(tag="qwen3:8b", family=None)]


def test_list_models_missing_family_key_yields_none_family() -> None:
    """An entry with a `details` object but no `family` key still returns
    `family=None` -- never dropped."""
    body = _tags_body([{"model": "qwen3:8b", "details": {}}])
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == [InstalledModel(tag="qwen3:8b", family=None)]


def test_list_models_falls_back_to_name_field() -> None:
    """An entry with `name` but no `model` key still yields its tag (D2 field
    variance: Installed entry exposes its tag only under name); family is
    parsed the same way regardless of which tag field was used."""
    body = _tags_body([{"name": "mistral:7b", "details": {"family": "llama"}}])
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == [InstalledModel(tag="mistral:7b", family="llama")]


def test_list_models_skips_malformed_entries() -> None:
    """A non-dict entry, or a dict entry with neither `model` nor `name`, is
    skipped rather than raised or included."""
    entries: list[Any] = [{"model": "qwen3:8b"}, {"other": "junk"}, "not-a-dict"]
    body = json.dumps({"models": entries}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == [InstalledModel(tag="qwen3:8b", family=None)]


def test_list_models_unreachable_raises_ollama_unavailable() -> None:
    """A `URLError`/`TimeoutError` from `urlopen` maps to `OllamaUnavailable`,
    no raw transport exception leaks (Scenario: Unreachable server raises
    OllamaUnavailable)."""
    client = OllamaClient(
        "qwen3", urlopen=_raising_urlopen(urllib.error.URLError("Connection refused"))
    )

    with pytest.raises(OllamaUnavailable):
        client.list_models()


def test_list_models_body_read_failure_raises_ollama_unavailable() -> None:
    """A transport failure while reading the response body (not while
    connecting) still maps to `OllamaUnavailable`, symmetric with `chat()`'s
    body-read guard."""
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen_returning(
            _RaisingReadResponse(TimeoutError("timed out mid-read"))
        ),
    )

    with pytest.raises(OllamaUnavailable):
        client.list_models()


def test_list_models_non_200_raises_ollama_error() -> None:
    """An `HTTPError` maps to `OllamaError`, reusing `_map_http_error`
    (Scenario: Non-200 or malformed response raises OllamaError)."""
    body = json.dumps({"error": "internal server error"}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_raising_urlopen(_http_error(500, body)))

    with pytest.raises(OllamaError):
        client.list_models()


def test_list_models_malformed_json_raises_ollama_error() -> None:
    """A 200 body that is not valid JSON raises `OllamaError`."""
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(b"not json at all {{{", []))

    with pytest.raises(OllamaError):
        client.list_models()


@pytest.mark.parametrize("models_value", ["null", "42"])
def test_list_models_non_list_models_value_raises_ollama_error(
    models_value: str,
) -> None:
    """A valid-JSON body whose `models` is null or a non-iterable scalar maps to
    the `OllamaError` family -- never leaks a bare `TypeError` from the entry
    iteration (symmetric with `chat()`'s all-parsing-in-one-try guard)."""
    body = f'{{"models": {models_value}}}'.encode()
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    with pytest.raises(OllamaError):
        client.list_models()


# --- Phase 9b: is_embedding_model() ---------------------------------------------


@pytest.mark.parametrize("family", ["bert", "BERT", "nomic-bert", "Nomic-Bert"])
def test_is_embedding_model_known_embedding_family_returns_true(family: str) -> None:
    """A known embedding family (`bert`/`nomic-bert`, case-insensitive)
    classifies as an embedding model (Scenario: known embedding family
    classifies as embedding)."""
    model = InstalledModel(tag="bge-m3:latest", family=family)

    assert is_embedding_model(model) is True


@pytest.mark.parametrize("family", ["qwen", "llama", "unknownfamily"])
def test_is_embedding_model_non_embedding_family_returns_false(family: str) -> None:
    """A chat-model family never classifies as embedding."""
    model = InstalledModel(tag="qwen3:8b", family=family)

    assert is_embedding_model(model) is False


def test_is_embedding_model_none_family_returns_false() -> None:
    """A missing/unknown family classifies as NON-embedding -- never exclude
    on ambiguity (Scenario: missing/unknown family classifies as
    non-embedding)."""
    model = InstalledModel(tag="qwen3:8b", family=None)

    assert is_embedding_model(model) is False


@pytest.mark.parametrize(
    "tag",
    [
        "qwen3-embedding:0.6b",
        "Qwen3-Embedding:0.6B",
        "nomic-embed-text:latest",
        "granite-embedding",
    ],
)
def test_is_embedding_model_embedding_marker_in_tag_returns_true(tag: str) -> None:
    """A tag naming itself an embedding model classifies as embedding even
    when the server reports no family -- the name is evidence, not ambiguity
    (Scenario: embedding marker in tag classifies as embedding)."""
    model = InstalledModel(tag=tag, family=None)

    assert is_embedding_model(model) is True


def test_is_embedding_model_embedding_marker_outranks_chat_family() -> None:
    """The tag marker also applies when the server reports a plausible chat
    family: a mislabelled family must not readmit an embedding model to the
    chat-model picker."""
    model = InstalledModel(tag="qwen3-embedding:0.6b", family="qwen")

    assert is_embedding_model(model) is True


@pytest.mark.parametrize("tag", ["qwen3:8b", "embers:latest", "gemma3:12b"])
def test_is_embedding_model_tag_without_marker_stays_candidate(tag: str) -> None:
    """Tags that merely look similar carry no embedding evidence and keep
    chat-model candidacy -- the marker is `embed`, not a fuzzy match."""
    model = InstalledModel(tag=tag, family=None)

    assert is_embedding_model(model) is False


# --- Phase 10: model_tag_matches() ---------------------------------------------


def test_model_tag_matches_exact_match() -> None:
    """A configured tag equal to an installed entry's tag matches (Scenario:
    Exact tag match)."""
    assert model_tag_matches("qwen3:8b", ["qwen3:8b"]) is True


def test_model_tag_matches_bare_configured_matches_latest_installed() -> None:
    """A bare configured tag (`qwen3`) matches an installed `qwen3:latest`
    entry (D4 symmetric normalization; Scenario: Bare configured tag matches
    a :latest installed entry)."""
    assert model_tag_matches("qwen3", ["qwen3:latest"]) is True


def test_model_tag_matches_no_match_returns_false() -> None:
    """No installed tag matches under either normalization returns `False`
    (Scenario: No matching entry returns False)."""
    assert model_tag_matches("qwen3:8b", ["llama3.2:1b", "mistral:7b"]) is False


def test_model_tag_matches_case_sensitive_mismatch() -> None:
    """Differing case never matches (D4 honest exact match, no lowercasing)."""
    assert model_tag_matches("Qwen3:8b", ["qwen3:8b"]) is False


# --- Phase 11: embed() ---------------------------------------------------------


def _embed_body(rows: list[list[float]]) -> bytes:
    """Build a well-formed `/api/embed` success body using the plural
    `embeddings` key (current Ollama response shape)."""
    return json.dumps({"embeddings": rows}).encode("utf-8")


def _embed_body_singular(row: list[float]) -> bytes:
    """Build a `/api/embed` success body using the legacy singular
    `embedding` key (older Ollama response shape, D2 field variance)."""
    return json.dumps({"embedding": row}).encode("utf-8")


def _poison_urlopen(
    request: urllib.request.Request, timeout: float | None = None
) -> Any:
    """An `urlopen` stand-in that fails the test if it is ever called --
    used to prove the empty-input short-circuit makes zero network calls."""
    raise AssertionError("urlopen must not be called")


def test_embed_empty_input_returns_empty_list_without_network_call() -> None:
    """`embed([])` short-circuits to `[]` with no HTTP call at all."""
    client = OllamaClient("qwen3-embedding:0.6b", urlopen=_poison_urlopen)

    result = client.embed([])

    assert result == []


def test_embed_success_posts_model_and_input_returns_ordered_rows() -> None:
    """`embed(texts)` POSTs `{model, input}` to `{host}/api/embed` and returns
    one `EMBED_DIM`-float row per input, in the same order (embeddings-key
    happy path)."""
    captured: list[urllib.request.Request] = []
    row_a = [0.1] * EMBED_DIM
    row_b = [0.2] * EMBED_DIM
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        host="http://example.internal:11434",
        urlopen=_fake_urlopen(_embed_body([row_a, row_b]), captured),
    )

    result = client.embed(["a", "b"])

    assert result == [row_a, row_b]
    assert captured[0].full_url == "http://example.internal:11434/api/embed"
    sent = _sent_body(captured[0])
    assert sent["model"] == "qwen3-embedding:0.6b"
    assert sent["input"] == ["a", "b"]


def test_embed_row_count_mismatch_fewer_rows_raises_ollama_error() -> None:
    """Ollama returning fewer embedding rows than input texts violates the
    Embedder contract (one row per input, in order) and must raise
    `OllamaError` rather than silently returning a length-mismatched list."""
    row = [0.1] * EMBED_DIM
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(_embed_body([row]), []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError):
        client.embed(["a", "b"])


def test_embed_row_count_mismatch_more_rows_raises_ollama_error() -> None:
    """Ollama returning more embedding rows than input texts also violates
    the Embedder contract and must raise `OllamaError`."""
    row_a = [0.1] * EMBED_DIM
    row_b = [0.2] * EMBED_DIM
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(_embed_body([row_a, row_b]), []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError):
        client.embed(["only"])


def test_embed_singular_embedding_key_wrapped_as_one_item_list() -> None:
    """A legacy singular `embedding` key (one row, no `embeddings` plural) is
    parsed and wrapped as a one-item list (D2 response-shape drift)."""
    row = [0.3] * EMBED_DIM
    client = OllamaClient(
        "qwen3-embedding:0.6b", urlopen=_fake_urlopen(_embed_body_singular(row), [])
    )

    result = client.embed(["only"])

    assert result == [row]


def test_embed_row_wrong_dimension_raises_ollama_error() -> None:
    """A returned row whose length is not `EMBED_DIM` raises `OllamaError`."""
    short_row = [0.1] * (EMBED_DIM - 1)
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(_embed_body([short_row]), []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_row_non_numeric_values_raises_ollama_error() -> None:
    """A returned row containing a non-numeric value raises `OllamaError`
    even when its length matches `EMBED_DIM`."""
    bad_row: list[Any] = [0.1] * (EMBED_DIM - 1) + ["not-a-float"]
    body = json.dumps({"embeddings": [bad_row]}).encode("utf-8")
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(body, []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_malformed_json_raises_ollama_error() -> None:
    """A 200 body that is not valid JSON raises `OllamaError`."""
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(b"not json at all {{{", []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_non_object_json_body_raises_ollama_error() -> None:
    """A 200 body that is valid JSON but not a JSON object at the top level
    (e.g. a bare list) raises `OllamaError`, not an unguarded `TypeError`."""
    body = json.dumps([0.1] * EMBED_DIM).encode("utf-8")
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(body, []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_missing_embeddings_and_embedding_keys_raises_ollama_error() -> None:
    """A well-formed JSON body carrying neither `embeddings` nor `embedding`
    (response-shape drift beyond the two recognized keys) raises `OllamaError`."""
    body = json.dumps({"unexpected": "shape"}).encode("utf-8")
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(body, []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_connect_phase_transport_failure_raises_ollama_unavailable() -> None:
    """A `URLError` while connecting maps to `OllamaUnavailable` (mirrors
    `chat()`). `embed_retry_attempts=1`: this test asserts the error
    MAPPING, not the retry loop (D3's retry behavior has its own dedicated
    tests) -- no real sleep."""
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_raising_urlopen(urllib.error.URLError("Connection refused")),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaUnavailable):
        client.embed(["a"])


def test_embed_read_phase_transport_failure_raises_ollama_unavailable() -> None:
    """A `TimeoutError` while reading the response body (not while connecting)
    still maps to `OllamaUnavailable` (mirrors `chat()`'s body-read guard)."""
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen_returning(
            _RaisingReadResponse(TimeoutError("timed out mid-read"))
        ),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaUnavailable):
        client.embed(["a"])


def test_embed_non_404_http_error_raises_ollama_error() -> None:
    """A non-404 HTTP error is mapped to `OllamaError` (reuses `_map_http_error`)."""
    body = json.dumps({"error": "internal server error"}).encode("utf-8")
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_raising_urlopen(_http_error(500, body)),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError) as excinfo:
        client.embed(["a"])

    assert not isinstance(excinfo.value, OllamaModelNotFound)


def test_embed_404_model_not_found_body_raises_ollama_model_not_found() -> None:
    """A 404 whose body reports the model tag as not found raises
    `OllamaModelNotFound` (reuses `_map_http_error`)."""
    body = json.dumps({"error": "model 'ghost-embed:latest' not found"}).encode("utf-8")
    client = OllamaClient(
        "ghost-embed:latest", urlopen=_raising_urlopen(_http_error(404, body))
    )

    with pytest.raises(OllamaModelNotFound):
        client.embed(["a"])


# --- Phase 12: embed() retry-with-backoff (D1/D3) -------------------------


def _sequenced_urlopen(effects: list[Any]) -> Any:
    """Return an `urlopen` stand-in that replays `effects` in order: an
    `Exception` instance is raised, anything else is returned as the
    response. Raises `AssertionError` if called more times than `effects`
    provides -- proves the retry loop makes exactly the expected number of
    attempts, no more."""
    state = {"calls": 0}

    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        idx = state["calls"]
        state["calls"] += 1
        if idx >= len(effects):
            raise AssertionError(
                f"urlopen called {idx + 1} times, only {len(effects)} effects configured"
            )
        effect = effects[idx]
        if isinstance(effect, Exception):
            raise effect
        return effect

    return _urlopen


class _SleepSpy:
    """A `sleep`-shaped fake: records every duration it was called with,
    never actually sleeps (Strict TDD: no real sleep in tests)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def test_embed_transient_error_retries_then_succeeds() -> None:
    """A transient generic `OllamaError` on attempt 1 (a 500) is retried, and
    a success on attempt 2 returns the vectors with no exception raised --
    the caller observes no partial result and no trace of the retry beyond
    the injected sleep spy (llm-client: Transient failure followed by
    success is transparent to the caller)."""
    row = [0.1] * EMBED_DIM
    sleep_spy = _SleepSpy()
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_sequenced_urlopen(
            [
                _http_error(500, b'{"error": "internal"}'),
                _FakeResponse(_embed_body([row])),
            ]
        ),
        sleep=sleep_spy,
    )

    result = client.embed(["a"])

    assert result == [row]
    assert sleep_spy.calls == [pytest.approx(0.5)]


def test_embed_immediate_success_makes_no_retry_attempt() -> None:
    """A first-attempt success makes exactly one request and never calls
    `sleep` (llm-client: Immediate success makes no retry attempt)."""
    row = [0.1] * EMBED_DIM
    captured: list[urllib.request.Request] = []
    sleep_spy = _SleepSpy()
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(_embed_body([row]), captured),
        sleep=sleep_spy,
    )

    result = client.embed(["a"])

    assert result == [row]
    assert len(captured) == 1
    assert sleep_spy.calls == []


def test_embed_persistent_error_exhausts_default_retries_and_raises() -> None:
    """A generic `OllamaError` (500) on every attempt exhausts the default 3
    attempts, then raises -- `sleep` is called exactly 2 times (between the
    3 attempts), with exponentially increasing durations (llm-client:
    Exhausted retry budget raises to the caller)."""
    sleep_spy = _SleepSpy()
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_sequenced_urlopen(
            [
                _http_error(500, b'{"error": "internal"}'),
                _http_error(500, b'{"error": "internal"}'),
                _http_error(500, b'{"error": "internal"}'),
            ]
        ),
        sleep=sleep_spy,
    )

    with pytest.raises(OllamaError) as excinfo:
        client.embed(["a"])

    assert not isinstance(excinfo.value, (OllamaUnavailable, OllamaModelNotFound))
    assert sleep_spy.calls == [pytest.approx(0.5), pytest.approx(1.0)]


def test_embed_model_not_found_never_retried() -> None:
    """`OllamaModelNotFound` raises immediately on its first occurrence,
    consuming zero retry attempts -- `sleep` is never called and `urlopen`
    is called exactly once (llm-client: OllamaModelNotFound is never
    retried)."""
    sleep_spy = _SleepSpy()
    body = json.dumps({"error": "model 'ghost-embed:latest' not found"}).encode("utf-8")
    client = OllamaClient(
        "ghost-embed:latest",
        urlopen=_sequenced_urlopen([_http_error(404, body)]),
        sleep=sleep_spy,
    )

    with pytest.raises(OllamaModelNotFound):
        client.embed(["a"])

    assert sleep_spy.calls == []


def test_embed_ollama_unavailable_may_retry_but_raises_once_exhausted() -> None:
    """An unreachable server (`OllamaUnavailable` on every attempt) may be
    retried like the generic case, but still ultimately raises once the
    retry budget is exhausted (design D3: OllamaUnavailable may retry but
    is ultimately FATAL if exhausted)."""
    sleep_spy = _SleepSpy()
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_sequenced_urlopen(
            [
                urllib.error.URLError("Connection refused"),
                urllib.error.URLError("Connection refused"),
                urllib.error.URLError("Connection refused"),
            ]
        ),
        sleep=sleep_spy,
    )

    with pytest.raises(OllamaUnavailable):
        client.embed(["a"])

    assert sleep_spy.calls == [pytest.approx(0.5), pytest.approx(1.0)]


def test_embed_retry_attempts_and_backoff_base_are_injectable() -> None:
    """`embed_retry_attempts` and `embed_retry_backoff_base` constructor
    args override the defaults, proving both knobs are independently
    injectable (D3: both injectable)."""
    sleep_spy = _SleepSpy()
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_sequenced_urlopen(
            [
                _http_error(500, b'{"error": "internal"}'),
                _http_error(500, b'{"error": "internal"}'),
            ]
        ),
        sleep=sleep_spy,
        embed_retry_attempts=2,
        embed_retry_backoff_base=0.1,
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])

    assert sleep_spy.calls == [pytest.approx(0.1)]


def test_chat_never_retries_on_transient_error() -> None:
    """`chat()` is untouched by the `embed()` retry loop (D1: retry home is
    `embed` only, no scope creep into `chat`) -- a single transient failure
    raises immediately, `sleep` is never called."""
    sleep_spy = _SleepSpy()
    client = OllamaClient(
        "qwen3",
        urlopen=_raising_urlopen(_http_error(500, b'{"error": "internal"}')),
        sleep=sleep_spy,
    )

    with pytest.raises(OllamaError):
        client.chat([{"role": "user", "content": "hi"}])

    assert sleep_spy.calls == []


# --- Phase 13: OllamaEmbeddingDimensionMismatch (D7/D8) -----------------------


def test_embed_row_wrong_length_raises_dimension_mismatch_not_generic_error() -> None:
    """A wrong-length row raises the distinct `OllamaEmbeddingDimensionMismatch`,
    not the generic `OllamaError` (task 2.1; llm-client: Wrong-dimension row
    raises the distinct permanent error)."""
    short_row = [0.1] * (EMBED_DIM - 1)
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(_embed_body([short_row]), []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaEmbeddingDimensionMismatch):
        client.embed(["a"])


def test_embed_dimension_mismatch_message_names_actual_and_expected_length() -> None:
    """The message states both the actual row length and the expected
    `EMBED_DIM` (task 2.2; llm-client: Message names the actual and expected
    dimension), AND names the fault as permanent.

    The permanence phrasing is load-bearing for two CLI requirements that
    render this exception's own text rather than restating it -- `reindex`'s
    permanent-mismatch failure message and, since issue #209, `query`'s. Both
    are specified to identify the fault as a permanent dimension mismatch and
    to avoid transient "will retry next run" phrasing, so a reword here would
    silently stop satisfying them: each CLI test supplies its own exception
    instance and therefore cannot catch it. This assertion is the only thing
    pinning the production wording those requirements depend on."""
    short_row = [0.1] * 768
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(_embed_body([short_row]), []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaEmbeddingDimensionMismatch) as excinfo:
        client.embed(["a"])

    message = str(excinfo.value)
    assert "768" in message
    assert str(EMBED_DIM) in message
    assert "permanent dimension mismatch" in message
    assert "will retry next run" not in message


def test_embed_dimension_mismatch_is_a_ollama_error_subclass() -> None:
    """`OllamaEmbeddingDimensionMismatch` subclasses `OllamaError`, so an
    unmodified bare `except OllamaError` still catches it (task 2.2;
    llm-client: Subclass relationship preserves existing broad catches)."""
    assert issubclass(OllamaEmbeddingDimensionMismatch, OllamaError)


def test_embed_dimension_mismatch_never_retried() -> None:
    """A dimension mismatch on every attempt is raised immediately on the
    first occurrence, consuming zero retry attempts -- `sleep` is never
    called and `urlopen` is called exactly once (task 2.4/2.5; llm-client:
    Dimension mismatch is never retried)."""
    sleep_spy = _SleepSpy()
    short_row = [0.1] * (EMBED_DIM - 1)
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_sequenced_urlopen([_FakeResponse(_embed_body([short_row]))]),
        sleep=sleep_spy,
    )

    with pytest.raises(OllamaEmbeddingDimensionMismatch):
        client.embed(["a"])

    assert sleep_spy.calls == []


def test_embed_row_non_numeric_value_still_raises_generic_ollama_error() -> None:
    """A non-numeric row entry of the CORRECT length still raises the
    generic `OllamaError`, not `OllamaEmbeddingDimensionMismatch` (task 2.6;
    D7 scope-discipline regression guard: only the wrong-LENGTH branch gets
    the new permanent error)."""
    bad_row: list[Any] = [0.1] * (EMBED_DIM - 1) + ["not-a-float"]
    body = json.dumps({"embeddings": [bad_row]}).encode("utf-8")
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(body, []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError) as excinfo:
        client.embed(["a"])

    assert not isinstance(excinfo.value, OllamaEmbeddingDimensionMismatch)


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all {{{",
        json.dumps({"unexpected": "shape"}).encode("utf-8"),
    ],
)
def test_embed_malformed_or_missing_key_response_stays_generic_ollama_error(
    body: bytes,
) -> None:
    """Malformed JSON and a missing vector key both still raise the generic
    `OllamaError`, unaffected by the new dimension-mismatch branch (task 2.7;
    parity with the pre-existing malformed-response behavior)."""
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(body, []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaError) as excinfo:
        client.embed(["a"])

    assert not isinstance(excinfo.value, OllamaEmbeddingDimensionMismatch)


def test_embed_singular_key_wrong_dimension_also_raises_dimension_mismatch() -> None:
    """The dimension-mismatch branch applies identically to the legacy
    singular `embedding` key, not only the plural `embeddings` key (task 2.7
    parity guard)."""
    short_row = [0.1] * (EMBED_DIM - 1)
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_fake_urlopen(_embed_body_singular(short_row), []),
        embed_retry_attempts=1,
    )

    with pytest.raises(OllamaEmbeddingDimensionMismatch):
        client.embed(["only"])


# --- issue #355: a transport failure never displays credentials ----------------

_CREDENTIALED_HOST = "http://user:s3cret@remote.example:11434"
"""A host carrying userinfo, the shape a user gets by exporting `OLLAMA_HOST`
with an inline password for some other tool. `OllamaClient` inherits that env
var, so this is not a contrived value (issue #355)."""

_PLANTED_USERINFO = "s3cret"


def test_chat_connect_failure_never_displays_userinfo() -> None:
    """A connect-phase transport failure against a credentialed host must not
    put the password in the `OllamaUnavailable` message (issue #355).

    The CLI handlers echo this exception verbatim to stderr, so whatever the
    message carries is printed. `display_host` is the module's ONE authority
    for a host string that may be shown (`BackendHostLocality`); the transport
    error path used to bypass it and interpolate the raw resolved host."""
    client = OllamaClient(
        "qwen3",
        host=_CREDENTIALED_HOST,
        urlopen=_raising_urlopen(urllib.error.URLError("Connection refused")),
    )

    with pytest.raises(OllamaUnavailable) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    assert _PLANTED_USERINFO not in str(excinfo.value)
    assert "user:" not in str(excinfo.value)


def test_chat_body_read_failure_never_displays_userinfo() -> None:
    """The read-phase branch shares `_unavailable` with the connect-phase one,
    so it must redact identically -- pinned separately because a future edit
    could give either branch its own message (issue #355)."""
    client = OllamaClient(
        "qwen3",
        host=_CREDENTIALED_HOST,
        urlopen=_fake_urlopen_returning(
            _RaisingReadResponse(TimeoutError("timed out mid-read"))
        ),
    )

    with pytest.raises(OllamaUnavailable) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    assert _PLANTED_USERINFO not in str(excinfo.value)


def test_embed_transport_failure_never_displays_userinfo() -> None:
    """`embed()` reaches the same `_unavailable`, after its retry ladder is
    exhausted (issue #355)."""
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        host=_CREDENTIALED_HOST,
        urlopen=_raising_urlopen(urllib.error.URLError("Connection refused")),
        embed_retry_attempts=1,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(OllamaUnavailable) as excinfo:
        client.embed(["anything"])

    assert _PLANTED_USERINFO not in str(excinfo.value)


def test_list_models_transport_failure_never_displays_userinfo() -> None:
    """`list_models()` is the probe `doctor` and `init`'s preflight run, so it
    is the most likely of the three to be hit by a misconfigured user -- and
    the most likely to have its error surfaced (issue #355)."""
    client = OllamaClient(
        "qwen3",
        host=_CREDENTIALED_HOST,
        urlopen=_raising_urlopen(urllib.error.URLError("Connection refused")),
    )

    with pytest.raises(OllamaUnavailable) as excinfo:
        client.list_models()

    assert _PLANTED_USERINFO not in str(excinfo.value)


def test_transport_failure_still_names_the_redacted_host() -> None:
    """Redaction must not degrade the message into uselessness: the host is
    still named, minus the userinfo, so "not reachable at ..." keeps telling
    the user WHERE the attempt went (issue #355).

    Without this pin, dropping the host entirely would pass every assertion
    above while making the error undiagnosable."""
    client = OllamaClient(
        "qwen3",
        host=_CREDENTIALED_HOST,
        urlopen=_raising_urlopen(urllib.error.URLError("Connection refused")),
    )

    with pytest.raises(OllamaUnavailable) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    assert "remote.example:11434" in str(excinfo.value)


def test_transport_failure_on_an_unparseable_host_displays_the_placeholder() -> None:
    """A malformed host whose redacted remainder is empty displays the shared
    `<unparseable>` placeholder rather than an empty string or the raw value --
    the same degradation `classify_backend_host` already applies everywhere
    else a host is shown (issues #353, #355)."""
    client = OllamaClient(
        "qwen3",
        host="user:s3cret/x@",
        urlopen=_raising_urlopen(urllib.error.URLError("Connection refused")),
    )

    with pytest.raises(OllamaUnavailable) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    assert _PLANTED_USERINFO not in str(excinfo.value)
    assert "<unparseable>" in str(excinfo.value)


# --- Phase 8: layering guard --------------------------------------------------


_REQUEST_ENDPOINTS = frozenset({"/api/chat", "/api/embed", "/api/tags"})
"""The EXACT endpoint paths `OllamaClient` builds request URLs from -- the
whole sanctioned vocabulary, enumerated rather than characterized
(issue #359).

Every earlier version of this carve-out tried to DESCRIBE an endpoint path
-- first "starts with the prefix", then "starts with the prefix and is the
only constant", then "matches an endpoint-shaped pattern in full". Each
description admitted something that merely resembled the real thing, and
each was found by a reviewer rather than by this suite. The last one still
admitted a hyphen- or underscore-joined continuation of the endpoint name,
because those characters are legitimately part of endpoint names and cannot
be excluded without also rejecting a plausible future endpoint.

Set membership ends that whole class of defect: there is no pattern left to
resemble. The cost is real and accepted -- adding an endpoint to the client
means adding its path here -- and that friction is the point. A new URL
built from the raw credential-bearing host should be a line someone writes
deliberately in the security guard, not something a regex waves through."""


def _raw_host_offenders(source: str) -> list[int]:
    """Return the line numbers of every f-string in `source` that interpolates
    the raw `self._host` outside the one sanctioned request-URL shape.

    The sanctioned shape is EXACTLY two parts -- the host, then a single
    string constant that is a bare endpoint path in full
    (`f"{self._host}/api/chat"`). The request must go to the real host,
    credentials included, so that one shape is admitted and every other use
    is an offender.

    Both halves of that rule were learned the hard way, each from a review
    finding, and each time the defect was the same one: admitting something
    that merely RESEMBLES the sanctioned shape.

    - #355: a carve-out asking whether SOME constant segment began with the
      endpoint prefix admitted `f"{self._host}/api/chat unreachable: {exc}"`.
      Fixed by requiring exactly two parts in fixed positions.
    - #359: that still left the two-part form, where a single trailing
      constant begins with an endpoint name and fuses straight into prose --
      no second interpolation needed to leak. A full match against an
      endpoint-shaped pattern closed the space-joined form but not the
      hyphen- or underscore-joined one, since those characters belong to
      real endpoint names. Fixed by dropping description entirely: the
      constant must be a member of `_REQUEST_ENDPOINTS`.

    Nothing here characterizes an endpoint any more, so there is no shape
    left to resemble and no fourth variation of this defect to find. Every
    near-miss above is pinned by
    `test_the_raw_host_guard_rejects_a_url_prefixed_diagnostic`."""

    def _is_raw_host(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "_host"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    def _is_endpoint_constant(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _REQUEST_ENDPOINTS
        )

    offenders: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.JoinedStr):
            continue
        if not any(
            isinstance(part, ast.FormattedValue) and _is_raw_host(part.value)
            for part in node.values
        ):
            continue
        parts = node.values
        is_request_url = (
            len(parts) == 2
            and isinstance(parts[0], ast.FormattedValue)
            and _is_raw_host(parts[0].value)
            and _is_endpoint_constant(parts[1])
        )
        if not is_request_url:
            offenders.append(node.lineno)
    return offenders


def test_no_error_message_interpolates_the_raw_host() -> None:
    """No f-string anywhere in `llm/ollama.py` interpolates `self._host`
    outside the request-URL shape (issue #355).

    The instance-level pins above prove `_unavailable` is clean today. This
    guard proves the CLASS of bug stays closed: any future error message,
    log line, or diagnostic that reaches for the raw resolved host instead
    of the redacted `display_host` fails here, without anyone remembering to
    re-audit. Written in the spirit of the sibling structural guards in this
    file and `test_offline_stub_covers_every_network_method` -- the bug
    #355 fixed was ONE site, but nothing stopped the next one."""
    source = (_REPO_ROOT / "src" / "openkos" / "llm" / "ollama.py").read_text(
        encoding="utf-8"
    )

    offenders = _raw_host_offenders(source)

    assert not offenders, (
        f"ollama.py interpolates the raw `self._host` into a non-URL string at "
        f"line(s) {offenders}; display a host only via `locality.display_host`, "
        f"which redacts userinfo (issue #355)"
    )


def test_the_raw_host_guard_rejects_a_url_prefixed_diagnostic() -> None:
    """The guard admits the request-URL shape and NOTHING that merely looks
    like it -- the near-misses raised by the #355 and #359 reviews.

    A guard for a credential leak is only worth its line count if it rejects
    the shapes a future author would plausibly write. `f"{self._host}/api/chat
    unreachable: {exc}"` reads like a URL, begins like a URL, and leaks the
    password; an `any(... startswith("/api/"))` carve-out would have waved it
    through while the CHANGELOG claimed the class was closed.

    Issue #359 is the SAME defect one layer down, and the reason the endpoint
    constant is now matched in full rather than by prefix. Tightening the
    shape to exactly two parts closed the three-part form above but left the
    two-part one open: a single trailing constant that begins with an
    endpoint name and then continues with prose needs no second interpolation
    to leak. Two rounds of "starts with" were two rounds of admitting
    something that merely resembles the sanctioned shape -- which is why the
    predicate below is an anchored full match with no prefix semantics left
    to exploit."""
    # The one sanctioned shape, and the three real endpoints it must admit.
    assert _raw_host_offenders('x = f"{self._host}/api/chat"') == []
    assert _raw_host_offenders('x = f"{self._host}/api/embed"') == []
    assert _raw_host_offenders('x = f"{self._host}/api/tags"') == []
    # An endpoint NOT in the client's vocabulary is not sanctioned: adding
    # one is a deliberate line in the guard, not something a shape admits.
    assert _raw_host_offenders('x = f"{self._host}/api/show"') == [1]
    # #359: two parts, prose fused onto the endpoint name, no second
    # interpolation -- the shape a prefix test cannot tell from a URL.
    assert _raw_host_offenders('x = f"{self._host}/api/chat unreachable"') == [1]
    assert _raw_host_offenders('x = f"{self._host}/api/chat is not reachable"') == [1]
    # #359 again: prose fused with a hyphen or underscore, the characters a
    # real endpoint name may legitimately contain.
    assert _raw_host_offenders('x = f"{self._host}/api/chat-unreachable"') == [1]
    assert _raw_host_offenders('x = f"{self._host}/api/chat_unreachable"') == [1]
    # #355: three parts, prose after the endpoint name.
    assert _raw_host_offenders('x = f"{self._host}/api/chat unreachable: {exc}"') == [1]
    # Prose before the host, and the plain diagnostic shape the bug shipped as.
    assert _raw_host_offenders('x = f"connecting to {self._host}/api/chat"') == [1]
    assert _raw_host_offenders('x = f"not reachable at {self._host}: {exc}"') == [1]
    # The redacted accessor is not the raw host and is never an offender.
    assert _raw_host_offenders('x = f"reached {self.locality.display_host}"') == []


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


def test_generation_capped_message_names_the_ceiling_when_one_is_configured() -> None:
    """With a ceiling configured, the message names it, so the operator knows
    which number to raise (#440)."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        urlopen=_fake_urlopen(
            _ok_body_with_done_reason("truncated mid-", "length"), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    assert "8192" in str(caught.value)


def test_generation_capped_message_blames_the_backend_when_no_ceiling_is_set() -> None:
    """With NO ceiling configured, the message must not claim a configured
    ceiling of `None` (#440).

    `done_reason == "length"` is reachable without `num_predict` binding --
    Ollama reports it when generation stops for length reasons at all,
    including the model's own context window filling. The old message
    interpolated the ceiling unconditionally and rendered "the configured
    max_generation_tokens ceiling (None)", which is self-contradictory:
    nothing was configured."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=None,
        urlopen=_fake_urlopen(
            _ok_body_with_done_reason("truncated mid-", "length"), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    # Pinned POSITIVELY against the sentence actually raised, not only by the
    # absence of "None"/"configured": absence assertions alone would keep
    # passing against any vaguer message that merely avoided those two words
    # (review finding on this change).
    assert message == (
        "Ollama stopped generation for length before the reply finished, "
        "with no max_generation_tokens ceiling set on this client -- the "
        "backend's own limit cut it off; the response is truncated and "
        "unusable."
    )
    # The two words the old message got wrong stay explicitly excluded, so a
    # regression back to "the configured ceiling (None)" fails here loudly.
    assert "None" not in message
    assert "configured" not in message


def test_chat_does_not_raise_when_done_reason_is_an_unexpected_string() -> None:
    """A `done_reason` the client does not know about is not a cap (#440).

    The check is an equality test against `"length"`, so this is correct by
    construction -- pinned because "unexpected value" is exactly the case a
    future backend change would introduce."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        urlopen=_fake_urlopen(
            _ok_body_with_done_reason("a complete reply", "load"), captured
        ),
    )

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result == "a complete reply"


def test_chat_does_not_raise_when_done_reason_is_not_a_string() -> None:
    """A non-string `done_reason` is not a cap either, and must not raise a
    `TypeError` on the comparison (#440)."""
    captured: list[urllib.request.Request] = []
    body = json.dumps(
        {
            "message": {"role": "assistant", "content": "a complete reply"},
            "done": True,
            "done_reason": 7,
        }
    ).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, captured))

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result == "a complete reply"


# --- chat() must stay safe to call from several threads (#748) ---------------


def _self_mutation_offenders(source: str, *, method: str = "chat") -> list[int]:
    """Line numbers inside `method` that write to state reachable from `self`.

    `extraction.concept._fan_out_windows` (#744) calls `chat` from two worker
    threads against ONE shared client. That is safe today for a single
    structural reason: `chat` builds everything it needs as locals and writes
    nothing back onto the instance. Nothing enforced it -- the claim lived in
    a docstring, and two review lenses flagged that independently (#748).

    A comment is too weak because the invariant is one line from being false
    and breaking it is INVISIBLE to the rest of the suite. Adding

        self._last_reply = content

    introduces a data race no existing test can see: the default path is
    serial, and the concurrency tests use a fake backend rather than this
    client. The suite stays green and the corruption appears only on a real
    opted-in run, intermittently.

    **This asks the AST what a node IS, never what form it was written in.**
    An earlier version enumerated `Assign`, `AugAssign`, `AnnAssign`, tuple
    and starred targets -- and four review rounds found four more ways
    through it: nested attributes, subscripts, `for` targets, `with ... as`.
    That list has no end. Python already marks every write: a target node
    carries `ctx=Store` (or `Del`), a read carries `ctx=Load`. Flagging any
    `self`-rooted `Attribute` or `Subscript` in a store context therefore
    covers every assignment form the language has, including ones added
    later, with no form-by-form tail to maintain.

    Two things a store context cannot express are handled separately: a bare
    call that mutates (`self._cache.append(x)`), and `setattr`.
    """
    tree = ast.parse(source)
    owner = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(
                isinstance(item, ast.FunctionDef) and item.name == method
                for item in node.body
            )
        ),
        None,
    )
    assert owner is not None, f"no class defines a method named {method!r}"
    # Scoped to THIS class's own body: keying every FunctionDef in the file by
    # bare name would let an unrelated class's same-named method stand in for
    # the one actually called.
    methods = {
        item.name: item for item in owner.body if isinstance(item, ast.FunctionDef)
    }

    def _is_self(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == "self"

    def _rooted_at_self(node: ast.expr) -> bool:
        """Whether an attribute/subscript chain bottoms out at `self`."""
        while isinstance(node, ast.Attribute | ast.Subscript):
            node = node.value
        return _is_self(node)

    def _mutating_call(node: ast.stmt) -> bool:
        """A bare call that can only be there for its effect on `self`.

        A pure call's value gets used -- `chat` itself does
        `response = self._urlopen(...)` and `raise self._unavailable(exc)`,
        neither of which is a bare statement. Discarding the result of a call
        on instance state is what mutation looks like.
        """
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            return False
        func = node.value.func
        if isinstance(func, ast.Name) and func.id == "setattr":
            return bool(node.value.args) and _is_self(node.value.args[0])
        return isinstance(func, ast.Attribute) and _rooted_at_self(func.value)

    def _self_methods_called(fn: ast.FunctionDef) -> set[str]:
        """Names of same-class methods `fn` invokes as `self.<name>(...)`.

        The guard has to follow these or it is trivially escapable: `chat`
        really does call `self._unavailable(exc)`, so a mutation moved one
        frame down would satisfy a subtree-only check while breaking the
        exact property the check exists to protect.

        Names that are not methods of this class are skipped, which is
        correct rather than lax: `self._urlopen` is an INJECTED callable, not
        code this repository controls, and following it would mean auditing
        whatever a caller passed.
        """
        return {
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _is_self(node.func.value)
            and node.func.attr in methods
        }

    offenders: list[int] = []
    pending, seen = [method], {method}
    while pending:
        fn = methods[pending.pop()]
        for node in ast.walk(fn):
            if (isinstance(node, ast.stmt) and _mutating_call(node)) or (
                isinstance(node, ast.Attribute | ast.Subscript)
                and isinstance(node.ctx, ast.Store | ast.Del)
                and _rooted_at_self(node.value)
            ):
                offenders.append(node.lineno)
        for name in _self_methods_called(fn) - seen:
            seen.add(name)
            pending.append(name)
    return sorted(offenders)


def test_chat_writes_nothing_back_onto_the_client() -> None:
    """`OllamaClient.chat` must remain free of writes to `self`.

    This is the property that makes #744's concurrent fan-out correct. If it
    ever fails, the fix is NOT to relax this test -- it is to keep the state
    out of `chat`, or to stop calling it concurrently."""
    source = (_REPO_ROOT / "src" / "openkos" / "llm" / "ollama.py").read_text(
        encoding="utf-8"
    )

    assert _self_mutation_offenders(source) == []


def test_the_self_mutation_guard_catches_every_write_form() -> None:
    """The guard's own detection, form by form.

    Without this the guard could be silently inert -- a walker that matched
    nothing would report a clean `chat` just as convincingly as a correct one
    does. Every case below is a real way to mutate shared state, and the last
    group is the one an enumerate-the-forms guard kept missing across four
    review rounds until the check was rebuilt on `ctx`.
    """

    def _chat_body(line: str) -> str:
        return f"class C:\n    def chat(self, messages):\n        {line}\n"

    # Plain, augmented, annotated.
    assert _self_mutation_offenders(_chat_body("self._last = 1")) == [3]
    assert _self_mutation_offenders(_chat_body("self._calls += 1")) == [3]
    assert _self_mutation_offenders(_chat_body("self._n: int = 1")) == [3]
    # Unpacking targets.
    assert _self_mutation_offenders(_chat_body("self._a, x = 1, 2")) == [3]
    assert _self_mutation_offenders(_chat_body("[self._a, x] = [1, 2]")) == [3]
    assert _self_mutation_offenders(_chat_body("*self._a, x = [1, 2]")) == [3]
    # Nested attributes and SUBSCRIPTS -- neither is a one-level attribute.
    assert _self_mutation_offenders(_chat_body("self._session.timeout = 5")) == [3]
    assert _self_mutation_offenders(_chat_body("self._a.b.c = 5")) == [3]
    assert _self_mutation_offenders(_chat_body('self._cache["k"] = 1')) == [3]
    assert _self_mutation_offenders(_chat_body('self._a.b["k"] += 1')) == [3]
    # Binding forms that are not assignments at all.
    assert _self_mutation_offenders(_chat_body("for self._i in range(3): pass")) == [3]
    assert _self_mutation_offenders(_chat_body("with open('f') as self._f: pass")) == [
        3
    ]
    assert _self_mutation_offenders(_chat_body("del self._cache")) == [3]
    # Mutation through a CALL leaves no store context behind.
    assert _self_mutation_offenders(_chat_body("self._cache.append(1)")) == [3]
    assert _self_mutation_offenders(_chat_body("self._reset()")) == [3]
    assert _self_mutation_offenders(_chat_body('setattr(self, "_x", 1)')) == [3]
    # READS of self, locals, and writes to anything else are all fine.
    assert _self_mutation_offenders(_chat_body("url = self._host")) == []
    assert _self_mutation_offenders(_chat_body('x = self._cache["k"]')) == []
    assert _self_mutation_offenders(_chat_body("other._last = 1")) == []
    assert _self_mutation_offenders(_chat_body("other.session.timeout = 5")) == []
    assert _self_mutation_offenders(_chat_body('setattr(other, "_x", 1)')) == []
    # A call whose VALUE is used is not a bare mutation -- chat() really does
    # `response = self._urlopen(...)` and `raise self._unavailable(exc)`.
    assert _self_mutation_offenders(_chat_body("r = self._urlopen(1)")) == []
    assert _self_mutation_offenders(_chat_body("raise self._unavailable(1)")) == []


def test_the_self_mutation_guard_scopes_to_the_right_methods() -> None:
    """What the guard follows, and what it deliberately does not."""
    # An UNRELATED method is out of scope; one `chat` CALLS is not.
    unrelated = (
        "class C:\n"
        "    def chat(self, messages):\n"
        "        return 1\n"
        "    def embed(self, texts):\n"
        "        self._last = 1\n"
    )
    assert _self_mutation_offenders(unrelated) == []
    assert _self_mutation_offenders(unrelated, method="embed") == [5]
    # Transitive: moving the write one frame down must not hide it.
    delegated = (
        "class C:\n"
        "    def chat(self, messages):\n"
        "        return self._build(messages)\n"
        "    def _build(self, messages):\n"
        "        self._last = 1\n"
        "        return 1\n"
    )
    assert _self_mutation_offenders(delegated) == [5]
    # An injected callable is NOT followed: it is not ours to audit.
    injected = (
        "class C:\n"
        "    def chat(self, messages):\n"
        "        return self._urlopen(messages)\n"
    )
    assert _self_mutation_offenders(injected) == []
    # A same-named method on an UNRELATED class must not stand in for ours.
    shadowed = (
        "class Other:\n"
        "    def _build(self, m):\n"
        "        self._last = 1\n"
        "class C:\n"
        "    def chat(self, messages):\n"
        "        return self._build(messages)\n"
        "    def _build(self, m):\n"
        "        return 1\n"
    )
    assert _self_mutation_offenders(shadowed) == []


def test_chat_serves_concurrent_callers_their_own_replies() -> None:
    """Several threads sharing ONE client each get their own reply back.

    The structural guard above proves `chat` writes nothing to `self`; this
    proves the behaviour that property exists for, against the real client
    rather than a fake backend. The barrier forces every caller to be INSIDE
    `chat` simultaneously, so a shared buffer or a reused request object would
    have to survive genuine overlap rather than an accidental serial order.

    Replies are keyed on each request's own body, which is the only way an
    answer can be attributed to its asker: if `chat` ever routed a reply
    through instance state, the callers would collide here and at least one
    would receive another's text.
    """
    callers = 4
    barrier = threading.Barrier(callers, timeout=10)

    def _urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        sent = json.loads(cast("bytes", request.data))
        asked = sent["messages"][0]["content"]
        barrier.wait()  # every caller is inside chat() at this instant
        return _FakeResponse(_ok_body(f"reply to {asked}"))

    client = OllamaClient("qwen3", urlopen=_urlopen)
    replies: dict[int, str] = {}
    lock = threading.Lock()

    def _ask(index: int) -> None:
        text = client.chat([Message(role="user", content=f"question {index}")])
        with lock:
            replies[index] = text

    threads = [threading.Thread(target=_ask, args=(i,)) for i in range(callers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not [t for t in threads if t.is_alive()]
    assert replies == {i: f"reply to question {i}" for i in range(callers)}


# --- is_timeout_failure: was this failure the deadline, or something else? ---


def _chained(exc: Exception, cause: BaseException) -> Exception:
    """`exc` as if it had been raised `from cause`.

    Assigning `__cause__` is what `raise ... from ...` does, and doing it
    directly keeps the assertion out of an `except` block -- which is both
    what ruff's PT017 asks for and clearer: these tests are about the
    predicate reading a cause, not about raising anything.
    """
    exc.__cause__ = cause
    return exc


def test_is_timeout_failure_recognises_a_direct_timeout() -> None:
    """A read-phase timeout surfaces as `TimeoutError` and is wrapped
    `from exc`, so the cause is what identifies it."""
    exc = _chained(OllamaUnavailable("not reachable"), TimeoutError("timed out"))

    assert is_timeout_failure(exc)


def test_is_timeout_failure_recognises_a_timeout_wrapped_in_urlerror() -> None:
    """The connect phase raises `URLError` whose `reason` is the timeout --
    the shape `urlopen(..., timeout=T)` actually produces."""
    cause = urllib.error.URLError(TimeoutError("timed out"))
    exc = _chained(OllamaUnavailable("not reachable"), cause)

    assert is_timeout_failure(exc)


def test_is_timeout_failure_rejects_an_ordinary_connection_failure() -> None:
    """A refused connection is NOT a deadline. Treating it as one would
    attach #746's queuing advice to a server that is simply not running,
    sending the operator after a setting that has nothing to do with it."""
    cause = urllib.error.URLError(ConnectionRefusedError("refused"))
    exc = _chained(OllamaUnavailable("not reachable"), cause)

    assert not is_timeout_failure(exc)


def test_is_timeout_failure_rejects_unrelated_ollama_errors() -> None:
    """A missing model and a truncated reply are both real failures with
    causes of their own; neither is the deadline."""
    assert not is_timeout_failure(OllamaModelNotFound("no such model"))
    assert not is_timeout_failure(OllamaGenerationCapped("truncated"))
    assert not is_timeout_failure(OllamaError("malformed response"))


def test_is_timeout_failure_survives_a_missing_cause() -> None:
    """An exception raised with no `from` has `__cause__` of None, and the
    predicate must answer rather than raise -- it runs on a degrade path that
    is already handling one failure."""
    assert not is_timeout_failure(OllamaUnavailable("not reachable"))


def test_is_timeout_failure_recognises_what_chat_itself_raises() -> None:
    """The predicate must agree with the REAL raise site, not with a
    hand-built exception that merely resembles it.

    Every other test here chains the cause itself, which assumes the shape
    `chat` produces. That assumption is the whole coupling: if
    `_unavailable` ever stopped wrapping `from exc`, or wrapped a different
    cause, `is_timeout_failure` would answer False on genuine timeouts and
    the #746 advisory would go quiet exactly when it is needed. Here the
    exception comes out of `chat` itself.
    """

    def _timing_out(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        raise TimeoutError("timed out")

    client = OllamaClient("qwen3", urlopen=_timing_out)

    with pytest.raises(OllamaUnavailable) as caught:
        client.chat([Message(role="user", content="hi")])

    assert is_timeout_failure(caught.value)


def test_is_timeout_failure_says_no_to_what_a_refused_connection_raises() -> None:
    """The same coupling in the negative direction, also through `chat`."""

    def _refusing(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    client = OllamaClient("qwen3", urlopen=_refusing)

    with pytest.raises(OllamaUnavailable) as caught:
        client.chat([Message(role="user", content="hi")])

    assert not is_timeout_failure(caught.value)


def _capped_body(*, prompt_tokens: int, generated: int) -> bytes:
    """A `done_reason == "length"` body carrying Ollama's own counters.

    `_ok_body_with_done_reason` omits them, which is what let the capped
    message blame the ceiling without ever checking whether the ceiling was
    reached (#829)."""
    return json.dumps(
        {
            "message": {"role": "assistant", "content": "truncated mid-"},
            "done": True,
            "done_reason": "length",
            "prompt_eval_count": prompt_tokens,
            "eval_count": generated,
        }
    ).encode("utf-8")


def test_generation_capped_blames_the_context_window_when_that_is_what_bound() -> None:
    """#829: `num_ctx` bounds prompt AND completion together, so a large
    prompt can leave less generation room than the configured ceiling — and
    the old message named the ceiling anyway.

    On the shipped defaults a 5398-token prompt leaves `12288 - 5398 = 6890`
    tokens of room against a ceiling of 8192. A reply cut at 6890 was
    reported as having hit a ceiling it never reached, sending the operator
    to raise `max_generation_tokens`, which changes nothing because `num_ctx`
    is what has to move."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        context_window=12288,
        urlopen=_fake_urlopen(
            _capped_body(prompt_tokens=5398, generated=6900), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    # Pinned against the WHOLE sentence, not against substrings. `generated`
    # is 6900 against a room of 12288 - 5398 = 6890 so the two numbers
    # differ -- and substring assertions still would not have separated
    # them, because both appear whichever slot they land in. Swapping the
    # two interpolations passes `"6890" in message and "6900" in message`
    # and tells the operator something false; it fails here. (The repo
    # already learned this on the no-ceiling message below.)
    assert str(caught.value) == (
        "Ollama stopped generation because the context_window (12288) "
        "filled, NOT the max_generation_tokens ceiling (8192), which was "
        "never reached: the prompt took 5398 tokens, leaving 6890 for the "
        "reply, and generation stopped at 6900. Raise context_window (or "
        "shorten the prompt); raising max_generation_tokens will not help. "
        "The response is truncated and unusable."
    )
    # And the request really did carry the window this message blames, so
    # the number is not merely echoed back from the constructor.
    assert json.loads(cast("bytes", captured[0].data))["options"]["num_ctx"] == 12288


def test_generation_capped_still_names_the_ceiling_when_the_ceiling_bound() -> None:
    """The other side of the same branch: when generation really did reach
    the configured ceiling, the ceiling is what the message must name.

    Without this the fix could name `context_window` unconditionally and
    move the misattribution rather than remove it."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        context_window=12288,
        urlopen=_fake_urlopen(
            _capped_body(prompt_tokens=1000, generated=8192), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert "max_generation_tokens ceiling (8192)" in message
    assert "context_window" not in message
    assert json.loads(cast("bytes", captured[0].data))["options"]["num_predict"] == 8192


def test_generation_capped_blames_neither_when_neither_was_reached() -> None:
    """Counters present and NEITHER bound explains the stop: the honest
    report names both and blames neither.

    `done_reason == "length"` is reachable without either binding — the
    model's own limits can cut a reply short — and #440 already established
    that this exception must not invent a cause. The counters are what make
    that case distinguishable at all."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        context_window=12288,
        urlopen=_fake_urlopen(_capped_body(prompt_tokens=100, generated=200), captured),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    assert str(caught.value) == (
        "Ollama stopped generation for length before the reply finished, and "
        "NEITHER configured bound was reached: the prompt took 100 tokens "
        "and generation stopped at 200, against a max_generation_tokens "
        "ceiling of 8192 and a context_window of 12288. The backend's own "
        "limit cut it off; the response is truncated and unusable."
    )


def test_generation_capped_never_names_a_context_window_that_is_not_set() -> None:
    """The `neither bound` message must not render "a context_window of
    None".

    `context_window` is optional, and #440 removed exactly this
    self-contradiction from the ceiling half of the same exception: a
    message naming a setting the client does not have sends the operator
    looking for something they never configured."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        context_window=None,
        urlopen=_fake_urlopen(_capped_body(prompt_tokens=100, generated=200), captured),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert "None" not in message
    assert "no context_window set on this client" in message


def test_generation_capped_blames_the_window_even_with_no_ceiling_set() -> None:
    """The symmetric half. With no ceiling configured the old message always
    reached for "the backend's own limit" -- true only when the window was
    not what filled. The counters are present on that branch too, so it can
    say which it was."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=None,
        context_window=8192,
        urlopen=_fake_urlopen(
            _capped_body(prompt_tokens=6000, generated=2200), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    # Whole sentence, for the reason the sibling test above records.
    assert str(caught.value) == (
        "Ollama stopped generation because the context_window (8192) filled: "
        "the prompt took 6000 tokens, leaving 2192 for the reply, and "
        "generation stopped at 2200. No max_generation_tokens ceiling is set "
        "on this client; raise context_window (or shorten the prompt). The "
        "response is truncated and unusable."
    )


def test_generation_capped_ignores_boolean_counters() -> None:
    """`bool` subclasses `int`, so an `isinstance(x, int)` pair alone would
    accept `true`/`false` as counters and build an account out of 1 and 0.

    A counter this code cannot trust must fall through to the unmeasured
    message, not produce a confident wrong one."""
    body = json.dumps(
        {
            "message": {"role": "assistant", "content": "truncated mid-"},
            "done": True,
            "done_reason": "length",
            "prompt_eval_count": True,
            "eval_count": False,
        }
    ).encode("utf-8")
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        context_window=12288,
        urlopen=_fake_urlopen(body, captured),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert message == (
        "Ollama stopped generation at the configured max_generation_tokens "
        "ceiling (8192) before the reply finished; the response is truncated "
        "and unusable."
    )


def test_generation_capped_falls_back_when_the_counters_are_absent() -> None:
    """The counters-absent branch, named rather than merely reached by an
    older test: with nothing to measure, the configured-and-forwarded
    ceiling remains the best available account."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        context_window=12288,
        urlopen=_fake_urlopen(
            _ok_body_with_done_reason("truncated mid-", "length"), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    assert "max_generation_tokens ceiling (8192)" in str(caught.value)


def test_generation_capped_with_no_bounds_at_all_still_reports_the_backend_limit() -> (
    None
):
    """The mirror client (#849): NO ceiling, NO window, and counters PRESENT.

    The no-ceiling branch guards its comparison with
    `self._context_window is not None`, and the twin guard on the ceiling
    branch is pinned by `test_generation_capped_never_names_a_context_window_
    that_is_not_set` — but every pre-existing no-ceiling test either set a
    window or omitted the counters, so deleting THIS guard evaluated
    `300 >= None`, a TypeError, with the suite green."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=None,
        context_window=None,
        urlopen=_fake_urlopen(_capped_body(prompt_tokens=100, generated=200), captured),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    assert str(caught.value) == (
        "Ollama stopped generation for length before the reply finished, "
        "with no max_generation_tokens ceiling set on this client -- the "
        "backend's own limit cut it off; the response is truncated and "
        "unusable."
    )


def test_generation_capped_prefers_the_ceiling_at_the_exact_tie() -> None:
    """Both bounds hold at once exactly when `prompt_tokens +
    max_generation_tokens == context_window` — a real backend stops at
    `min(room, ceiling)`, so the tie is the only reachable double-truth —
    and the two messages give OPPOSITE remediation: the ceiling message says
    raise `max_generation_tokens`, the window message says raising it will
    not help.

    The intended precedence is the ceiling: generation consumed every token
    the ceiling allows, so raising it genuinely is the first lever, and the
    check order in `chat` encodes that. Each earlier test disables one
    predicate, so swapping the two blocks passed both; this one fails."""
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        context_window=12288,
        urlopen=_fake_urlopen(
            _capped_body(prompt_tokens=4096, generated=8192), captured
        ),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert "max_generation_tokens ceiling (8192)" in message
    assert "context_window" not in message


@pytest.mark.parametrize(
    ("prompt_counter", "generated_counter"),
    [
        pytest.param(True, 8300, id="boolean-prompt-counter"),
        pytest.param(8191, True, id="boolean-generated-counter"),
    ],
)
def test_generation_capped_ignores_boolean_counters_with_no_ceiling_set(
    prompt_counter: object, generated_counter: object
) -> None:
    """The boolean-counter rule on the NO-CEILING path (#849): its sibling
    above pins the ceiling branch only, so the trustworthiness predicate
    could be relaxed on this path with the suite green.

    One boolean PER SIDE, not one body with two: the predicate rejects on
    either clause, so a body whose counters are both booleans stays
    rejected when only one clause is relaxed and detects nothing. Each case
    pairs its boolean with a legit counter large enough that the relaxed
    arithmetic (`True + 8300`, `8191 + True`) crosses the 8192 window --
    the mutation must surface as the WRONG MESSAGE, not stay hidden.

    Untrusted counters must fall through to the unmeasured account here too
    — not name a window the code never measured against."""
    body = json.dumps(
        {
            "message": {"role": "assistant", "content": "truncated mid-"},
            "done": True,
            "done_reason": "length",
            "prompt_eval_count": prompt_counter,
            "eval_count": generated_counter,
        }
    ).encode("utf-8")
    captured: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=None,
        context_window=8192,
        urlopen=_fake_urlopen(body, captured),
    )

    with pytest.raises(OllamaGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert "context_window (8192) filled" not in message
    assert message == (
        "Ollama stopped generation for length before the reply finished, "
        "with no max_generation_tokens ceiling set on this client -- the "
        "backend's own limit cut it off; the response is truncated and "
        "unusable."
    )


def test_context_window_property_exposes_the_configured_window() -> None:
    """`context_window` reads back exactly what was configured (#866):
    the prompt-bounding seam in `extraction.concept` plans its excerpt
    against the window the client will ACTUALLY send as `num_ctx`, and it
    must read that fact off the client rather than re-deriving it from
    config -- the same read-the-real-send rationale as `resolved_host`."""
    assert OllamaClient(model="m", context_window=12288).context_window == 12288
    assert OllamaClient(model="m").context_window is None
