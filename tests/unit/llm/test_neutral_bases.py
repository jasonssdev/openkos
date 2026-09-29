"""Unit tests for the three neutral mid-level error bases (issue #1057
Phase 2a, Decision 3): `BackendModelNotFound`, `BackendGenerationCapped`,
`BackendEmbeddingDimensionMismatch`.

These sit between `BackendError` (the root every backend's own hierarchy
already subclasses, issue #995) and each concrete backend's own leaf class
(`OllamaModelNotFound`, the future `OpenAICompatibleModelNotFound`, ...),
mirroring `BackendUnavailable`'s existing shape. A caller scoped to
`openkos.llm.base` -- never importing a concrete backend module -- can
catch "model not found" / "generation capped" / "embedding dimension
mismatch" by name, across every backend, the same way it can already catch
"unavailable" that way.
"""

from openkos.llm.base import (
    BackendEmbeddingDimensionMismatch,
    BackendError,
    BackendGenerationCapped,
    BackendModelNotFound,
)


def test_neutral_bases_subclass_backend_error() -> None:
    """`BackendModelNotFound`, `BackendGenerationCapped` and
    `BackendEmbeddingDimensionMismatch` each subclass `BackendError` --
    the same root `BackendUnavailable` already subclasses (issue #995).

    **RED today**: `ImportError` -- `openkos.llm.base` defines none of the
    three yet."""
    assert issubclass(BackendModelNotFound, BackendError)
    assert issubclass(BackendGenerationCapped, BackendError)
    assert issubclass(BackendEmbeddingDimensionMismatch, BackendError)


def test_neutral_bases_are_distinct_classes() -> None:
    """The three bases are distinct from each other and from `BackendError`
    itself -- a caller catching one must not silently also catch another,
    the same discrimination Ollama's own leaf classes already provide."""
    classes = {
        BackendError,
        BackendModelNotFound,
        BackendGenerationCapped,
        BackendEmbeddingDimensionMismatch,
    }
    assert len(classes) == 4
