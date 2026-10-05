"""Unit tests for `OpenAICompatibleClient.list_models` (issue #1057 Phase 7,
tasks 7.1-7.4): `GET /v1/models`, mapped to `InstalledModel(tag=id,
family=None)`.
"""

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from openkos.llm.base import InstalledModel
from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleError,
    OpenAICompatibleUnavailable,
)


class _FakeResponse:
    """Minimal `urlopen`-shaped stand-in for a 200 JSON response."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        """Return the canned response body."""
        return self._body


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


def _http_error(status: int, body: bytes = b"") -> urllib.error.HTTPError:
    """Build a real `HTTPError` with a readable `body`, like a genuine
    server reply (mirrors `test_openai_compatible_ladder.py`'s helper)."""
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/models",
        code=status,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def test_list_models_returns_ids_with_null_family() -> None:
    """`{"data":[{"id":"a"},{"id":"b"}]}` maps to two
    `InstalledModel(tag=..., family=None)` (spec: "List Installed Models
    Via /v1/models"). **RED today**: `list_models` not implemented."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(
            json.dumps({"data": [{"id": "a"}, {"id": "b"}]}).encode("utf-8"),
            captured,
        ),
    )

    result = client.list_models()

    assert result == [
        InstalledModel(tag="a", family=None),
        InstalledModel(tag="b", family=None),
    ]
    assert captured[0].full_url == "http://127.0.0.1:8080/v1/models"
    assert captured[0].get_method() == "GET"


def test_list_models_never_reports_a_digest() -> None:
    """`/v1/models` carries no content digest, so every entry's digest is
    `None` even when the entry happens to include a `digest`-looking key
    (spec: "A backend that reports no digest returns entries without one").
    Pins the no-change contract: the auto-merge class treats it as unknown."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(
            json.dumps(
                {"data": [{"id": "a", "digest": "ab" * 32}, {"id": "b"}]}
            ).encode("utf-8"),
            [],
        ),
    )

    result = client.list_models()

    assert [m.tag for m in result] == ["a", "b"]
    assert all(m.digest is None for m in result)


def test_list_models_unreachable_raises_unavailable() -> None:
    """A connection failure raises `OpenAICompatibleUnavailable`."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_raising_urlopen(urllib.error.URLError(ConnectionRefusedError())),
    )

    with pytest.raises(OpenAICompatibleUnavailable):
        client.list_models()


def test_list_models_malformed_raises_generic_error() -> None:
    """A non-200/malformed body raises `OpenAICompatibleError`."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen(b"not json", []),
    )

    with pytest.raises(OpenAICompatibleError):
        client.list_models()


def test_list_models_non_200_raises_generic_error() -> None:
    """A non-200 HTTP status raises `OpenAICompatibleError` through the
    shared `_map_http_error` classification."""
    client = OpenAICompatibleClient(
        model="bge-m3",
        base_url="http://127.0.0.1:8080",
        urlopen=_raising_urlopen(_http_error(500, b"server error")),
    )

    with pytest.raises(OpenAICompatibleError):
        client.list_models()
