"""`OllamaClient`'s setup capability: the version probe and the model pull.

Every test injects a fake `urlopen`; no live Ollama server is needed.
"""

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from openkos.llm.base import BackendDiagnostics, ModelRuntime
from openkos.llm.ollama import OllamaClient, OllamaError, OllamaUnavailable


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


def _replying(body: bytes, sent: list[urllib.request.Request]) -> Any:
    def urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        sent.append(request)
        return _Response(body)

    return urlopen


def _refusing(request: urllib.request.Request, timeout: float | None = None) -> Any:
    raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))


def test_version_reads_the_version_endpoint() -> None:
    sent: list[urllib.request.Request] = []
    client = OllamaClient(
        "qwen3:8b",
        host="http://127.0.0.1:11434",
        urlopen=_replying(json.dumps({"version": "0.32.9"}).encode(), sent),
    )

    assert client.version() == "0.32.9"
    assert sent[0].full_url == "http://127.0.0.1:11434/api/version"
    assert sent[0].get_method() == "GET"


def test_a_refused_version_probe_is_unavailable_and_names_only_the_redacted_host() -> (
    None
):
    client = OllamaClient(
        "qwen3:8b", host="http://user:s3cret@127.0.0.1:11434", urlopen=_refusing
    )

    with pytest.raises(OllamaUnavailable) as caught:
        client.version()
    assert "s3cret" not in str(caught.value)


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"version": 7}', b"[]"])
def test_a_malformed_version_reply_is_an_ollama_error(body: bytes) -> None:
    client = OllamaClient("qwen3:8b", urlopen=_replying(body, []))

    with pytest.raises(OllamaError):
        client.version()


def test_the_client_satisfies_the_model_runtime_protocol() -> None:
    client: ModelRuntime = OllamaClient("qwen3:8b", urlopen=_replying(b"{}", []))
    diagnostics: BackendDiagnostics = client

    assert isinstance(client, ModelRuntime)
    assert diagnostics is client
