"""The query bounded-context application service (ADR-0018).

Composes the read-path orchestration around `retrieval.answer()` --
existence-gated store opening with degrade-to-`None` handling, and the
`answer()` call itself -- into a synchronous callable any adapter can use
without importing `openkos.cli`. `llm` and `embedder` are constructor
parameters built by the caller (D1): this module never binds a concrete
backend, so it stays usable by an MVP 3 `api`/`mcp` adapter as well as the
CLI. Exceptions from `answer()` propagate unwrapped (D2) -- ordering,
rendering and exit-code selection stay the calling adapter's job.

`--save` filing composition (staging, title derivation, duplicate-question
disclosure) is out of scope for this slice; it lands in a later change.
"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from openkos import config
from openkos.llm.base import Embedder, LLMBackend
from openkos.retrieval.answer import AnswerResult, answer
from openkos.state import fts
from openkos.state.vectorstore import VectorStoreDB, VecUnavailable, open_vector_store


@dataclass(frozen=True)
class QueryOutcome:
    """One `run_query` call's typed result.

    `result` is `answer()`'s own return value, unmodified. `vector_store_unavailable`
    and `fts_unavailable` report whether THIS call detected the corresponding
    derived store as unavailable (absent, or unopenable/corrupt) -- distinct
    from `result.dense_degraded`, which `answer()` sets for a read-path
    failure at query time rather than at store-open time."""

    result: AnswerResult
    vector_store_unavailable: bool
    fts_unavailable: bool


def _open_vector_store_or_degrade(
    path: Path,
) -> tuple[AbstractContextManager[VectorStoreDB | None], bool]:
    """Existence-gated store open for the read-only dense seam.

    `run_query` never CREATES `vectors.db` -- `open_vector_store` (which
    lazily creates `.openkos/vectors.db` on a successful open) is only
    called when `path` already exists on disk. Returns a context manager
    yielding either an open `VectorStoreDB` or `None`, plus whether this
    call detected the store as unavailable (absent, `VecUnavailable` at
    open, or a raw `sqlite3.Error` -- e.g. a corrupt/locked EXISTING
    `vectors.db` raising `DatabaseError`/`OperationalError` from
    `open_vector_store`'s CREATE TABLE step, which is not mapped to
    `VecUnavailable`) -- distinct from `AnswerResult.dense_degraded`, which
    is set INSIDE `answer()` for a read-path failure at query time."""
    if not path.exists():
        return nullcontext(None), True
    try:
        return open_vector_store(path), False
    except (VecUnavailable, sqlite3.Error):
        return nullcontext(None), True


def _open_fts_or_degrade(
    path: Path,
) -> tuple[AbstractContextManager[fts.FtsIndex | None], bool]:
    """Existence-gated, read-only handle open for the persisted FTS seam.

    Same INTENT and RETURN SHAPE as `_open_vector_store_or_degrade` --
    `(context_manager, bool)`, degrading to `(nullcontext(None), True)` on
    absence or failure -- but not structurally identical: this function has
    no explicit existence check of its own, because
    `fts.open_fts_index_readonly` is already existence-gated internally and
    returns `None` for an absent path on its own; and it catches only
    `sqlite3.Error`, since FTS has no typed "unavailable" exception
    analogous to `VecUnavailable`."""
    try:
        handle = fts.open_fts_index_readonly(path)
    except sqlite3.Error:
        return nullcontext(None), True
    if handle is None:
        return nullcontext(None), True
    return handle, False


def run_query(
    question: str,
    *,
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    llm: LLMBackend,
    embedder: Embedder,
    limit: int,
    include_deprecated: bool,
    include_confidential: bool,
    local_exemption: bool,
) -> QueryOutcome:
    """Compose store opening (degrade-to-`None`) and the `answer()` call for
    one query.

    Raises:
        OllamaUnavailable: the configured Ollama server is unreachable.
        OllamaModelNotFound: the configured chat or embedding model is not
            pulled.
        OllamaEmbeddingDimensionMismatch: the configured embedding model
            does not emit `EMBED_DIM`-dimensional vectors -- a permanent
            misconfiguration, not a transient failure.
        FtsUnavailable: sqlite's `fts5` module is not compiled in.
        OllamaError: any other, generic backend failure.

    The three `OllamaError` subclasses above (`OllamaUnavailable`,
    `OllamaModelNotFound`, `OllamaEmbeddingDimensionMismatch`) MUST be
    handled before a catch-all `except (FtsUnavailable, OllamaError)` --
    reordering silently swallows them into the generic branch and loses
    their actionable, cause-specific remediation (D2, ADR-0018). This
    function itself never catches any of them; it propagates whatever
    `answer()` raises unwrapped.
    """
    vector_store_cm, vector_store_unavailable = _open_vector_store_or_degrade(
        layout.vectors_db_path
    )
    fts_index_cm, fts_unavailable = _open_fts_or_degrade(layout.fts_db_path)
    with vector_store_cm as vector_store, fts_index_cm as fts_index:
        result = answer(
            question,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=fts_index,
            limit=limit,
            include_deprecated=include_deprecated,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
            # The ONE place that injects `cfg.sufficiency_check` explicitly
            # (#760), so the product-ON default lives in the config and
            # `answer` itself stays OFF for library callers.
            sufficiency_check=cfg.sufficiency_check,
        )
    return QueryOutcome(
        result=result,
        vector_store_unavailable=vector_store_unavailable,
        fts_unavailable=fts_unavailable,
    )
