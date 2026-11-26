"""Unit tests for `OpenAICompatibleClient.chat`'s bounded retry of transient
failures (issue #1764): which failures retry, the backoff sequence,
`Retry-After` handling and its cap, and what must never retry.

Every test injects `urlopen` and `sleep`; nothing touches the network and
nothing really sleeps.
"""

import http.client
import io
import json
import urllib.error
from email.message import Message as EmailMessage
from typing import Any

import pytest

from openkos.llm.base import Message
from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleError,
    OpenAICompatibleGenerationCapped,
    OpenAICompatibleModelNotFound,
    OpenAICompatibleUnavailable,
)

_MESSAGES: list[Message] = [{"role": "user", "content": "hi"}]
_KEY = "sk-secret-key-1764"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


class _BrokenBodyResponse:
    """A 200 whose body read fails: the request was already sent."""

    def read(self) -> bytes:
        raise http.client.IncompleteRead(b"partial")


def _ok(content: str = "done", finish_reason: str = "stop") -> _FakeResponse:
    return _FakeResponse(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish_reason,
                    }
                ]
            }
        ).encode("utf-8")
    )


def _http_error(
    status: int, body: bytes = b"boom", retry_after: str | None = None
) -> urllib.error.HTTPError:
    headers = EmailMessage()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/chat/completions",
        code=status,
        msg="error",
        hdrs=headers,
        fp=io.BytesIO(body),
    )


class _Script:
    """An `urlopen` stand-in that plays `outcomes` in order and counts calls."""

    def __init__(self, *outcomes: Any) -> None:
        self._remaining = list(outcomes)
        self.calls = 0

    def __call__(self, request: Any, timeout: float | None = None) -> Any:
        self.calls += 1
        outcome = self._remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client(
    script: _Script, sleeps: list[float], **kwargs: Any
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        "m",
        base_url="http://127.0.0.1:8080",
        urlopen=script,
        sleep=sleeps.append,
        **kwargs,
    )


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_each_transient_status_is_retried_then_succeeds(status: int) -> None:
    script = _Script(_http_error(status), _ok("recovered"))
    sleeps: list[float] = []

    assert _client(script, sleeps).chat(_MESSAGES) == "recovered"
    assert script.calls == 2
    assert sleeps == [0.5]


def test_exhausted_budget_raises_the_mapped_error_with_exponential_backoff() -> None:
    script = _Script(_http_error(503), _http_error(503), _http_error(503))
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError, match=r"\(503\)") as excinfo:
        _client(script, sleeps).chat(_MESSAGES)

    assert type(excinfo.value) is OpenAICompatibleError
    assert script.calls == 3
    assert sleeps == [0.5, 1.0]


def test_retry_budget_and_backoff_base_are_constructor_parameters() -> None:
    script = _Script(*[_http_error(502) for _ in range(4)])
    sleeps: list[float] = []
    client = _client(script, sleeps, chat_retry_attempts=4, chat_retry_backoff_base=2.0)

    with pytest.raises(OpenAICompatibleError):
        client.chat(_MESSAGES)

    assert script.calls == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_a_single_attempt_budget_never_retries() -> None:
    script = _Script(_http_error(503))
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError):
        _client(script, sleeps, chat_retry_attempts=1).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


def test_retry_after_seconds_replace_the_backoff() -> None:
    script = _Script(_http_error(429, retry_after="2"), _ok())
    sleeps: list[float] = []

    _client(script, sleeps).chat(_MESSAGES)

    assert sleeps == [2.0]


def test_retry_after_is_capped() -> None:
    script = _Script(_http_error(503, retry_after="3600"), _ok())
    sleeps: list[float] = []

    _client(script, sleeps).chat(_MESSAGES)

    assert sleeps == [30.0]


@pytest.mark.parametrize(
    "value", ["Wed, 21 Oct 2026 07:28:00 GMT", "-5", "soon", "1.5", ""]
)
def test_unusable_retry_after_falls_back_to_the_backoff(value: str) -> None:
    script = _Script(_http_error(503, retry_after=value), _ok())
    sleeps: list[float] = []

    _client(script, sleeps).chat(_MESSAGES)

    assert sleeps == [0.5]


def test_retry_after_zero_is_honoured() -> None:
    script = _Script(_http_error(429, retry_after="0"), _ok())
    sleeps: list[float] = []

    _client(script, sleeps).chat(_MESSAGES)

    assert sleeps == [0.0]


def test_a_missing_headers_object_is_tolerated() -> None:
    error = urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/chat/completions",
        code=503,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b"boom"),
    )
    script = _Script(error, _ok())
    sleeps: list[float] = []

    _client(script, sleeps).chat(_MESSAGES)

    assert sleeps == [0.5]


def test_a_connect_phase_failure_is_retried() -> None:
    refused = urllib.error.URLError(ConnectionRefusedError(61, "refused"))
    script = _Script(refused, _ok("up"))
    sleeps: list[float] = []

    assert _client(script, sleeps).chat(_MESSAGES) == "up"
    assert script.calls == 2
    assert sleeps == [0.5]


def test_a_persistent_connect_failure_raises_unavailable_after_the_budget() -> None:
    script = _Script(
        *[urllib.error.URLError(ConnectionResetError(54, "reset")) for _ in range(3)]
    )
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleUnavailable):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize(
    "outcome",
    [
        TimeoutError("timed out"),
        urllib.error.URLError(TimeoutError("timed out")),
    ],
    ids=["read-timeout", "connect-timeout"],
)
def test_a_timeout_is_not_retried(outcome: BaseException) -> None:
    script = _Script(outcome, _ok())
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleUnavailable):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


def test_a_failure_while_reading_the_body_is_not_retried() -> None:
    script = _Script(_BrokenBodyResponse(), _ok())
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleUnavailable):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 408, 422, 501])
def test_other_statuses_are_not_retried(status: int) -> None:
    script = _Script(_http_error(status), _ok())
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


def test_a_redirect_is_not_retried() -> None:
    script = _Script(_http_error(302), _ok())
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError, match="redirect"):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


def test_model_not_found_is_not_retried() -> None:
    script = _Script(_http_error(404, b"model does not exist"), _ok())
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleModelNotFound):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


def test_a_generation_cap_is_not_retried() -> None:
    script = _Script(_ok("cut", finish_reason="length"), _ok())
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleGenerationCapped):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


def test_a_malformed_body_is_not_retried() -> None:
    script = _Script(_FakeResponse(b"not json"), _ok())
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError, match="Malformed"):
        _client(script, sleeps).chat(_MESSAGES)

    assert script.calls == 1
    assert sleeps == []


def test_the_api_key_never_appears_in_an_exhausted_retry_error() -> None:
    script = _Script(*[_http_error(503) for _ in range(3)])
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError) as excinfo:
        _client(script, sleeps, api_key=_KEY).chat(_MESSAGES)

    assert _KEY not in str(excinfo.value)
    assert _KEY not in repr(excinfo.value)


# --- A refused sampling setting names the config key (issue #1766) ----------

_TEMPERATURE_REFUSAL = (
    b'{"error": {"message": "Unsupported value: \'temperature\' does not '
    b'support 0.2 with this model. Only the default (1) value is supported."}}'
)


def test_refused_temperature_names_the_config_key_and_is_not_retried() -> None:
    script = _Script(_http_error(400, _TEMPERATURE_REFUSAL))
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError) as excinfo:
        _client(script, sleeps, temperature=0.2, api_key=_KEY).chat(_MESSAGES)

    message = str(excinfo.value)
    assert "`temperature` in openkos.yaml" in message
    assert "unset" in message
    assert "Only the default (1) value is supported" in message
    assert _KEY not in message
    assert script.calls == 1
    assert sleeps == []


def test_refused_deprecated_temperature_is_named_too() -> None:
    body = b'{"error": {"message": "`temperature` is deprecated for this model."}}'
    script = _Script(_http_error(400, body))

    with pytest.raises(OpenAICompatibleError, match=r"`temperature` in openkos\.yaml"):
        _client(script, []).chat(_MESSAGES)


def test_refused_seed_names_the_config_key() -> None:
    body = (
        b'{"error": {"message": "Unsupported parameter: \'seed\' is not supported."}}'
    )
    script = _Script(_http_error(400, body))

    with pytest.raises(OpenAICompatibleError, match=r"`seed` in openkos\.yaml"):
        _client(script, []).chat(_MESSAGES)


def test_unrelated_400_is_mapped_as_before() -> None:
    script = _Script(_http_error(400, b"bad request"))

    with pytest.raises(OpenAICompatibleError) as excinfo:
        _client(script, []).chat(_MESSAGES)

    assert "openkos.yaml" not in str(excinfo.value)
    assert "(400)" in str(excinfo.value)


def test_refused_reasoning_effort_names_the_config_key_and_is_not_retried() -> None:
    body = (
        b'{"error": {"message": "Unrecognized request argument supplied: '
        b'reasoning_effort"}}'
    )
    script = _Script(_http_error(400, body))
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError) as excinfo:
        _client(script, sleeps, reasoning_effort="low", api_key=_KEY).chat(_MESSAGES)

    message = str(excinfo.value)
    assert "`reasoning_effort` in openkos.yaml" in message
    assert "unset" in message
    assert "Unrecognized request argument" in message
    assert _KEY not in message
    assert script.calls == 1
    assert sleeps == []


_GOOGLE_THINKING_REFUSAL = (
    b'[{"error": {"code": 400, "message": "Thinking level MINIMAL is not '
    b'supported for this model.", "status": "INVALID_ARGUMENT"}}]'
)


def test_a_google_shaped_refusal_names_reasoning_effort_when_it_was_sent() -> None:
    script = _Script(_http_error(400, _GOOGLE_THINKING_REFUSAL))
    sleeps: list[float] = []

    with pytest.raises(OpenAICompatibleError) as excinfo:
        _client(script, sleeps, reasoning_effort="minimal").chat(_MESSAGES)

    message = str(excinfo.value)
    assert "`reasoning_effort` in openkos.yaml" in message
    assert "Thinking level MINIMAL is not supported" in message
    assert script.calls == 1
    assert sleeps == []


def test_a_thinking_400_is_not_attributed_when_no_effort_was_sent() -> None:
    script = _Script(_http_error(400, _GOOGLE_THINKING_REFUSAL))

    with pytest.raises(OpenAICompatibleError) as excinfo:
        _client(script, []).chat(_MESSAGES)

    assert "openkos.yaml" not in str(excinfo.value)
