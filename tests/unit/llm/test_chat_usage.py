"""Chat usage reporting (#1807): the neutral `ChatUsage` value, the
thread-safe `UsageTally`, and the `usage_sink` both chat clients hand one
`ChatUsage` per parsed response -- success, length-capped, or otherwise.

Tokens only, never money. Every test injects `urlopen`; nothing touches the
network.
"""

import io
import json
import sys
import threading
import urllib.error
import urllib.request
from typing import Any

import pytest

from openkos.llm.base import ChatUsage, UsageSummary, UsageTally, chat_usage_of
from openkos.llm.ollama import OllamaClient, OllamaGenerationCapped
from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleGenerationCapped,
)

_MESSAGES: list[Any] = [{"role": "user", "content": "hi"}]


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


def _urlopen(*bodies: bytes | BaseException, captured: list[Any] | None = None) -> Any:
    remaining = list(bodies)

    def _call(request: urllib.request.Request, timeout: float | None = None) -> Any:
        if captured is not None:
            captured.append(request)
        item = remaining.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Response(item)

    return _call


def _oai(
    usage: dict[str, Any] | None, finish_reason: str = "stop", content: str = "ok"
) -> bytes:
    body: dict[str, Any] = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return json.dumps(body).encode()


def _ollama(**fields: Any) -> bytes:
    return json.dumps(
        {"message": {"role": "assistant", "content": "ok"}, **fields}
    ).encode()


def _oai_client(
    urlopen: Any, sink: Any = None, **kwargs: Any
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        "m",
        base_url="http://127.0.0.1:8080",
        urlopen=urlopen,
        sleep=lambda _s: None,
        usage_sink=sink,
        **kwargs,
    )


# --- chat_usage_of ------------------------------------------------------------


def test_chat_usage_of_keeps_a_reported_total() -> None:
    assert chat_usage_of(10, 3, 331) == ChatUsage(10, 3, 331)


def test_chat_usage_of_falls_back_to_the_sum_when_total_is_absent() -> None:
    assert chat_usage_of(10, 3, None) == ChatUsage(10, 3, 13)


def test_chat_usage_of_has_no_total_when_a_part_is_missing() -> None:
    assert chat_usage_of(10, None, None) == ChatUsage(10, None, None)


def test_chat_usage_of_rejects_bools_negatives_and_non_ints() -> None:
    assert chat_usage_of(True, "5", -1) == ChatUsage(None, None, None)
    assert chat_usage_of(1.5, None, None) == ChatUsage(None, None, None)


# --- UsageTally -----------------------------------------------------------------


def test_tally_starts_empty() -> None:
    assert UsageTally().snapshot() == UsageSummary(0, 0, 0, 0, 0)


def test_tally_sums_reported_calls() -> None:
    tally = UsageTally()
    tally.record(ChatUsage(10, 2, 12))
    tally.record(ChatUsage(5, 1, 20))
    assert tally.snapshot() == UsageSummary(
        calls=2,
        reported_calls=2,
        prompt_tokens=15,
        completion_tokens=3,
        total_tokens=32,
    )


def test_tally_counts_a_call_that_reported_nothing_without_adding_zeros() -> None:
    tally = UsageTally()
    tally.record(ChatUsage(10, 2, 12))
    tally.record(ChatUsage(None, None, None))
    summary = tally.snapshot()
    assert summary.calls == 2
    assert summary.reported_calls == 1
    assert summary.total_tokens == 12


def test_tally_is_thread_safe() -> None:
    tally = UsageTally()
    barrier = threading.Barrier(8)

    def _work() -> None:
        barrier.wait()
        for _ in range(20000):
            tally.record(ChatUsage(1, 1, 2))

    # A tiny switch interval makes an unguarded read-modify-write lose
    # updates reliably instead of once in a blue moon.
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=_work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(previous)
    summary = tally.snapshot()
    assert summary.calls == 160000
    assert summary.total_tokens == 320000
    assert summary.prompt_tokens == 160000


def test_tally_guards_every_read_and_write_with_its_lock() -> None:
    """Interleaving is not reliably reproducible under the GIL, so pin the
    guard itself: each `record` and each `snapshot` holds the lock."""

    class _CountingLock:
        def __init__(self) -> None:
            self.entered = 0

        def __enter__(self) -> None:
            self.entered += 1

        def __exit__(self, *exc: object) -> None:
            return None

    tally = UsageTally()
    lock = _CountingLock()
    tally._lock = lock  # type: ignore[assignment]
    tally.record(ChatUsage(1, 1, 2))
    tally.record(ChatUsage())
    tally.snapshot()
    assert lock.entered == 3


# --- OpenAI-compatible ----------------------------------------------------------


def test_openai_success_records_prompt_completion_and_total() -> None:
    seen: list[ChatUsage] = []
    usage = {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 99}
    client = _oai_client(_urlopen(_oai(usage)), seen.append)
    assert client.chat(_MESSAGES) == "ok"
    assert seen == [ChatUsage(20, 6, 99)]


def test_openai_total_absent_falls_back_to_prompt_plus_completion() -> None:
    seen: list[ChatUsage] = []
    usage = {"prompt_tokens": 20, "completion_tokens": 6}
    _oai_client(_urlopen(_oai(usage)), seen.append).chat(_MESSAGES)
    assert seen == [ChatUsage(20, 6, 26)]


def test_openai_hidden_reasoning_stays_in_total() -> None:
    """Gemini: completion 26, total 331 -- the gap is reasoning, billed."""
    seen: list[ChatUsage] = []
    usage = {"prompt_tokens": 305, "completion_tokens": 26, "total_tokens": 331}
    _oai_client(_urlopen(_oai(usage)), seen.append).chat(_MESSAGES)
    assert seen[0].total_tokens == 331
    assert seen[0].completion_tokens == 26


def test_openai_capped_response_still_records_usage_before_raising() -> None:
    seen: list[ChatUsage] = []
    usage = {"prompt_tokens": 50, "completion_tokens": 8192, "total_tokens": 9000}
    client = _oai_client(
        _urlopen(_oai(usage, finish_reason="length")),
        seen.append,
        max_generation_tokens=8192,
    )
    with pytest.raises(OpenAICompatibleGenerationCapped):
        client.chat(_MESSAGES)
    assert seen == [ChatUsage(50, 8192, 9000)]


def test_openai_missing_usage_still_counts_the_call() -> None:
    seen: list[ChatUsage] = []
    _oai_client(_urlopen(_oai(None)), seen.append).chat(_MESSAGES)
    assert seen == [ChatUsage(None, None, None)]


def test_openai_garbled_usage_is_fail_open() -> None:
    seen: list[ChatUsage] = []
    body = _oai({"prompt_tokens": True, "completion_tokens": "x", "total_tokens": None})
    _oai_client(_urlopen(body), seen.append).chat(_MESSAGES)
    assert seen == [ChatUsage(None, None, None)]


def test_openai_usage_that_is_not_an_object_is_fail_open() -> None:
    body = json.dumps(
        {"choices": [{"message": {"content": "ok"}}], "usage": [1, 2]}
    ).encode()
    seen: list[ChatUsage] = []
    _oai_client(_urlopen(body), seen.append).chat(_MESSAGES)
    assert seen == [ChatUsage(None, None, None)]


def test_openai_retry_counts_the_response_that_arrived_once() -> None:
    """A transient 503 carries no completion; the retried attempt that gets
    one is recorded exactly once, so a retry neither drops nor doubles it."""
    err = urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/chat/completions",
        code=503,
        msg="busy",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b"busy"),
    )
    seen: list[ChatUsage] = []
    usage = {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}
    client = _oai_client(_urlopen(err, _oai(usage)), seen.append)
    assert client.chat(_MESSAGES) == "ok"
    assert seen == [ChatUsage(4, 1, 5)]


def test_openai_no_sink_is_the_default() -> None:
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    assert _oai_client(_urlopen(_oai(usage))).chat(_MESSAGES) == "ok"


def test_openai_request_body_is_identical_with_and_without_a_sink() -> None:
    plain: list[Any] = []
    with_sink: list[Any] = []
    _oai_client(_urlopen(_oai(None), captured=plain)).chat(_MESSAGES)
    _oai_client(_urlopen(_oai(None), captured=with_sink), lambda _u: None).chat(
        _MESSAGES
    )
    assert plain[0].data == with_sink[0].data
    assert dict(plain[0].header_items()) == dict(with_sink[0].header_items())


def test_openai_sink_is_not_called_for_a_request_that_failed() -> None:
    seen: list[ChatUsage] = []
    err = urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/chat/completions",
        code=401,
        msg="no",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b"no"),
    )
    with pytest.raises(Exception, match=r".+"):
        _oai_client(_urlopen(err), seen.append).chat(_MESSAGES)
    assert seen == []


# --- Ollama ---------------------------------------------------------------------


def test_ollama_records_prompt_eval_and_eval_counts() -> None:
    seen: list[ChatUsage] = []
    client = OllamaClient(
        "qwen3",
        urlopen=_urlopen(_ollama(prompt_eval_count=30, eval_count=7)),
        usage_sink=seen.append,
    )
    assert client.chat(_MESSAGES) == "ok"
    assert seen == [ChatUsage(30, 7, 37)]


def test_ollama_capped_response_still_records_usage() -> None:
    seen: list[ChatUsage] = []
    client = OllamaClient(
        "qwen3",
        max_generation_tokens=8192,
        urlopen=_urlopen(
            _ollama(done_reason="length", prompt_eval_count=30, eval_count=8192)
        ),
        usage_sink=seen.append,
    )
    with pytest.raises(OllamaGenerationCapped):
        client.chat(_MESSAGES)
    assert seen == [ChatUsage(30, 8192, 8222)]


def test_ollama_missing_counters_still_count_the_call() -> None:
    seen: list[ChatUsage] = []
    client = OllamaClient("qwen3", urlopen=_urlopen(_ollama()), usage_sink=seen.append)
    client.chat(_MESSAGES)
    assert seen == [ChatUsage(None, None, None)]


def test_ollama_one_counter_only_reports_no_total() -> None:
    seen: list[ChatUsage] = []
    client = OllamaClient(
        "qwen3", urlopen=_urlopen(_ollama(eval_count=7)), usage_sink=seen.append
    )
    client.chat(_MESSAGES)
    assert seen == [ChatUsage(None, 7, None)]


def test_ollama_request_body_is_identical_with_and_without_a_sink() -> None:
    plain: list[Any] = []
    with_sink: list[Any] = []
    OllamaClient("q", urlopen=_urlopen(_ollama(), captured=plain)).chat(_MESSAGES)
    OllamaClient(
        "q", urlopen=_urlopen(_ollama(), captured=with_sink), usage_sink=lambda _u: None
    ).chat(_MESSAGES)
    assert plain[0].data == with_sink[0].data
