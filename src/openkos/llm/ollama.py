"""`OllamaClient`: a concrete `LLMBackend` over a local Ollama server.

Leaf module: stdlib `urllib.request`/`json`/`os` only, no `openkos.config`
import (mirrors `fsio`) -- the caller resolves the model tag and passes it
in as an argument.

`classify_backend_host` (and its six private helpers, e.g.
`_plausible_bracketless_ipv6`), `measured_counters` (kept here as the
private alias `_measured_counters`), and `is_timeout_failure` moved to
`llm/base.py` (issue #1057 Phase 1, Decision 1): they are pure and
backend-agnostic, and the OpenAI-compatible client needs the SAME locality
classifier and timeout predicate this module used to define locally. This
module re-exports all three (plus the helpers) so every existing `from
openkos.llm.ollama import classify_backend_host` call site keeps working
unchanged; only the DEFINITION moved. `is_embedding_model` and the rest of
the Ollama-specific model/family classification logic stay here -- they are
about how to derive a value from Ollama's own wire shapes, not a shared,
backend-agnostic value or check.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from openkos.llm.base import (
    _HEX_DIGITS as _HEX_DIGITS,
)
from openkos.llm.base import (
    _LOCAL_HOST_LITERALS as _LOCAL_HOST_LITERALS,
)
from openkos.llm.base import (
    _UNPARSEABLE_DISPLAY as _UNPARSEABLE_DISPLAY,
)
from openkos.llm.base import (
    EMBED_DIM,
    BackendEmbeddingDimensionMismatch,
    BackendError,
    BackendGenerationCapped,
    BackendModelNotFound,
    BackendUnavailable,
    Message,
    build_backend_opener,
)
from openkos.llm.base import (
    BackendHostLocality as BackendHostLocality,
)
from openkos.llm.base import (
    InstalledModel as InstalledModel,
)
from openkos.llm.base import (
    _is_clean_hostport as _is_clean_hostport,
)
from openkos.llm.base import (
    _is_loopback_ipv4_literal as _is_loopback_ipv4_literal,
)
from openkos.llm.base import (
    _plausible_bracketless_ipv6 as _plausible_bracketless_ipv6,
)
from openkos.llm.base import (
    classify_backend_host as classify_backend_host,
)
from openkos.llm.base import (
    is_timeout_failure as is_timeout_failure,
)
from openkos.llm.base import (
    measured_counters as measured_counters,
)
from openkos.llm.base import (
    model_tag_matches as model_tag_matches,
)

_measured_counters = measured_counters
"""Private alias for `base.measured_counters` (issue #1057 Phase 1, Decision
1): every existing internal call site in this module keeps working
unchanged, and `test_ollama_private_alias_is_the_public_function` pins that
this is the SAME object, never a re-implementation that could drift."""

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


class OllamaError(BackendError):
    """Base error for any Ollama chat failure (D4); also raised directly for
    non-404 HTTP errors and malformed/unexpected response bodies.

    Subclasses the backend-agnostic `BackendError` (issue #995, PR 6) so a
    caller scoped to `openkos.llm.base` -- never importing this concrete
    module -- can still catch the generic Ollama-reachable-but-erroring
    family by its shared base; every existing `except OllamaError` handler
    is unaffected, since `OllamaError` itself is unchanged in every other
    respect."""


class OllamaUnavailable(OllamaError, BackendUnavailable):
    """Raised on any transport failure: connection refused or timeout while
    connecting, or a reset/timeout/incomplete read while streaming the
    response body after a successful connect (D4).

    Also subclasses `BackendUnavailable` (issue #995, PR 6), alongside its
    existing `OllamaError` base, so `application/doctor.py`'s
    Ollama-reachable check can distinguish "nothing is listening" from a
    generic `OllamaError` without importing `openkos.llm.ollama` -- every
    existing `except OllamaError`/`except OllamaUnavailable` handler still
    catches it unchanged."""


class OllamaModelNotFound(OllamaError, BackendModelNotFound):
    """Raised on a 404 response whose body reports the model tag as not found (D4).

    Also subclasses `BackendModelNotFound` (issue #1057 Phase 2a, Decision
    3), alongside its existing `OllamaError` base, exactly as
    `OllamaUnavailable` subclasses `BackendUnavailable`: every existing
    `except OllamaError`/`except OllamaModelNotFound` handler still catches
    it unchanged."""


class OllamaGenerationCapped(OllamaError, BackendGenerationCapped):
    """Raised when `chat()`'s response reports `done_reason == "length"`
    (issue #422): generation stopped for length before it finished, so the
    reply was cut off mid-generation and is unusable.

    The cause is NOT always this client's `max_generation_tokens` (#440).
    Ollama reports `"length"` whenever generation stops for length reasons,
    which includes the model's own context window filling, so an
    unconfigured client can legitimately reach this branch. The raised
    message therefore branches: it names the ceiling only when one was
    actually configured, and otherwise says the backend's own limit cut the
    reply off.

    A truncated reply is not a partial success -- `llm.parsing.
    extract_json_items` returns `[]` on a mid-JSON truncation, so there is
    nothing to salvage. Subclasses `OllamaError` so it propagates through
    every existing `except OllamaError` handler unmodified (D4), landing in
    exactly the same handling a hung call gets today: loud, per-source
    failure, never a silent empty result. Also subclasses
    `BackendGenerationCapped` (issue #1057 Phase 2a, Decision 3), alongside
    its existing `OllamaError` base."""


class OllamaEmbeddingDimensionMismatch(OllamaError, BackendEmbeddingDimensionMismatch):
    """Raised when an `/api/embed` response row has a length other than
    `EMBED_DIM` (D7): a PERMANENT, non-healing misconfiguration -- the
    configured embedding model itself does not emit `EMBED_DIM`-dimensional
    vectors -- distinct from the generic transient `OllamaError` (malformed
    JSON, missing vector key, non-numeric row entries). Subclasses
    `OllamaError` so an unmodified bare `except OllamaError` still catches
    it, but MUST be checked ahead of that bare clause at any call site that
    needs to treat it as fatal rather than transient (mirrors the existing
    `OllamaModelNotFound` ordering discipline). Never retried by `embed()`'s
    retry-with-backoff loop (D8): a wrong dimension cannot heal by retry.
    Also subclasses `BackendEmbeddingDimensionMismatch` (issue #1057 Phase
    2a, Decision 3), alongside its existing `OllamaError` base."""


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


class OllamaClient:
    """A chat-completion client for a locally running Ollama server (D1-D6)."""

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_generation_tokens: int | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        context_window: int | None = None,
        urlopen: Callable[..., Any] | None = None,
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

        `temperature`/`seed` are CHAT-only sampling pins forwarded as
        `options.temperature`/`options.seed` under the same contract:
        `None` (both defaults) contributes nothing to `options`, and
        `temperature=0.0` is a real value, not an omission -- greedy
        decoding is precisely the deterministic setting a caller pins.
        Without them the send inherits whatever the model's Modelfile
        ships (qwen3:8b: temperature 0.6, top_p 0.95), which is measured
        run-to-run variance on identical extraction input (#454).

        `context_window` (issue #691) is the CHAT-only context window
        forwarded as `options.num_ctx`, under the same contract: `None` (the
        default) contributes nothing. Left unpinned, the model reserves
        whatever its Modelfile ships -- 32768 tokens and a ~10 GB footprint
        on `qwen3:8b`, which is the whole of a 16 GB machine and makes a
        second slot impossible. `num_ctx` bounds prompt AND completion
        together, so it is NOT interchangeable with `num_predict` above:
        `num_predict` caps how much may be generated, `num_ctx` caps how much
        may be held at once. Setting it too LOW is worse than leaving it
        unset -- Ollama silently drops the head of the prompt rather than
        raising -- which is why the workspace-facing value is floor-checked
        in `config.read_config` and never validated here: this seam forwards
        what it is given, exactly like the three options above it.

        `embed_retry_attempts`/`embed_retry_backoff_base`/`sleep` configure
        ONLY `embed()`'s retry-with-backoff loop (D1/D3) -- `chat()` and
        `list_models()` are untouched, never retried. `sleep` defaults to
        the real `time.sleep`; tests inject a spy to prove no real sleep
        occurs and to observe the backoff schedule."""
        self._model = model
        self._timeout = timeout
        self._max_generation_tokens = max_generation_tokens
        self._temperature = temperature
        self._seed = seed
        self._context_window = context_window
        self._embed_retry_attempts = embed_retry_attempts
        self._embed_retry_backoff_base = embed_retry_backoff_base
        self._sleep = sleep
        resolved_host = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        self._host = _normalize_host(resolved_host).rstrip("/")
        # Default transport is chosen by locality so a loopback host never
        # consults environment proxy settings (issue #1127); an injected
        # `urlopen` (tests) is used as given.
        self._urlopen = urlopen or build_backend_opener(self.locality)

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

    @property
    def context_window(self) -> int | None:
        """The `num_ctx` this client will ACTUALLY send with every chat
        call, or `None` when the workspace left the window unpinned (#866).

        Public for the same read-the-real-send reason as `resolved_host`:
        the whole-source prompt bound in `extraction.concept` plans its
        excerpt against the window the backend will truncate to, and
        re-deriving that from config at the call site would answer a
        different question the moment a client is constructed with an
        explicit override. Returns a value stored at construction time --
        no I/O, never raises."""
        return self._context_window

    @property
    def max_generation_tokens(self) -> int | None:
        """The `num_predict` this client will ACTUALLY send with every chat
        call, or `None` when the workspace left the ceiling unpinned (#896).

        The sibling of `context_window` above, and it was missing for as long
        as that one existed. `prompt_budget.reply_reserve` reads this
        attribute to decide how much of the window to hold back for the
        reply; with nothing to read it returned its own 8192 default, which
        happens to EQUAL `config.DEFAULT_MAX_GENERATION_TOKENS`. So the
        shipped configuration computed the right budget for the wrong reason
        and every other configuration computed zero -- `budget_chars` floors
        there, `fair_shares` then allows every block nothing, and `query`
        answered NO_MATCH on a fully populated bundle.

        `num_ctx` bounds prompt and completion together, which is why a seam
        that plans a prompt needs BOTH numbers and why neither can be
        re-derived from config at the call site: a client constructed with an
        explicit override would answer a different question. Returns a value
        stored at construction time -- no I/O, never raises."""
        return self._max_generation_tokens

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
        options: dict[str, Any] = {}
        if self._max_generation_tokens is not None:
            options["num_predict"] = self._max_generation_tokens
        if self._temperature is not None:
            options["temperature"] = self._temperature
        if self._seed is not None:
            options["seed"] = self._seed
        if self._context_window is not None:
            options["num_ctx"] = self._context_window
        if options:
            request_body["options"] = options
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
            # The message branches on whether a ceiling was actually
            # configured (#440). `done_reason == "length"` is reachable
            # WITHOUT `num_predict` binding -- Ollama reports it whenever
            # generation stops for length reasons, including the model's own
            # context window filling -- so an unconfigured client blaming
            # "the configured ceiling (None)" would contradict itself and
            # send the operator looking for a setting they never set.
            # The trustworthiness rule lives in `_measured_counters` -- one
            # spelling for both branches below, returning the narrowed pair
            # or nothing.
            counters = _measured_counters(
                data.get("prompt_eval_count"), data.get("eval_count")
            )
            if self._max_generation_tokens is None:
                # The window can bind here too, and #440's rule -- never name
                # a cause that was not the cause -- does not stop applying
                # just because no ceiling is set. With counters in hand this
                # branch can say which it was rather than always reaching for
                # "the backend's own limit".
                if counters is not None and self._context_window is not None:
                    prompt_tokens, generated = counters
                    if prompt_tokens + generated >= self._context_window:
                        raise OllamaGenerationCapped(
                            "Ollama stopped generation because the "
                            f"context_window ({self._context_window}) "
                            f"filled: the prompt took {prompt_tokens} "
                            "tokens, leaving "
                            f"{max(self._context_window - prompt_tokens, 0)} "
                            f"for the reply, and generation stopped at "
                            f"{generated}. No max_generation_tokens ceiling "
                            "is set on this client; raise context_window "
                            "(or shorten the prompt). The response is "
                            "truncated and unusable."
                        )
                raise OllamaGenerationCapped(
                    "Ollama stopped generation for length before the reply "
                    "finished, with no max_generation_tokens ceiling set on "
                    "this client -- the backend's own limit cut it off; the "
                    "response is truncated and unusable."
                )
            # WHICH bound actually bound (#829). `num_ctx` covers prompt AND
            # completion together, so a large prompt can leave less
            # generation room than `num_predict` allows -- measured on the
            # shipped defaults, a 5398-token prompt leaves 12288 - 5398 =
            # 6890 against a ceiling of 8192. Blaming the ceiling there is
            # not merely imprecise: it sends the operator to raise
            # `max_generation_tokens`, which changes nothing, when `num_ctx`
            # is what has to move.
            #
            # The response carries both counters, so this is read rather
            # than inferred. #440 established that this exception must not
            # invent a cause; this is the same rule applied to the branch
            # where a ceiling IS set.
            if counters is not None:
                prompt_tokens, generated = counters
                if generated >= self._max_generation_tokens:
                    raise OllamaGenerationCapped(
                        "Ollama stopped generation at the configured "
                        f"max_generation_tokens ceiling "
                        f"({self._max_generation_tokens}) before the reply "
                        "finished; the response is truncated and unusable."
                    )
                if (
                    self._context_window is not None
                    and prompt_tokens + generated >= self._context_window
                ):
                    raise OllamaGenerationCapped(
                        "Ollama stopped generation because the context_window "
                        f"({self._context_window}) filled, NOT the "
                        f"max_generation_tokens ceiling "
                        f"({self._max_generation_tokens}), which was never "
                        f"reached: the prompt took {prompt_tokens} tokens, "
                        f"leaving "
                        f"{max(self._context_window - prompt_tokens, 0)} for "
                        f"the reply, and generation stopped at {generated}. "
                        "Raise context_window (or shorten the prompt); "
                        "raising max_generation_tokens will not help. The "
                        "response is truncated and unusable."
                    )
                # Counters read, and NEITHER bound explains the stop. Naming
                # one anyway is the defect this branch exists to avoid --
                # `done_reason == "length"` is reachable on the model's own
                # limits, exactly as the no-ceiling branch above records.
                # `context_window` is optional, so it is named only when
                # one is set. Interpolating it unconditionally would render
                # "a context_window of None" -- the same self-contradiction
                # #440 removed from the ceiling half of this message.
                window_clause = (
                    f" and a context_window of {self._context_window}"
                    if self._context_window is not None
                    else " and no context_window set on this client"
                )
                raise OllamaGenerationCapped(
                    "Ollama stopped generation for length before the reply "
                    f"finished, and NEITHER configured bound was reached: "
                    f"the prompt took {prompt_tokens} tokens and generation "
                    f"stopped at {generated}, against a "
                    f"max_generation_tokens ceiling of "
                    f"{self._max_generation_tokens}{window_clause}. The "
                    "backend's own limit cut it off; the response is "
                    "truncated and unusable."
                )
            # No counters to read. Fail-open on the SIGNAL, exactly as the
            # `done_reason` guard above does: the ceiling is configured and
            # forwarded as `num_predict`, so naming it remains the best
            # available account. Every real Ollama reply carries both
            # counters, so this is the shape a stub or a future backend
            # produces rather than the common path.
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
                    raw_digest = entry.get("digest")
                    digest = (
                        raw_digest
                        if isinstance(raw_digest, str) and raw_digest
                        else None
                    )
                    models.append(
                        InstalledModel(
                            tag=tag,
                            family=family if isinstance(family, str) else None,
                            digest=digest,
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
