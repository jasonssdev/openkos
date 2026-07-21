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
    """A chat-completion backend: send `messages`, get assistant text back."""

    def chat(self, messages: Sequence[Message]) -> str:
        """Send `messages` to the backend and return the assistant's reply text."""
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
