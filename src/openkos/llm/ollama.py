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

from openkos.llm.base import EMBED_DIM, Message

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

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """POST `texts` to `{host}/api/embed` and return one `EMBED_DIM`-float
        vector per input, in order (Embedder contract).

        Short-circuits to `[]` with no HTTP call when `texts` is empty.
        Reuses `chat()`'s connect/read transport ladder and `_map_http_error`.
        Parses defensively: prefers the plural `embeddings` key, falls back
        to the legacy singular `embedding` key (wrapped as a one-item list),
        and validates every returned row is exactly `EMBED_DIM` numeric
        values -- any other shape raises `OllamaError`.
        """
        if not texts:
            return []
        url = f"{self._host}/api/embed"
        payload = json.dumps({"model": self._model, "input": list(texts)}).encode(
            "utf-8"
        )
        # Same trusted-host rationale as `chat()`'s S310 note (D2: host is
        # user/env config, normalized to a scheme, never derived from
        # document content).
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
            # Same rationale as `chat()`'s read-phase guard: a transport
            # failure can strike mid-stream too, not only while connecting.
            raise self._unavailable(exc) from exc

        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise TypeError(f"expected a JSON object, got {type(data)!r}")
            if "embeddings" in data:
                rows = data["embeddings"]
            elif "embedding" in data:
                rows = [data["embedding"]]
            else:
                raise KeyError("embeddings")
            if len(rows) != len(texts):
                raise ValueError(
                    f"Ollama /api/embed returned {len(rows)} embeddings "
                    f"for {len(texts)} inputs"
                )
            result = [_validate_embedding_row(row) for row in rows]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OllamaError(f"Malformed response from Ollama: {exc}") from exc
        return result

    def list_models(self) -> list[str]:
        """GET `{host}/api/tags`; return installed model tags (D1). Config-free."""
        url = f"{self._host}/api/tags"
        # Same trusted-host rationale as `chat()`'s S310 note (D2: host is
        # user/env config, normalized to a scheme, never derived from
        # document content).
        request = urllib.request.Request(url, method="GET")  # noqa: S310
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
            raise self._unavailable(exc) from exc

        # Mirror `chat()`'s guard: wrap ALL body parsing -- json.loads, the
        # `models` extraction AND the entry iteration -- in one try/except, so a
        # valid-JSON body whose `models` is null or a non-iterable scalar (e.g.
        # `{"models": null}`, `{"models": 42}`) maps to the OllamaError family
        # instead of leaking a bare TypeError from the `for` loop.
        try:
            entries = json.loads(body)["models"]
            tags: list[str] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                tag = entry.get("model") or entry.get("name")  # D2 field variance
                if isinstance(tag, str) and tag:
                    tags.append(tag)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OllamaError(f"Malformed response from Ollama: {exc}") from exc
        return tags

    def _unavailable(self, exc: BaseException) -> OllamaUnavailable:
        """Build the `OllamaUnavailable` for a transport failure, shared by
        both the connect-phase and read-phase except branches (D4)."""
        return OllamaUnavailable(f"Ollama not reachable at {self._host}: {exc}")


def model_tag_matches(configured: str, installed: list[str]) -> bool:
    """True if `configured` matches any installed tag (D3, D4).

    A bare name (no `:`) normalizes to `<name>:latest` per Ollama
    convention, applied symmetrically to both `configured` and each
    installed tag; comparison after normalization is case-sensitive.
    """
    wanted = configured if ":" in configured else f"{configured}:latest"
    for tag in installed:
        normalized = tag if ":" in tag else f"{tag}:latest"
        if normalized == wanted:
            return True
    return False


def _validate_embedding_row(row: object) -> list[float]:
    """Validate one embedding row: exactly `EMBED_DIM` numeric entries,
    coerced to `float` (Embedder contract). Raises `ValueError` on a wrong
    length or a non-numeric entry, always caught and rewrapped as
    `OllamaError` by the caller."""
    if not isinstance(row, list) or len(row) != EMBED_DIM:
        got = len(row) if isinstance(row, list) else type(row).__name__
        raise ValueError(
            f"expected each embedding row to have exactly {EMBED_DIM} entries, got {got}"
        )
    validated: list[float] = []
    for value in row:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"expected each embedding entry to be numeric, got {type(value)!r}"
            )
        validated.append(float(value))
    return validated


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
