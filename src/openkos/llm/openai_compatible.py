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
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from openkos.llm.base import (
    EMBED_DIM,
    BackendEmbeddingDimensionMismatch,
    BackendError,
    BackendGenerationCapped,
    BackendHostLocality,
    BackendModelNotFound,
    BackendUnavailable,
    InstalledModel,
    Message,
    classify_backend_host,
    measured_counters,
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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuses to follow ANY HTTP redirect, for every status and method
    (security fix, issue #1057 Phase 4).

    `HTTPRedirectHandler.redirect_request`'s default implementation
    copies every non-content-* header -- including `Authorization` --
    onto the redirected request, and for 301/302/303 silently turns a
    POST into a GET. Following a redirect would therefore forward this
    client's bearer key to whatever host a 3xx `Location` header names.
    The `openai-compatible-client` spec requires the key never be
    redirected to another host.

    Returning `None` here does NOT hand back a normal response to
    inspect: `urllib.request.OpenerDirector._call_chain` treats a `None`
    result as "no handler answered" and falls through to
    `http_error_default`, which RAISES `urllib.error.HTTPError` carrying
    the original 3xx status and headers. Every refused redirect therefore
    surfaces through `chat()`'s existing `except urllib.error.HTTPError`
    branch, mapped by `_map_redirect` below -- never silently followed,
    and never returned as if it were a normal reply."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Never redirect -- see the class docstring."""
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)
"""Built once, at import time: `build_opener` detects that `_NoRedirectHandler`
subclasses the default `HTTPRedirectHandler` and therefore installs it
INSTEAD of (not alongside) the normal, redirect-following default --
`urllib.request.build_opener`'s own documented behavior. Every other
default handler (proxy, HTTP, HTTPS, ...) is unaffected.

`OpenAICompatibleClient.__init__`'s `urlopen` default is this opener's
`.open` method, not `urllib.request.urlopen` -- so a caller who never
overrides `urlopen` gets the redirect refusal automatically, and the
injectable `urlopen` parameter still lets a test (or `embed`/
`list_models`, once they land) inject a fake transport exactly as
before."""


def _redact_location_query(location: str) -> str:
    """Strip the query string and fragment from a `Location` header value
    before it is ever interpolated into a message this client produces
    (security fix, issue #1057 Phase 4): a server could otherwise smuggle
    a token or other secret into the query string of its own redirect
    target, and this client would echo it straight back into its own
    diagnostic text."""
    without_fragment = location.split("#", 1)[0]
    return without_fragment.split("?", 1)[0]


def _order_embedding_rows(rows: list[Any]) -> list[Any]:
    """Sort `/v1/embeddings`' `data` rows by integer `index` when EVERY
    row carries one; otherwise keep response order (Decision 2:
    "sorted by integer index when every entry carries one, else response
    order"). Returns each row's `embedding` field, not the row dict
    itself -- a row missing that key, or one that is not a dict at all,
    raises `KeyError`/`TypeError`, both caught and rewrapped by
    `_embed_once`'s caller."""
    every_row_has_index = all(
        isinstance(row, dict)
        and isinstance(row.get("index"), int)
        and not isinstance(row.get("index"), bool)
        for row in rows
    )
    ordered = (
        sorted(rows, key=lambda row: row["index"]) if every_row_has_index else rows
    )
    return [row["embedding"] for row in ordered]


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
        urlopen: Callable[..., Any] = _NO_REDIRECT_OPENER.open,
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
        contract once `embed()` lands (Phase 6).

        `urlopen` defaults to `_NO_REDIRECT_OPENER.open`, NOT
        `urllib.request.urlopen` (security fix, issue #1057 Phase 4): see
        `_NoRedirectHandler`'s docstring for why the plain default would
        forward the `Authorization` header to whatever host a 3xx
        `Location` names."""
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
    def context_window(self) -> int | None:
        """The configured context window, read-only and advisory-only
        (task 5.13): usable for OpenKOS's own prompt-budget planning
        exactly as `OllamaClient.context_window` is used today, but NEVER
        threaded into any request body -- `chat()` never reads
        `self._context_window` when building a request, only
        `_generation_capped` reads it, purely to build a message (spec:
        "context_window Is Advisory-Only And Never Sent")."""
        return self._context_window

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
        # `max_tokens`/`temperature`/`seed` are TOP-LEVEL fields (unlike
        # Ollama's nested `options`, Decision 2) and each follows the same
        # `is not None` -> omit rule: `temperature=0.0`/`seed=0` are real
        # values, not absent ones, so a falsy check would drop them.
        # `context_window` is deliberately NOT read here at all -- it is
        # advisory-only and MUST NEVER be sent (spec: "context_window Is
        # Advisory-Only And Never Sent").
        if self._max_generation_tokens is not None:
            request_body["max_tokens"] = self._max_generation_tokens
        if self._temperature is not None:
            request_body["temperature"] = self._temperature
        if self._seed is not None:
            request_body["seed"] = self._seed
        payload = json.dumps(request_body).encode("utf-8")
        request = self._build_request(url, payload, method="POST")
        try:
            response = self._urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                # A refused redirect (security fix, issue #1057 Phase 4):
                # `_NoRedirectHandler` never returns a `Request`, so ANY
                # 3xx surfaces here rather than being silently followed.
                raise self._map_redirect(exc) from exc
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
            choice = data["choices"][0]
            content = choice["message"]["content"]
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

        # `finish_reason` is read AFTER the malformed-response guard above,
        # on the already-validated `choice` dict, so a response missing it
        # entirely never raises here -- `.get` returns `None`, which simply
        # does not equal `"length"` (fail-open on the signal; mirrors
        # `OllamaClient.chat`'s `done_reason` handling).
        finish_reason = (
            choice.get("finish_reason") if isinstance(choice, dict) else None
        )
        if finish_reason == "length":
            usage = data.get("usage") if isinstance(data, dict) else None
            counters = (
                measured_counters(
                    usage.get("prompt_tokens"), usage.get("completion_tokens")
                )
                if isinstance(usage, dict)
                else None
            )
            raise self._generation_capped(counters)
        return content

    def _generation_capped(
        self, counters: tuple[int, int] | None
    ) -> OpenAICompatibleGenerationCapped:
        """Build the `OpenAICompatibleGenerationCapped` for a
        `finish_reason == "length"` response (task 5.5), keeping Ollama's
        #440/#829 "which bound actually bound" branching but naming the
        SERVER's own context size -- never `context_window`'s value as if
        it were sent -- when the window is what filled, because
        `context_window` is advisory-only and never sent (Decision 2).

        (a) a configured `max_generation_tokens` ceiling was reached ->
            names the ceiling.
        (b) no ceiling configured, but a `context_window` is, and it is
            what filled (or the ceiling could not be confirmed while a
            window is configured) -> names the server's own context size,
            with the raise-the-server-setting remediation.
        (c) neither bound is configured -> generic "the backend's own
            limit cut the reply" wording.

        Fails open on the SIGNAL exactly as the `finish_reason` check
        above does: missing/non-numeric counters fall through to the
        best-available account from whichever bound IS configured, never
        raising a `TypeError`/`KeyError` of their own."""
        ceiling = self._max_generation_tokens
        window = self._context_window
        if counters is not None:
            prompt_tokens, generated = counters
            if ceiling is not None and generated >= ceiling:
                return OpenAICompatibleGenerationCapped(
                    "OpenAI-compatible server stopped generation at the "
                    f"configured max_generation_tokens ceiling ({ceiling}) "
                    "before the reply finished; the response is truncated "
                    "and unusable."
                )
            if window is not None and prompt_tokens + generated >= window:
                return OpenAICompatibleGenerationCapped(
                    "OpenAI-compatible server stopped generation because "
                    "its own context size filled, not a configured "
                    "max_generation_tokens ceiling: the prompt took "
                    f"{prompt_tokens} tokens and generation stopped at "
                    f"{generated}. Raise the server's own context size "
                    "(`-c` for llama.cpp, `--max-model-len` for vLLM) and "
                    f"set context_window to match (currently {window}); "
                    "this client never sends context_window to the "
                    "server."
                )
        if ceiling is not None:
            return OpenAICompatibleGenerationCapped(
                "OpenAI-compatible server stopped generation at the "
                f"configured max_generation_tokens ceiling ({ceiling}) "
                "before the reply finished; the response is truncated and "
                "unusable."
            )
        if window is not None:
            return OpenAICompatibleGenerationCapped(
                "OpenAI-compatible server stopped generation for length "
                "before the reply finished, with no max_generation_tokens "
                "ceiling set on this client. Raise the server's own "
                "context size (`-c` for llama.cpp, `--max-model-len` for "
                f"vLLM) and set context_window to match (currently "
                f"{window}); this client never sends context_window to "
                "the server."
            )
        return OpenAICompatibleGenerationCapped(
            "OpenAI-compatible server stopped generation for length "
            "before the reply finished, with no max_generation_tokens "
            "ceiling or context_window configured on this client -- the "
            "backend's own limit cut the reply; the response is truncated "
            "and unusable."
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """POST `texts` to `{resolved_base_url}/v1/embeddings` and return
        one `EMBED_DIM`-float, L2-normalized vector per input, in order
        (Embedder contract; Decision 2's `/v1/embeddings` mapping).

        Short-circuits to `[]` with no HTTP call when `texts` is empty,
        mirroring `OllamaClient.embed`. Wraps `_embed_once` in the same
        retry-with-backoff loop `OllamaClient.embed` uses (task 6.15):
        `OpenAICompatibleModelNotFound` and
        `OpenAICompatibleEmbeddingDimensionMismatch` both raise
        immediately, consuming no retry attempt -- neither a missing
        model nor a wrong-dimension response can heal mid-run. Every
        other `OpenAICompatibleError` (the generic transient class, AND
        `OpenAICompatibleUnavailable`) is retried up to
        `embed_retry_attempts` times total, sleeping
        `embed_retry_backoff_base * 2 ** (attempt - 1)` between attempts
        (exponential)."""
        if not texts:
            return []
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._embed_once(texts)
            except (
                OpenAICompatibleModelNotFound,
                OpenAICompatibleEmbeddingDimensionMismatch,
            ):
                raise
            except OpenAICompatibleError:
                if attempt >= self._embed_retry_attempts:
                    raise
                backoff = self._embed_retry_backoff_base * 2 ** (attempt - 1)
                self._sleep(backoff)

    def _embed_once(self, texts: Sequence[str]) -> list[list[float]]:
        """One `embed()` attempt: a single POST to
        `{resolved_base_url}/v1/embeddings` and response parse, with no
        retry logic of its own (the retry loop lives in `embed()`).

        Reuses `chat()`'s connect/read transport ladder, `_map_http_error`
        and `_map_redirect`. `encoding_format: "float"` is sent explicitly
        (Decision 2): a server defaulting to base64 would otherwise fail
        row validation. Rows are ordered by `_order_embedding_rows`, then
        each is length-checked against `EMBED_DIM` and L2-normalized by
        `_validate_and_normalize_row` -- any other shape raises
        `OpenAICompatibleError`."""
        url = f"{self.resolved_base_url}/v1/embeddings"
        payload = json.dumps(
            {
                "model": self._model,
                "input": list(texts),
                "encoding_format": "float",
            }
        ).encode("utf-8")
        request = self._build_request(url, payload, method="POST")
        try:
            response = self._urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise self._map_redirect(exc) from exc
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
            raise self._unavailable(exc) from exc

        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise TypeError(f"expected a JSON object, got {type(data)!r}")
            rows = data["data"]
            if not isinstance(rows, list):
                raise TypeError(f"expected data to be a list, got {type(rows)!r}")
            if len(rows) != len(texts):
                raise ValueError(
                    f"OpenAI-compatible server returned {len(rows)} "
                    f"embeddings for {len(texts)} inputs"
                )
            ordered = _order_embedding_rows(rows)
            result = [self._validate_and_normalize_row(row) for row in ordered]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OpenAICompatibleError(
                f"Malformed response from OpenAI-compatible server: {exc}"
            ) from exc
        return result

    def _validate_and_normalize_row(self, row: object) -> list[float]:
        """Validate one embedding row: exactly `EMBED_DIM` numeric
        entries, then L2-normalized (task 6.11).

        The `EMBED_DIM` length check runs FIRST and raises the distinct,
        PERMANENT `OpenAICompatibleEmbeddingDimensionMismatch` -- never
        retried by `embed()`'s loop. A correct-length row with a
        non-numeric entry raises a plain `ValueError`, always caught and
        rewrapped as the generic `OpenAICompatibleError` by `_embed_once`'s
        caller (scope discipline: only the wrong-LENGTH branch is
        permanent). A zero-norm row (division-by-zero hazard) is the same
        generic, RETRYABLE class, not the dimension-mismatch one."""
        if not isinstance(row, list) or len(row) != EMBED_DIM:
            got = len(row) if isinstance(row, list) else type(row).__name__
            raise OpenAICompatibleEmbeddingDimensionMismatch(
                f"OpenAI-compatible server returned an embedding row of "
                f"length {got}, expected exactly {EMBED_DIM} (EMBED_DIM) "
                "-- this is a permanent dimension mismatch caused by the "
                "configured embedding model, not a transient failure; it "
                "will not heal by retrying."
            )
        values: list[float] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"expected each embedding entry to be numeric, got {type(value)!r}"
                )
            values.append(float(value))
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0:
            raise ValueError(
                "OpenAI-compatible server returned an all-zero embedding "
                "row, which cannot be L2-normalized (division by zero)"
            )
        return [value / norm for value in values]

    def list_models(self) -> list[InstalledModel]:
        """GET `{resolved_base_url}/v1/models`; return installed models
        with `tag=id`, `family=None` (Decision 2's "List Installed Models
        Via /v1/models" mapping: this backend's model listing carries no
        family information, unlike Ollama's `/api/tags`). Config-free,
        like `OllamaClient.list_models`."""
        url = f"{self.resolved_base_url}/v1/models"
        request = self._build_request(url, None, method="GET")
        try:
            response = self._urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise self._map_redirect(exc) from exc
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
            raise self._unavailable(exc) from exc

        # Mirror `chat()`'s guard: wrap ALL body parsing in one
        # try/except, so a valid-JSON body whose `data` is null or a
        # non-iterable scalar maps to `OpenAICompatibleError` instead of
        # leaking a bare `TypeError` from the list comprehension.
        try:
            entries = json.loads(body)["data"]
            models = [InstalledModel(tag=entry["id"], family=None) for entry in entries]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OpenAICompatibleError(
                f"Malformed response from OpenAI-compatible server: {exc}"
            ) from exc
        return models

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

    def _map_redirect(self, exc: urllib.error.HTTPError) -> OpenAICompatibleError:
        """Build the error for a refused HTTP redirect (security fix,
        issue #1057 Phase 4): `_NoRedirectHandler` never follows one, so
        this is the only place a 3xx ever surfaces from `chat()`.

        Never reads `self._api_key` (so it cannot leak it, mirroring
        `_map_http_error`'s discipline), and strips the `Location`
        header's query string/fragment via `_redact_location_query`
        before interpolating it -- a server could otherwise smuggle a
        token into its own redirect target and have this client echo it
        straight back into a diagnostic."""
        location = exc.headers.get("Location")
        target = (
            _redact_location_query(location) if location else "<no Location header>"
        )
        return OpenAICompatibleError(
            f"OpenAI-compatible server responded with a redirect "
            f"({exc.code}) to {target}; this client never follows "
            "redirects, because doing so could forward the Authorization "
            "header to a different host. Set base_url directly to the "
            "server's final URL."
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
