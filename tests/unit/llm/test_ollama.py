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
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from openkos.llm.base import EMBED_DIM, Embedder, Message
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
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


def _tags_body(entries: list[dict[str, Any]]) -> bytes:
    """Build a well-formed `/api/tags` success body containing `entries`."""
    return json.dumps({"models": entries}).encode("utf-8")


# --- Phase 9: list_models() ---------------------------------------------------


def test_list_models_returns_installed_tags_from_model_field() -> None:
    """A reachable server's `model` entries are returned as a list of tags
    (Scenario: Reachable server returns installed tags)."""
    body = _tags_body([{"model": "qwen3:8b"}, {"model": "llama3.2:1b"}])
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == ["qwen3:8b", "llama3.2:1b"]


def test_list_models_falls_back_to_name_field() -> None:
    """An entry with `name` but no `model` key still yields its tag (D2 field
    variance: Installed entry exposes its tag only under name)."""
    body = _tags_body([{"name": "mistral:7b"}])
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == ["mistral:7b"]


def test_list_models_skips_malformed_entries() -> None:
    """A non-dict entry, or a dict entry with neither `model` nor `name`, is
    skipped rather than raised or included."""
    entries: list[Any] = [{"model": "qwen3:8b"}, {"other": "junk"}, "not-a-dict"]
    body = json.dumps({"models": entries}).encode("utf-8")
    client = OllamaClient("qwen3", urlopen=_fake_urlopen(body, []))

    result = client.list_models()

    assert result == ["qwen3:8b"]


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
        "qwen3-embedding:0.6b", urlopen=_fake_urlopen(_embed_body([row]), [])
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
        "qwen3-embedding:0.6b", urlopen=_fake_urlopen(_embed_body([short_row]), [])
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_row_non_numeric_values_raises_ollama_error() -> None:
    """A returned row containing a non-numeric value raises `OllamaError`
    even when its length matches `EMBED_DIM`."""
    bad_row: list[Any] = [0.1] * (EMBED_DIM - 1) + ["not-a-float"]
    body = json.dumps({"embeddings": [bad_row]}).encode("utf-8")
    client = OllamaClient("qwen3-embedding:0.6b", urlopen=_fake_urlopen(body, []))

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_malformed_json_raises_ollama_error() -> None:
    """A 200 body that is not valid JSON raises `OllamaError`."""
    client = OllamaClient(
        "qwen3-embedding:0.6b", urlopen=_fake_urlopen(b"not json at all {{{", [])
    )

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_non_object_json_body_raises_ollama_error() -> None:
    """A 200 body that is valid JSON but not a JSON object at the top level
    (e.g. a bare list) raises `OllamaError`, not an unguarded `TypeError`."""
    body = json.dumps([0.1] * EMBED_DIM).encode("utf-8")
    client = OllamaClient("qwen3-embedding:0.6b", urlopen=_fake_urlopen(body, []))

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_missing_embeddings_and_embedding_keys_raises_ollama_error() -> None:
    """A well-formed JSON body carrying neither `embeddings` nor `embedding`
    (response-shape drift beyond the two recognized keys) raises `OllamaError`."""
    body = json.dumps({"unexpected": "shape"}).encode("utf-8")
    client = OllamaClient("qwen3-embedding:0.6b", urlopen=_fake_urlopen(body, []))

    with pytest.raises(OllamaError):
        client.embed(["a"])


def test_embed_connect_phase_transport_failure_raises_ollama_unavailable() -> None:
    """A `URLError` while connecting maps to `OllamaUnavailable` (mirrors `chat()`)."""
    client = OllamaClient(
        "qwen3-embedding:0.6b",
        urlopen=_raising_urlopen(urllib.error.URLError("Connection refused")),
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
    )

    with pytest.raises(OllamaUnavailable):
        client.embed(["a"])


def test_embed_non_404_http_error_raises_ollama_error() -> None:
    """A non-404 HTTP error is mapped to `OllamaError` (reuses `_map_http_error`)."""
    body = json.dumps({"error": "internal server error"}).encode("utf-8")
    client = OllamaClient(
        "qwen3-embedding:0.6b", urlopen=_raising_urlopen(_http_error(500, body))
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
