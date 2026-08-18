"""Unit tests for `retrieval/answer.py`: the cited answer library.

`answer()` composes three INJECTED seams end-to-end (Slice 5, PR3, design
D4): a read-only `fts_index` handle (lexical), an injected `Embedder` +
`VectorStore` (dense, optional), and an injected `llm.LLMBackend` (answer).
`answer()` no longer builds `fts_index` itself -- tests inject either a real
`fts.build_index(bundle_dir)` handle (via a `with` block) for genuine
end-to-end coverage, or a lightweight structural fake for isolated/degrade-
path coverage. All tests use a `tmp_path` bundle and a structural fake
`LLMBackend` -- zero network, zero real Ollama process.

Issue #434 removed the fourth seam, the seeded personalized-PageRank graph
stage, so every fixture, spy, and degrade-matrix row it owned is gone from
this module. The removal is about the RANKING FUNCTION, not the typed graph:
PPR ranks by global centrality, which is not relevance to a question. The
typed graph itself still backs contradiction candidates and is untouched.
"""

import ast
import dataclasses
import importlib
import inspect
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import lifecycle, sensitivity
from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM, Message
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.retrieval import answer as answer_mod
from openkos.state import fts
from openkos.state.vectorstore import VecHit, VecUnavailable

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    description: str = "",
    body: str = "",
    status: str | None = None,
    relations: list[tuple[str, str]] | None = None,
    sensitivity_value: str | None = "private",
) -> None:
    """Write a minimal concept `.md` file. `status`/`relations` are optional
    lifecycle frontmatter (status-aware-retrieval, Phase 2): `relations` is a
    list of `(target, type)` pairs, mirroring `test_lifecycle.py`'s helper so
    both test modules build fixtures the same way. `sensitivity_value`
    defaults to `"private"` (`config.DEFAULT_SENSITIVITY`, matching what a
    real `ingest` always writes) so fixtures unrelated to the
    sensitivity-fail-closed-filter feature are never collaterally blocked by
    the fail-closed default; pass `None` explicitly for the absent-field
    case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"type: {doc_type}",
        f"title: {title}",
        f"description: {description}",
    ]
    if status is not None:
        lines.append(f"status: {status}")
    if sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    if relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns a fixed reply."""

    def __init__(self, reply: str = "the reply") -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.reply


class _RecordingIndex:
    """A fake `fts.FtsSearchHandle`: records `search()` args, returns fixed
    hits -- injected directly as `fts_index=...`, no build/context-manager
    step (Slice 5, PR3: `answer()` reads an already-open handle)."""

    def __init__(
        self, hits: list[fts.FtsHit], skipped: list[str] | None = None
    ) -> None:
        self._hits = hits
        self.calls: list[tuple[str, int]] = []
        self.skipped = skipped if skipped is not None else []

    def search(self, query: str, limit: int = 10) -> list[fts.FtsHit]:
        self.calls.append((query, limit))
        return self._hits


class _RaisingIndex:
    """A fake `fts.FtsSearchHandle` whose `search()` always raises `exc` --
    exercises the exception-vs-degrade boundary: `answer()` never wraps
    `fts_index.search` in a try/except, so this propagates unswallowed."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.skipped: list[str] = []

    def search(self, query: str, limit: int = 10) -> list[fts.FtsHit]:
        raise self._exc


class _SpyFtsIndex:
    """A fake `fts.FtsSearchHandle` recording whether `search()` was ever
    called at all -- follow-up #1's empty-question spy."""

    def __init__(self) -> None:
        self.calls = 0
        self.skipped: list[str] = []

    def search(self, query: str, limit: int = 10) -> list[fts.FtsHit]:
        self.calls += 1
        return []


class _FakeEmbedder:
    """A structural `Embedder`: records every `embed()` call's texts, returns
    a fixed `EMBED_DIM`-float vector per input (exact Protocol signature,
    Engram #1363 -- `Sequence[str]`, never narrowed to `list[str]`). Raises
    `raises` instead, if set (never both) -- mirrors `_FakeVectorStore`'s
    `raises` seam, used to exercise the question-embed dense-degrade path."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._raises = raises

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._raises is not None:
            raise self._raises
        return [[0.0] * EMBED_DIM for _ in texts]


class _FakeVectorStore:
    """A structural `VectorStore`: implements all 10 Protocol methods
    (Slice 5, follow-up #4 added `upsert_many`/`prune_many`/`commit`;
    MVP-2 follow-up #5 added `read_model_tag`/`write_model_tag`).
    `query` returns fixed `hits`, or raises `raises` if set (never both)."""

    def __init__(
        self, hits: list[VecHit] | None = None, *, raises: Exception | None = None
    ) -> None:
        self._hits = hits if hits is not None else []
        self._raises = raises
        self.calls: list[tuple[list[float], int]] = []

    def upsert(
        self, concept_id: str, embedding: Sequence[float], content_hash: str
    ) -> None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def upsert_many(self, items: Sequence[tuple[str, Sequence[float], str]]) -> None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def query(self, embedding: Sequence[float], k: int) -> list[VecHit]:
        self.calls.append((list(embedding), k))
        if self._raises is not None:
            raise self._raises
        return self._hits

    def meta_hashes(self) -> dict[str, str]:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def prune(self, concept_id: str) -> None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def prune_many(self, concept_ids: Sequence[str]) -> None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def commit(self) -> None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def read_model_tag(self) -> str | None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def write_model_tag(self, tag: str) -> None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def close(self) -> None:
        pass


# --- Phase 1: scaffold -------------------------------------------------


def test_citation_is_a_frozen_dataclass() -> None:
    """`Citation` carries `concept_id` and `title`, and is immutable."""
    citation = answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")

    assert citation.concept_id == "concepts/stoicism"
    assert citation.title == "Stoicism"
    with pytest.raises(dataclasses.FrozenInstanceError):
        citation.title = "Other"  # type: ignore[misc]


def test_answer_result_is_a_frozen_dataclass() -> None:
    """`AnswerResult` carries `answer` text, a `citations` list, retrieval
    metadata (`fts_hit_count`, `llm_invoked`, `no_match_cause`,
    `skip_notices`), and the additive dense/fused metadata
    (`dense_hit_count`, `fused_count`, `dense_degraded`), and is immutable."""
    citation = answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    result = answer_mod.AnswerResult(
        answer="the reply",
        citations=[citation],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=2,
        fused_count=1,
        dense_degraded=False,
    )

    assert result.answer == "the reply"
    assert result.citations == [citation]
    assert result.fts_hit_count == 1
    assert result.llm_invoked is True
    assert result.no_match_cause == "none"
    assert result.skip_notices == []
    assert result.dense_hit_count == 2
    assert result.fused_count == 1
    assert result.dense_degraded is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.answer = "other"  # type: ignore[misc]


# --- Phase 2/3: happy path, default limit, prompt shape -----------------


def test_matching_concepts_produce_a_cited_answer(tmp_path: Path) -> None:
    """A question matching a bundle concept calls `chat` once and cites it."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control is central to stoicism",
    )
    llm = _FakeLLM(reply="Stoicism teaches the dichotomy of control.")

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx
        )

    assert result.answer == "Stoicism teaches the dichotomy of control."
    assert len(llm.calls) == 1
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    ]
    assert result.fts_hit_count == 1
    assert result.llm_invoked is True
    assert result.no_match_cause == "none"


def test_caller_omits_limit_search_called_with_pool_ten(tmp_path: Path) -> None:
    """`limit` defaults to 5, but each retriever is called with
    `pool_limit = max(limit, 10)` -- `fts_index.search` is forwarded `10`,
    not the display `limit` itself (spec: Default Retrieval Limit)."""
    bundle_dir = tmp_path / "bundle"
    recording_index = _RecordingIndex(hits=[])

    answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=_FakeLLM(), fts_index=recording_index
    )

    assert recording_index.calls == [("dichotomyzz", 10)]


def test_caller_omits_limit_vector_store_query_called_with_pool_ten(
    tmp_path: Path,
) -> None:
    """Omitting `limit` also forwards `pool_limit=10` to
    `vector_store.query` (spec: Default Retrieval Limit)."""
    bundle_dir = tmp_path / "bundle"
    recording_index = _RecordingIndex(hits=[])
    vector_store = _FakeVectorStore(hits=[])

    answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=_FakeLLM(),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        fts_index=recording_index,
    )

    assert vector_store.calls == [([0.0] * EMBED_DIM, 10)]


def test_prompt_shape_has_system_grounding_and_labeled_context_blocks(
    tmp_path: Path,
) -> None:
    """System message carries grounding text; user message has one labeled
    block per hit followed by the question."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM()

    with fts.build_index(bundle_dir) as idx:
        answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx)

    assert len(llm.calls) == 1
    messages = llm.calls[0]
    assert messages[0]["role"] == "system"
    assert "do not use outside knowledge" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    user_content = messages[1]["content"]
    assert "[concept_id: concepts/stoicism — Stoicism]" in user_content
    assert "dichotomyzz of control" in user_content
    assert "QUESTION:\ndichotomyzz" in user_content


# --- Phase 2: dense injection + fuse --------------------------------------


def test_both_retrievers_produce_a_cited_answer(tmp_path: Path) -> None:
    """Both `fts_index.search` and `vector_store.query` are called, the
    fused list feeds context assembly, `llm.chat` is called exactly once,
    and `AnswerResult.answer` equals the LLM's response text (spec: Matching
    concepts produce a cited answer)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    _write_doc(
        bundle_dir / "concepts" / "epictetus.md",
        title="Epictetus",
        body="a stoic philosopher",
    )
    llm = _FakeLLM(reply="the fused reply")
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(
        hits=[VecHit(concept_id="concepts/epictetus", distance=0.1)]
    )

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=idx,
        )

    assert embedder.calls == [["dichotomyzz"]]
    assert vector_store.calls == [([0.0] * EMBED_DIM, 10)]
    assert len(llm.calls) == 1
    assert result.answer == "the fused reply"


def test_dense_only_match_is_retrievable(tmp_path: Path) -> None:
    """A concept absent from FTS hits but present in dense hits is placed in
    context via the fused list and appears in `citations` (spec: Dense-only
    match is retrievable)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "epictetus.md",
        title="Epictetus",
        body="a stoic philosopher",
    )
    recording_index = _RecordingIndex(hits=[])
    llm = _FakeLLM(reply="from epictetus alone")
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(
        hits=[VecHit(concept_id="concepts/epictetus", distance=0.05)]
    )

    result = answer_mod.answer(
        "meaning of stoicism",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=recording_index,
    )

    assert result.llm_invoked is True
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/epictetus", title="Epictetus")
    ]


def test_dense_only_hit_surfaces_within_truncated_limit_via_fused_pool(
    tmp_path: Path,
) -> None:
    """`pool_limit = max(limit, 10)` retrieves a wider pool than the display
    `limit`, so a dense-only concept -- absent from the FTS hits entirely --
    can still fuse into a top rank and survive truncation to a SMALL
    `limit`, proving the pool>limit truncation genuinely surfaces dense-only
    hits rather than just re-ranking what FTS already returned within
    `limit` (spec: Default Retrieval Limit; Dense-only match is
    retrievable)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "one.md", title="One", body="fts hit one")
    _write_doc(
        bundle_dir / "concepts" / "dense-star.md",
        title="Dense Star",
        body="dense-only concept",
    )
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/one", score=0.0),
            fts.FtsHit(concept_id="concepts/two", score=0.0),
            fts.FtsHit(concept_id="concepts/three", score=0.0),
            fts.FtsHit(concept_id="concepts/four", score=0.0),
        ]
    )
    llm = _FakeLLM(reply="fused reply")
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(
        hits=[VecHit(concept_id="concepts/dense-star", distance=0.0)]
    )

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=recording_index,
        limit=2,
    )

    assert result.fused_count == 2
    assert [citation.concept_id for citation in result.citations] == [
        "concepts/dense-star",
        "concepts/one",
    ]
    assert len(llm.calls) == 1


def test_dense_and_fused_counts_reflect_retrieval(tmp_path: Path) -> None:
    """3 dense hits and a fused list of 4 distinct `concept_id`s -> `dense_hit_count`
    equals 3 and `fused_count` equals 4 (spec: Dense and fused counts reflect
    retrieval)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/x", score=0.0)]
    )
    vector_store = _FakeVectorStore(
        hits=[
            VecHit(concept_id="concepts/a", distance=0.0),
            VecHit(concept_id="concepts/b", distance=0.1),
            VecHit(concept_id="concepts/c", distance=0.2),
        ]
    )

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=_FakeLLM(),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
        fts_index=recording_index,
    )

    assert result.dense_hit_count == 3
    assert result.fused_count == 4


def test_successful_answer_sets_dense_degraded_false(tmp_path: Path) -> None:
    """Dense retrieval completing normally sets `dense_degraded=False`
    (spec: dense_degraded reflects whether dense retrieval ran)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    vector_store = _FakeVectorStore(hits=[])

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(),
            embedder=_FakeEmbedder(),
            vector_store=vector_store,
            fts_index=idx,
        )

    assert result.dense_degraded is False


# --- Phase 4/5: zero/degraded no-match -----------------------------------


def test_no_matching_concepts_returns_canned_no_match(tmp_path: Path) -> None:
    """Zero FTS hits never call `chat` and return the stable no-match text."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM()

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "nonexistentqueryzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx
        )

    assert llm.calls == []
    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert result.fts_hit_count == 0
    assert result.llm_invoked is False
    assert result.no_match_cause == "zero_hits"


def test_all_hits_unreadable_degrades_to_no_match(tmp_path: Path) -> None:
    """Every hit unreadable/unparseable at answer time -> zero-hit contract."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/vanished", score=0.0)]
    )
    llm = _FakeLLM()

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert llm.calls == []
    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert result.fts_hit_count == 1
    assert result.llm_invoked is False
    assert result.no_match_cause == "all_unreadable"


def test_unparseable_frontmatter_hit_is_skipped(tmp_path: Path) -> None:
    """A hit whose frontmatter fails to parse is skipped, not raised."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "corrupt.md").write_text(
        "---\ntitle: [unclosed\n---\ndichotomyzz of control",
        encoding="utf-8",
    )
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/corrupt", score=0.0),
            fts.FtsHit(concept_id="concepts/stoicism", score=1.0),
        ]
    )
    llm = _FakeLLM(reply="answered from stoicism only")

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert len(llm.calls) == 1
    assert result.answer == "answered from stoicism only"
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    ]


def test_multiple_surviving_hits_cite_in_rank_order_and_join_context(
    tmp_path: Path,
) -> None:
    """Two readable concepts both survive `_assemble_context`: citations come
    back in hit-rank order (not just present), and the user message's
    context carries BOTH blocks joined by `\\n\\n`, in that same rank order
    (design's "Multi-survivor test follow-up")."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="the dichotomy of control",
    )
    _write_doc(
        bundle_dir / "concepts" / "epictetus.md",
        title="Epictetus",
        body="a Stoic philosopher",
    )
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/stoicism", score=1.0),
            fts.FtsHit(concept_id="concepts/epictetus", score=0.5),
        ]
    )
    llm = _FakeLLM(reply="Stoicism was practiced by Epictetus.")

    result = answer_mod.answer(
        "stoicism", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism"),
        answer_mod.Citation(concept_id="concepts/epictetus", title="Epictetus"),
    ]
    assert len(llm.calls) == 1
    user_content = llm.calls[0][1]["content"]
    stoicism_block = (
        "[concept_id: concepts/stoicism — Stoicism]\nthe dichotomy of control"
    )
    epictetus_block = (
        "[concept_id: concepts/epictetus — Epictetus]\na Stoic philosopher"
    )
    assert stoicism_block in user_content
    assert epictetus_block in user_content
    # The blocks carry their 1-based attribution number (#753). The number is
    # part of the join, so the adjacency below is asserted WITH it rather than
    # around it -- a version checking only the bare blocks would pass with the
    # numbering silently dropped, and the numbering is the vocabulary the
    # model attributes with.
    assert f"[1] {stoicism_block}\n\n[2] {epictetus_block}" in user_content
    assert user_content.index(stoicism_block) < user_content.index(epictetus_block)


def test_one_hit_vanished_skips_it_and_still_answers_with_the_rest(
    tmp_path: Path,
) -> None:
    """One vanished hit is skipped; `chat` still runs with the readable hit."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/vanished", score=0.0),
            fts.FtsHit(concept_id="concepts/stoicism", score=1.0),
        ]
    )
    llm = _FakeLLM(reply="answered from stoicism only")

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert len(llm.calls) == 1
    assert result.answer == "answered from stoicism only"
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    ]


def test_classify_no_match_empty_query_wins_over_present_hits() -> None:
    """`_classify_no_match` gives `"empty_query"` priority: a blank question
    classifies as `"empty_query"` even when hits are present, so it never
    collapses into `"zero_hits"` or `"all_unreadable"`. A hit from either
    retriever alone is enough to avoid `"zero_hits"`."""
    hits = [fts.FtsHit(concept_id="concepts/stoicism", score=1.0)]
    vec_hits = [VecHit(concept_id="concepts/epictetus", distance=0.0)]

    assert answer_mod._classify_no_match("   ", hits, []) == "empty_query"
    assert answer_mod._classify_no_match("", hits, []) == "empty_query"
    assert answer_mod._classify_no_match("real question", [], []) == "zero_hits"
    assert answer_mod._classify_no_match("real question", hits, []) == "all_unreadable"
    assert (
        answer_mod._classify_no_match("real question", [], vec_hits) == "all_unreadable"
    )


def test_skip_notices_carried_on_matched_path(tmp_path: Path) -> None:
    """Non-empty `fts_index.skipped` is carried onto `AnswerResult.skip_notices`
    even when the query matches and the LLM is invoked."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    skip_notices = ["concepts/corrupt.md: skipped (unreadable)"]
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/stoicism", score=0.0)],
        skipped=skip_notices,
    )
    llm = _FakeLLM(reply="the reply")

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert result.skip_notices == skip_notices
    assert result.llm_invoked is True


def test_skip_notices_carried_on_no_match_path(tmp_path: Path) -> None:
    """Non-empty `fts_index.skipped` is carried onto `AnswerResult.skip_notices`
    even on a no-match (zero-hit) path."""
    bundle_dir = tmp_path / "bundle"
    skip_notices = ["concepts/corrupt.md: skipped (unreadable)"]
    recording_index = _RecordingIndex(hits=[], skipped=skip_notices)
    llm = _FakeLLM()

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert result.skip_notices == skip_notices
    assert result.no_match_cause == "zero_hits"


# --- Phase 3: zero-hit reclassification across both retrievers -----------


def test_zero_fts_and_zero_dense_hits_returns_no_match(tmp_path: Path) -> None:
    """Zero hits from BOTH retrievers never calls `chat`, returns empty
    citations, and a non-empty no-match message (spec: No matching concepts
    found in either list)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(hits=[])
    llm = _FakeLLM()
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(hits=[])

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=recording_index,
    )

    assert llm.calls == []
    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert result.no_match_cause == "zero_hits"


def test_dense_only_hit_avoids_the_zero_hit_path(tmp_path: Path) -> None:
    """Zero FTS hits but at least one dense hit invokes the LLM and does NOT
    classify as `"zero_hits"` (spec: Dense-only hit avoids the zero-hit
    path)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "epictetus.md",
        title="Epictetus",
        body="a stoic philosopher",
    )
    recording_index = _RecordingIndex(hits=[])
    llm = _FakeLLM(reply="answered from dense alone")
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(
        hits=[VecHit(concept_id="concepts/epictetus", distance=0.0)]
    )

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=recording_index,
    )

    assert len(llm.calls) == 1
    assert result.no_match_cause != "zero_hits"


def test_empty_question_touches_no_injected_handle(tmp_path: Path) -> None:
    """A whitespace-only question short-circuits BEFORE any retrieval --
    `fts_index.search`, `embedder.embed`, and `vector_store.query` are ALL
    untouched, `llm.chat` is never invoked, and `no_match_cause` is
    `"empty_query"` (follow-up #1; query-answer: Whitespace-only question
    touches no injected handle)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    fts_index = _SpyFtsIndex()
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(hits=[])
    llm = _FakeLLM()

    result = answer_mod.answer(
        "   ",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=fts_index,
    )

    assert fts_index.calls == 0
    assert embedder.calls == []
    assert vector_store.calls == []
    assert llm.calls == []
    assert result.no_match_cause == "empty_query"
    assert result.answer == answer_mod.NO_MATCH
    assert result.fts_hit_count == 0
    assert result.llm_invoked is False


# --- Phase 3: dense degrade to FTS-only ------------------------------------


def test_vector_store_query_raises_vec_unavailable_degrades_to_fts_only(
    tmp_path: Path,
) -> None:
    """`vector_store.query` raising `VecUnavailable` degrades to FTS-only
    fusion, sets `dense_degraded=True`, and never raises (spec:
    VecUnavailable degrades to FTS-only)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="fts only reply")
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(raises=VecUnavailable("boom"))

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=idx,
        )

    assert result.dense_degraded is True
    assert result.dense_hit_count == 0
    assert result.llm_invoked is True
    assert result.answer == "fts only reply"


def test_vector_store_query_raises_sqlite_error_degrades_to_fts_only(
    tmp_path: Path,
) -> None:
    """`vector_store.query` raising a read-path `sqlite3.Error` degrades to
    FTS-only fusion and never raises (spec: Read-path sqlite3.Error degrades
    to FTS-only)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="fts only reply")
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(raises=sqlite3.OperationalError("locked"))

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=idx,
        )

    assert result.dense_degraded is True
    assert result.dense_hit_count == 0
    assert result.llm_invoked is True


def test_question_embed_ollama_error_degrades_to_fts_only(tmp_path: Path) -> None:
    """`embedder.embed([question])` raising an `OllamaError`-family exception
    (the flaky embedding path) degrades to FTS-only fusion, sets
    `dense_degraded=True`, and never raises from `answer` -- the caller
    (`query`) still exits 0 (spec: query-answer Dense Retrieval Degrades To
    FTS-Only -- Question-embed OllamaError degrades to FTS-only, not exit
    1)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="fts only reply")
    embedder = _FakeEmbedder(raises=OllamaError("EOF mid-embed"))
    vector_store = _FakeVectorStore()

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=idx,
        )

    assert result.dense_degraded is True
    assert result.dense_hit_count == 0
    assert result.llm_invoked is True
    assert result.answer == "fts only reply"
    assert vector_store.calls == []  # query() never reached -- embed failed first


def test_question_embed_ollama_unavailable_propagates(tmp_path: Path) -> None:
    """`embedder.embed([question])` raising `OllamaUnavailable` (a down
    server, `OllamaError` subclass) PROPAGATES out of `answer()` -- it must
    NOT be swallowed into `dense_degraded=True` -- so `query`'s fatal exit-1
    ladder can report it (mirrors the same fatal-subclass carve-out already
    fixed on the reindex side)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="unused")
    embedder = _FakeEmbedder(raises=OllamaUnavailable("connection refused"))
    vector_store = _FakeVectorStore()

    with fts.build_index(bundle_dir) as idx, pytest.raises(OllamaUnavailable):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=idx,
        )


def test_question_embed_ollama_model_not_found_propagates(tmp_path: Path) -> None:
    """`embedder.embed([question])` raising `OllamaModelNotFound` (an
    `OllamaError` subclass) PROPAGATES out of `answer()` -- it must NOT
    degrade to FTS-only, so `query`'s fatal exit-1 ladder can report the
    actionable missing-model message."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="unused")
    embedder = _FakeEmbedder(raises=OllamaModelNotFound("model not found"))
    vector_store = _FakeVectorStore()

    with fts.build_index(bundle_dir) as idx, pytest.raises(OllamaModelNotFound):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=idx,
        )


def test_question_embed_dimension_mismatch_propagates(tmp_path: Path) -> None:
    """`embedder.embed([question])` raising
    `OllamaEmbeddingDimensionMismatch` (an `OllamaError` subclass)
    PROPAGATES out of `answer()` -- it must NOT be swallowed into
    `dense_degraded=True` (issue #209): a wrong-dimension response is a
    PERMANENT misconfiguration of the configured `embedding_model` that no
    retry and no re-run can fix, so semantic retrieval is structurally
    impossible rather than merely unhelpful, and `query`'s fatal exit-1
    ladder must report it instead of silently returning an FTS-only
    answer."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="unused")
    embedder = _FakeEmbedder(
        raises=OllamaEmbeddingDimensionMismatch(
            "Ollama returned an embedding row of length 768, expected "
            f"exactly {EMBED_DIM} (EMBED_DIM) -- this is a permanent "
            "dimension mismatch caused by the configured embedding model, "
            "not a transient failure; it will not heal by retrying."
        )
    )
    vector_store = _FakeVectorStore()

    with (
        fts.build_index(bundle_dir) as idx,
        pytest.raises(OllamaEmbeddingDimensionMismatch),
    ):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=embedder,
            vector_store=vector_store,
            fts_index=idx,
        )

    assert llm.calls == []  # never reached a degraded FTS-only answer


def test_cold_store_vector_store_none_degrades_cleanly(tmp_path: Path) -> None:
    """`vector_store=None` (workspace never ran `reindex`) proceeds using FTS
    hits alone, `dense_hit_count` is 0, `dense_degraded` is `True`, and no
    exception propagates (spec: Cold store degrades cleanly)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="fts only")

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            embedder=None,
            vector_store=None,
            fts_index=idx,
        )

    assert result.dense_hit_count == 0
    assert result.dense_degraded is True
    assert result.llm_invoked is True


# --- Phase 3 (Slice 5, PR3): absent/corrupt fts_index degrade + boundary --


def test_absent_fts_index_degrades_to_empty_not_raise(tmp_path: Path) -> None:
    """`fts_index=None` (the default -- workspace never ran `reindex`, or
    the CLI resolved an unopenable/corrupt store to `None`) proceeds using
    dense (and graph) hits alone; `fts_hit_count` is `0` and no exception
    propagates (query-answer: Absent FTS handle degrades to empty, not
    raise)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "epictetus.md",
        title="Epictetus",
        body="a stoic philosopher",
    )
    llm = _FakeLLM(reply="dense only, no fts")
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(
        hits=[VecHit(concept_id="concepts/epictetus", distance=0.0)]
    )

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=None,
    )

    assert result.fts_hit_count == 0
    assert result.llm_invoked is True
    assert result.answer == "dense only, no fts"


def test_fts_query_terms_drop_function_words() -> None:
    """#648: ES/EN function words in the QUESTION match the wrong domain's
    documents in FTS (`¿qué es MCP y para qué sirve?` ranked the Spanish
    MVP concept FIRST for an English-domain question). The FTS query keeps
    content words only; the dense channel keeps the full question."""
    assert answer_mod._fts_query_terms("¿qué es MCP y para qué sirve?") == "MCP sirve"
    assert answer_mod._fts_query_terms("¿qué es el Model Context Protocol?") == (
        "Model Context Protocol"
    )
    assert answer_mod._fts_query_terms("what is the context window?") == (
        "what context window"
    )


def test_fts_query_terms_fall_open_when_nothing_would_remain() -> None:
    """A question made ONLY of function words keeps the raw question --
    an empty FTS query would silently disable the lexical channel."""
    assert answer_mod._fts_query_terms("¿qué es y para qué?") == "¿qué es y para qué?"


def test_answer_searches_fts_with_content_words_only(tmp_path: Path) -> None:
    """The wiring: `fts_index.search` receives the filtered query, never
    the raw question (#648) -- pinned via the recording handle."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "mcp.md", title="MCP")
    index = _RecordingIndex(hits=[fts.FtsHit(concept_id="concepts/mcp", score=0.0)])
    llm = _FakeLLM(reply="answer")

    answer_mod.answer(
        "¿qué es MCP?",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=index,
    )

    assert [call[0] for call in index.calls] == ["MCP"]


def test_typed_exception_from_fts_search_propagates_unswallowed(
    tmp_path: Path,
) -> None:
    """A typed exception raised by an injected `fts_index.search()` call
    (e.g. a genuine `FtsUnavailable` from an availability failure OUTSIDE
    the store-open path) still propagates unswallowed -- the
    exception-vs-degrade boundary applies ONLY at the caller's store-open
    call site, never inside `answer()` (query-answer: Typed Exceptions
    Propagate Unswallowed, exception-vs-degrade boundary)."""
    bundle_dir = tmp_path / "bundle"
    raising_index = _RaisingIndex(fts.FtsUnavailable("fts5 not compiled in"))

    with pytest.raises(fts.FtsUnavailable):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(),
            embedder=_FakeEmbedder(),
            vector_store=_FakeVectorStore(hits=[]),
            fts_index=raising_index,
        )


def test_typed_exception_from_fts_search_propagates_even_with_no_dense(
    tmp_path: Path,
) -> None:
    """The same propagation holds with no dense seams injected at all --
    mirrors the previous build-time propagation test, now via the injected
    handle's own `search()` call."""
    bundle_dir = tmp_path / "bundle"
    raising_index = _RaisingIndex(fts.FtsUnavailable("fts5 not compiled in"))

    with pytest.raises(fts.FtsUnavailable):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(),
            fts_index=raising_index,
        )


# --- Phase 6/7: title fallback -------------------------------------------


def test_missing_title_falls_back_to_concept_id(tmp_path: Path) -> None:
    """A concept with no frontmatter `title` cites with `concept_id` as its title."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "untitled.md").write_text(
        "---\ntype: Concept\ndescription: ''\nsensitivity: private\n---\n"
        "dichotomyzz of control",
        encoding="utf-8",
    )
    llm = _FakeLLM()

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx
        )

    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/untitled", title="concepts/untitled")
    ]


# --- Phase 8: typed exception propagation ---------------------------------


def test_llm_chat_error_propagates_unswallowed(tmp_path: Path) -> None:
    """An `OllamaError`-family exception raised by `chat` is never caught here."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )

    class _ExplodingLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            raise OllamaUnavailable("Ollama not reachable")

    with fts.build_index(bundle_dir) as idx, pytest.raises(OllamaUnavailable):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_ExplodingLLM(),
            fts_index=idx,
        )


# --- ingest-source-body: zero-change confirmation ------------------------


def test_query_retrieves_and_cites_ingested_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`openkos ingest` embeds a source's verbatim text into its Source
    concept body, and `answer()` retrieves and cites that concept with NO
    changes to `state/fts.py` or `retrieval/answer.py`'s ingest-facing
    contract -- embedding alone makes the content reachable via the
    existing generic body-indexing and body-feeding behavior (design's
    zero-change confirmation, scenario: query retrieves and cites ingested
    content)."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0

    distinctive_phrase = "the flurbnorxal protocol requires triple validation"
    source = tmp_path / "protocol-notes.txt"
    source.write_text(distinctive_phrase, encoding="utf-8")
    ingest_result = runner.invoke(app, ["ingest", "protocol-notes.txt", "--auto"])
    assert ingest_result.exit_code == 0

    llm = _FakeLLM(reply="The flurbnorxal protocol requires triple validation.")
    bundle_dir = tmp_path / "bundle"
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "flurbnorxal", bundle_dir=bundle_dir, llm=llm, fts_index=idx
        )

    assert result.answer != answer_mod.NO_MATCH
    assert any(
        citation.concept_id == "sources/protocol-notes" for citation in result.citations
    )
    assert len(llm.calls) == 1
    user_content = llm.calls[0][1]["content"]
    assert distinctive_phrase in user_content


# --- Phase 9: layering / static-import guards ------------------------------


def test_answer_module_does_not_import_config() -> None:
    """`retrieval/answer.py` does not import `openkos.config` (leaf discipline)."""
    module_path = _REPO_ROOT / "src" / "openkos" / "retrieval" / "answer.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any("config" in name for name in imported), (
        f"{module_path} imports config: {imported}"
    )


def test_answer_module_never_computes_or_imports_bundle_manifest_hash() -> None:
    """`retrieval/answer.py` never imports `state.derived` (the sole home of
    `bundle_manifest_hash`) and never references `bundle_manifest_hash` by
    name -- a static, structural proof of the D2 binding contract:
    `answer()`/`query` NEVER recompute or compare the bundle manifest hash;
    that comparison is `reindex`'s exclusive job."""
    module_path = _REPO_ROOT / "src" / "openkos" / "retrieval" / "answer.py"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any("derived" in name for name in imported), (
        f"{module_path} imports state.derived: {imported}"
    )
    assert "bundle_manifest_hash" not in source


def test_answer_module_no_longer_reads_the_graph_at_all() -> None:
    """`retrieval/answer.py` imports nothing from `openkos.graph` and names
    no graph-stage IDENTIFIER (issue #434).

    The flow is retrieve -> fuse -> assemble -> answer; the seed/graph/fuse
    stages are gone. This is a STATIC guard because the failure mode it
    protects against is silent: a re-added graph stage would still answer
    every question, just worse -- the measured harm was 7 harmful, 3
    neutral, 0 beneficial over 10 questions, including evicting
    `sources/mcp-origin` from "When did MCP originate?" to seat the corpus's
    most central node. Centrality is not relevance.

    Prose is checked separately from identifiers on purpose: the module
    docstring MAY (and does) explain why the channel was removed, so this
    asserts on names a re-added stage would have to use, not on the word
    "graph" appearing anywhere."""
    module_path = _REPO_ROOT / "src" / "openkos" / "retrieval" / "answer.py"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any(
        name.startswith("openkos.graph") or "graph_retrieve" in name
        for name in imported
    ), f"{module_path} still imports the graph layer: {imported}"
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    banned = {
        "graph_index",
        "graph_rank",
        "graph_hits",
        "graph_degraded",
        "graph_hit_count",
        "graph_contributed_count",
        "fuse_with_graph",
        "GraphHit",
        "GraphStore",
    }
    assert not (names & banned), f"{module_path} still names: {names & banned}"


def test_answer_takes_no_graph_index_parameter() -> None:
    """`answer()`'s signature has no `graph_index` seam left to inject
    (issue #434) -- passing one is a `TypeError`, not a silently ignored
    keyword."""
    parameters = inspect.signature(answer_mod.answer).parameters

    assert "graph_index" not in parameters


def test_answer_result_carries_no_graph_metadata() -> None:
    """`AnswerResult` reports nothing about a graph channel that no longer
    runs: `graph_hit_count`, `graph_contributed_count`, and `graph_degraded`
    are all gone (issue #434). A field that always reported zero would be
    worse than absent -- it would read as a channel that contributed
    nothing, rather than one that is not there."""
    field_names = {field.name for field in dataclasses.fields(answer_mod.AnswerResult)}

    assert "graph_hit_count" not in field_names
    assert "graph_contributed_count" not in field_names
    assert "graph_degraded" not in field_names


def test_graph_retrieve_module_is_gone() -> None:
    """`retrieval/graph_retrieve.py` had exactly one importer, `answer.py`,
    so removing the stage removed the module's only consumer (issue #434).
    `openkos.graph` -- the typed projection that backs contradiction
    candidates -- is deliberately untouched and still imports cleanly."""
    module_path = _REPO_ROOT / "src" / "openkos" / "retrieval" / "graph_retrieve.py"

    assert not module_path.exists()
    with pytest.raises(ImportError):
        importlib.import_module("openkos.retrieval.graph_retrieve")
    assert importlib.import_module("openkos.graph.sqlite_graph") is not None


# --- status-aware-retrieval, Phase 2/PR2: query-path lifecycle filtering --


def test_deprecated_concept_excluded_from_fts_hits_by_default(tmp_path: Path) -> None:
    """A concept with `status: deprecated` matching lexically is absent from
    the fused/cited result by default, while a live match still surfaces;
    `fts_hit_count` reports the POST-filter count, not the raw 2 (spec:
    Deprecated concept absent from a matching query)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "old.md",
        title="Old",
        body="deprecated dichotomyzz note",
        status="deprecated",
    )
    _write_doc(
        bundle_dir / "concepts" / "live.md",
        title="Live",
        body="live dichotomyzz note",
    )
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/old", score=1.0),
            fts.FtsHit(concept_id="concepts/live", score=0.5),
        ]
    )
    llm = _FakeLLM(reply="live answer only")

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    cited_ids = {citation.concept_id for citation in result.citations}
    assert "concepts/old" not in cited_ids
    assert "concepts/live" in cited_ids
    assert result.fts_hit_count == 1


def test_deprecated_concept_excluded_from_vector_hits_by_default(
    tmp_path: Path,
) -> None:
    """A concept with `status: deprecated` matching only via dense retrieval
    is absent from the fused/cited result by default; `dense_hit_count`
    reports the POST-filter count, not the raw 2 (spec: No leak via any
    single input)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "old.md", title="Old", status="deprecated")
    _write_doc(bundle_dir / "concepts" / "live.md", title="Live")
    recording_index = _RecordingIndex(hits=[])
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(
        hits=[
            VecHit(concept_id="concepts/old", distance=0.0),
            VecHit(concept_id="concepts/live", distance=0.1),
        ]
    )
    llm = _FakeLLM(reply="live via dense only")

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=recording_index,
    )

    cited_ids = {citation.concept_id for citation in result.citations}
    assert "concepts/old" not in cited_ids
    assert "concepts/live" in cited_ids
    assert result.dense_hit_count == 1


def test_superseded_concept_excluded_end_to_end(tmp_path: Path) -> None:
    """A concept that is the TARGET of another concept's `supersedes` edge is
    excluded through `answer()`, even though its own `status` frontmatter is
    untouched (spec: superseded concept is deprecated regardless of its own
    status)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "superseder.md",
        title="Superseder",
        relations=[("concepts/old", "supersedes")],
    )
    _write_doc(bundle_dir / "concepts" / "old.md", title="Old")
    _write_doc(bundle_dir / "concepts" / "live.md", title="Live")
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/old", score=1.0),
            fts.FtsHit(concept_id="concepts/live", score=0.5),
        ]
    )
    llm = _FakeLLM(reply="live only")

    result = answer_mod.answer(
        "q", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    cited_ids = {citation.concept_id for citation in result.citations}
    assert "concepts/old" not in cited_ids
    assert "concepts/live" in cited_ids


def test_only_deprecated_match_yields_zero_hits_no_match_by_default(
    tmp_path: Path,
) -> None:
    """When the ONLY concept matching the question anywhere is deprecated,
    the default run degrades to the standard zero-hit no-match outcome, not
    an error (spec: Only match is deprecated yields the standard no-match
    result)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "old.md", title="Old", status="deprecated")
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/old", score=1.0)]
    )
    llm = _FakeLLM()

    result = answer_mod.answer(
        "q", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert llm.calls == []
    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert result.no_match_cause == "zero_hits"
    assert result.fts_hit_count == 0


def test_include_deprecated_true_restores_the_only_match_and_skips_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_deprecated=True` restores the deprecated-only match to full
    participation AND never calls `lifecycle.deprecated_concept_ids` at all
    (spy) -- the escape flag skips the predicate walk entirely, at zero
    added cost (spec: Flag restores a deprecated concept; design R1)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "old.md", title="Old", status="deprecated")
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/old", score=1.0)]
    )
    llm = _FakeLLM(reply="restored")
    walk_calls: list[Path] = []
    original_predicate = lifecycle.deprecated_concept_ids

    def _spy_predicate(bundle_dir: Path) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir)

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _spy_predicate)

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        include_deprecated=True,
    )

    assert walk_calls == []
    assert result.answer == "restored"
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/old", title="Old")
    ]
    assert result.fts_hit_count == 1


def test_default_include_deprecated_false_calls_the_predicate_walk_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default `include_deprecated=False` DOES call
    `lifecycle.deprecated_concept_ids` exactly once per `answer()` call
    (design R1: the walk is reintroduced deliberately, paid only when
    filtering is actually needed)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "live.md", title="Live", body="dichotomyzz")
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/live", score=1.0)]
    )
    walk_calls: list[Path] = []
    original_predicate = lifecycle.deprecated_concept_ids

    def _spy_predicate(bundle_dir: Path) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir)

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _spy_predicate)

    answer_mod.answer(
        "q", bundle_dir=bundle_dir, llm=_FakeLLM(reply="ok"), fts_index=recording_index
    )

    assert walk_calls == [bundle_dir]


def test_all_live_bundle_is_identical_with_and_without_include_deprecated(
    tmp_path: Path,
) -> None:
    """A bundle where every concept's effective status is live produces the
    identical fused/cited result whether `include_deprecated` is `False`
    (the default) or `True` -- filtering against an empty `deprecated` set
    is a no-op (spec: All-live bundle is unaffected)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        status="active",
        body="dichotomyzz a",
    )
    _write_doc(bundle_dir / "concepts" / "b.md", title="B", body="dichotomyzz b")
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/a", score=1.0),
            fts.FtsHit(concept_id="concepts/b", score=0.5),
        ]
    )
    llm = _FakeLLM(reply="both live")

    default_result = answer_mod.answer(
        "q", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )
    include_result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        include_deprecated=True,
    )

    assert default_result.fts_hit_count == include_result.fts_hit_count == 2
    assert (
        [c.concept_id for c in default_result.citations]
        == [c.concept_id for c in include_result.citations]
        == ["concepts/a", "concepts/b"]
    )


def test_r3_counts_and_fused_count_report_post_filter_values(tmp_path: Path) -> None:
    """`fts_hit_count`, `dense_hit_count`, and `fused_count` all report
    POST-filter values -- filtering happens BEFORE these counts are captured,
    not after (design R3, pinned). Both input channels (fts, dense)
    contribute one deprecated concept that must not leak into the count or
    the citations."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "fts-old.md", title="FTS Old", status="deprecated"
    )
    _write_doc(
        bundle_dir / "concepts" / "fts-live.md",
        title="FTS Live",
        body="dichotomyzz",
    )
    _write_doc(
        bundle_dir / "concepts" / "vec-old.md", title="Vec Old", status="deprecated"
    )
    _write_doc(bundle_dir / "concepts" / "vec-live.md", title="Vec Live")
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/fts-old", score=1.0),
            fts.FtsHit(concept_id="concepts/fts-live", score=0.5),
        ]
    )
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(
        hits=[
            VecHit(concept_id="concepts/vec-old", distance=0.0),
            VecHit(concept_id="concepts/vec-live", distance=0.1),
        ]
    )
    llm = _FakeLLM(reply="post-filter counts")

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=recording_index,
    )

    assert result.fts_hit_count == 1  # 2 raw FTS hits, 1 deprecated filtered out
    assert result.dense_hit_count == 1  # 2 raw dense hits, 1 deprecated filtered out
    assert result.fused_count == 2  # fts-live + vec-live only
    cited_ids = {citation.concept_id for citation in result.citations}
    assert "concepts/fts-old" not in cited_ids
    assert "concepts/vec-old" not in cited_ids


# --- sensitivity-fail-closed-filter, S3a/PR1: query-path sensitivity filtering --


def test_confidential_concept_excluded_from_fts_hits_by_default(
    tmp_path: Path,
) -> None:
    """A concept with `sensitivity: confidential` matching lexically is
    absent from the fused/cited result by default, while a private match
    still surfaces (spec: Confidential excluded from query/answer)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    _write_doc(
        bundle_dir / "concepts" / "open.md",
        title="Open",
        body="dichotomyzz private note",
        sensitivity_value="private",
    )
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/secret", score=1.0),
            fts.FtsHit(concept_id="concepts/open", score=0.5),
        ]
    )
    llm = _FakeLLM(reply="private answer only")

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    cited_ids = {citation.concept_id for citation in result.citations}
    assert "concepts/secret" not in cited_ids
    assert "concepts/open" in cited_ids
    assert result.fts_hit_count == 1
    for message in llm.calls[0]:
        assert "confidential note" not in message["content"]


def test_include_confidential_true_restores_the_only_match_and_skips_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include_confidential=True` restores a confidential-only match to full
    participation AND reaches `sensitivity.sensitive_concept_ids` as the flag
    it is (spy), so the predicate short-circuits before touching
    `okf._iter_docs` -- the escape flag is still zero added cost (spec:
    `--include-confidential` Escape Flag).

    Issue #240 moved WHERE the skip is decided, not whether it happens: the
    guarding `if not include_confidential:` used to live in `answer`, but a
    second hatch (`local_exemption`) would have made that a two-term
    disjunction restated at five call sites, which is the duplication
    `sensitivity.py`'s module docstring exists to prevent. The walk-skip
    itself is pinned once, centrally, by
    `tests/unit/test_sensitivity.py::test_sensitive_concept_ids_escape_hatches_skip_the_walk_entirely`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        sensitivity_value="confidential",
    )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/secret", score=1.0)]
    )
    llm = _FakeLLM(reply="restored")
    walk_calls: list[dict[str, object]] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(kwargs)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        include_confidential=True,
    )

    assert walk_calls == [{"include_confidential": True, "local_exemption": False}]
    assert result.answer == "restored"
    assert result.citations == [
        answer_mod.Citation(
            concept_id="concepts/secret", title="Secret", confidential=True
        )
    ]


def test_default_include_confidential_false_calls_the_predicate_walk_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default `include_confidential=False` DOES call
    `sensitivity.sensitive_concept_ids` exactly once per `answer()` call."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "live.md",
        title="Live",
        body="dichotomyzz",
        sensitivity_value="private",
    )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/live", score=1.0)]
    )
    walk_calls: list[Path] = []
    original_predicate = sensitivity.sensitive_concept_ids

    def _spy_predicate(bundle_dir: Path, **kwargs: object) -> frozenset[str]:
        walk_calls.append(bundle_dir)
        return original_predicate(bundle_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _spy_predicate)

    answer_mod.answer(
        "q", bundle_dir=bundle_dir, llm=_FakeLLM(reply="ok"), fts_index=recording_index
    )

    assert walk_calls == [bundle_dir]


# --- sensitivity-fail-closed-filter, S3b: _assemble_context defense-in-depth --


def test_assemble_context_skips_a_blocked_cid_directly(tmp_path: Path) -> None:
    """`_assemble_context` itself (not just the hit-seam filter upstream)
    skips any `concept_id` present in `blocked`, even though its document is
    perfectly readable and parseable -- defense-in-depth (spec: Exclusion,
    Not Redaction): none of that concept's content, full or partial, ever
    appears in the assembled context."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    _write_doc(
        bundle_dir / "concepts" / "open.md",
        title="Open",
        body="dichotomyzz private note",
        sensitivity_value="private",
    )

    context_blocks, citations = answer_mod._assemble_context(
        bundle_dir,
        ["concepts/secret", "concepts/open"],
        blocked=frozenset({"concepts/secret"}),
    )

    assert citations == [answer_mod.Citation(concept_id="concepts/open", title="Open")]
    assert not any("confidential note" in block for block in context_blocks)
    assert any("private note" in block for block in context_blocks)


def test_assemble_context_default_blocked_is_empty_and_skips_nothing(
    tmp_path: Path,
) -> None:
    """Omitting `blocked` (default empty frozenset) assembles every concept
    id unchanged -- a no-op, matching pre-S3b behavior byte-for-byte."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="A", body="dichotomyzz")

    context_blocks, citations = answer_mod._assemble_context(bundle_dir, ["concepts/a"])

    assert citations == [answer_mod.Citation(concept_id="concepts/a", title="A")]
    assert len(context_blocks) == 1


# --- correction batch (post-4R-review), FIX 2: walk-bypass leak -----------


def test_assemble_context_independently_excludes_a_doc_the_walk_never_saw(
    tmp_path: Path,
) -> None:
    """`_assemble_context` re-checks EACH doc's own frontmatter at re-read
    time, independent of the precomputed `blocked` set -- a confidential
    concept that the `okf._iter_docs` walk silently missed (e.g. an
    unlistable subtree, `okf.py`'s documented `_walk_errors` case) but that
    is still directly readable by path MUST still be excluded, never reach
    the assembled context (correction batch, post-4R-review FIX 2: R4
    walk-bypass leak, defense-in-depth)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    _write_doc(
        bundle_dir / "concepts" / "open.md",
        title="Open",
        body="dichotomyzz private note",
        sensitivity_value="private",
    )

    # `blocked` is EMPTY -- simulating the walk-based predicate never having
    # seen "concepts/secret" at all (not merely a no-op filter upstream, as
    # the existing walk-bypass test above already covers).
    context_blocks, citations = answer_mod._assemble_context(
        bundle_dir, ["concepts/secret", "concepts/open"], blocked=frozenset()
    )

    cited_ids = {citation.concept_id for citation in citations}
    assert "concepts/secret" not in cited_ids
    assert "concepts/open" in cited_ids
    assert not any("confidential note" in block for block in context_blocks)
    assert any("private note" in block for block in context_blocks)


def test_assemble_context_include_confidential_skips_the_independent_recheck(
    tmp_path: Path,
) -> None:
    """`include_confidential=True` skips the independent per-doc re-check
    too -- the escape flag restores full participation byte-for-byte, not
    just at the upstream hit-seam filter."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )

    context_blocks, citations = answer_mod._assemble_context(
        bundle_dir,
        ["concepts/secret"],
        blocked=frozenset(),
        include_confidential=True,
    )

    assert citations == [
        answer_mod.Citation(
            concept_id="concepts/secret", title="Secret", confidential=True
        )
    ]
    assert any("confidential note" in block for block in context_blocks)


def test_confidential_doc_invisible_to_the_walk_is_still_excluded_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: even if `sensitivity.sensitive_concept_ids` itself missed
    a confidential doc entirely (simulating an unlistable subtree the walk
    silently dropped), `answer()` still never sends its content to the LLM,
    because `_assemble_context`'s independent per-doc re-check catches it at
    the actual send point (correction batch, post-4R-review FIX 2)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/secret", score=1.0)]
    )
    llm = _FakeLLM(reply="should never be reached")

    def _blind_predicate(_bundle_dir: Path, **_kwargs: object) -> frozenset[str]:
        return frozenset()  # the walk saw nothing -- simulates a dropped subtree

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", _blind_predicate)

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert llm.calls == []


def test_confidential_cid_that_slips_past_the_hit_seam_filter_is_still_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense-in-depth end-to-end: even if `lifecycle.filter_hits` were a
    no-op (simulating a confidential concept that slipped past the hit-seam
    filter), `answer()`'s guarded re-read at `_assemble_context` still
    excludes it -- proving the exclusion is not solely dependent on the
    upstream hit-seam filter (spec: Exclusion, Not Redaction)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/secret", score=1.0)]
    )
    llm = _FakeLLM(reply="should never be reached")

    def _noop_filter_hits(hits: list[object], _blocked: frozenset[str]) -> list[object]:
        return hits

    monkeypatch.setattr(lifecycle, "filter_hits", _noop_filter_hits)

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=recording_index
    )

    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH


# --- #193: internal concept_id scaffolding must not reach the reader -------


def _answer_with_reply(tmp_path: Path, reply: str) -> answer_mod.AnswerResult:
    """Drive a successful one-hit `answer()` whose LLM returns `reply`.

    A helper rather than five copies of the same four-line setup, so each
    test below is its scaffolding shape plus its assertion.
    """
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    with fts.build_index(bundle_dir) as idx:
        return answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(reply=reply),
            fts_index=idx,
        )


def test_bracketed_concept_id_scaffolding_is_stripped(tmp_path: Path) -> None:
    """`[concept_id: ...]` must not survive into the answer text.

    This is the exact shape reported in #193. It leaks because the context
    blocks label each concept that way and the prompt used to invite citing
    by id, so the model was copying the label it was shown.
    """
    result = _answer_with_reply(
        tmp_path,
        "MCP was launched on 2024-11. This information is based on "
        "[concept_id: sources/mcp-launch].",
    )

    assert "concept_id" not in result.answer
    assert result.answer == "MCP was launched on 2024-11. This information is based on."


def test_parenthesised_concept_id_scaffolding_is_stripped(tmp_path: Path) -> None:
    """The other shape #193 reports, `(concept_id: ...)`, is stripped too.

    Pinned separately from the bracketed form rather than parametrized: the
    delimiters need different character classes to terminate on, so one
    passing says nothing about the other.
    """
    result = _answer_with_reply(
        tmp_path,
        "It can reference other files or scripts. (concept_id: concepts/skills-in-ai)",
    )

    assert "concept_id" not in result.answer
    assert result.answer == "It can reference other files or scripts."


def test_bare_concept_id_scaffolding_is_stripped(tmp_path: Path) -> None:
    """An undelimited `concept_id: <id>` is stripped, and stops at the id.

    The delimited forms have an unambiguous end; this one has to guess, and
    it guesses conservatively -- terminating at whitespace or sentence
    punctuation, so a mis-scoped match truncates the identifier rather than
    eating the rest of the sentence.
    """
    result = _answer_with_reply(
        tmp_path,
        "Stoicism is a philosophy, per concept_id: concepts/stoicism. It endures.",
    )

    assert "concept_id" not in result.answer
    assert result.answer == "Stoicism is a philosophy, per. It endures."


def test_bare_scaffolding_consumes_an_id_containing_a_dot(tmp_path: Path) -> None:
    """A dot INSIDE the id must not end the match.

    Concept ids can carry one: a Source's slug is its filename stem with only
    the final extension removed, so ingesting `notes.v2.txt` yields
    `sources/notes.v2`. A terminator class that treats every dot as
    sentence-final stops mid-identifier and leaves the remainder welded onto
    the preceding word -- `...documented in.v2 for reference` -- which is
    corruption of neighbouring prose, not the harmless truncation the
    conservative-matching rationale describes.

    The companion of the test above: that one proves a SENTENCE-ending dot
    still terminates, this one proves an id-internal dot does not. Both
    directions are needed, since a rule that satisfies either alone is easy
    to write by accident.
    """
    result = _answer_with_reply(
        tmp_path,
        "It is documented in concept_id: sources/notes.v2 for reference.",
    )

    assert "concept_id" not in result.answer
    assert ".v2" not in result.answer
    assert result.answer == "It is documented in for reference."


def test_scaffolding_carrying_a_title_is_stripped_whole(tmp_path: Path) -> None:
    """The context-block label form, id plus em-dashed title, is one unit.

    This is the literal string `_assemble_context` writes, so it is the shape
    a model is most likely to echo verbatim.
    """
    result = _answer_with_reply(
        tmp_path,
        "Control is the core idea [concept_id: concepts/stoicism — Stoicism].",
    )

    assert "concept_id" not in result.answer
    assert "Stoicism]" not in result.answer
    assert result.answer == "Control is the core idea."


def test_stripping_preserves_line_structure(tmp_path: Path) -> None:
    """Removing scaffolding must not join two lines into one.

    The answer is rendered as-is and may be markdown, so absorbing the
    newline before a scaffold would silently reflow a list or a heading into
    the previous paragraph.
    """
    result = _answer_with_reply(
        tmp_path,
        "First point.\n[concept_id: concepts/stoicism] Second point.",
    )

    assert result.answer == "First point.\nSecond point."


def test_stripping_leaves_the_citation_list_intact(tmp_path: Path) -> None:
    """Ids leave the PROSE, not the answer's provenance.

    The structured citations are the supported way to trace an answer, and
    #193's whole argument is that the inline copy is redundant BECAUSE this
    exists. A strip that also emptied this would remove traceability instead
    of tidying it.

    Both halves are asserted, and the prose half is what makes this a test of
    the strip at all: citations are built by `_assemble_context` from the
    fused ids and never parsed back out of the reply, so a version that
    checked only the citation list would pass unchanged with the whole #193
    feature reverted -- pinning an independent code path while appearing to
    guard this one.
    """
    result = _answer_with_reply(tmp_path, "An answer [concept_id: concepts/stoicism].")

    assert result.answer == "An answer."
    assert [c.concept_id for c in result.citations] == ["concepts/stoicism"]


def test_scaffolding_inside_a_code_fence_is_stripped_too(tmp_path: Path) -> None:
    """The strip is content-blind, and that is pinned rather than accidental.

    An answer explaining the bundle's own shape could legitimately quote the
    context-block label inside a fence, and this removes it there as well --
    corrupting the example it was illustrating.

    Accepted rather than solved. Making the strip fence-aware means tracking
    markdown state across a stream the model controls, which is a parser's
    worth of machinery guarding a BACKSTOP whose real job is done by the
    prompt. The failure it prevents is a permanent one -- `query --save`
    files the answer as a bundle concept -- while the failure it causes is a
    mangled illustration in a single reply.

    Pinned as a characterization test so the trade is visible to whoever
    reconsiders it, and so a future fence-aware version fails here loudly
    instead of quietly changing what this module promises.
    """
    result = _answer_with_reply(
        tmp_path,
        "A source block is headed like this:\n\n"
        "```\n[concept_id: sources/example — Example]\n```",
    )

    assert "concept_id" not in result.answer
    assert result.answer == "A source block is headed like this:\n\n```\n\n```"


def test_ordinary_prose_is_returned_unchanged(tmp_path: Path) -> None:
    """A reply with no scaffolding must pass through byte-identical.

    The stripping is a backstop for a prompt the model may not obey; it must
    not become a rewriting pass that touches compliant answers.
    """
    reply = "Stoicism  teaches the dichotomy of control.\n\n- One\n- Two"

    result = _answer_with_reply(tmp_path, reply)

    assert result.answer == reply


def test_system_prompt_does_not_invite_inlining_concept_ids(tmp_path: Path) -> None:
    """The prompt must stop asking for what the strip then removes.

    The leak's root cause was an instruction to "cite the concepts you rely
    on by their concept id" -- the model was obeying. Stripping alone would
    leave the prompt fighting the post-processor on every call.
    """
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM()

    with fts.build_index(bundle_dir) as idx:
        answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx)

    system = llm.calls[0][0]["content"]
    assert "by their concept id" not in system
    assert "concept id" in system


# --- issue #240: the confidential local exemption on the query path ----------


def test_local_exemption_true_restores_a_confidential_concept_to_the_answer(
    tmp_path: Path,
) -> None:
    """With a verified-local backend (`local_exemption=True`), a
    `confidential` concept participates in retrieval and reaches the prompt
    without `--include-confidential` (#240).

    `sensitivity` governs what LEAVES the machine; an Ollama on loopback is
    not egress, so the gate has nothing to protect against here."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/secret", score=1.0)]
    )
    llm = _FakeLLM(reply="answered from the confidential concept")

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        local_exemption=True,
    )

    assert result.citations == [
        answer_mod.Citation(
            concept_id="concepts/secret", title="Secret", confidential=True
        )
    ]
    assert any("confidential note" in m["content"] for m in llm.calls[0])


def test_local_exemption_defaults_to_false_on_answer(tmp_path: Path) -> None:
    """`answer`'s `local_exemption` defaults to `False`, so a caller that
    cannot prove the backend is local keeps today's blanket blocking (#240).

    Fail-closed by omission: the CLI is the only layer that knows which
    client the send will use, and a library caller that never says anything
    must never be assumed to be local."""
    parameter = inspect.signature(answer_mod.answer).parameters["local_exemption"]
    assert parameter.default is False

    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/secret", score=1.0)]
    )

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=_FakeLLM(reply="unused"),
        fts_index=recording_index,
    )

    assert result.citations == []


def test_assemble_context_local_exemption_skips_the_independent_recheck(
    tmp_path: Path,
) -> None:
    """The walk-independent per-doc re-check honors the exemption too (#240).

    Both layers must agree: if the upstream filter admitted a confidential
    concept because the backend is local, a re-check that still dropped it
    at the send point would make the exemption silently ineffective for
    every concept, since this is the layer that actually assembles the
    prompt."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        body="dichotomyzz confidential note",
        sensitivity_value="confidential",
    )

    context_blocks, citations = answer_mod._assemble_context(
        bundle_dir,
        ["concepts/secret"],
        local_exemption=True,
    )

    assert any("confidential note" in block for block in context_blocks)
    assert [c.concept_id for c in citations] == ["concepts/secret"]


# --- #569: citations carry the confidential-disclosure bit ------------------


def test_citation_marks_an_explicitly_confidential_source(tmp_path: Path) -> None:
    """#569: the write path discloses confidential content, the read path
    did not. `_assemble_context` re-reads every cited doc's frontmatter
    anyway, so the `Citation` now carries whether that doc is EXPLICITLY
    `sensitivity: confidential` -- transparency, not a gate, so it mirrors
    `_commit_has_confidential`'s explicit-value-only posture: a doc with no
    `sensitivity` field is NOT marked (a false 'confidential' alarm on an
    unlabeled doc would train users to ignore the real ones)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "secret.md",
        title="Secret",
        sensitivity_value="confidential",
    )
    _write_doc(bundle_dir / "concepts" / "open.md", title="Open")
    recording_index = _RecordingIndex(
        hits=[
            fts.FtsHit(concept_id="concepts/secret", score=1.0),
            fts.FtsHit(concept_id="concepts/open", score=0.9),
        ]
    )
    llm = _FakeLLM(reply="answered")

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        include_confidential=True,
    )

    by_id = {c.concept_id: c for c in result.citations}
    assert by_id["concepts/secret"].confidential is True
    assert by_id["concepts/open"].confidential is False


# --- Insight context marking (issue #570) -----------------------------------


def test_assemble_context_labels_an_insight_as_filed_synthesis(
    tmp_path: Path,
) -> None:
    """An `Insight`-typed context block carries the filed-synthesis note in
    its label, so the synthesizer knows which of its context legs stand on
    model output rather than on a source-backed concept (issue #570). A
    Concept block stays label-identical to before."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "insights" / "earlier-answer.md",
        doc_type="Insight",
        title="Earlier Answer",
        body="A synthesis filed by an earlier query.",
    )
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="A source-backed concept.",
    )

    context_blocks, citations = answer_mod._assemble_context(
        bundle_dir, ["insights/earlier-answer", "concepts/stoicism"]
    )

    assert len(context_blocks) == 2
    assert "filed synthesis" in context_blocks[0]
    assert context_blocks[1] == (
        "[concept_id: concepts/stoicism — Stoicism]\nA source-backed concept."
    )
    assert [c.concept_id for c in citations] == [
        "insights/earlier-answer",
        "concepts/stoicism",
    ]


# --- citation attribution (#753, citation half) -----------------------------
#
# Before this feature `citations` was the retrieval set renamed: it was built
# by `_assemble_context` BEFORE `llm.chat` ran and was never compared to the
# reply, so every answer cited exactly `limit` concepts whatever it actually
# said. Measured over the 170 stored answers in `evals/query_title/results/`:
# 170/170 cited exactly 5, in only 4 distinct sets, and not one answer had all
# five citations supported by its own text. `query --save` then wrote all five
# as permanent provenance (`cli/main.py`), so the defect outlived the screen.
#
# The mechanism is model self-attribution in the SAME chat call: the context
# blocks are numbered and the model closes with a `USED:` line naming the ones
# it drew on. Numbers, never concept ids -- #193's leak was the model copying
# the `[concept_id: ...]` label it was shown, and re-introducing ids as the
# attribution vocabulary would re-open exactly that.


def _answer_over_three(tmp_path: Path, reply: str) -> answer_mod.AnswerResult:
    """Drive a successful THREE-hit `answer()` whose LLM returns `reply`.

    Three docs, not one, because every assertion here is about WHICH subset
    survives -- a one-hit fixture cannot tell "kept the reported block" from
    "kept everything", which is the distinction under test.
    """
    bundle_dir = tmp_path / "bundle"
    for slug, title in (("alpha", "Alpha"), ("beta", "Beta"), ("gamma", "Gamma")):
        _write_doc(
            bundle_dir / "concepts" / f"{slug}.md",
            title=title,
            body=f"dichotomyzz {slug} body",
        )
    with fts.build_index(bundle_dir) as idx:
        return answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(reply=reply),
            fts_index=idx,
        )


def test_context_blocks_are_numbered_for_attribution(tmp_path: Path) -> None:
    """The user message numbers each context block `[1]`, `[2]`, ...

    The system prompt has always called them "the numbered CONTEXT concepts",
    but nothing ever numbered them -- the blocks were headed by
    `[concept_id: ...]` alone. Attribution needs a vocabulary the model can
    use WITHOUT naming an id, so the number is that vocabulary and this pins
    that it actually reaches the prompt.
    """
    bundle_dir = tmp_path / "bundle"
    for slug in ("alpha", "beta"):
        _write_doc(
            bundle_dir / "concepts" / f"{slug}.md",
            title=slug.capitalize(),
            body=f"dichotomyzz {slug}",
        )
    llm = _FakeLLM(reply="An answer.")
    with fts.build_index(bundle_dir) as idx:
        answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx)

    user_content = llm.calls[0][1]["content"]
    assert "[1]" in user_content
    assert "[2]" in user_content


def test_reported_blocks_filter_the_citations(tmp_path: Path) -> None:
    """`USED: 1, 3` cites the first and third block, and nothing else.

    This is the whole feature: the citation list becomes a function of what
    the answer says it drew on, instead of a function of retrieval rank.
    """
    result = _answer_over_three(tmp_path, "An answer.\n\nUSED: 1, 3")

    assert len(result.citations) == 2
    assert result.attribution == "reported"


def test_the_attribution_line_never_reaches_the_prose(tmp_path: Path) -> None:
    """The marker is machinery, so it is stripped like #193's ids.

    `query --save` files `AnswerResult.answer` as a real bundle concept, so a
    surviving marker is not a cosmetic blemish shown once -- it is written
    into the bundle permanently.
    """
    result = _answer_over_three(tmp_path, "An answer.\n\nUSED: 1, 3")

    assert "USED" not in result.answer
    assert result.answer == "An answer."


def test_an_answer_reporting_no_support_cites_nothing(tmp_path: Path) -> None:
    """`USED: none` is a REPORT, not a parse failure, and it empties the list.

    This is #753's own specimen: the model answered from its own knowledge and
    the caller stapled five citations to it. An honest "I drew on none of
    these" must produce zero citations -- which also makes `query --save`
    refuse the filing outright, since it requires non-empty provenance.
    """
    result = _answer_over_three(tmp_path, "A general essay.\n\nUSED: none")

    assert result.citations == []
    assert result.attribution == "reported"


def test_an_absent_marker_keeps_every_citation(tmp_path: Path) -> None:
    """A model that never reports falls back to today's behavior exactly.

    Deliberately NOT fail-closed. Emptying the citations of every
    non-compliant model would turn a citation-precision fix into a silent
    outage for anyone on a weaker one, and the compliance rate is the thing
    the eval harness is there to measure before that trade is even offered.
    """
    result = _answer_over_three(tmp_path, "An answer with no marker at all.")

    assert len(result.citations) == 3
    assert result.attribution == "absent"


def test_a_malformed_marker_keeps_every_citation(tmp_path: Path) -> None:
    """Garbage after `USED:` is indistinguishable from not reporting."""
    result = _answer_over_three(tmp_path, "An answer.\n\nUSED: banana")

    assert len(result.citations) == 3
    assert result.attribution == "unparsed"


def test_out_of_range_indices_are_dropped_not_trusted(tmp_path: Path) -> None:
    """A number naming no block cites nothing, rather than indexing wildly.

    Three blocks were sent, so `9` is a hallucinated slot. It must not wrap,
    clamp, or raise -- and with no valid index left the reply carries no
    usable report, so the conservative fallback applies.
    """
    result = _answer_over_three(tmp_path, "An answer.\n\nUSED: 9")

    assert len(result.citations) == 3
    assert result.attribution == "unparsed"


def test_a_partially_valid_report_keeps_only_its_valid_half(tmp_path: Path) -> None:
    """`USED: 2, 9` cites block 2 -- the real index survives the fake one."""
    result = _answer_over_three(tmp_path, "An answer.\n\nUSED: 2, 9")

    assert len(result.citations) == 1
    assert result.attribution == "reported"


def test_citations_keep_fused_rank_order_after_filtering(tmp_path: Path) -> None:
    """Filtering is a subset, never a reordering.

    The order is the documented contract of `AnswerResult.citations` ("in
    fused-rank order") and the CLI renders the list in it, so a filter that
    returned the model's own listing order would quietly re-rank the output
    by whatever sequence the model happened to type.
    """
    result = _answer_over_three(tmp_path, "An answer.\n\nUSED: 3, 1")
    filtered = [c.concept_id for c in result.citations]

    unfiltered = _answer_over_three(tmp_path, "An answer.")
    expected = [c.concept_id for c in unfiltered.citations]

    assert filtered == [expected[0], expected[2]]


def test_attribution_defaults_to_absent_on_a_no_match(tmp_path: Path) -> None:
    """A short-circuited answer never called the LLM, so nothing reported."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "", bundle_dir=bundle_dir, llm=_FakeLLM(), fts_index=idx
        )

    assert result.attribution == "absent"


def test_the_last_marker_wins_when_a_reply_carries_several(tmp_path: Path) -> None:
    """A reply restating the format before emitting the real line attributes
    from the LAST one.

    The instruction asks for a CLOSING line, so the final marker is the one
    that was meant; an earlier one is the model quoting the format back. The
    rule was documented from the first version of `_split_attribution` but
    nothing exercised it, so a regression to `search()` (first match) would
    have passed the whole suite.
    """
    # BOTH lines must start with the keyword, or the regex matches only one
    # and the test cannot tell first from last. An earlier revision opened
    # with "I will end with a line like USED: 1", which is not at a line
    # start, so a mutation to `matches[0]` survived it -- the exact vacuous
    # test the finding was about, reproduced while fixing the finding.
    result = _answer_over_three(
        tmp_path,
        "USED: 1\n\nThe actual answer.\n\nUSED: 2, 3",
    )

    assert len(result.citations) == 2
    unfiltered = _answer_over_three(tmp_path, "An answer.")
    expected = [c.concept_id for c in unfiltered.citations]
    assert [c.concept_id for c in result.citations] == [expected[1], expected[2]]


def test_prose_after_the_marker_is_stitched_onto_the_prose_before_it(
    tmp_path: Path,
) -> None:
    """Characterization: a marker mid-reply splices, it does not truncate.

    `_split_attribution` removes the matched line and joins what surrounded
    it, so a model that writes past its own closing line has the remainder
    welded onto the preceding paragraph rather than dropped.

    Pinned rather than fixed. Truncating at the marker instead would DISCARD
    model output on a reply that merely mis-ordered its line, which is the
    worse failure -- and the alternative, refusing to attribute unless the
    marker is last, throws away a correct report over formatting. The trade
    is visible here so whoever reconsiders it fails loudly.
    """
    result = _answer_over_three(tmp_path, "First part.\n\nUSED: 1\n\nSecond part.")

    assert "USED" not in result.answer
    assert "First part." in result.answer
    assert "Second part." in result.answer
    assert len(result.citations) == 1


def test_context_block_count_reports_what_the_model_was_shown(
    tmp_path: Path,
) -> None:
    """`context_block_count` is the blocks SENT, not the concepts fused.

    `fused_count` is computed before `_assemble_context`'s per-concept skip
    guard, so on a bundle where a fused hit is unreadable the two differ --
    and any caller reporting "the model drew on none of N concepts" from
    `fused_count` would overstate N.
    """
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "alpha.md", title="Alpha", body="dichotomyzz alpha"
    )
    _write_doc(
        bundle_dir / "concepts" / "beta.md", title="Beta", body="dichotomyzz beta"
    )
    with fts.build_index(bundle_dir) as idx:
        # Delete one indexed doc so its fused hit survives the fuse but is
        # skipped at the guarded re-read -- the exact divergence under test.
        (bundle_dir / "concepts" / "beta.md").unlink()
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(reply="An answer."),
            fts_index=idx,
        )

    assert result.fused_count == 2
    assert result.context_block_count == 1


def test_context_block_count_is_zero_on_a_short_circuit(tmp_path: Path) -> None:
    """A short-circuited answer assembled no context, so it counts none.

    The field's docstring claims `0` for every early return; nothing proved
    it, and a default asserted only in prose is a contract the next edit can
    break silently. Both short-circuits are exercised — the empty question,
    which returns before retrieval runs at all, and the zero-hit search,
    which returns after it.
    """
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    with fts.build_index(bundle_dir) as idx:
        empty_question = answer_mod.answer(
            "", bundle_dir=bundle_dir, llm=_FakeLLM(), fts_index=idx
        )
        no_hits = answer_mod.answer(
            "dichotomyzz", bundle_dir=bundle_dir, llm=_FakeLLM(), fts_index=idx
        )

    assert empty_question.no_match_cause == "empty_query"
    assert empty_question.context_block_count == 0
    assert no_hits.no_match_cause == "zero_hits"
    assert no_hits.context_block_count == 0


# --- pre-synthesis sufficiency check (#760) ---------------------------------
#
# #753 ruled "below a relevance floor, refuse". #760 measured that no floor
# exists on any retrieval signal -- `fusion.fuse` is RRF and encodes position
# only, and `VecHit.distance` reports topical relatedness, which is the
# defect's own premise. It proposed instead a cheap model call over the
# assembled context, BEFORE synthesis.
#
# Measured in `evals/query_sufficiency/` (qwen3:8b, 10 runs, 400 checks): the
# evidence-first formulation -- quote the sentence that answers, or NONE --
# refused 0 of 10 grounded questions across 100 grounded checks while
# refusing all 10 adjacent ones, including the three the shipped `USED:`
# attribution does not catch and the one #753 itself reports. A yes/no
# formulation false-refused a grounded question and was rejected.


class _ScriptedLLM:
    """An `LLMBackend` returning queued replies in order, recording each call.

    `answer()` may now make TWO chat calls -- the sufficiency check and then
    synthesis -- so a fixture returning one fixed string can no longer
    distinguish them. Replies are consumed in order; running out is an error
    rather than a repeat, because a test that silently reused the last reply
    would pass while asserting the wrong call.
    """

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError(
                f"_ScriptedLLM ran out of replies on call {len(self.calls)}"
            )
        return self._replies.pop(0)


class _RaisingOnceLLM:
    """Raises on the FIRST chat call, then returns `reply`.

    Models a sufficiency check whose backend hiccups while synthesis would
    still succeed.
    """

    def __init__(self, exc: Exception, reply: str) -> None:
        self._exc = exc
        self._reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            raise self._exc
        return self._reply


def _bundle_with_two(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "bundle"
    for slug in ("alpha", "beta"):
        _write_doc(
            bundle_dir / "concepts" / f"{slug}.md",
            title=slug.capitalize(),
            body=f"dichotomyzz {slug} body",
        )
    return bundle_dir


def test_an_insufficient_context_refuses_before_synthesis(tmp_path: Path) -> None:
    """`NONE` from the check means synthesis is never called at all.

    Not merely "the answer is discarded": the whole point of placing this
    BEFORE synthesis rather than reading the shipped `USED:` line after it is
    that the expensive call is never paid, and that the user is never shown
    an ungrounded essay to ignore.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("NONE")
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )

    assert len(llm.calls) == 1
    assert result.no_match_cause == "insufficient_context"
    assert result.citations == []
    assert result.llm_invoked is False


def test_a_sufficient_context_proceeds_to_synthesis(tmp_path: Path) -> None:
    """A quotation from the check lets the answer through, unchanged."""
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("dichotomyzz alpha body", "The answer.\n\nUSED: 1")
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )

    assert len(llm.calls) == 2
    assert result.no_match_cause == "none"
    assert result.answer == "The answer."
    assert len(result.citations) == 1


def test_the_check_is_off_by_default_and_costs_nothing(tmp_path: Path) -> None:
    """A caller that never passes the flag makes exactly ONE chat call.

    The library default is `False` so every existing caller -- the eval
    harnesses included -- keeps byte-identical behavior and pays no added
    latency. The workspace default is where it is turned ON.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("The answer.")
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx
        )

    assert len(llm.calls) == 1
    assert result.no_match_cause == "none"


def test_a_failing_check_falls_through_to_synthesis(tmp_path: Path) -> None:
    """A backend error in the CHECK must not become a refusal.

    An error is not evidence of insufficiency, and refusing on one would deny
    answers for infrastructure reasons. Fails OPEN deliberately: the shipped
    `USED:` attribution still strips the citations off an ungrounded answer,
    so the backstop that made this check optional is exactly what covers its
    failure.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _RaisingOnceLLM(OllamaError("transient"), "The answer.")
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )

    assert len(llm.calls) == 2
    assert result.no_match_cause == "none"
    assert result.answer == "The answer."


def test_a_fatal_backend_error_in_the_check_still_propagates(tmp_path: Path) -> None:
    """Failing open covers TRANSIENT errors, never a dead backend.

    `OllamaUnavailable` and its siblings are the fatal family issue #209
    keeps propagating rather than degrading, and swallowing one here would
    turn "Ollama is not running" into a silently answered question against a
    backend that answered nothing.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _RaisingOnceLLM(OllamaUnavailable("down"), "The answer.")
    with fts.build_index(bundle_dir) as idx, pytest.raises(OllamaUnavailable):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )


def test_the_check_reads_the_same_numbered_context_as_synthesis(
    tmp_path: Path,
) -> None:
    """The check judges exactly what synthesis would be given.

    A check reading a different context than the one that produces the
    answer is judging a different question, and would drift the moment
    either assembly changed.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("dichotomyzz alpha body", "The answer.")
    with fts.build_index(bundle_dir) as idx:
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )

    check_user = llm.calls[0][1]["content"]
    synthesis_user = llm.calls[1][1]["content"]
    assert check_user == synthesis_user
    assert "[1]" in check_user


def test_the_check_never_runs_when_no_context_was_assembled(tmp_path: Path) -> None:
    """A zero-hit question short-circuits before any chat call, as before.

    There is nothing for the check to judge, and paying for a call to be told
    so would add latency to the one path that is already free.
    """
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    llm = _ScriptedLLM()
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )

    assert llm.calls == []
    assert result.no_match_cause == "zero_hits"


def test_a_quotation_containing_the_sentinel_is_not_a_refusal(
    tmp_path: Path,
) -> None:
    """The refusal token must BE the reply, never merely appear in it.

    The check asks the model to QUOTE the sentence that answers, so any word
    of the corpus can come back inside a legitimate quotation -- `none`
    included. Matching the sentinel by substring would read this answer-
    bearing quotation as a refusal and silently refuse an answerable
    question, which is the false refusal the whole mechanism was chosen to
    avoid. Prose in `_context_holds_the_answer` claimed this; nothing tested
    it, and a substring mutation survived the suite.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM(
        "None of the participants objected to the dichotomyzz.", "The answer."
    )
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )

    assert len(llm.calls) == 2
    assert result.no_match_cause == "none"
    assert result.answer == "The answer."


def test_a_decorated_sentinel_is_still_a_refusal(tmp_path: Path) -> None:
    """`"NONE."`, `**none**`, ` none ` — all refusals.

    The other side of the same rule. A model asked for one bare word returns
    it wrapped in whatever formatting it was in the mood for, and treating a
    quoted or bolded sentinel as a quotation would let every such reply
    through as sufficient.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    for decorated in ('"NONE."', "**none**", "  none  \n", "`None`"):
        llm = _ScriptedLLM(decorated)
        with fts.build_index(bundle_dir) as idx:
            result = answer_mod.answer(
                "dichotomyzz",
                bundle_dir=bundle_dir,
                llm=llm,
                fts_index=idx,
                sufficiency_check=True,
            )
        assert result.no_match_cause == "insufficient_context", decorated
        assert len(llm.calls) == 1, decorated


def test_an_empty_check_reply_lets_the_answer_through(tmp_path: Path) -> None:
    """Characterization: a reply that strips to nothing is NOT a refusal.

    The verdict is "did the model name the refusal sentinel", so an empty
    reply — no quotation and no explicit `NONE` — passes. Pinned rather than
    changed, for two reasons.

    First, this is the exact rule the winning arm was measured under
    (`evals/query_sufficiency/`, 400 checks): treating empty as a refusal
    ships a refusal path that measurement never saw, and this repo's whole
    posture on #753 is that treatments are adopted on evidence.

    Second, it matches the module's failure direction. `_context_holds_the_answer`
    already fails OPEN on a transient backend error, because an absence of
    evidence is not evidence of insufficiency — and the `USED:` attribution
    still strips the citations off whatever synthesis produces, so the
    backstop that makes this check optional at all is what covers the case.

    Raised by the reliability lens as untested. It is now tested, and the
    trade is visible to whoever reconsiders it.
    """
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("   \n  ", "The answer.")
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            sufficiency_check=True,
        )

    assert len(llm.calls) == 2
    assert result.no_match_cause == "none"
    assert result.answer == "The answer."
