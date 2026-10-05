"""The `LLMBackend` seam: a chat-completion Protocol and its message shape.

This module is a leaf: stdlib `typing`/`urllib.error` only, no import of
`openkos.config` or any other `openkos` module. Any concrete backend (e.g.
`ollama.OllamaClient`) implements `LLMBackend` structurally -- no explicit
inheritance required.

`InstalledModel`, `BackendHostLocality`, `model_tag_matches`,
`BackendError`/`BackendUnavailable`, and `BackendDiagnostics` (issue #995,
PR 6) moved here from `openkos/llm/ollama.py` for the same reason
`Message`/`EMBED_DIM` already lived here: `application/doctor.py` needs
them and, like every other `application/*` module (ADR-0018 D1,
`tests/unit/application/test_layering.py::
test_application_modules_bind_no_concrete_llm_backend`), may import only
this leaf module, never a concrete backend. `ollama.py` imports all five
back from here rather than redefining them, so every existing `from
openkos.llm.ollama import InstalledModel` (etc.) call site -- `cli/main.py`,
the test suite -- keeps working unchanged; only the DEFINITION moved.

`classify_backend_host` (and its six private helpers), `measured_counters`
(public name for `ollama._measured_counters`), and `is_timeout_failure`
moved here from `ollama.py` too (issue #1057 Phase 1, Decision 1): they are
pure and backend-agnostic, and the OpenAI-compatible client (Phase 4 on)
needs the SAME locality classifier and timeout predicate `OllamaClient`
uses, rather than a duplicate. `ollama.py` re-exports all three (plus the
six private helpers) so every existing `from openkos.llm.ollama import
classify_backend_host` call site keeps working unchanged; `is_embedding_model`
and the rest of the Ollama-specific model/family classification logic stayed
in `ollama.py` -- that is about how to derive a value from Ollama's own wire
shapes, not a shared, backend-agnostic value or check.
"""

import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict


class Message(TypedDict):
    """One chat turn, forwarded verbatim into the backend's request body."""

    role: str
    """`"system"`, `"user"`, or `"assistant"`."""
    content: str
    """The turn's text."""


class LLMBackend(Protocol):
    """A chat-completion backend: send `messages`, get assistant text back.

    **`chat` must be safe to call from several threads on one instance.**
    Since #744 `extraction.concept._fan_out_windows` calls it concurrently
    against a single shared backend when a workspace opts into
    `concurrent_extraction`, so an implementation that carries per-call state
    on the instance would corrupt replies across callers rather than merely
    run slower.

    The bar is structural and easy to meet: build every per-call value as a
    local and write nothing back onto the instance. `ollama.OllamaClient`
    satisfies it, and `tests/unit/llm/test_ollama.py` pins that with an AST
    guard rather than a comment (#748) -- because breaking the property is a
    one-line edit whose damage is invisible to a serial test suite.
    """

    def chat(self, messages: Sequence[Message]) -> str:
        """Send `messages` to the backend and return the assistant's reply text.

        Must tolerate concurrent calls on one instance -- see the class
        docstring."""
        ...  # pragma: no cover -- Protocol stub body, never executed


EMBED_DIM = 1024
"""Fixed dimension every `Embedder.embed()` row must have (contract constant)."""


class Embedder(Protocol):
    """A text-embedding backend: send `texts`, get one order-preserving
    `EMBED_DIM`-float vector back per input."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one `EMBED_DIM`-float vector per entry in `texts`, in order.

        Empty `texts` returns an empty list.
        """
        ...  # pragma: no cover -- Protocol stub body, never executed


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """One entry from a backend's model listing: its tag plus optional
    model family.

    `family` is `None` when the backend omits family information entirely
    -- always still returned, never dropped. Moved here from `ollama.py`
    (issue #995, PR 6) -- see the module docstring.

    `digest` is the backend's content digest for the model as listed (Ollama:
    64 hex characters, no `sha256:` prefix), or `None` when the backend does
    not report one (an `openai-compatible` backend never does) or reported
    something unusable. It exists only in memory: never written to a file.
    It is the LAST field, with a default, so every two-field construction
    stays valid."""

    tag: str
    family: str | None
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class BackendHostLocality:
    """A backend-introspection verdict: whether a configured backend host
    is literally local, plus the userinfo-REDACTED host string that is the
    ONLY value a caller may print (issue #199).

    Named for the BACKEND, not the embedder (issue #240): the same verdict
    also decides whether a `confidential` concept may be included in an
    `llm.chat` payload, so a name that said "embed" would misdescribe half
    its consumers. Moved here from `ollama.py` (issue #995, PR 6) -- see the
    module docstring; `ollama.classify_backend_host` still owns the
    classification LOGIC (Ollama's own host-string parsing), this module
    owns only the shared result TYPE."""

    is_local: bool
    display_host: str


def model_tag_matches(configured: str, installed: list[str]) -> bool:
    """True if `configured` matches any installed tag.

    A bare name (no `:`) normalizes to `<name>:latest` per Ollama
    convention, applied symmetrically to both `configured` and each
    installed tag; comparison after normalization is case-sensitive.

    A pure, backend-agnostic string comparison -- moved here from
    `ollama.py` (issue #995, PR 6) because `application/doctor.py`'s
    model-installed checks (4, 5, 5b) call it directly and, like every
    other `application/*` module, may import only this leaf module."""
    wanted = configured if ":" in configured else f"{configured}:latest"
    for tag in installed:
        normalized = tag if ":" in tag else f"{tag}:latest"
        if normalized == wanted:
            return True
    return False


class BackendError(Exception):
    """The generic failure family for a capability-scoped backend call --
    e.g. a malformed response, or an unexpected status from a REACHABLE
    backend. A concrete backend's own error hierarchy (e.g.
    `ollama.OllamaError`) subclasses this, so a caller scoped to this leaf
    module -- like `application/doctor.py`, which must never import a
    concrete backend module (ADR-0018 D1) -- can still catch it by name."""


class BackendUnavailable(BackendError):
    """Raised when a capability-scoped backend could not be reached at all
    -- connection refused, DNS failure, timeout before any response --
    distinct from a REACHABLE backend that errors (`BackendError`). A
    concrete backend's own "unreachable" exception (e.g.
    `ollama.OllamaUnavailable`) subclasses both this and its own error
    base, so `application/doctor.py`'s Ollama-reachable check can still
    tell "nothing is listening" (this) apart from "the backend answered
    with an error" (the plain `BackendError` branch) without importing
    `openkos.llm.ollama`."""


class BackendModelNotFound(BackendError):
    """Raised when a capability-scoped backend reports the configured model
    is not installed/pulled -- distinct from a generic `BackendError`
    (issue #1057 Phase 2a, Decision 3). A concrete backend's own
    "model not found" exception (e.g. `ollama.OllamaModelNotFound`)
    subclasses both this and its own error base, so `application/doctor.py`
    and every other caller scoped to this leaf module can tell "the model
    is missing" apart from any other backend failure without importing a
    concrete backend module."""


class BackendGenerationCapped(BackendError):
    """Raised when a capability-scoped backend's reply was cut off before
    it finished generating -- the model's own length ceiling, or a
    configured one, was reached (issue #1057 Phase 2a, Decision 3). A
    concrete backend's own "generation capped" exception (e.g.
    `ollama.OllamaGenerationCapped`) subclasses both this and its own
    error base."""


class BackendEmbeddingDimensionMismatch(BackendError):
    """Raised when a capability-scoped backend's embedding response has a
    length other than the expected `EMBED_DIM` -- a PERMANENT,
    non-healing misconfiguration, distinct from a generic transient
    `BackendError` (issue #1057 Phase 2a, Decision 3). A concrete
    backend's own "wrong dimension" exception (e.g.
    `ollama.OllamaEmbeddingDimensionMismatch`) subclasses both this and its
    own error base."""


class BackendDiagnostics(Protocol):
    """A capability-scoped backend-introspection seam: enumerate installed
    models and report host locality, without sending a chat or embedding
    request.

    Deliberately separate from `LLMBackend` (`chat`) and `Embedder`
    (`embed`) rather than folded into either (issue #995, PR 6):
    `application/doctor.py` needs `list_models()`/`locality` but never
    calls `.chat()` or `.embed()`. Widening `LLMBackend` to also declare
    these was rejected: 15+ existing chat-only test doubles implement
    `LLMBackend`, and would all need stub `list_models`/`locality` methods
    for a capability none of them exercise; it would also leak the
    Ollama-shaped `BackendHostLocality` into the backend-agnostic chat seam
    every other `application/*` module depends on. `application/doctor.py`
    takes this Protocol as an injected parameter and imports only this
    module, never a concrete backend (ADR-0018 D1) -- `ollama.OllamaClient`
    satisfies it structurally, with no additional construction, because it
    already implements both members below."""

    def list_models(self) -> list[InstalledModel]:
        """Return every model the backend currently reports as installed."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    @property
    def locality(self) -> BackendHostLocality:
        """This backend's own locality verdict (issue #240)."""
        ...  # pragma: no cover -- Protocol stub body, never executed


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


def build_backend_opener(
    locality: BackendHostLocality,
    *handlers: urllib.request.BaseHandler | type[urllib.request.BaseHandler],
) -> Callable[..., Any]:
    """The `urlopen` a backend client sends through, chosen by its locality
    (issue #1127). The ONE place that decides it, for every client.

    A host classified local is loopback by literal form, but urllib's default
    `ProxyHandler` reads `http_proxy`/`https_proxy` and its `proxy_bypass`
    does not exempt loopback names unless `no_proxy` lists them -- so a
    machine with a system-wide proxy would route a "local" backend's request
    body and `Authorization` header through that proxy while the confidential
    local exemption believed nothing left the device. A local host therefore
    gets `ProxyHandler({})`: the environment is never consulted. A non-local
    host keeps urllib's default behaviour, environment proxies honoured.

    `handlers` are extra handlers for `build_opener` (the openai-compatible
    client's no-redirect handler); they compose with, not replace, the proxy
    decision."""
    extra: list[Any] = list(handlers)
    if locality.is_local:
        extra.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*extra).open


def measured_counters(
    prompt_tokens: object, generated: object
) -> tuple[int, int] | None:
    """Both token counters, iff BOTH are trustworthy -- else `None`.

    `bool` is a subclass of `int`, so an `isinstance(x, int)` pair alone
    would accept `true`/`false` as counters and build a message out of 1
    and 0. Excluded explicitly: a counter this code cannot trust must fall
    through to the unmeasured account, not produce a confident wrong one.

    ONE spelling, shared by the ceiling and no-ceiling branches of the
    `done_reason == "length"` handling (#849): the same predicate written
    twice is how the two drift, and returning the narrowed pair is what
    lets both branches do arithmetic without `type: ignore`.

    Moved here from `ollama._measured_counters` (issue #1057 Phase 1,
    Decision 1) and renamed public: it is a pure, backend-agnostic check,
    and the OpenAI-compatible client's `usage.prompt_tokens`/
    `usage.completion_tokens` need the same trustworthy-pair discipline
    Ollama's `prompt_eval_count`/`eval_count` already gets.
    `ollama._measured_counters` keeps this exact object as a private alias
    so every existing internal call site in `ollama.py` keeps working
    unchanged."""
    if (
        isinstance(prompt_tokens, int)
        and not isinstance(prompt_tokens, bool)
        and isinstance(generated, int)
        and not isinstance(generated, bool)
    ):
        return prompt_tokens, generated
    return None


def is_timeout_failure(exc: BaseException) -> bool:
    """Whether `exc` is a request that ran out of TIME, rather than one that
    failed for any other reason (issue #746).

    The distinction matters because #744's `concurrent_extraction` inflates
    per-call wall time when the server is not configured to run requests in
    parallel: each request's own timeout keeps running while it waits its
    turn. A caller can only offer that explanation for a failure that is
    actually a deadline. Attaching it to a refused connection would send an
    operator after a concurrency setting when their server is simply not
    running, which is worse than saying nothing.

    Widened from `OllamaUnavailable` to the backend-agnostic
    `BackendUnavailable` (issue #1057 Phase 1, Decision 3): any backend's
    own "unreachable" exception subclasses `BackendUnavailable` (mirroring
    `OllamaUnavailable`), so this predicate keeps working for a second
    backend without a duplicate. Both shapes `urlopen(..., timeout=...)`
    produces are accepted: a bare `TimeoutError` (the read phase, and
    `socket.timeout` since Python 3.10) and a `URLError` wrapping one (the
    connect phase).

    Never raises, including on an exception with no cause at all: it runs on
    a degrade path that is already handling a failure, and a predicate that
    could fail there would replace a handled error with an unhandled one."""
    if not isinstance(exc, BackendUnavailable):
        return False
    cause = exc.__cause__
    if isinstance(cause, TimeoutError):
        return True
    return isinstance(cause, urllib.error.URLError) and isinstance(
        cause.reason, TimeoutError
    )
