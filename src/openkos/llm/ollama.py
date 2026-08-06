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
DEFAULT_TIMEOUT = 600.0
"""Generous default: model inference is slow, avoid premature timeouts (D6).

Raised from 120s (issue #405). 120s was measured too low for real sources:
across 9 sources x 4 sampling arms x 5 runs, 8 calls timed out, all of them
on 6-17 KB real documents and none on the 700-800 B demo fixtures. This is
the floor every caller inherits; a workspace tunes the CHAT seams through
`config.Config.chat_timeout`, which defaults to this same value.

A longer deadline does not rescue a model that never terminates -- the same
measurement saw 5 of 5 timeouts at 120s and 5 of 5 again at 300s under
greedy decoding. That is #404's territory, not this constant's."""
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


class OllamaGenerationCapped(OllamaError):
    """Raised when `chat()`'s response reports `done_reason == "length"`
    (issue #422): the model hit the configured `max_generation_tokens`
    ceiling before it finished, so the reply was cut off mid-generation and
    is unusable.

    A truncated reply is not a partial success -- `llm.parsing.
    extract_json_items` returns `[]` on a mid-JSON truncation, so there is
    nothing to salvage. Subclasses `OllamaError` so it propagates through
    every existing `except OllamaError` handler unmodified (D4), landing in
    exactly the same handling a hung call gets today: loud, per-source
    failure, never a silent empty result."""


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
class BackendHostLocality:
    """`classify_backend_host`'s verdict: whether a configured Ollama
    BACKEND host is literally local, plus the userinfo-REDACTED host string
    that is the ONLY value a caller may print (issue #199).

    Named for the BACKEND, not the embedder (issue #240): the same verdict
    now also decides whether a `confidential` concept may be included in an
    `llm.chat` payload, so a name that said "embed" would misdescribe half
    its consumers. Only the name changed -- the classification logic
    hardened by #199 and #353 is frozen behavior."""

    is_local: bool
    display_host: str


_LOCAL_HOST_LITERALS = frozenset({"localhost", "::1"})
"""Non-IPv4 literal loopback spellings (after lowercasing, one optional
trailing root dot stripped, brackets removed). LITERAL forms only: the
expanded-zeros IPv6 loopback (`0:0:0:0:0:0:0:1`) deliberately does not
count -- over-warning is the accepted failure direction (issue #199)."""


_UNPARSEABLE_DISPLAY = "<unparseable>"
"""`display_host` placeholder for a malformed value whose redacted remainder
is EMPTY (`@`, `user:s3cret/x@`): the advisory must name something, an empty
string reads like a bug, and the raw value can never be shown (issue #353)."""

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _plausible_bracketless_ipv6(value: str) -> bool:
    """True when a bracket-less multi-colon value plausibly IS an IPv6
    literal: it contains `::`, or it consists solely of hex digits and
    colons with every segment at most 4 hex chars (issue #353, item 1).

    Anything else (`user:s3cret:extra`) is NOT granted the whole-value-host
    treatment -- the whole value would put a pasted credential on stderr."""
    if "::" in value:
        return True
    return all(
        len(segment) <= 4 and all(char in _HEX_DIGITS for char in segment)
        for segment in value.split(":")
    )


def _is_clean_hostport(authority: str) -> bool:
    """True when `authority` parses as a plain host[:numeric-port]: no `@`,
    non-empty host, port absent or all digits (issue #353, item 5).

    This is the authority-shape discriminator for a value whose only `@`
    sits AFTER the first path/query/fragment separator: a clean authority
    means the `@` belongs to the path and the authority is the host; a
    suspicious one (non-numeric port -- the credential-typo shape) keeps
    the redact-against-full-remainder treatment."""
    if "@" in authority:
        return False
    if authority.startswith("["):
        closing = authority.find("]")
        if closing == -1:
            return False
        host = authority[1:closing]
        tail = authority[closing + 1 :]
        return bool(host) and (
            not tail or (tail.startswith(":") and tail[1:].isdigit())
        )
    if authority.count(":") > 1:
        return _plausible_bracketless_ipv6(authority)
    host, colon, port = authority.partition(":")
    return bool(host) and (not colon or port.isdigit())


def _is_loopback_ipv4_literal(host: str) -> bool:
    """True for a literal `127.0.0.0/8` dotted quad: four ASCII-digit
    octets, each 0-255, the first exactly `127`. No DNS, no `ipaddress`
    equivalence -- literal form only (issue #199)."""
    parts = host.split(".")
    if len(parts) != 4 or parts[0] != "127":
        return False
    return all(part.isascii() and part.isdigit() and int(part) <= 255 for part in parts)


def classify_backend_host(raw: str | None) -> BackendHostLocality:
    """Classify a configured Ollama host value as literally local or not,
    never raising, with userinfo always redacted from `display_host`
    (issue #199; the withdrawn #183-PR3 predecessor is the trap spec).

    This is the ONE locality authority in the codebase: the embedding-host
    advisory (#199/#353) and the confidential local exemption (#240) both
    read it, so locality can never be answered two different ways by two
    different callers. Its classification rules below are frozen -- #240
    changed only WHAT gets passed in (the host a client actually resolved,
    not an env var read independently), never how a value is judged.

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
    never displayed -- it can be a credential pasted without a host.

    The #199 review's deterministic follow-ups tighten the corners
    (issue #353):

    1. The bracket-less multi-colon whole-value-host treatment applies only
       to values that plausibly ARE IPv6 literals (contain `::`, or are
       solely hex segments of at most 4 chars and colons). Anything else
       (`user:s3cret:extra`) falls through to plain host:port handling,
       where the non-numeric-port guard drops everything after the host.
    2. An empty host that came out of an EXPLICIT balanced bracket pair
       (`[]`, `[]:11434`) is not the local default: non-local, displaying
       the hostport.
    3. A value that reduces to an empty host only AFTER userinfo redaction
       (`@`, `@@@`, `http://user@`) is malformed: non-local, displayed as
       the `<unparseable>` placeholder (never an empty string). A lone `:`
       (no `@`) stays the local default like `:11434`.
    4. When the only `@` sits AFTER the first `/?#` separator, the
       AUTHORITY decides: a clean host[:numeric-port] authority means the
       `@` belongs to the path and the authority classifies normally
       (`http://localhost:11434/v1@x` is local and silent); only a
       suspicious authority (non-numeric port -- the credential-typo
       shape) keeps the redact-against-full-remainder treatment, and an
       empty redacted remainder displays the placeholder."""
    if raw is None or not raw.strip():
        return BackendHostLocality(is_local=True, display_host="localhost")
    rest = raw.strip()
    if "://" in rest:
        rest = rest.split("://", 1)[1]
    authority = rest
    for separator in "/?#":
        authority = authority.split(separator, 1)[0]
    if "@" in rest and "@" not in authority and not _is_clean_hostport(authority):
        # A reserved separator sits BEFORE the `@` and the authority itself
        # is suspicious (empty host, or a non-numeric "port" -- the
        # credential-typo shape): the authority cut went through userinfo
        # and discarded the `@host` remainder, which is how the
        # R1-userinfo-redaction-bypass leaked a credential as the "host".
        # The value is malformed, so redact against the full remainder and
        # classify non-local outright. When the authority is instead a
        # CLEAN host[:numeric-port], the `@` belongs to the PATH
        # (`http://localhost:11434/v1@x`) and the authority classifies
        # normally below (issue #353, item 5).
        hostport = rest.rpartition("@")[2]
        for separator in "/?#":
            hostport = hostport.split(separator, 1)[0]
        return BackendHostLocality(
            is_local=False, display_host=hostport or _UNPARSEABLE_DISPLAY
        )
    # Redact userinfo FIRST: everything below sees only the host[:port]
    # remainder, so no later branch -- parseable or not -- can leak it.
    had_userinfo = "@" in authority
    hostport = authority.rpartition("@")[2]
    if hostport.startswith("["):
        closing = hostport.find("]")
        if closing == -1:
            # Unmatched bracket: unparseable. Degrade to non-local rather
            # than raise -- this runs inside fail-open paths after ingest
            # has already committed.
            return BackendHostLocality(is_local=False, display_host=hostport)
        host = hostport[1:closing]
        tail = hostport[closing + 1 :]
        if tail.startswith(":") and not tail[1:].isdigit():
            # A non-numeric "port" is not a port; it can be a pasted
            # credential. Never display it.
            hostport = hostport[: closing + 1]
        if not host:
            # An EXPLICIT balanced-but-empty bracket pair (`[]`,
            # `[]:11434`) is not the unset local default: someone
            # configured it, and it names no local literal (issue #353,
            # item 2).
            return BackendHostLocality(is_local=False, display_host=hostport)
    elif hostport.count(":") > 1 and _plausible_bracketless_ipv6(hostport):
        # Bracket-less IPv6 literal: the whole value is the host; splitting
        # at the first colon would invent a host nobody configured. Only a
        # PLAUSIBLE IPv6 literal earns this -- `user:s3cret:extra` would
        # put a pasted credential on stderr (issue #353, item 1).
        host = hostport
    else:
        host, colon, port = hostport.partition(":")
        if colon and not port.isdigit():
            # Same rule as the bracketed branch: a non-numeric "port" may
            # be a credential (`user:s3cret` pasted bare, `s3cret:extra`
            # after a multi-colon fallthrough). Display only the host part
            # that precedes it -- EVERYTHING after the first colon drops.
            hostport = host
    if not host:
        if had_userinfo:
            # The host is empty only AFTER userinfo redaction (`@`, `@@@`,
            # `http://user@`): malformed, not the local default. The
            # remainder may be empty, and an empty display reads like a
            # bug, so fall back to the placeholder (issue #353, item 3).
            return BackendHostLocality(
                is_local=False, display_host=hostport or _UNPARSEABLE_DISPLAY
            )
        return BackendHostLocality(is_local=True, display_host=hostport or "localhost")
    normalized = host.lower().removesuffix(".")
    is_local = normalized in _LOCAL_HOST_LITERALS or _is_loopback_ipv4_literal(
        normalized
    )
    return BackendHostLocality(is_local=is_local, display_host=hostport)


class OllamaClient:
    """A chat-completion client for a locally running Ollama server (D1-D6)."""

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_generation_tokens: int | None = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        embed_retry_attempts: int = DEFAULT_EMBED_RETRY_ATTEMPTS,
        embed_retry_backoff_base: float = DEFAULT_EMBED_RETRY_BACKOFF_BASE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Resolve `host` (arg > `OLLAMA_HOST` env > default) and store config (D2).

        `max_generation_tokens` (issue #422) is the CHAT-only generation
        ceiling: when given, `chat()` forwards it as `options.num_predict`
        and raises `OllamaGenerationCapped` if the reply is cut off before
        finishing. `None` (the default) omits `options` entirely, so an
        opted-out caller sends a request byte-identical to before this
        change -- never affects `embed()` or `list_models()`.

        `embed_retry_attempts`/`embed_retry_backoff_base`/`sleep` configure
        ONLY `embed()`'s retry-with-backoff loop (D1/D3) -- `chat()` and
        `list_models()` are untouched, never retried. `sleep` defaults to
        the real `time.sleep`; tests inject a spy to prove no real sleep
        occurs and to observe the backoff schedule."""
        self._model = model
        self._timeout = timeout
        self._max_generation_tokens = max_generation_tokens
        self._urlopen = urlopen
        self._embed_retry_attempts = embed_retry_attempts
        self._embed_retry_backoff_base = embed_retry_backoff_base
        self._sleep = sleep
        resolved_host = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        self._host = _normalize_host(resolved_host).rstrip("/")

    @property
    def resolved_host(self) -> str:
        """The normalized host this client will ACTUALLY send to -- the exact
        string every request URL is built from (issue #240).

        Public because locality must be decided on the fact of the real
        send, not on a re-derivation of it. `os.environ.get("OLLAMA_HOST")`
        read at a call site answers a DIFFERENT question: it ignores an
        explicit `host=` argument, so a client constructed against a remote
        host while `OLLAMA_HOST` happens to be loopback would be judged
        local and handed the confidential local exemption for a send that
        leaves the machine. Reading the resolved value closes that gap by
        construction.

        Never raises: it returns a string stored at construction time, with
        no I/O, no DNS, and no parsing of its own."""
        return self._host

    @property
    def locality(self) -> BackendHostLocality:
        """This client's own locality verdict, from the ONE shared authority
        (`classify_backend_host`) applied to `resolved_host` (issue #240).

        Never raises and never touches the network: `classify_backend_host`
        is a pure literal-form check that degrades an unparseable value to
        non-local rather than raising. Both consumers depend on that --
        the embedding-host advisory (#199/#353) must not be able to crash a
        command it only annotates, and the confidential local exemption must
        fail CLOSED on any host it cannot prove is loopback.

        Recomputed on each access rather than cached: the classification is
        a handful of string operations over an already-resolved host, and a
        cached field would be one more piece of state to keep honest for no
        measurable gain."""
        return classify_backend_host(self._host)

    def chat(self, messages: Sequence[Message]) -> str:
        """POST `messages` to `{host}/api/chat` and return `message.content`
        (D5, D6). Raises `OllamaGenerationCapped` if the response reports
        `done_reason == "length"` (issue #422): the model hit the
        configured `max_generation_tokens` ceiling and the reply is
        truncated."""
        url = f"{self._host}/api/chat"
        request_body: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": False,
            "think": False,
        }
        if self._max_generation_tokens is not None:
            request_body["options"] = {"num_predict": self._max_generation_tokens}
        payload = json.dumps(request_body).encode("utf-8")
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

        # `done_reason` is read AFTER the malformed-response guard above, on
        # the already-validated `data` dict, so a response missing it
        # entirely never raises here -- `.get` returns `None`, which simply
        # does not equal `"length"` (fail-open on the signal; the bound
        # itself, not this check, is what protects the caller).
        if isinstance(data, dict) and data.get("done_reason") == "length":
            raise OllamaGenerationCapped(
                "Ollama stopped generation at the configured "
                f"max_generation_tokens ceiling ({self._max_generation_tokens}) "
                "before the reply finished; the response is truncated and unusable."
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
        both the connect-phase and read-phase except branches (D4).

        Names the host through `locality.display_host`, NEVER the raw
        `self._host` (issue #355). Every CLI handler echoes this exception
        verbatim to stderr, so the message is displayed output and is bound
        by the same rule every other displayed host obeys: `display_host` is
        the ONE value a caller may print, userinfo-redacted on every path
        including the unparseable one (see `BackendHostLocality` and
        `classify_backend_host`). Interpolating `self._host` bypassed that
        authority, so a user who had exported
        `OLLAMA_HOST=http://user:s3cret@host` -- for some other tool, since
        openkos merely INHERITS the variable -- got the password printed by
        the first connection failure. That is the same class of leak #183-PR3
        was withdrawn for and #199/#353 hardened the advisory path against;
        this closes the failure path, which is the one a misconfigured user
        reaches first.

        `locality` never raises and never touches the network, so building
        this error cannot itself fail."""
        return OllamaUnavailable(
            f"Ollama not reachable at {self.locality.display_host}: {exc}"
        )


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
