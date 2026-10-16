"""The ADR-0058 guard, called directly with synthetic ASGI scopes."""

import asyncio
import hmac
from typing import Any

import pytest

from openkos.localapi import _errors
from openkos.localapi._guard import (
    MAX_REQUEST_BODY_BYTES,
    LocalApiGuard,
    _GuardConfig,
)
from tests.unit.localapi.conftest import (
    PORT,
    TAURI,
    TOKEN,
    TOKEN_DIGEST,
    RecordingApp,
    Reply,
    call,
    make_scope,
    preflight_scope,
)

ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")


@pytest.fixture
def guard(inner: RecordingApp) -> LocalApiGuard:
    return LocalApiGuard(inner, _GuardConfig(port=PORT, token_digest=TOKEN_DIGEST))


def _assert_envelope(reply: Reply, status: int, code: str) -> None:
    assert reply.status == status
    assert reply.json == {
        "code": code,
        "retryable": False,
        "message": reply.json["message"],
    }
    assert reply.json["message"]
    assert reply.header("content-type") == "application/json"
    assert reply.header("content-length") == str(len(reply.body))


# --- S2a.1 Host --------------------------------------------------------------


@pytest.mark.parametrize(
    "host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"LocalHost:{PORT}"]
)
def test_exact_host_passes(
    guard: LocalApiGuard, inner: RecordingApp, host: str
) -> None:
    reply = call(guard, make_scope(host=host))
    assert reply.status == 200
    assert len(inner.scopes) == 1


@pytest.mark.parametrize(
    "host",
    [
        "evil.com",
        f"127.0.0.1:{PORT + 1}",
        f"[::1]:{PORT}",
        f"127.0.0.1:{PORT}.evil.com",
        "127.0.0.1",
    ],
)
def test_foreign_host_is_refused(
    guard: LocalApiGuard, inner: RecordingApp, host: str
) -> None:
    reply = call(guard, make_scope(host=host))
    _assert_envelope(reply, 403, "host_not_allowed")
    assert inner.scopes == []


def test_absent_host_is_refused(guard: LocalApiGuard, inner: RecordingApp) -> None:
    _assert_envelope(call(guard, make_scope(host=None)), 403, "host_not_allowed")
    assert inner.scopes == []


def test_duplicated_host_is_refused(guard: LocalApiGuard, inner: RecordingApp) -> None:
    scope = make_scope([("host", f"localhost:{PORT}")])
    _assert_envelope(call(guard, scope), 403, "host_not_allowed")
    assert inner.scopes == []


def test_forwarded_host_does_not_change_the_verdict(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    good = make_scope([("x-forwarded-host", "evil.com")])
    assert call(guard, good).status == 200
    bad = make_scope([("x-forwarded-host", f"127.0.0.1:{PORT}")], host="evil.com")
    _assert_envelope(call(guard, bad), 403, "host_not_allowed")


def test_host_check_runs_before_origin_and_token(guard: LocalApiGuard) -> None:
    scope = make_scope(
        [("origin", "https://evil.example")], host="evil.com", token=None
    )
    _assert_envelope(call(guard, scope), 403, "host_not_allowed")


# --- S2a.2 Preflight ---------------------------------------------------------


def test_allowed_preflight_answers_204_without_a_token(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    reply = call(guard, preflight_scope())
    assert reply.status == 204
    assert reply.header("access-control-allow-origin") == TAURI
    assert reply.header("access-control-allow-methods") == "GET, POST"
    assert reply.header("access-control-allow-headers") == "Authorization, Content-Type"
    assert reply.header("access-control-max-age") == "600"
    assert reply.header("vary") == "Origin"
    assert reply.header("access-control-allow-credentials") is None
    assert reply.body == b""
    assert inner.scopes == []


def test_preflight_without_requested_headers_is_allowed(guard: LocalApiGuard) -> None:
    assert call(guard, preflight_scope(request_headers="")).status == 204


@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"request_method": "DELETE"},
        {"request_method": "PUT"},
        {"request_headers": "authorization, x-evil"},
    ],
)
def test_rejected_preflight_is_403_without_allow_headers(
    guard: LocalApiGuard, inner: RecordingApp, kwargs: dict[str, Any]
) -> None:
    reply = call(guard, preflight_scope(**kwargs))
    _assert_envelope(reply, 403, "preflight_rejected")
    assert not reply.has_allow_header
    assert inner.scopes == []


def test_preflight_with_foreign_host_is_host_not_allowed(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    reply = call(guard, preflight_scope(host="evil.example"))
    _assert_envelope(reply, 403, "host_not_allowed")
    assert not reply.has_allow_header
    assert inner.scopes == []


def test_options_without_request_method_is_not_a_preflight(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    scope = make_scope([("origin", TAURI)], method="OPTIONS", token=None)
    _assert_envelope(call(guard, scope), 401, "unauthorized")
    assert inner.scopes == []


# --- S2a.3 Origin ------------------------------------------------------------


def test_absent_origin_passes(guard: LocalApiGuard) -> None:
    assert call(guard, make_scope()).status == 200


@pytest.mark.parametrize("origin", ORIGINS)
def test_allowlisted_origin_passes(guard: LocalApiGuard, origin: str) -> None:
    reply = call(guard, make_scope([("origin", origin)]))
    assert reply.status == 200
    assert reply.header("access-control-allow-origin") == origin


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "null", "tauri://localhost/", "TAURI://localhost"],
)
def test_other_origin_is_refused(
    guard: LocalApiGuard, inner: RecordingApp, origin: str
) -> None:
    reply = call(guard, make_scope([("origin", origin)]))
    _assert_envelope(reply, 403, "origin_not_allowed")
    assert not reply.has_allow_header
    assert inner.scopes == []


def test_disallowed_origin_without_token_is_403_not_401(guard: LocalApiGuard) -> None:
    scope = make_scope([("origin", "https://evil.example")], token=None)
    _assert_envelope(call(guard, scope), 403, "origin_not_allowed")


# --- S2a.4 Token -------------------------------------------------------------


def _unauthorized(reply: Reply) -> None:
    _assert_envelope(reply, 401, "unauthorized")
    assert reply.header("www-authenticate") == "Bearer"


def test_missing_authorization_is_401(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    _unauthorized(call(guard, make_scope(token=None)))
    assert inner.scopes == []


@pytest.mark.parametrize(
    "value", ["Basic x", "Bearer", "Bearer ", "Bearer  ", TOKEN, ""]
)
def test_malformed_authorization_is_401(
    guard: LocalApiGuard, inner: RecordingApp, value: str
) -> None:
    _unauthorized(call(guard, make_scope([("authorization", value)], token=None)))
    assert inner.scopes == []


def test_duplicated_authorization_is_401_even_if_one_is_right(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    scope = make_scope([("authorization", f"Bearer {TOKEN}")])
    _unauthorized(call(guard, scope))
    assert inner.scopes == []


def test_wrong_token_is_403_forbidden(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    reply = call(guard, make_scope(token="wrong"))  # noqa: S106
    _assert_envelope(reply, 403, "forbidden")
    assert reply.header("www-authenticate") is None
    assert inner.scopes == []


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_scheme_is_case_insensitive(guard: LocalApiGuard, scheme: str) -> None:
    scope = make_scope([("authorization", f"{scheme} {TOKEN}")], token=None)
    assert call(guard, scope).status == 200


def test_token_only_in_the_query_string_is_401(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    scope = make_scope(token=None, query=f"token={TOKEN}&access_token={TOKEN}")
    _unauthorized(call(guard, scope))
    assert inner.scopes == []


def test_comparison_is_constant_time_over_equal_length_digests(
    guard: LocalApiGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr("openkos.localapi._guard.hmac.compare_digest", spy)
    call(guard, make_scope(token="short"))  # noqa: S106
    call(guard, make_scope(token="x" * 500))
    assert [(len(a), len(b)) for a, b in seen] == [(32, 32), (32, 32)]
    assert all(b == TOKEN_DIGEST for _, b in seen)


# --- S2a.5 Response headers and scopes --------------------------------------


@pytest.mark.parametrize(
    ("token", "status"),
    [(TOKEN, 200), ("wrong", 403), (None, 401)],
)
def test_allowed_origin_response_carries_cors_headers(
    guard: LocalApiGuard, token: str | None, status: int
) -> None:
    reply = call(guard, make_scope([("origin", TAURI)], token=token))
    assert reply.status == status
    assert reply.header("access-control-allow-origin") == TAURI
    assert reply.header("vary") == "Origin"
    assert reply.header("access-control-expose-headers") == "Location, Retry-After"
    assert reply.header("access-control-allow-credentials") is None


def test_no_origin_response_carries_no_cors_headers(guard: LocalApiGuard) -> None:
    reply = call(guard, make_scope())
    assert not [k for k, _ in reply.headers if k.startswith("access-control-")]
    assert reply.header("x-inner") == "1"


def test_lifespan_scope_passes_through(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    call(guard, {"type": "lifespan"})
    assert inner.scopes == [{"type": "lifespan"}]


def test_websocket_scope_is_closed_with_1008(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    reply = call(guard, make_scope(type_="websocket"))
    assert reply.messages == [{"type": "websocket.close", "code": 1008}]
    assert inner.scopes == []


def test_constants_match_the_design() -> None:
    assert _errors.WRITE_WAIT_SECONDS == 5.0
    assert _errors.RETRY_AFTER_SECONDS == 2
    body = _errors.ErrorBody(code="x", retryable=False, message="m")
    assert body.model_dump() == {"code": "x", "retryable": False, "message": "m"}
    with pytest.raises(ValueError, match="extra"):
        _errors.ErrorBody(code="x", retryable=False, message="m", extra="y")  # type: ignore[call-arg]


# --- S2a.6 Token handling ----------------------------------------------------


def test_guard_config_repr_hides_the_digest() -> None:
    text = repr(_GuardConfig(port=PORT, token_digest=TOKEN_DIGEST))
    assert TOKEN not in text
    assert TOKEN_DIGEST.hex() not in text
    assert repr(TOKEN_DIGEST) not in text
    assert str(PORT) in text


def test_duplicated_origin_is_refused(
    guard: LocalApiGuard, inner: RecordingApp
) -> None:
    scope = make_scope([("origin", TAURI), ("origin", TAURI)])
    _assert_envelope(call(guard, scope), 403, "origin_not_allowed")
    assert inner.scopes == []


# --- request body cap ---------------------------------------------------------


class _Body:
    """A scripted request body, delivered one chunk per `receive()` call."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = list(chunks)
        self.delivered = 0

    async def receive(self) -> dict[str, Any]:
        if not self._chunks:
            return {"type": "http.disconnect"}
        chunk = self._chunks.pop(0)
        self.delivered += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": bool(self._chunks),
        }


class _Reader:
    """An inner app that drains the body, like a route that parses it."""

    def __init__(self) -> None:
        self.read = 0
        self.reached = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.reached = True
        while True:
            message = await receive()
            self.read += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


def _post(app: Any, scope: Any, body: _Body) -> Reply:
    reply = Reply()

    async def send(message: dict[str, Any]) -> None:
        reply.messages.append(message)

    asyncio.run(app(scope, body.receive, send))
    return reply


def _guarded(app: Any) -> LocalApiGuard:
    return LocalApiGuard(app, _GuardConfig(port=PORT, token_digest=TOKEN_DIGEST))


def test_declared_oversized_content_length_is_413_without_reading() -> None:
    reader = _Reader()
    body = _Body(b"x")
    scope = make_scope(
        [("content-length", str(MAX_REQUEST_BODY_BYTES + 1))], method="POST"
    )

    reply = _post(_guarded(reader), scope, body)

    _assert_envelope(reply, 413, "request_too_large")
    assert reader.reached is False
    assert body.delivered == 0


def test_chunked_body_over_the_cap_is_413_and_aborts_the_read() -> None:
    reader = _Reader()
    half = b"x" * (MAX_REQUEST_BODY_BYTES // 2 + 1)
    body = _Body(half, half, half, half)
    scope = make_scope([("transfer-encoding", "chunked")], method="POST")

    reply = _post(_guarded(reader), scope, body)

    _assert_envelope(reply, 413, "request_too_large")
    assert body.delivered == 2  # aborted at the chunk that crossed the cap
    assert [m["status"] for m in reply.messages if "status" in m] == [413]


def test_lying_content_length_cannot_smuggle_a_larger_body() -> None:
    reader = _Reader()
    body = _Body(b"x" * (MAX_REQUEST_BODY_BYTES + 1))
    scope = make_scope([("content-length", "10")], method="POST")

    reply = _post(_guarded(reader), scope, body)

    _assert_envelope(reply, 413, "request_too_large")


def test_body_of_exactly_the_cap_is_accepted() -> None:
    reader = _Reader()
    body = _Body(b"x" * MAX_REQUEST_BODY_BYTES)
    scope = make_scope([("content-length", str(MAX_REQUEST_BODY_BYTES))], method="POST")

    reply = _post(_guarded(reader), scope, body)

    assert reply.status == 200
    assert reader.read == MAX_REQUEST_BODY_BYTES


def test_unauthenticated_oversized_request_is_401_not_413() -> None:
    reader = _Reader()
    scope = make_scope(
        [("content-length", str(MAX_REQUEST_BODY_BYTES + 1))],
        method="POST",
        token=None,
    )

    reply = _post(_guarded(reader), scope, _Body(b"x"))

    _assert_envelope(reply, 401, "unauthorized")
    assert reader.reached is False


def test_foreign_host_oversized_request_is_403_not_413() -> None:
    scope = make_scope(
        [("content-length", str(MAX_REQUEST_BODY_BYTES + 1))],
        method="POST",
        host="evil.com",
    )

    reply = _post(_guarded(_Reader()), scope, _Body(b"x"))

    _assert_envelope(reply, 403, "host_not_allowed")
