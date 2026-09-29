"""`OpenAICompatibleClient`: a concrete `LLMBackend` over any server that
speaks the OpenAI-compatible HTTP API (llama.cpp's `llama-server`, LM
Studio, vLLM, LocalAI).

Leaf module (issue #1057 Phase 4, Decision 1): stdlib
`urllib.request`/`json`/`http.client` and `openkos.llm.base` only. No
`openkos.config` import, and no environment read of any kind -- the caller
(`application/backends.py`, Phase 9) resolves the model tag, base URL(s)
and optional API key and passes them in as constructor arguments. This
mirrors `openkos.llm.ollama`'s own leaf discipline
(`test_llm_modules_do_not_import_config`), which is glob-based over
`llm/*.py` and therefore already covers this module without any edit
(`tests/unit/llm/test_layering.py`).

`OpenAICompatibleClient` is a SIBLING of `OllamaClient`, not a subclass and
not built on a shared transport base class (Decision 1): the two servers'
request/response shapes, error mapping and retry semantics differ in
detail, and the `LLMBackend`/`Embedder`/`BackendDiagnostics` Protocols in
`llm/base.py` are the shared contract, not a shared implementation. Only
the backend-agnostic pure helpers (`classify_backend_host`,
`measured_counters`, `is_timeout_failure`) are reused, imported from
`llm/base.py` exactly as `ollama.py` does.
"""

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from openkos.llm.base import (
    BackendEmbeddingDimensionMismatch,
    BackendError,
    BackendGenerationCapped,
    BackendHostLocality,
    BackendModelNotFound,
    BackendUnavailable,
    Message,
    classify_backend_host,
)

DEFAULT_TIMEOUT = 600.0
"""Same generous floor as `ollama.DEFAULT_TIMEOUT` (Decision 1: mirror
`OllamaClient`'s shape) -- local inference is slow regardless of backend."""

DEFAULT_EMBED_RETRY_ATTEMPTS = 3
"""Default number of `embed()` attempts (1 initial + 2 retries) before
raising a persistent failure to the caller, mirroring
`ollama.DEFAULT_EMBED_RETRY_ATTEMPTS` (wired here in Phase 4 for
constructor-shape parity; `embed()` itself lands in Phase 6)."""

DEFAULT_EMBED_RETRY_BACKOFF_BASE = 0.5
"""Default exponential-backoff base, in seconds, mirroring
`ollama.DEFAULT_EMBED_RETRY_BACKOFF_BASE` (see note above)."""

_API_KEY_ENV_NAME = "OPENKOS_OPENAI_API_KEY"
"""The environment variable name named in user-facing error text (issue
#1057 backend-selection spec). This module never reads the environment --
`application/backends.py` (Phase 9) does that and passes the resolved
value in as `api_key` -- this is a string literal used ONLY for message
text, so the leaf constraint above still holds."""


class OpenAICompatibleError(BackendError):
    """Base error for any OpenAI-compatible chat/embed/diagnostics failure;
    also raised directly for non-404/400-marker HTTP errors and
    malformed/unexpected response bodies.

    Subclasses the backend-agnostic `BackendError` (issue #1057 Phase 4,
    mirroring `OllamaError`'s relationship to it since #995) so a caller
    scoped to `openkos.llm.base` can catch this backend's generic failures
    by the same shared name it already catches Ollama's with."""


class OpenAICompatibleUnavailable(OpenAICompatibleError, BackendUnavailable):
    """Raised on any transport failure: connection refused or timeout while
    connecting, or a reset/timeout/incomplete read while streaming the
    response body after a successful connect.

    Also subclasses `BackendUnavailable`, alongside its own
    `OpenAICompatibleError` base, exactly as `OllamaUnavailable` does for
    `BackendUnavailable` -- a caller scoped to the neutral bases catches
    this backend's connectivity failures with no import of this module."""


class OpenAICompatibleModelNotFound(OpenAICompatibleError, BackendModelNotFound):
    """Raised when the server reports the configured model is not
    available -- a 404 (or 400 carrying a `model_not_found` marker) whose
    body identifies the model as the cause. Also subclasses
    `BackendModelNotFound`, mirroring `OllamaModelNotFound`."""


class OpenAICompatibleGenerationCapped(OpenAICompatibleError, BackendGenerationCapped):
    """Raised when `chat()`'s response reports
    `choices[0].finish_reason == "length"` (Phase 5): generation stopped
    for length before it finished, and the reply is truncated and unusable.
    Also subclasses `BackendGenerationCapped`, mirroring
    `OllamaGenerationCapped`."""


class OpenAICompatibleEmbeddingDimensionMismatch(
    OpenAICompatibleError, BackendEmbeddingDimensionMismatch
):
    """Raised when an `/v1/embeddings` response row has a length other than
    `EMBED_DIM` (Phase 6): a PERMANENT, non-healing misconfiguration,
    distinct from a generic transient `OpenAICompatibleError`. Also
    subclasses `BackendEmbeddingDimensionMismatch`, mirroring
    `OllamaEmbeddingDimensionMismatch`. Never retried by `embed()`'s
    retry-with-backoff loop."""


class OpenAICompatibleClient:
    """A chat-completion (and, from Phase 6, embedding) client for a local
    server that speaks the OpenAI-compatible HTTP API (Decisions 1-2)."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_generation_tokens: int | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        context_window: int | None = None,
        api_key: str | None = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        embed_retry_attempts: int = DEFAULT_EMBED_RETRY_ATTEMPTS,
        embed_retry_backoff_base: float = DEFAULT_EMBED_RETRY_BACKOFF_BASE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Store every constructor argument; no I/O, no environment read.

        `base_url` is a REQUIRED keyword argument (unlike `OllamaClient`'s
        optional `host`, Decision 1): this client never reads an
        environment variable equivalent to `OLLAMA_HOST`, so a caller that
        omits it would otherwise construct a client with nothing to POST
        to. `application/backends.py` (Phase 9) is the one seam that
        resolves it before construction.

        `max_generation_tokens`/`temperature`/`seed`/`context_window` are
        stored now (constructor-shape parity with `OllamaClient`) but are
        not yet wired into `chat()`'s request body or response handling --
        that lands in Phase 5. `context_window` is advisory-only and MUST
        NEVER be sent in any request (backend-selection spec): unlike
        Ollama's `options.num_ctx`, an OpenAI-compatible server fixes its
        context size at server start and accepts no per-request override.

        `api_key`, when not `None`, is sent as `Authorization: Bearer
        <key>` on every request via `_build_request` and is NEVER
        interpolated into any exception message, log line, or other
        diagnostic text this client produces.

        `urlopen`/`embed_retry_attempts`/`embed_retry_backoff_base`/`sleep`
        mirror `OllamaClient`'s injection points exactly, for the same
        testability reason (no live server needed) and the same retry
        contract once `embed()` lands (Phase 6)."""
        self._model = model
        self._base_url = base_url
        self._timeout = timeout
        self._max_generation_tokens = max_generation_tokens
        self._temperature = temperature
        self._seed = seed
        self._context_window = context_window
        self._api_key = api_key
        self._urlopen = urlopen
        self._embed_retry_attempts = embed_retry_attempts
        self._embed_retry_backoff_base = embed_retry_backoff_base
        self._sleep = sleep

    @property
    def resolved_base_url(self) -> str:
        """The `base_url` this client will ACTUALLY send to: exactly one
        trailing `/` stripped, then exactly one trailing `/v1` stripped, so
        both `http://host:8080` and `http://host:8080/v1` produce the same
        `{root}/v1/chat/completions` request URL. Any other path prefix
        (a reverse-proxy mount, e.g. `http://host/proxy/v1`) is kept as-is
        past that single `/v1` strip -- never stripped further.

        Never raises: a pure string operation over a value stored at
        construction time, mirroring `OllamaClient.resolved_host`'s
        read-the-real-send contract (issue #240) -- `locality` below reads
        THIS property, never `self._base_url` directly, for the same
        reason `OllamaClient.locality` reads `resolved_host`."""
        stripped = self._base_url.rstrip("/")
        if stripped.endswith("/v1"):
            stripped = stripped[: -len("/v1")]
        return stripped

    @property
    def locality(self) -> BackendHostLocality:
        """This client's own locality verdict, from the ONE shared
        authority (`classify_backend_host`) applied to `resolved_base_url`
        (backend-selection spec: "Locality Uses The Shared Classifier").

        Never raises and never touches the network -- see
        `classify_backend_host`'s own contract. `_unavailable` below reads
        `display_host` from this, never the raw configured `base_url`, so
        a transport-failure message can never leak userinfo (issue #355's
        rule, applied to this backend)."""
        return classify_backend_host(self.resolved_base_url)

    def chat(self, messages: Sequence[Message]) -> str:
        """POST `messages` to `{resolved_base_url}/v1/chat/completions` and
        return `choices[0].message.content` (openai-compatible-client spec:
        "Successful Chat Call Returns Assistant Text").

        Builds everything as locals and writes nothing back onto `self` --
        the same thread-safety contract `OllamaClient.chat` upholds for
        #744's concurrent fan-out, pinned here by an AST guard mirroring
        `test_ollama.py`'s (#748)."""
        url = f"{self.resolved_base_url}/v1/chat/completions"
        request_body: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": False,
        }
        payload = json.dumps(request_body).encode("utf-8")
        request = self._build_request(url, payload, method="POST")
        try:
            response = self._urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            raise self._map_http_error(exc) from exc
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
            # A transport failure can strike mid-stream too, not only while
            # connecting -- same rationale as `OllamaClient.chat`'s
            # equivalent read-phase guard.
            raise self._unavailable(exc) from exc

        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
            IndexError,
            ValueError,
        ) as exc:
            raise OpenAICompatibleError(
                f"Malformed response from OpenAI-compatible server: {exc}"
            ) from exc

        if not isinstance(content, str):
            raise OpenAICompatibleError(
                "Expected choices[0].message.content to be a string, got "
                f"{type(content)!r}"
            )
        return content

    def _build_request(
        self, url: str, payload: bytes | None, *, method: str
    ) -> urllib.request.Request:
        """Build one `Request`, shared by `chat`/`embed`/`list_models`
        (task 4.22): sets `Content-Type` when a body is present, and adds
        `Authorization: Bearer <key>` iff `api_key` was configured -- never
        added otherwise, per the spec's "An Optional Bearer Key Is Applied
        Per Request And Never Logged" requirement.

        The URL is always `{trusted base_url}/...` (Decision 1: `base_url`
        is caller/config-resolved, never derived from document content) --
        not an arbitrary user-supplied URL, so the S310 scheme audit does
        not apply, mirroring `OllamaClient`'s own `# noqa: S310` sites."""
        headers: dict[str, str] = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return urllib.request.Request(  # noqa: S310
            url, data=payload, headers=headers, method=method
        )

    def _map_http_error(self, exc: urllib.error.HTTPError) -> OpenAICompatibleError:
        """Classify an `HTTPError` (Decision 2's `_map_http_error` table):
        404, or 400 whose body carries `"model_not_found"`, with a detail
        naming the model as missing -> `OpenAICompatibleModelNotFound`;
        401/403 -> `OpenAICompatibleError` naming `OPENKOS_OPENAI_API_KEY`,
        never `self._api_key`'s value; everything else ->
        `OpenAICompatibleError` with the status and the server's detail.

        An instance method (not a bare module function) so a
        mutation-proof test can plausibly demonstrate what a regression
        that DID interpolate `self._api_key` here would look like (task
        4.20) -- the shipped implementation below never does."""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.IncompleteRead,
        ):
            # A transport failure can strike while reading the *error*
            # response body too -- degrade to an empty detail rather than
            # leaking a raw exception; the HTTP status code alone still
            # classifies the failure into a typed error.
            detail = ""
        detail_lower = detail.lower()
        model_not_found_marker = (
            "not found" in detail_lower or "does not exist" in detail_lower
        )
        if exc.code == 404 and model_not_found_marker:
            return OpenAICompatibleModelNotFound(
                f"Model not found ({exc.code}): {detail}"
            )
        if exc.code == 400 and "model_not_found" in detail_lower:
            return OpenAICompatibleModelNotFound(
                f"Model not found ({exc.code}): {detail}"
            )
        if exc.code in (401, 403):
            return OpenAICompatibleError(
                f"OpenAI-compatible request failed ({exc.code}): "
                "authentication failed; set the "
                f"{_API_KEY_ENV_NAME} environment variable to a valid key. "
                f"{detail}"
            )
        return OpenAICompatibleError(
            f"OpenAI-compatible request failed ({exc.code}): {detail}"
        )

    def _unavailable(self, exc: BaseException) -> OpenAICompatibleUnavailable:
        """Build the `OpenAICompatibleUnavailable` for a transport failure,
        shared by every transport except-branch (mirrors
        `OllamaClient._unavailable`).

        Names the host through `locality.display_host`, NEVER
        `self._base_url` directly (issue #355's rule, applied to this
        backend): `locality` never raises and never touches the network, so
        building this error cannot itself fail."""
        return OpenAICompatibleUnavailable(
            f"OpenAI-compatible server not reachable at "
            f"{self.locality.display_host}: {exc}"
        )
