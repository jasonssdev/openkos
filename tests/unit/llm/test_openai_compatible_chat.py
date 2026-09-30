"""Unit tests for `OpenAICompatibleClient.chat`'s happy path: request shape
and response parsing (issue #1057 Phase 4, tasks 4.7-4.11), plus chat
BOUNDS -- `max_tokens`, `finish_reason`, `usage`, `temperature`/`seed`,
`context_window` (issue #1057 Phase 5, tasks 5.1-5.13).
"""

import json
import urllib.request
from typing import Any

import pytest

from openkos.llm.base import Message
from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleGenerationCapped,
)


class _FakeResponse:
    """Minimal `urlopen`-shaped stand-in for a 200 JSON response, mirroring
    `test_ollama.py`'s own fake."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        """Return the canned response body, mirroring
        `http.client.HTTPResponse`."""
        return self._body


def _ok_body(content: str) -> bytes:
    """Build a well-formed `/v1/chat/completions` success body containing
    `content`."""
    return json.dumps(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ]
        }
    ).encode("utf-8")


def _sent_body(request: urllib.request.Request) -> dict[str, Any]:
    """Decode the JSON body of a captured `Request` for assertion
    (mypy-narrowed)."""
    data = request.data
    assert isinstance(data, bytes)
    parsed: dict[str, Any] = json.loads(data)
    return parsed


def _fake_urlopen(body: bytes, captured: list[urllib.request.Request]) -> Any:
    """Return an `urlopen` stand-in that records the request and replies
    with `body`."""

    def _urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse(body)

    return _urlopen


def test_chat_posts_to_v1_chat_completions_with_stream_false() -> None:
    """The URL is `{base_url}/v1/chat/completions`, method POST, body
    carries `"model"`, `"messages"`, `"stream": false` (openai-compatible-
    client spec: "Successful Chat Call Returns Assistant Text", request
    half).

    **RED today**: `chat` not implemented."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    assert len(captured) == 1
    request = captured[0]
    assert request.full_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert request.get_method() == "POST"
    sent = _sent_body(request)
    assert sent["model"] == "qwen3"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert sent["stream"] is False


def test_chat_returns_assistant_content_string() -> None:
    """A response of `{"choices":[{"message":{"role":"assistant",
    "content":"hi"},"finish_reason":"stop"}]}` makes `chat(...)` return
    `"hi"` (response half of the same requirement)."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result == "hi"


def test_chat_forwards_system_and_user_messages_in_order() -> None:
    """A message list with one `system` and one `user` entry is forwarded
    verbatim, same order (spec: "System And User Roles Supported")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )
    messages: list[Message] = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "What is stoicism?"},
    ]

    client.chat(messages)

    sent = _sent_body(captured[0])
    assert sent["messages"] == messages


# --- Generation ceiling: `max_tokens` and `finish_reason == "length"` ------


def _capped_body(
    content: str,
    *,
    prompt_tokens: object = None,
    completion_tokens: object = None,
) -> bytes:
    """Build a `/v1/chat/completions` body reporting
    `finish_reason == "length"`, optionally carrying a `usage` object."""
    choice: dict[str, Any] = {
        "message": {"role": "assistant", "content": content},
        "finish_reason": "length",
    }
    payload: dict[str, Any] = {"choices": [choice]}
    if prompt_tokens is not None or completion_tokens is not None:
        payload["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
    return json.dumps(payload).encode("utf-8")


def test_max_tokens_forwarded_when_configured() -> None:
    """`max_generation_tokens=256` sends top-level `max_tokens: 256`
    (spec: "max_tokens is forwarded per request")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        max_generation_tokens=256,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["max_tokens"] == 256


def test_max_tokens_omitted_when_none() -> None:
    """`max_generation_tokens=None` (the default) sends no `max_tokens` key
    at all."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert "max_tokens" not in sent


def test_finish_reason_length_raises_generation_capped() -> None:
    """`choices[0].finish_reason == "length"` raises
    `OpenAICompatibleGenerationCapped` (spec: "finish_reason length raises
    the generation-capped error")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_capped_body("truncated mid-"), captured),
    )

    with pytest.raises(OpenAICompatibleGenerationCapped):
        client.chat([{"role": "user", "content": "hi"}])


def test_generation_capped_message_names_the_ceiling_when_reached() -> None:
    """Case (a): a configured `max_generation_tokens` ceiling was reached --
    the message names it, mirroring Ollama's #440 rule."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        max_generation_tokens=256,
        urlopen=_fake_urlopen(
            _capped_body("truncated mid-", prompt_tokens=100, completion_tokens=256),
            captured,
        ),
    )

    with pytest.raises(OpenAICompatibleGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert "256" in message
    assert "context size" not in message


def test_generation_capped_message_blames_server_context_size_when_window_set() -> None:
    """Case (b): no `max_generation_tokens` ceiling is configured but
    `finish_reason == "length"` anyway, with a `context_window` configured --
    the message must name the SERVER's own context size, never claim
    `context_window` was sent, and must not blame a ceiling that was never
    configured."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        context_window=8192,
        urlopen=_fake_urlopen(
            _capped_body("truncated mid-", prompt_tokens=8000, completion_tokens=192),
            captured,
        ),
    )

    with pytest.raises(OpenAICompatibleGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert "8192" in message
    assert "server" in message.lower()
    assert "context size" in message
    assert "max_generation_tokens ceiling of" not in message


def test_generation_capped_generic_wording_when_neither_bound_configured() -> None:
    """Case (c): neither `max_generation_tokens` nor `context_window` is
    configured -- the message falls back to the generic "backend's own
    limit cut the reply" wording, never a `None`-ceiling sentence."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_capped_body("truncated mid-"), captured),
    )

    with pytest.raises(OpenAICompatibleGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert "backend's own limit cut the reply" in message
    assert "None" not in message


def test_usage_counters_read_when_present() -> None:
    """Present `usage.prompt_tokens`/`usage.completion_tokens` are read and
    feed the capped-message branching exactly like Ollama's counters
    (spec: "Present usage counters are read")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        max_generation_tokens=256,
        urlopen=_fake_urlopen(
            _capped_body("truncated mid-", prompt_tokens=100, completion_tokens=256),
            captured,
        ),
    )

    with pytest.raises(OpenAICompatibleGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    assert "256" in str(caught.value)


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens"),
    [
        (None, None),
        (True, 5),
        (5, "not-a-number"),
        (None, 5),
    ],
)
def test_usage_absent_or_non_int_does_not_fail_the_call(
    prompt_tokens: object, completion_tokens: object
) -> None:
    """Absent `usage`, or a non-numeric/boolean counter, must not raise --
    the call still completes and, when it also caps, still raises the
    generic capped error rather than a `TypeError`/`KeyError` (spec:
    "Absent usage does not fail the call")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(
            _capped_body(
                "truncated mid-",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            captured,
        ),
    )

    with pytest.raises(OpenAICompatibleGenerationCapped) as caught:
        client.chat([{"role": "user", "content": "hi"}])

    assert "backend's own limit cut the reply" in str(caught.value)


def test_usage_absent_entirely_does_not_prevent_a_successful_reply() -> None:
    """A normal, non-capped response with no `usage` object at all still
    returns the assistant text (the non-error half of the fail-open
    contract)."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result == "hi"


# --- Temperature and seed: top-level fields, None-means-omit ---------------


def test_temperature_and_seed_sent_top_level_when_set() -> None:
    """Configured `temperature`/`seed` are sent as TOP-LEVEL fields, not
    nested under any sub-object (spec: "Supplied temperature and seed are
    sent top-level")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        temperature=0.7,
        seed=7,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["temperature"] == 0.7
    assert sent["seed"] == 7
    assert "options" not in sent


def test_temperature_zero_is_sent_because_it_is_a_real_value() -> None:
    """`temperature=0.0` is falsy but a real, deterministic setting -- an
    `is not None` check (never a falsy check) must still send it."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        temperature=0.0,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert sent["temperature"] == 0.0


def test_temperature_and_seed_omitted_when_none() -> None:
    """`temperature`/`seed` both `None` (the default) omits both keys
    entirely -- never sent as `null` (spec: "None values are omitted, not
    sent as null")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert "temperature" not in sent
    assert "seed" not in sent


# --- context_window: advisory-only, never sent ------------------------------


def test_context_window_never_sent_in_request_body() -> None:
    """A client constructed with `context_window=8192` never sends any
    request field carrying that value on `chat()` (spec: "context_window
    is never part of the request body")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        context_window=8192,
        urlopen=_fake_urlopen(_ok_body("hi"), captured),
    )

    client.chat([{"role": "user", "content": "hi"}])

    sent = _sent_body(captured[0])
    assert 8192 not in sent.values()
    assert "context_window" not in sent
    assert "num_ctx" not in sent


def test_context_window_property_readable() -> None:
    """The `context_window` property returns the constructed value (spec:
    "context_window is still readable for prompt budgeting")."""
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        context_window=8192,
    )

    assert client.context_window == 8192
