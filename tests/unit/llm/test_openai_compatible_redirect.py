"""`OpenAICompatibleClient` must never follow an HTTP redirect (security
fix, issue #1057 Phase 4): `urllib.request.HTTPRedirectHandler.
redirect_request`'s default implementation copies every non-content
header -- including `Authorization` -- onto a redirected 301/302/303
request (converted to GET, but the header rides along), and would forward
this client's bearer key to whatever host a 3xx `Location` header names.
The `openai-compatible-client` spec already requires the key never be
redirected to another host.

Mechanically: when `redirect_request` returns `None` (this client's
`_NoRedirectHandler`, unconditionally, for every status/method), urllib
does NOT hand back a normal response object -- `OpenerDirector._call_chain`
treats the `None` as "no handler answered" and falls through to
`http_error_default`, which RAISES `urllib.error.HTTPError` carrying the
original 3xx status and headers. Every refused redirect therefore surfaces
through `chat()`'s existing `except urllib.error.HTTPError` branch, not as
a response with a 3xx status to inspect after the fact.

`test_redirect_is_never_followed_and_key_never_forwarded` is a REAL
integration test (two local `http.server` instances on `127.0.0.1`, no
fakes) proving the actual default `urlopen` -- not a mock of it -- refuses
to forward the request. It uses a 303 (See Other) specifically because
that is a status the STDLIB's own default `redirect_request` WOULD
auto-follow for a POST (converting it to GET while still copying the
`Authorization` header) -- a 307/308 POST redirect is already refused by
the stock `urllib.request.urlopen` for an unrelated reason (RFC 2616
consent), so it would not actually exercise this fix. The test needs
`@pytest.mark.live_backend` to lift `tests/unit/conftest.py`'s
fail-closed socket guard for its loopback `connect()` calls; that
fixture's own docstring anticipates exactly this ("a future
loopback-server test will need to opt out explicitly"). Every other test
in this module uses a fake `urlopen` and needs no such marker.
"""

import http.server
import io
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

import pytest

from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleError,
)


def _http_redirect_error(code: int, location: str | None) -> urllib.error.HTTPError:
    """Build a real `HTTPError` shaped like a refused redirect: the
    headers `_NoRedirectHandler`'s refusal preserves are exactly the
    ORIGINAL 3xx response's headers, which is where `Location` lives."""
    headers: dict[str, str] = {}
    if location is not None:
        headers["Location"] = location
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8080/v1/chat/completions",
        code=code,
        msg="redirect",
        hdrs=headers,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )


def _fake_urlopen_raising_after_capture(
    exc: Exception, captured: list[urllib.request.Request]
) -> Any:
    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        captured.append(request)
        raise exc

    return _urlopen


@pytest.mark.parametrize(
    ("status", "location"),
    [
        (307, "http://127.0.0.1:9/v1/chat/completions"),
        (301, "http://127.0.0.1:9/v1/chat/completions"),
        (302, "http://127.0.0.1:9/v1/chat/completions"),
        (303, "http://127.0.0.1:9/v1/chat/completions"),
        (308, None),
    ],
)
def test_any_3xx_response_raises_mapped_error(
    status: int, location: str | None
) -> None:
    """Any refused-redirect `HTTPError` -- with or without a `Location`
    header -- raises a typed, non-retryable `OpenAICompatibleError` naming
    the status, rather than being forwarded to `_map_http_error`'s generic
    "request failed" branch or silently followed.

    **RED today**: `chat` has no dedicated redirect classification;
    `_map_http_error`'s generic fallback happens to also produce an
    `OpenAICompatibleError`, but naming that requirement explicitly (via
    the mutation-proof below) is what proves this path exists on
    purpose."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen_raising_after_capture(
            _http_redirect_error(status, location), captured
        ),
    )

    with pytest.raises(OpenAICompatibleError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])
    assert str(status) in str(excinfo.value)
    assert "redirect" in str(excinfo.value).lower()


def test_redirect_error_message_never_contains_configured_key() -> None:
    """Precondition: the key IS configured to be sent (the captured
    request really does carry it) -- proving there is something that
    could have leaked. Assertion: the raised error's message never
    contains it anyway."""
    captured: list[urllib.request.Request] = []
    sentinel = "sk-fake-redirect-sentinel"
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        api_key=sentinel,
        urlopen=_fake_urlopen_raising_after_capture(
            _http_redirect_error(
                303, "http://127.0.0.1:9/v1/chat/completions?token=leak"
            ),
            captured,
        ),
    )

    with pytest.raises(OpenAICompatibleError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    # Precondition: the key really was sent on the request that got the
    # redirect response.
    assert captured[0].get_header("Authorization") == f"Bearer {sentinel}"
    assert sentinel not in str(excinfo.value)


def test_redirect_message_does_not_echo_location_query_string() -> None:
    """The `Location` header's query string (a server could smuggle a
    token there) is never echoed into this client's own diagnostics."""
    captured: list[urllib.request.Request] = []
    client = OpenAICompatibleClient(
        model="qwen3",
        base_url="http://127.0.0.1:8080",
        urlopen=_fake_urlopen_raising_after_capture(
            _http_redirect_error(
                303, "http://127.0.0.1:9/v1/chat/completions?token=leak"
            ),
            captured,
        ),
    )

    with pytest.raises(OpenAICompatibleError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}])

    assert "token=leak" not in str(excinfo.value)
    assert "?" not in str(excinfo.value)


def test_default_urlopen_is_the_no_redirect_opener() -> None:
    """The default `urlopen` bound to a fresh client is the refusing
    opener's `.open`, NOT `urllib.request.urlopen` -- a structural pin
    independent of the fake- and real-server-based tests above, so a
    regression that swaps the default back is caught even if the heavier
    tests are skipped or slow to run.

    **RED today**: the default is still the plain, redirect-following
    `urllib.request.urlopen`."""
    client = OpenAICompatibleClient(model="qwen3", base_url="http://127.0.0.1:8080")

    assert client._urlopen is not urllib.request.urlopen


class _RedirectingHandler(http.server.BaseHTTPRequestHandler):
    """Real HTTP server: replies with a configurable 3xx `status` to
    every POST, redirecting to `location` (both class attributes set
    per-test)."""

    status: int = 303
    location: str = ""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(self.status)
        self.send_header("Location", self.location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format_: str, *args: object) -> None:
        """Silence the default stderr access log."""


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Real HTTP server standing in for the redirect TARGET: records every
    request it receives (there should be none) and would reply 200 with a
    real chat body if it ever were reached -- via either verb, since a
    303 redirect converts the original POST into a GET."""

    received: ClassVar[list[dict[str, str]]] = []

    def do_GET(self) -> None:
        self._record_and_reply()

    def do_POST(self) -> None:
        self._record_and_reply()

    def _record_and_reply(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        type(self).received.append(dict(self.headers.items()))
        body = b'{"choices":[{"message":{"role":"assistant","content":"leaked"}}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_: str, *args: object) -> None:
        """Silence the default stderr access log."""


@contextmanager
def _run_server(
    handler: type[http.server.BaseHTTPRequestHandler],
) -> Iterator[http.server.HTTPServer]:
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.live_backend
def test_redirect_is_never_followed_and_key_never_forwarded() -> None:
    """Real end-to-end proof: server A (the configured `base_url`) replies
    303 to server B; server B must NEVER receive a request (and therefore
    never see the `Authorization` header at all), and `chat()` must raise
    a typed error rather than transparently returning server B's reply.

    **RED today**: the default `urlopen` follows a 303 (stdlib converts
    the POST to a GET but still copies `Authorization`), so server B DOES
    receive the request with the header, and `chat()` returns `"leaked"`
    instead of raising."""
    received: list[dict[str, str]] = []

    class _Target(_RecordingHandler):
        pass

    _Target.received = received

    with _run_server(_Target) as target_server:
        target_port = target_server.server_port

        class _Source(_RedirectingHandler):
            pass

        _Source.status = 303
        _Source.location = f"http://127.0.0.1:{target_port}/v1/chat/completions"

        with _run_server(_Source) as source_server:
            sentinel = "sk-real-redirect-proof-sentinel"
            client = OpenAICompatibleClient(
                model="qwen3",
                base_url=f"http://127.0.0.1:{source_server.server_port}",
                api_key=sentinel,
                timeout=5.0,
            )

            with pytest.raises(OpenAICompatibleError) as excinfo:
                client.chat([{"role": "user", "content": "hi"}])

            assert received == []
            assert sentinel not in str(excinfo.value)
