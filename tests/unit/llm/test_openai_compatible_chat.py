"""Unit tests for `OpenAICompatibleClient.chat`'s happy path: request shape
and response parsing (issue #1057 Phase 4, tasks 4.7-4.11).

Chat BOUNDS (`max_tokens`, `finish_reason`, `usage`, `temperature`/`seed`,
`context_window`) are Phase 5's job and land in this same file there, per
tasks.md's own note -- not duplicated in this apply batch.
"""

import json
import urllib.request
from typing import Any

from openkos.llm.base import Message
from openkos.llm.openai_compatible import OpenAICompatibleClient


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
