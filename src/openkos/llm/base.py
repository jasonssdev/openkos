"""The `LLMBackend` seam: a chat-completion Protocol and its message shape.

This module is a leaf: stdlib `typing` only, no import of `openkos.config`
or any other `openkos` module. Any concrete backend (e.g. `ollama.OllamaClient`)
implements `LLMBackend` structurally -- no explicit inheritance required.

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
`classify_backend_host`, `is_embedding_model`, and the rest of the
Ollama-specific host/family classification logic stayed in `ollama.py`:
they are about how to derive a value from Ollama's own wire shapes, not
the shared TYPE those values are handed around as.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict


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
    (issue #995, PR 6) -- see the module docstring."""

    tag: str
    family: str | None


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
