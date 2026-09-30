"""A backend classified local must never be reached through an environment
proxy (issue #1127).

`classify_backend_host` judges locality from the URL literal, but urllib's
default `ProxyHandler` reads `http_proxy`/`https_proxy` and its
`proxy_bypass` does not exempt loopback names unless `no_proxy` lists them.
Without a guard, a machine with a system-wide proxy sends a "local" backend's
request body (confidential text) and `Authorization` header to the proxy.

These tests use a REAL loopback HTTP stub as the backend and point the proxy
variables at a closed port: a request that consults the proxy fails to
connect, a request that goes direct reaches the stub.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import urllib.request
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

from openkos.llm.base import BackendHostLocality, build_backend_opener
from openkos.llm.ollama import OllamaClient
from openkos.llm.openai_compatible import OpenAICompatibleClient

_OLLAMA_REPLY = {"message": {"content": "pong"}, "done": True, "done_reason": "stop"}
_OPENAI_REPLY = {
    "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
}


class _Recorder(http.server.BaseHTTPRequestHandler):
    """Records every request it receives; replies for both wire formats."""

    seen: ClassVar[list[dict[str, Any]]]

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).seen.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            }
        )
        reply = _OPENAI_REPLY if "/v1/" in self.path else _OLLAMA_REPLY
        payload = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return None


@pytest.fixture
def stub() -> Iterator[tuple[int, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []
    handler = type("_H", (_Recorder,), {"seen": seen})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def dead_proxy_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point every proxy variable at a port nothing listens on, no `no_proxy`."""
    proxy = f"http://127.0.0.1:{_closed_port()}"
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, proxy)
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    return proxy


@pytest.mark.live_backend
@pytest.mark.parametrize("name", ["localhost", "127.0.0.1"])
def test_ollama_local_host_bypasses_env_proxy(
    name: str, stub: tuple[int, list[dict[str, Any]]], dead_proxy_env: str
) -> None:
    port, seen = stub
    client = OllamaClient("m", host=f"http://{name}:{port}")

    assert client.locality.is_local
    assert client.chat([{"role": "user", "content": "SECRET-TEXT"}]) == "pong"

    assert [entry["path"] for entry in seen] == ["/api/chat"]
    assert b"SECRET-TEXT" in seen[0]["body"]


@pytest.mark.live_backend
@pytest.mark.parametrize("name", ["localhost", "127.0.0.1"])
def test_openai_compatible_local_host_bypasses_env_proxy_and_keeps_auth(
    name: str, stub: tuple[int, list[dict[str, Any]]], dead_proxy_env: str
) -> None:
    port, seen = stub
    client = OpenAICompatibleClient(
        model="m", base_url=f"http://{name}:{port}", api_key="SECRETKEY"
    )

    assert client.locality.is_local
    assert client.chat([{"role": "user", "content": "SECRET-TEXT"}]) == "pong"

    assert [entry["path"] for entry in seen] == ["/v1/chat/completions"]
    assert seen[0]["auth"] == "Bearer SECRETKEY"
    assert b"SECRET-TEXT" in seen[0]["body"]


def test_openai_compatible_local_opener_still_refuses_redirects(
    dead_proxy_env: str,
) -> None:
    """Dropping the proxy handler must not drop `_NoRedirectHandler`."""
    client = OpenAICompatibleClient(model="m", base_url="http://127.0.0.1:1")

    opener = client._urlopen.__self__  # type: ignore[attr-defined]
    names = {type(handler).__name__ for handler in opener.handlers}
    assert "_NoRedirectHandler" in names
    assert "HTTPRedirectHandler" not in names


def test_injected_urlopen_is_kept(dead_proxy_env: str) -> None:
    def fake(request: Any, timeout: float) -> Any:
        raise AssertionError("not called")

    assert OllamaClient("m", urlopen=fake)._urlopen is fake
    assert (
        OpenAICompatibleClient(model="m", base_url="http://x", urlopen=fake)._urlopen
        is fake
    )


def _proxy_handlers(urlopen: Any) -> list[urllib.request.ProxyHandler]:
    return [
        h
        for h in urlopen.__self__.handlers
        if isinstance(h, urllib.request.ProxyHandler)
    ]


def test_local_opener_installs_no_proxy_handler(dead_proxy_env: str) -> None:
    """`ProxyHandler({})` registers no `*_open` methods, so the opener holds
    no proxy handler at all -- and `build_opener` does not add the default
    one because a `ProxyHandler` instance was supplied."""
    urlopen = build_backend_opener(BackendHostLocality(True, "localhost"))

    assert _proxy_handlers(urlopen) == []


def test_non_local_opener_honours_env_proxy(dead_proxy_env: str) -> None:
    urlopen = build_backend_opener(BackendHostLocality(False, "example.com"))

    (handler,) = _proxy_handlers(urlopen)
    assert handler.proxies["http"] == dead_proxy_env  # type: ignore[attr-defined]


@pytest.mark.live_backend
def test_non_local_request_is_routed_through_the_proxy(
    stub: tuple[int, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged behaviour for remote hosts: the env proxy is consulted (the
    stub plays the proxy and sees an absolute-URI request line)."""
    port, seen = stub
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{port}")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    urlopen = build_backend_opener(BackendHostLocality(False, "203.0.113.5"))
    request = urllib.request.Request(
        "http://203.0.113.5:9/api/chat", data=b"{}", method="POST"
    )

    urlopen(request, timeout=5).read()

    assert [entry["path"] for entry in seen] == ["http://203.0.113.5:9/api/chat"]
