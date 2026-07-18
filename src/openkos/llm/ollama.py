"""`OllamaClient`: a concrete `LLMBackend` over a local Ollama server.

Leaf module: stdlib `urllib.request`/`json`/`os` only, no `openkos.config`
import (mirrors `fsio`) -- the caller resolves the model tag and passes it
in as an argument.
"""

import http.client
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from openkos.llm.base import Message

DEFAULT_HOST = "http://localhost:11434"
"""Ollama's own local default, used when no override is given (D2)."""
DEFAULT_TIMEOUT = 120.0
"""Generous default: model inference is slow, avoid premature timeouts (D6)."""


class OllamaError(Exception):
    """Base error for any Ollama chat failure (D4); also raised directly for
    non-404 HTTP errors and malformed/unexpected response bodies."""


class OllamaUnavailable(OllamaError):
    """Raised on any transport failure: connection refused or timeout while
    connecting, or a reset/timeout/incomplete read while streaming the
    response body after a successful connect (D4)."""


class OllamaModelNotFound(OllamaError):
    """Raised on a 404 response whose body reports the model tag as not found (D4)."""


def _normalize_host(host: str) -> str:
    """Prepend `http://` to a bare `host:port` value (D2 risk note)."""
    if host.startswith(("http://", "https://")):
        return host
    return f"http://{host}"


class OllamaClient:
    """A chat-completion client for a locally running Ollama server (D1-D6)."""

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        """Resolve `host` (arg > `OLLAMA_HOST` env > default) and store config (D2)."""
        self._model = model
        self._timeout = timeout
        self._urlopen = urlopen
        resolved_host = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        self._host = _normalize_host(resolved_host).rstrip("/")

    def chat(self, messages: Sequence[Message]) -> str:
        """POST `messages` to `{host}/api/chat` and return `message.content` (D5, D6)."""
        url = f"{self._host}/api/chat"
        payload = json.dumps(
            {
                "model": self._model,
                "messages": list(messages),
                "stream": False,
                "think": False,
            }
        ).encode("utf-8")
        # The URL is always `{trusted host}/api/chat` (D2: host is user/env
        # config, normalized to a scheme, never derived from document content) --
        # not an arbitrary user-supplied URL, so the S310 scheme audit does not
        # apply here.
        request = urllib.request.Request(  # noqa: S310
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = self._urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            raise _map_http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise self._unavailable(exc) from exc

        try:
            body = response.read()
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.IncompleteRead,
        ) as exc:
            # A transport failure (timeout, reset connection, incomplete
            # read, ...) can surface here too: Ollama streams the body over
            # the same socket for up to `timeout` seconds, so this is not
            # merely a theoretical branch (D6). `IncompleteRead` is listed
            # explicitly because it subclasses `http.client.HTTPException`,
            # not `OSError`, so it would otherwise leak uncaught.
            raise self._unavailable(exc) from exc

        try:
            data = json.loads(body)
            content = data["message"]["content"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OllamaError(f"Malformed response from Ollama: {exc}") from exc

        if not isinstance(content, str):
            raise OllamaError(
                f"Expected message.content to be a string, got {type(content)!r}"
            )
        return content

    def _unavailable(self, exc: BaseException) -> OllamaUnavailable:
        """Build the `OllamaUnavailable` for a transport failure, shared by
        both the connect-phase and read-phase except branches (D4)."""
        return OllamaUnavailable(f"Ollama not reachable at {self._host}: {exc}")


def _map_http_error(exc: urllib.error.HTTPError) -> OllamaError:
    """Map an `HTTPError` to `OllamaModelNotFound` (404 not-found) or `OllamaError` (D5)."""
    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.IncompleteRead,
    ):
        # A transport failure can strike while reading the *error* response
        # body too (symmetry with the success-path read in `chat`); degrade to
        # an empty detail rather than leaking a raw exception -- the HTTP status
        # code alone still classifies the failure into a typed `OllamaError`.
        detail = ""
    if exc.code == 404 and "not found" in detail.lower():
        return OllamaModelNotFound(f"Model not found ({exc.code}): {detail}")
    return OllamaError(f"Ollama request failed ({exc.code}): {detail}")
