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
