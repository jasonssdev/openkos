"""The `LLMBackend` seam: a chat-completion Protocol and its message shape.

This module is a leaf: stdlib `typing` only, no import of `openkos.config`
or any other `openkos` module. Any concrete backend (e.g. `ollama.OllamaClient`)
implements `LLMBackend` structurally -- no explicit inheritance required.
"""

from collections.abc import Sequence
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
