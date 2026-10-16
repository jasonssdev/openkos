"""The ADR-0058 guard: one pure-ASGI layer in front of every route.

For an `http` scope the four checks run in this order, so a request can be
refused at the first one that fails and no later check is reached:

1. Host: exactly one allowed loopback Host.
2. CORS preflight: answered here, never reaching the token check or a route.
3. Origin: absent passes; present must be on the allowlist.
4. Token: exactly one `Authorization: Bearer <token>`, compared as digests.

Stdlib only: the responses are hand-encoded so they cannot depend on routing.
"""

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Final

from openkos.localapi import _errors

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

ALLOWED_ORIGINS: Final = frozenset(
    {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
)
_ALLOWED_METHODS: Final = frozenset({"GET", "POST"})
_ALLOWED_REQUEST_HEADERS: Final = frozenset({"authorization", "content-type"})
_WS_POLICY_VIOLATION: Final = 1008


@dataclass(frozen=True)
class _GuardConfig:
    """What the guard needs: the bound port and the digest of the token."""

    port: int
    token_digest: bytes = field(repr=False)


def _headers(scope: Scope, name: bytes) -> list[str]:
    return [v.decode("latin-1") for k, v in scope.get("headers", ()) if k == name]


def _one(scope: Scope, name: bytes) -> str | None:
    values = _headers(scope, name)
    return values[0] if len(values) == 1 else None


async def _respond(
    send: Send,
    status: int,
    code: str,
    message: str,
    extra: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps({"code": code, "retryable": False, "message": message}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers + (extra or []),
        }
    )
    await send({"type": "http.response.body", "body": body})


def _cors(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode()),
        (b"vary", b"Origin"),
        (b"access-control-expose-headers", b"Location, Retry-After"),
    ]


class LocalApiGuard:
    """Wrap an ASGI app with the Host, preflight, Origin and token checks."""

    def __init__(self, app: ASGIApp, config: _GuardConfig) -> None:
        self._app = app
        self._config = config
        self._hosts = frozenset(
            {f"127.0.0.1:{config.port}", f"localhost:{config.port}"}
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "websocket":
            await send({"type": "websocket.close", "code": _WS_POLICY_VIOLATION})
            return
        if kind != "http":
            await self._app(scope, receive, send)
            return

        # 1. Host, before everything: a rebinding request reaches nothing.
        host = _one(scope, b"host")
        if host is None or host.lower() not in self._hosts:
            await _respond(send, 403, _errors.HOST_NOT_ALLOWED, "host not allowed")
            return

        origin = _one(scope, b"origin")
        if len(_headers(scope, b"origin")) > 1:
            await _respond(send, 403, _errors.ORIGIN_NOT_ALLOWED, "origin not allowed")
            return

        # 2. Preflight: answered here, never token-checked, never routed.
        if (
            scope["method"] == "OPTIONS"
            and origin is not None
            and _one(scope, b"access-control-request-method") is not None
        ):
            await self._preflight(scope, origin, send)
            return

        # 3. Origin.
        if origin is not None and origin not in ALLOWED_ORIGINS:
            await _respond(send, 403, _errors.ORIGIN_NOT_ALLOWED, "origin not allowed")
            return
        extra = _cors(origin) if origin is not None else []

        # 4. Token.
        verdict = self._token_verdict(scope)
        if verdict is not None:
            status, code, message, challenge = verdict
            await _respond(send, status, code, message, extra + challenge)
            return

        if not extra:
            await self._app(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [*message.get("headers", ()), *extra]
            await send(message)

        await self._app(scope, receive, send_with_cors)

    async def _preflight(self, scope: Scope, origin: str, send: Send) -> None:
        method = _one(scope, b"access-control-request-method")
        requested = _headers(scope, b"access-control-request-headers")
        names = {
            h.strip().lower()
            for value in requested
            for h in value.split(",")
            if h.strip()
        }
        if (
            origin not in ALLOWED_ORIGINS
            or method not in _ALLOWED_METHODS
            or not names <= _ALLOWED_REQUEST_HEADERS
        ):
            await _respond(send, 403, _errors.PREFLIGHT_REJECTED, "preflight rejected")
            return
        headers = [
            (b"access-control-allow-origin", origin.encode()),
            (b"access-control-allow-methods", b"GET, POST"),
            (b"access-control-allow-headers", b"Authorization, Content-Type"),
            (b"access-control-max-age", b"600"),
            (b"vary", b"Origin"),
            (b"content-length", b"0"),
        ]
        await send({"type": "http.response.start", "status": 204, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    def _token_verdict(
        self, scope: Scope
    ) -> tuple[int, str, str, list[tuple[bytes, bytes]]] | None:
        challenge = [(b"www-authenticate", b"Bearer")]
        unauthorized = (
            401,
            _errors.UNAUTHORIZED,
            "a bearer token is required",
            challenge,
        )
        value = _one(scope, b"authorization")
        if value is None:
            return unauthorized
        scheme, space, presented = value.partition(" ")
        if scheme.lower() != "bearer" or not space or not presented.strip():
            return unauthorized
        digest = hashlib.sha256(presented.encode("utf-8", "surrogateescape")).digest()
        if hmac.compare_digest(digest, self._config.token_digest):
            return None
        return (403, _errors.FORBIDDEN, "the bearer token is not valid", [])
