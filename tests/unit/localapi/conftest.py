"""Synthetic-scope helpers for the local API guard tests.

The guard is pure ASGI, so these tests call it directly with hand-built scopes
and a recording inner app: no server, no socket, no framework.
"""

import asyncio
import hashlib
import json
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass, field
from typing import Any

import pytest

PORT = 49152
TOKEN = "t0ken-for-the-guard-tests"  # noqa: S105 -- a fixture value, not a credential
TOKEN_DIGEST = hashlib.sha256(TOKEN.encode()).digest()
TAURI = "tauri://localhost"

Scope = MutableMapping[str, Any]


@dataclass
class Reply:
    """What a caller of the guard observed."""

    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> int:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return int(start["status"])

    @property
    def headers(self) -> list[tuple[str, str]]:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return [(k.decode(), v.decode()) for k, v in start["headers"]]

    def header(self, name: str) -> str | None:
        found = [v for k, v in self.headers if k == name.lower()]
        return found[0] if found else None

    @property
    def body(self) -> bytes:
        return b"".join(
            m.get("body", b"")
            for m in self.messages
            if m["type"] == "http.response.body"
        )

    @property
    def json(self) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.body)
        return parsed

    @property
    def has_allow_header(self) -> bool:
        return any(k.startswith("access-control-allow-") for k, _ in self.headers)


class RecordingApp:
    """An inner ASGI app that records the scopes it is reached with."""

    def __init__(self) -> None:
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        self.scopes.append(scope)
        if scope["type"] != "http":
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-inner", b"1")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def make_scope(
    headers: Iterable[tuple[str, str]] = (),
    *,
    method: str = "GET",
    path: str = "/v1/anything",
    query: str = "",
    type_: str = "http",
    host: str | None = f"127.0.0.1:{PORT}",
    token: str | None = TOKEN,
) -> Scope:
    """Build a scope; `host` and `token` default to a fully valid request."""
    pairs = list(headers)
    if host is not None:
        pairs.append(("host", host))
    if token is not None:
        pairs.append(("authorization", f"Bearer {token}"))
    return {
        "type": type_,
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in pairs],
    }


def preflight_scope(
    headers: Iterable[tuple[str, str]] = (),
    *,
    origin: str = TAURI,
    request_method: str = "POST",
    request_headers: str = "authorization, content-type",
    host: str | None = f"127.0.0.1:{PORT}",
) -> Scope:
    pairs = [
        ("origin", origin),
        ("access-control-request-method", request_method),
        ("access-control-request-headers", request_headers),
        *headers,
    ]
    return make_scope(pairs, method="OPTIONS", host=host, token=None)


def call(app: Any, scope: Scope) -> Reply:
    """Run one ASGI call to completion and return what was sent."""
    reply = Reply()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        reply.messages.append(message)

    asyncio.run(app(scope, receive, send))
    return reply


@pytest.fixture
def inner() -> RecordingApp:
    return RecordingApp()
