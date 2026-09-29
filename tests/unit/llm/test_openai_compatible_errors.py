"""Unit tests for `openkos.llm.openai_compatible`'s error hierarchy and
`OpenAICompatibleClient`'s construction/`resolved_base_url` (issue #1057
Phase 4, tasks 4.1-4.6).

Every test in this module (and its siblings `test_openai_compatible_chat.py`,
`test_openai_compatible_ladder.py`, `test_openai_compatible_thread_safety.py`)
injects a fake `urlopen` callable -- no live OpenAI-compatible server is
ever contacted, mirroring `test_ollama.py`'s own discipline.
"""

import pytest

from openkos.llm.base import (
    BackendEmbeddingDimensionMismatch,
    BackendError,
    BackendGenerationCapped,
    BackendModelNotFound,
    BackendUnavailable,
)
from openkos.llm.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleEmbeddingDimensionMismatch,
    OpenAICompatibleError,
    OpenAICompatibleGenerationCapped,
    OpenAICompatibleModelNotFound,
    OpenAICompatibleUnavailable,
)


def test_error_hierarchy() -> None:
    """`OpenAICompatibleError` subclasses `BackendError`;
    `OpenAICompatibleUnavailable` subclasses BOTH `OpenAICompatibleError`
    and `BackendUnavailable`; `OpenAICompatibleModelNotFound`/
    `GenerationCapped`/`EmbeddingDimensionMismatch` each subclass
    `OpenAICompatibleError` (openai-compatible-client spec: "Error
    Hierarchy Mirrors Ollama's Under The Neutral Bases").

    **RED today**: `ModuleNotFoundError` -- the module doesn't exist."""
    assert issubclass(OpenAICompatibleError, BackendError)
    assert issubclass(OpenAICompatibleUnavailable, OpenAICompatibleError)
    assert issubclass(OpenAICompatibleUnavailable, BackendUnavailable)
    assert issubclass(OpenAICompatibleModelNotFound, OpenAICompatibleError)
    assert issubclass(OpenAICompatibleModelNotFound, BackendModelNotFound)
    assert issubclass(OpenAICompatibleGenerationCapped, OpenAICompatibleError)
    assert issubclass(OpenAICompatibleGenerationCapped, BackendGenerationCapped)
    assert issubclass(OpenAICompatibleEmbeddingDimensionMismatch, OpenAICompatibleError)
    assert issubclass(
        OpenAICompatibleEmbeddingDimensionMismatch, BackendEmbeddingDimensionMismatch
    )


def test_class_constructs_with_required_args() -> None:
    """`OpenAICompatibleClient(model=..., base_url=...)` constructs with
    every other keyword defaulted, mirroring `OllamaClient`'s shape.

    **RED today**: `AttributeError`/`ModuleNotFoundError` -- class doesn't
    exist yet."""
    client = OpenAICompatibleClient(model="m", base_url="http://127.0.0.1:8080")

    assert client.resolved_base_url == "http://127.0.0.1:8080"


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
        ("http://127.0.0.1:8080/v1", "http://127.0.0.1:8080"),
        ("http://127.0.0.1:8080/v1/", "http://127.0.0.1:8080"),
        ("http://host/proxy/v1", "http://host/proxy"),
    ],
)
def test_resolved_base_url_strips_trailing_slash_and_one_v1(
    base_url: str, expected: str
) -> None:
    """Exactly one trailing `/` and exactly one trailing `/v1` are
    stripped; any other path prefix (a reverse-proxy mount) is kept.

    **RED today**: property not implemented."""
    client = OpenAICompatibleClient(model="m", base_url=base_url)

    assert client.resolved_base_url == expected
