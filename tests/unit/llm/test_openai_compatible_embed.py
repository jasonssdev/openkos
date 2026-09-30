"""Unit tests for `OpenAICompatibleClient.embed`: request shape, response
ordering, `EMBED_DIM` validation, L2 normalization, and the retry-with-
backoff loop (issue #1057 Phase 6, tasks 6.1-6.18).
"""

import io
import json
import math
import urllib.error
import urllib.request
from typing import Any

import pytest

from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleEmbeddingDimensionMismatch,
    OpenAICompatibleError,
    OpenAICompatibleModelNotFound,
    OpenAICompatibleUnavailable,
)

EMBED_DIM = 1024


class _FakeResponse:
    """Minimal `urlopen`-shaped stand-in for a 200 JSON response."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        """Return the canned response body."""
        return self._body


def _row(value: float, dim: int = EMBED_DIM) -> list[float]:
    return [value] * dim


def _one_hot_row(component_index: int, dim: int = EMBED_DIM) -> list[float]:
    """A row with `1.0` at `component_index` and `0.0` elsewhere: already
    at unit L2 norm, so normalization is a no-op and the row stays
    distinguishable by WHICH component is `1.0` -- unlike a uniform-value
    row, whose normalized form is identical regardless of magnitude."""
    row = [0.0] * dim
    row[component_index] = 1.0
    return row


def _embed_body(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps({"data": rows}).encode("utf-8")


def _sent_body(request: urllib.request.Request) -> dict[str, Any]:
    data = request.data
    assert isinstance(data, bytes)
    parsed: dict[str, Any] = json.loads(data)
    return parsed


def _fake_urlopen(body: bytes, captured: list[urllib.request.Request]) -> Any:
    def _urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse(body)

    return _urlopen


def _raising_urlopen(exc: Exception) -> Any:
    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise exc

    return _urlopen


def _sequenced_urlopen(*outcomes: Any) -> Any:
    """Return an `urlopen` stand-in that yields each of `outcomes` in
    order (an exception instance is raised, anything else is returned)."""
    remaining = list(outcomes)

    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _urlopen


def _http_error(status: int, body: bytes = b"") -> urllib.error.HTTPError:
    """Build a real `HTTPError` with a readable `body`, like a genuine
    server reply (mirrors `test_openai_compatible_ladder.py`'s helper)."""
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/embeddings",
        code=status,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def test_embed_posts_to_v1_embeddings_with_encoding_format_float() -> None:
    """URL is `{base_url}/v1/embeddings`, body carries `"model"`,
    `"input"`, `"encoding_format": "float"` (**RED today**: `embed` not
    implemented)."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(
            _embed_body([{"embedding": _row(1.0), "index": 0}]), captured
        ),
    )

    client.embed(["hello"])

    assert len(captured) == 1
    request = captured[0]
    assert request.full_url == "http://127.0.0.1:8080/v1/embeddings"
    assert request.get_method() == "POST"
    sent = _sent_body(request)
    assert sent["model"] == "bge-m3"
    assert sent["input"] == ["hello"]
    assert sent["encoding_format"] == "float"


def test_embed_never_sends_context_window() -> None:
    """A client constructed with `context_window=8192` never sends any
    request field carrying that value on `embed()` either (task 5.11's
    "OR embed" half; spec: "context_window Is Advisory-Only And Never
    Sent")."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        context_window=8192,
        urlopen=_fake_urlopen(
            _embed_body([{"embedding": _row(1.0), "index": 0}]), captured
        ),
    )

    client.embed(["hello"])

    sent = _sent_body(captured[0])
    assert 8192 not in sent.values()
    assert "context_window" not in sent


def test_embed_empty_input_returns_empty_list_without_network_call() -> None:
    """An empty `texts` sequence short-circuits to `[]`, no HTTP call."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_embed_body([]), captured),
    )

    result = client.embed([])

    assert result == []
    assert captured == []


def test_embed_rows_ordered_by_index_not_response_order() -> None:
    """`data` rows out of `index` order return ordered by `index` (spec:
    "Embedder Produces Order-Preserving...", ordering half)."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(
            _embed_body(
                [
                    {"embedding": _one_hot_row(1), "index": 1},
                    {"embedding": _one_hot_row(0), "index": 0},
                ]
            ),
            captured,
        ),
    )

    result = client.embed(["a", "b"])

    assert result[0] == _one_hot_row(0)
    assert result[1] == _one_hot_row(1)


def test_embed_falls_back_to_response_order_when_index_absent() -> None:
    """When every entry lacks `index`, rows return in response order."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(
            _embed_body(
                [
                    {"embedding": _one_hot_row(1)},
                    {"embedding": _one_hot_row(0)},
                ]
            ),
            captured,
        ),
    )

    result = client.embed(["a", "b"])

    assert result[0] == _one_hot_row(1)
    assert result[1] == _one_hot_row(0)


def test_embed_count_mismatch_raises() -> None:
    """`data` has fewer/more rows than input `texts` -> `OpenAICompatibleError`
    (a shape error, distinct from the per-row dimension-mismatch class)."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=1,
        urlopen=_fake_urlopen(_embed_body([{"embedding": _row(1.0), "index": 0}]), []),
    )

    with pytest.raises(OpenAICompatibleError) as caught:
        client.embed(["a", "b"])

    assert not isinstance(caught.value, OpenAICompatibleEmbeddingDimensionMismatch)


def test_wrong_dimension_row_raises_distinct_permanent_error() -> None:
    """One row has a length other than `EMBED_DIM` ->
    `OpenAICompatibleEmbeddingDimensionMismatch`, message names actual and
    expected length (spec: "Wrong-Dimension Row Raises A Distinct
    Permanent Error")."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=1,
        urlopen=_fake_urlopen(
            _embed_body([{"embedding": _row(1.0, dim=5), "index": 0}]), []
        ),
    )

    with pytest.raises(OpenAICompatibleEmbeddingDimensionMismatch) as caught:
        client.embed(["a"])

    message = str(caught.value)
    assert "5" in message
    assert str(EMBED_DIM) in message


def test_every_returned_vector_is_l2_normalized() -> None:
    """A row with a known non-unit norm returns with `abs(norm - 1.0) <
    1e-9` (spec: "Every returned vector is L2-normalized")."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(_embed_body([{"embedding": _row(2.0), "index": 0}]), []),
    )

    (vector,) = client.embed(["a"])

    norm = math.sqrt(sum(v * v for v in vector))
    assert abs(norm - 1.0) < 1e-9


def test_already_normalized_vector_is_numerically_unchanged() -> None:
    """A row already at unit L2 norm returns numerically equivalent
    (normalization is a no-op)."""
    value = 1.0 / math.sqrt(EMBED_DIM)
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(
            _embed_body([{"embedding": _row(value), "index": 0}]), []
        ),
    )

    (vector,) = client.embed(["a"])

    for component in vector:
        assert component == pytest.approx(value, abs=1e-9)


def test_zero_norm_row_raises_generic_retryable_error() -> None:
    """An all-zero row (division-by-zero hazard) raises the generic
    (retryable) `OpenAICompatibleError`, NOT the dimension-mismatch
    class."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=1,
        urlopen=_fake_urlopen(_embed_body([{"embedding": _row(0.0), "index": 0}]), []),
    )

    with pytest.raises(OpenAICompatibleError) as caught:
        client.embed(["a"])

    assert not isinstance(caught.value, OpenAICompatibleEmbeddingDimensionMismatch)


def test_transient_failure_then_success_is_transparent() -> None:
    """First attempt transient error, second (within `embed_retry_attempts`)
    succeeds; `embed(...)` returns validated vectors with no exception,
    `sleep` called with the expected backoff (spec: "Transient Embed
    Failures Are Retried Before Propagating", transparent half)."""
    sleeps: list[float] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=3,
        embed_retry_backoff_base=0.5,
        sleep=sleeps.append,
        urlopen=_sequenced_urlopen(
            _http_error(500, b"server error"),
            _FakeResponse(_embed_body([{"embedding": _row(1.0), "index": 0}])),
        ),
    )

    result = client.embed(["a"])

    assert len(result) == 1
    assert sleeps == [0.5]


def test_exhausted_retry_budget_raises() -> None:
    """Transport fails on every attempt within budget; the final
    exception propagates after the last attempt, `sleep` called
    `attempts - 1` times with `base * 2 ** (attempt - 1)` backoff."""
    sleeps: list[float] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=3,
        embed_retry_backoff_base=0.5,
        sleep=sleeps.append,
        urlopen=_raising_urlopen(_http_error(500, b"server error")),
    )

    with pytest.raises(OpenAICompatibleError):
        client.embed(["a"])

    assert sleeps == [0.5, 1.0]


def test_model_not_found_and_dimension_mismatch_are_never_retried() -> None:
    """Both classes propagate on the FIRST attempt, zero `sleep` calls."""
    sleeps: list[float] = []
    client_404 = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=3,
        sleep=sleeps.append,
        urlopen=_raising_urlopen(_http_error(404, b"model not found")),
    )

    with pytest.raises(OpenAICompatibleModelNotFound):
        client_404.embed(["a"])
    assert sleeps == []

    client_dim = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=3,
        sleep=sleeps.append,
        urlopen=_fake_urlopen(
            _embed_body([{"embedding": _row(1.0, dim=5), "index": 0}]), []
        ),
    )

    with pytest.raises(OpenAICompatibleEmbeddingDimensionMismatch):
        client_dim.embed(["a"])
    assert sleeps == []


def test_embed_server_unreachable_raises_unavailable() -> None:
    """Connection refused/timeout during `embed` raises
    `OpenAICompatibleUnavailable` (same mapping as `chat`) (spec: "Server
    Unavailable During Embedding Raises A Typed Error")."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        embed_retry_attempts=1,
        urlopen=_raising_urlopen(urllib.error.URLError(ConnectionRefusedError())),
    )

    with pytest.raises(OpenAICompatibleUnavailable):
        client.embed(["a"])


def test_embed_uses_its_own_base_url_argument() -> None:
    """A client constructed with a distinct embedding endpoint posts to
    that endpoint (the two-endpoint DISPATCH itself is `backend-selection`'s
    resolver, Phase 9 -- this is a client-level unit test only)."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:9090",
        urlopen=_fake_urlopen(
            _embed_body([{"embedding": _row(1.0), "index": 0}]), captured
        ),
    )

    client.embed(["a"])

    assert captured[0].full_url == "http://127.0.0.1:9090/v1/embeddings"
