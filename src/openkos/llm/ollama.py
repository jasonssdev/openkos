"""`OllamaClient`: a concrete `LLMBackend` over a local Ollama server.

Leaf module: stdlib `urllib.request`/`json`/`os` only, no `openkos.config`
import (mirrors `fsio`) -- the caller resolves the model tag and passes it
in as an argument.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openkos.llm.base import EMBED_DIM, Message

DEFAULT_HOST = "http://localhost:11434"
"""Ollama's own local default, used when no override is given (D2)."""
DEFAULT_TIMEOUT = 120.0
"""Generous default: model inference is slow, avoid premature timeouts (D6)."""
DEFAULT_EMBED_RETRY_ATTEMPTS = 3
"""Default number of `embed()` attempts (1 initial + 2 retries) before
raising a persistent `OllamaError`-family failure to the caller (D3)."""
DEFAULT_EMBED_RETRY_BACKOFF_BASE = 0.5
"""Default exponential-backoff base, in seconds, between `embed()` retry
attempts: sleep duration is `base * 2 ** (attempt - 1)` for the failed
1-indexed `attempt` (D3)."""


class OllamaError(Exception):
    """Base error for any Ollama chat failure (D4); also raised directly for
    non-404 HTTP errors and malformed/unexpected response bodies."""


class OllamaUnavailable(OllamaError):
    """Raised on any transport failure: connection refused or timeout while
    connecting, or a reset/timeout/incomplete read while streaming the
    response body after a successful connect (D4)."""


class OllamaModelNotFound(OllamaError):
    """Raised on a 404 response whose body reports the model tag as not found (D4)."""


class OllamaEmbeddingDimensionMismatch(OllamaError):
    """Raised when an `/api/embed` response row has a length other than
    `EMBED_DIM` (D7): a PERMANENT, non-healing misconfiguration -- the
    configured embedding model itself does not emit `EMBED_DIM`-dimensional
    vectors -- distinct from the generic transient `OllamaError` (malformed
    JSON, missing vector key, non-numeric row entries). Subclasses
    `OllamaError` so an unmodified bare `except OllamaError` still catches
    it, but MUST be checked ahead of that bare clause at any call site that
    needs to treat it as fatal rather than transient (mirrors the existing
    `OllamaModelNotFound` ordering discipline). Never retried by `embed()`'s
    retry-with-backoff loop (D8): a wrong dimension cannot heal by retry."""


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """One entry from `/api/tags`: its tag plus optional model family (D1).

    `family` is `None` when the server omits `details` or `details.family`
    entirely -- always still returned, never dropped."""

    tag: str
    family: str | None


_EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert"})
"""Known embedding-model families (D2), matched case-insensitively."""

_EMBEDDING_TAG_MARKER = "embed"
"""Substring that makes a tag self-describing as an embedding model
(`qwen3-embedding`, `nomic-embed-text`, ...), matched case-insensitively."""


def is_embedding_model(model: InstalledModel) -> bool:
    """True if `model.family` is a known embedding family (D2), or the tag
    itself names the model as an embedding one (issue #188).

    A missing/unknown family still classifies as NON-embedding -- ambiguity
    never excludes a model from chat-model candidacy. The tag marker is not
    ambiguity but evidence, so it classifies on its own: Ollama reports no
    family for some embedding tags, and a family it does report may be a
    plausible chat family (`qwen` for `qwen3-embedding`). Matching the marker
    regardless of family keeps that mislabelling from readmitting an
    unusable model to the chat-model picker."""
    if model.family is not None and model.family.lower() in _EMBEDDING_FAMILIES:
        return True
    return _EMBEDDING_TAG_MARKER in model.tag.lower()


def _normalize_host(host: str) -> str:
    """Prepend `http://` to a bare `host:port` value (D2 risk note)."""
    if host.startswith(("http://", "https://")):
        return host
    return f"http://{host}"


@dataclass(frozen=True, slots=True)
class EmbedHostLocality:
    """`classify_embed_host`'s verdict: whether the configured embedding
    host is literally local, plus the userinfo-REDACTED host string that is
    the ONLY value a caller may print (issue #199)."""

    is_local: bool
    display_host: str


_LOCAL_HOST_LITERALS = frozenset({"localhost", "::1"})
"""Non-IPv4 literal loopback spellings (after lowercasing, one optional
trailing root dot stripped, brackets removed). LITERAL forms only: the
expanded-zeros IPv6 loopback (`0:0:0:0:0:0:0:1`) deliberately does not
count -- over-warning is the accepted failure direction (issue #199)."""


def _is_loopback_ipv4_literal(host: str) -> bool:
    """True for a literal `127.0.0.0/8` dotted quad: four ASCII-digit
    octets, each 0-255, the first exactly `127`. No DNS, no `ipaddress`
    equivalence -- literal form only (issue #199)."""
    parts = host.split(".")
    if len(parts) != 4 or parts[0] != "127":
        return False
    return all(part.isascii() and part.isdigit() and int(part) <= 255 for part in parts)


def classify_embed_host(raw: str | None) -> EmbedHostLocality:
    """Classify a configured Ollama host value as literally local or not,
    never raising, with userinfo always redacted from `display_host`
    (issue #199; the withdrawn #183-PR3 predecessor is the trap spec).

    Local means loopback BY LITERAL FORM only -- `localhost` (any case, one
    optional trailing root dot), a `127.0.0.0/8` dotted quad, or `::1`
    (bracketed or not). No DNS resolution, ever: the check runs on every
    ingest/reindex/query and a lookup can hang. `None`/empty means the
    default local host; a port-only value (`:11434`) overrides only the
    port, so its empty host is likewise the local default.

    Deliberately does NOT use `urlsplit`: it raises `ValueError: Invalid
    IPv6 URL` on an unmatched bracket (`[::1:11434`, a plausible typo) and
    splits a bracket-less IPv6 literal at the FIRST colon (`fe80::1234:5678`
    -> host `fe80`, which nobody configured). This parse degrades instead:
    an unmatched bracket classifies as non-local (over-warning is the
    accepted direction for an advisory) and a bracket-less multi-colon
    value is one whole-value host. Userinfo (everything up to the LAST `@`
    in the authority, urlsplit's own rule) is stripped BEFORE `display_host`
    is built, on every path -- including the unparseable one, where the
    predecessor echoed a plaintext password. Two malformed shapes get the
    same treatment (review finding R1-userinfo-redaction-bypass): a reserved
    separator smuggled into userinfo ahead of the `@` redacts against the
    full remainder and classifies non-local, and a non-numeric "port" is
    never displayed -- it can be a credential pasted without a host."""
    if raw is None or not raw.strip():
        return EmbedHostLocality(is_local=True, display_host="localhost")
    rest = raw.strip()
    if "://" in rest:
        rest = rest.split("://", 1)[1]
    authority = rest
    for separator in "/?#":
        authority = authority.split(separator, 1)[0]
    if "@" in rest and "@" not in authority:
        # A reserved separator sits BEFORE the `@`: the authority cut went
        # through userinfo and discarded the `@host` remainder, which is
        # how the R1-userinfo-redaction-bypass leaked a credential as the
        # "host". The value is malformed, so redact against the full
        # remainder and classify non-local outright.
        hostport = rest.rpartition("@")[2]
        for separator in "/?#":
            hostport = hostport.split(separator, 1)[0]
        return EmbedHostLocality(is_local=False, display_host=hostport)
    # Redact userinfo FIRST: everything below sees only the host[:port]
    # remainder, so no later branch -- parseable or not -- can leak it.
    hostport = authority.rpartition("@")[2]
    if hostport.startswith("["):
        closing = hostport.find("]")
        if closing == -1:
            # Unmatched bracket: unparseable. Degrade to non-local rather
            # than raise -- this runs inside fail-open paths after ingest
            # has already committed.
            return EmbedHostLocality(is_local=False, display_host=hostport)
        host = hostport[1:closing]
        tail = hostport[closing + 1 :]
        if tail.startswith(":") and not tail[1:].isdigit():
            # A non-numeric "port" is not a port; it can be a pasted
            # credential. Never display it.
            hostport = hostport[: closing + 1]
    elif hostport.count(":") > 1:
        # Bracket-less IPv6 literal: the whole value is the host; splitting
        # at the first colon would invent a host nobody configured.
        host = hostport
    else:
        host, colon, port = hostport.partition(":")
        if colon and not port.isdigit():
            # Same rule as the bracketed branch: a non-numeric "port" may
            # be a credential (`user:s3cret` pasted bare). Display only the
            # host part that precedes it.
            hostport = host
    if not host:
        return EmbedHostLocality(is_local=True, display_host=hostport or "localhost")
    normalized = host.lower().removesuffix(".")
    is_local = normalized in _LOCAL_HOST_LITERALS or _is_loopback_ipv4_literal(
        normalized
    )
    return EmbedHostLocality(is_local=is_local, display_host=hostport)


class OllamaClient:
    """A chat-completion client for a locally running Ollama server (D1-D6)."""

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        embed_retry_attempts: int = DEFAULT_EMBED_RETRY_ATTEMPTS,
        embed_retry_backoff_base: float = DEFAULT_EMBED_RETRY_BACKOFF_BASE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Resolve `host` (arg > `OLLAMA_HOST` env > default) and store config (D2).

        `embed_retry_attempts`/`embed_retry_backoff_base`/`sleep` configure
        ONLY `embed()`'s retry-with-backoff loop (D1/D3) -- `chat()` and
        `list_models()` are untouched, never retried. `sleep` defaults to
        the real `time.sleep`; tests inject a spy to prove no real sleep
        occurs and to observe the backoff schedule."""
        self._model = model
        self._timeout = timeout
        self._urlopen = urlopen
        self._embed_retry_attempts = embed_retry_attempts
        self._embed_retry_backoff_base = embed_retry_backoff_base
        self._sleep = sleep
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
        Wraps `_embed_once` in a retry-with-backoff loop (D1/D3, llm-client:
        Transient Embed Failures Are Retried Before Propagating):
        `OllamaModelNotFound` and `OllamaEmbeddingDimensionMismatch` both
        raise immediately, consuming no retry attempt -- a missing model or
        a wrong-dimension response cannot heal mid-run (D8). Every other
        `OllamaError`
        (the generic transient class, AND `OllamaUnavailable`) is retried up
        to `embed_retry_attempts` times total, sleeping
        `embed_retry_backoff_base * 2 ** (attempt - 1)` between attempts
        (exponential). WHEN a retried attempt succeeds, the result is
        returned exactly as a first-attempt success would be -- no
        observable trace of the retry beyond the injected `sleep` calls.
        WHEN the retry budget is exhausted, the last exception raises
        unchanged to the caller.
        """
        if not texts:
            return []
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._embed_once(texts)
            except (OllamaModelNotFound, OllamaEmbeddingDimensionMismatch):
                raise
            except OllamaError:
                if attempt >= self._embed_retry_attempts:
                    raise
                backoff = self._embed_retry_backoff_base * 2 ** (attempt - 1)
                self._sleep(backoff)

    def _embed_once(self, texts: Sequence[str]) -> list[list[float]]:
        """One `embed()` attempt: a single POST to `{host}/api/embed` and
        response parse, with no retry logic of its own (the retry loop lives
        in `embed()`).

        Reuses `chat()`'s connect/read transport ladder and `_map_http_error`.
        Parses defensively: prefers the plural `embeddings` key, falls back
        to the legacy singular `embedding` key (wrapped as a one-item list),
        and validates every returned row is exactly `EMBED_DIM` numeric
        values -- any other shape raises `OllamaError`.
        """
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

    def list_models(self) -> list[InstalledModel]:
        """GET `{host}/api/tags`; return installed models with tag and
        family (D1). Config-free."""
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
            models: list[InstalledModel] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                tag = entry.get("model") or entry.get("name")  # D2 field variance
                if isinstance(tag, str) and tag:
                    details = entry.get("details", {})
                    family = (
                        details.get("family") if isinstance(details, dict) else None
                    )
                    models.append(
                        InstalledModel(
                            tag=tag, family=family if isinstance(family, str) else None
                        )
                    )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OllamaError(f"Malformed response from Ollama: {exc}") from exc
        return models

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
    coerced to `float` (Embedder contract). Raises the distinct
    `OllamaEmbeddingDimensionMismatch` (D7) on a wrong length -- NOT a
    `ValueError`, so it escapes `_embed_once`'s
    `except (JSONDecodeError, KeyError, TypeError, ValueError)` rewrap
    unwrapped, with the actual and expected (`EMBED_DIM`) length in its
    message. Raises `ValueError` on a non-numeric entry (correct length),
    always caught and rewrapped as the generic `OllamaError` by the caller
    -- scope discipline: only the wrong-LENGTH branch is permanent."""
    if not isinstance(row, list) or len(row) != EMBED_DIM:
        got = len(row) if isinstance(row, list) else type(row).__name__
        raise OllamaEmbeddingDimensionMismatch(
            f"Ollama returned an embedding row of length {got}, expected "
            f"exactly {EMBED_DIM} (EMBED_DIM) -- this is a permanent "
            "dimension mismatch caused by the configured embedding model, "
            "not a transient failure; it will not heal by retrying."
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
