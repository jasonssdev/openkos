"""Direct unit tests for `openkos.application.query`: store composition and
the `run_query`/`answer()` call (D1, D2, ADR-0018).

`answer()` is patched at `openkos.application.query.answer` -- the migrated
patch target after D1's decision -- zero network, zero real Ollama process.
`llm`/`embedder` are structural fakes never actually invoked once `answer`
itself is patched; they exist only to satisfy `run_query`'s Protocol-typed
parameters.
"""

from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

import pytest

from openkos import config
from openkos.application import query as query_service
from openkos.llm.base import EMBED_DIM, Message
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.retrieval.answer import AnswerResult
from openkos.state import fts
from openkos.state.fts import FtsUnavailable
from openkos.state.vectorstore import VectorStoreDB


class _FakeLLM:
    """A structural `LLMBackend`: never called once `answer` is patched."""

    def chat(self, messages: Sequence[Message]) -> str:
        raise AssertionError("llm.chat must not run -- answer() is patched")


class _FakeEmbedder:
    """A structural `Embedder`: never called once `answer` is patched."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("embedder.embed must not run -- answer() is patched")


def _workspace(tmp_path: Path) -> tuple[config.WorkspaceLayout, config.Config]:
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    return layout, config.read_config(tmp_path)


def _fixed_result() -> AnswerResult:
    return AnswerResult(
        answer="the answer",
        citations=[],
        fts_hit_count=0,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
    )


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: object
) -> query_service.QueryOutcome:
    layout, cfg = _workspace(tmp_path)
    kwargs: dict[str, object] = {
        "layout": layout,
        "cfg": cfg,
        "llm": _FakeLLM(),
        "embedder": _FakeEmbedder(),
        "limit": 5,
        "include_deprecated": False,
        "include_confidential": False,
        "local_exemption": False,
    }
    kwargs.update(overrides)
    return query_service.run_query("a question", **kwargs)  # type: ignore[arg-type]


def test_run_query_degrades_on_missing_vector_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh workspace has no `vectors.db` on disk -- `run_query` degrades
    to `vector_store_unavailable=True` and raises nothing. The FTS store is
    forced present (via `_open_fts_or_degrade`) to isolate the signal: only
    the vector-store flag should flip."""
    monkeypatch.setattr(query_service, "answer", lambda *a, **k: _fixed_result())
    monkeypatch.setattr(
        query_service, "_open_fts_or_degrade", lambda path: (nullcontext(None), False)
    )

    outcome = _run(tmp_path, monkeypatch)

    assert outcome.vector_store_unavailable is True
    assert outcome.fts_unavailable is False
    assert outcome.result.answer == "the answer"


def test_run_query_degrades_on_missing_fts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirror of the above for the FTS store: a fresh workspace has no
    `fts.db` on disk -- `run_query` degrades to `fts_unavailable=True`. The
    vector store is forced present to isolate the signal."""
    monkeypatch.setattr(query_service, "answer", lambda *a, **k: _fixed_result())
    monkeypatch.setattr(
        query_service,
        "_open_vector_store_or_degrade",
        lambda path: (nullcontext(None), False),
    )

    outcome = _run(tmp_path, monkeypatch)

    assert outcome.fts_unavailable is True
    assert outcome.vector_store_unavailable is False


def test_run_query_propagates_ollama_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_query` never catches `OllamaUnavailable` -- ordering and mapping
    to exit codes stay the adapter's job (D2)."""

    def _raise(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaUnavailable("down")

    monkeypatch.setattr(query_service, "answer", _raise)

    with pytest.raises(OllamaUnavailable):
        _run(tmp_path, monkeypatch)


def test_run_query_propagates_model_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_query` never catches `OllamaModelNotFound`."""

    def _raise(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaModelNotFound("missing")

    monkeypatch.setattr(query_service, "answer", _raise)

    with pytest.raises(OllamaModelNotFound):
        _run(tmp_path, monkeypatch)


def test_run_query_propagates_embedding_dimension_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_query` never catches `OllamaEmbeddingDimensionMismatch` -- a
    permanent misconfiguration, distinct from a transient `OllamaError`."""

    def _raise(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaEmbeddingDimensionMismatch("mismatch")

    monkeypatch.setattr(query_service, "answer", _raise)

    with pytest.raises(OllamaEmbeddingDimensionMismatch):
        _run(tmp_path, monkeypatch)


def test_run_query_propagates_generic_ollama_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_query` never catches the generic `OllamaError` fallback."""

    def _raise_ollama(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaError("generic backend failure")

    monkeypatch.setattr(query_service, "answer", _raise_ollama)
    with pytest.raises(OllamaError):
        _run(tmp_path, monkeypatch)


def test_run_query_propagates_fts_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_query` never catches `FtsUnavailable` either -- the other half
    of the generic fallback pair."""

    def _raise_fts(*args: object, **kwargs: object) -> AnswerResult:
        raise FtsUnavailable("fts5 not compiled in")

    monkeypatch.setattr(query_service, "answer", _raise_fts)
    with pytest.raises(FtsUnavailable):
        _run(tmp_path, monkeypatch)


def test_embed_dim_constant_is_importable() -> None:
    """Sanity check that the fixture module's `EMBED_DIM` import resolves --
    guards against a silent fixture typo breaking every test above at
    collection time with an unrelated traceback."""
    assert EMBED_DIM > 0


class TestOpenVectorStoreOrDegrade:
    """Direct coverage of `_open_vector_store_or_degrade`'s branches -- the
    success-open path and the caught-exception path, neither of which the
    `run_query` tests above exercise (they force this store present via a
    monkeypatched stand-in, never through a real on-disk open)."""

    def test_opens_an_existing_empty_store_successfully(self, tmp_path: Path) -> None:
        """A path that already exists opens successfully -- a 0-byte file is
        a valid target for `sqlite3.connect`, and `open_vector_store`
        idempotently creates its schema on it."""
        path = tmp_path / "vectors.db"
        path.touch()

        cm, unavailable = query_service._open_vector_store_or_degrade(path)

        assert unavailable is False
        with cm as store:
            assert isinstance(store, VectorStoreDB)

    def test_degrades_on_a_corrupt_existing_store(self, tmp_path: Path) -> None:
        """A path that exists but is not a valid SQLite database raises a
        raw `sqlite3.Error` from `open_vector_store`'s CREATE TABLE step,
        which this function catches and degrades rather than propagates."""
        path = tmp_path / "vectors.db"
        path.write_bytes(b"not a sqlite database")

        cm, unavailable = query_service._open_vector_store_or_degrade(path)

        assert unavailable is True
        with cm as store:
            assert store is None


class TestOpenFtsOrDegrade:
    """Direct coverage of `_open_fts_or_degrade`'s branches -- the
    success-open path and the caught-`sqlite3.Error` path."""

    def test_opens_an_existing_persisted_index_successfully(
        self, tmp_path: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        fts_path = tmp_path / "fts.db"
        fts.write_fts_index(fts_path, bundle_dir)

        cm, unavailable = query_service._open_fts_or_degrade(fts_path)

        assert unavailable is False
        with cm as handle:
            assert handle is not None

    def test_degrades_on_a_corrupt_existing_index(self, tmp_path: Path) -> None:
        """A path that exists but fails the validating `SELECT 1 FROM docs`
        read raises a raw `sqlite3.Error`, which this function catches and
        degrades rather than propagates."""
        fts_path = tmp_path / "fts.db"
        fts_path.write_bytes(b"not a sqlite database")

        cm, unavailable = query_service._open_fts_or_degrade(fts_path)

        assert unavailable is True
        with cm as handle:
            assert handle is None
