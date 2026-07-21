"""Unit tests for `retrieval/answer.py`: the cited answer library.

`answer()` composes three archived seams end-to-end: `state.fts.build_index`
(retrieve), a per-hit guarded `okf.load_frontmatter` re-read (assemble), and
an injected `llm.LLMBackend` (answer). All tests use a `tmp_path` bundle and
a structural fake `LLMBackend` -- zero network, zero real Ollama process.
"""

import ast
import dataclasses
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.graph.base import Edge, GraphStore
from openkos.llm.base import EMBED_DIM, Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.retrieval import answer as answer_mod
from openkos.retrieval import fusion, graph_retrieve
from openkos.retrieval.fusion import GraphHit
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
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns a fixed reply."""

    def __init__(self, reply: str = "the reply") -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.reply


class _RecordingIndex:
    """A fake `FtsIndex` context manager: records `search()` args, returns fixed hits."""

    def __init__(
        self, hits: list[fts.FtsHit], skipped: list[str] | None = None
    ) -> None:
        self._hits = hits
        self.calls: list[tuple[str, int]] = []
        self.skipped = skipped if skipped is not None else []

    def search(self, query: str, limit: int = 10) -> list[fts.FtsHit]:
        self.calls.append((query, limit))
        return self._hits

    def __enter__(self) -> "_RecordingIndex":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeEmbedder:
    """A structural `Embedder`: records every `embed()` call's texts, returns
    a fixed `EMBED_DIM`-float vector per input (exact Protocol signature,
    Engram #1363 -- `Sequence[str]`, never narrowed to `list[str]`)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.0] * EMBED_DIM for _ in texts]


class _FakeVectorStore:
    """A structural `VectorStore`: implements all 5 Protocol methods.
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

    def query(self, embedding: Sequence[float], k: int) -> list[VecHit]:
        self.calls.append((list(embedding), k))
        if self._raises is not None:
            raise self._raises
        return self._hits

    def meta_hashes(self) -> dict[str, str]:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def prune(self, concept_id: str) -> None:
        raise NotImplementedError  # pragma: no cover -- unused by answer()

    def close(self) -> None:
        pass


class _FakeGraphStore:
    """A minimal `GraphStore` fixture over an explicit node/edge list."""

    def __init__(self, nodes: list[str], edges: list[Edge]) -> None:
        self._nodes = nodes
        self._edges = edges

    def nodes(self) -> list[str]:
        return self._nodes

    def edges(self) -> list[Edge]:
        return self._edges

    def neighbors(self, concept_id: str) -> list[str]:
        return [edge.target_id for edge in self._edges if edge.source_id == concept_id]


class _FakeGraphStoreCM:
    """A context-manager wrapper mirroring `SqliteGraphStore`'s contract:
    `with build_graph(bundle) as store: ...` -- so a monkeypatched
    `build_graph` can return a fake `GraphStore` through the same `with`
    protocol `_graph_search` uses."""

    def __init__(self, store: GraphStore) -> None:
        self._store = store

    def __enter__(self) -> GraphStore:
        return self._store

    def __exit__(self, *exc_info: object) -> None:
        return None


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

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert result.answer == "Stoicism teaches the dichotomy of control."
    assert len(llm.calls) == 1
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    ]
    assert result.fts_hit_count == 1
    assert result.llm_invoked is True
    assert result.no_match_cause == "none"


def test_caller_omits_limit_search_called_with_pool_ten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`limit` defaults to 5, but each retriever is called with
    `pool_limit = max(limit, 10)` -- `FtsIndex.search` is forwarded `10`,
    not the display `limit` itself (spec: Default Retrieval Limit)."""
    bundle_dir = tmp_path / "bundle"
    recording_index = _RecordingIndex(hits=[])
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)

    answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=_FakeLLM())

    assert recording_index.calls == [("dichotomyzz", 10)]


def test_caller_omits_limit_vector_store_query_called_with_pool_ten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Omitting `limit` also forwards `pool_limit=10` to
    `vector_store.query` (spec: Default Retrieval Limit)."""
    bundle_dir = tmp_path / "bundle"
    recording_index = _RecordingIndex(hits=[])
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    vector_store = _FakeVectorStore(hits=[])

    answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=_FakeLLM(),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
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

    answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

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
    """Both `FtsIndex.search` and `vector_store.query` are called, the fused
    list feeds context assembly, `llm.chat` is called exactly once, and
    `AnswerResult.answer` equals the LLM's response text (spec: Matching
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

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
    )

    assert embedder.calls == [["dichotomyzz"]]
    assert vector_store.calls == [([0.0] * EMBED_DIM, 10)]
    assert len(llm.calls) == 1
    assert result.answer == "the fused reply"


def test_dense_only_match_is_retrievable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
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
    )

    assert result.llm_invoked is True
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/epictetus", title="Epictetus")
    ]


def test_dense_only_hit_surfaces_within_truncated_limit_via_fused_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
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
        limit=2,
    )

    assert result.fused_count == 2
    assert [citation.concept_id for citation in result.citations] == [
        "concepts/dense-star",
        "concepts/one",
    ]
    assert len(llm.calls) == 1


def test_dense_and_fused_counts_reflect_retrieval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """3 dense hits and a fused list of 4 distinct `concept_id`s -> `dense_hit_count`
    equals 3 and `fused_count` equals 4 (spec: Dense and fused counts reflect
    retrieval)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/x", score=0.0)]
    )
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
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

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=_FakeLLM(),
        embedder=_FakeEmbedder(),
        vector_store=vector_store,
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

    result = answer_mod.answer("nonexistentqueryzz", bundle_dir=bundle_dir, llm=llm)

    assert llm.calls == []
    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert result.fts_hit_count == 0
    assert result.llm_invoked is False
    assert result.no_match_cause == "zero_hits"


def test_all_hits_unreadable_degrades_to_no_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every hit unreadable/unparseable at answer time -> zero-hit contract."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/vanished", score=0.0)]
    )
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    llm = _FakeLLM()

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert llm.calls == []
    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert result.fts_hit_count == 1
    assert result.llm_invoked is False
    assert result.no_match_cause == "all_unreadable"


def test_unparseable_frontmatter_hit_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    llm = _FakeLLM(reply="answered from stoicism only")

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert len(llm.calls) == 1
    assert result.answer == "answered from stoicism only"
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    ]


def test_multiple_surviving_hits_cite_in_rank_order_and_join_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    llm = _FakeLLM(reply="Stoicism was practiced by Epictetus.")

    result = answer_mod.answer("stoicism", bundle_dir=bundle_dir, llm=llm)

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
    assert f"{stoicism_block}\n\n{epictetus_block}" in user_content
    assert user_content.index(stoicism_block) < user_content.index(epictetus_block)


def test_one_hit_vanished_skips_it_and_still_answers_with_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    llm = _FakeLLM(reply="answered from stoicism only")

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert len(llm.calls) == 1
    assert result.answer == "answered from stoicism only"
    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    ]


def test_empty_question_never_invokes_llm_and_sets_empty_query_cause(
    tmp_path: Path,
) -> None:
    """A whitespace-only question never reaches `chat` and returns
    `no_match_cause="empty_query"`, distinct from `"zero_hits"`."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    llm = _FakeLLM()

    result = answer_mod.answer("   ", bundle_dir=bundle_dir, llm=llm)

    assert llm.calls == []
    assert result.fts_hit_count == 0
    assert result.llm_invoked is False
    assert result.no_match_cause == "empty_query"
    assert result.answer == answer_mod.NO_MATCH


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


def test_skip_notices_carried_on_matched_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-empty `FtsIndex.skipped` is carried onto `AnswerResult.skip_notices`
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
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    llm = _FakeLLM(reply="the reply")

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert result.skip_notices == skip_notices
    assert result.llm_invoked is True


def test_skip_notices_carried_on_no_match_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-empty `FtsIndex.skipped` is carried onto `AnswerResult.skip_notices`
    even on a no-match (zero-hit) path."""
    bundle_dir = tmp_path / "bundle"
    skip_notices = ["concepts/corrupt.md: skipped (unreadable)"]
    recording_index = _RecordingIndex(hits=[], skipped=skip_notices)
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    llm = _FakeLLM()

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert result.skip_notices == skip_notices
    assert result.no_match_cause == "zero_hits"


# --- Phase 3: zero-hit reclassification across both retrievers -----------


def test_zero_fts_and_zero_dense_hits_returns_no_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Zero hits from BOTH retrievers never calls `chat`, returns empty
    citations, and a non-empty no-match message (spec: No matching concepts
    found in either list)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(hits=[])
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    llm = _FakeLLM()
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(hits=[])

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
    )

    assert llm.calls == []
    assert result.citations == []
    assert result.answer == answer_mod.NO_MATCH
    assert result.no_match_cause == "zero_hits"


def test_dense_only_hit_avoids_the_zero_hit_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
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
    )

    assert len(llm.calls) == 1
    assert result.no_match_cause != "zero_hits"


def test_empty_question_never_calls_embedder_or_vector_store(tmp_path: Path) -> None:
    """A whitespace-only question short-circuits BEFORE any retrieval --
    `embedder.embed`/`vector_store.query` are never called (spec: Empty
    Query Sets A Distinct No-Match Cause)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(hits=[])
    llm = _FakeLLM()

    result = answer_mod.answer(
        "   ",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
    )

    assert embedder.calls == []
    assert vector_store.calls == []
    assert llm.calls == []
    assert result.no_match_cause == "empty_query"


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

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
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

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
    )

    assert result.dense_degraded is True
    assert result.dense_hit_count == 0
    assert result.llm_invoked is True


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

    result = answer_mod.answer(
        "dichotomyzz", bundle_dir=bundle_dir, llm=llm, embedder=None, vector_store=None
    )

    assert result.dense_hit_count == 0
    assert result.dense_degraded is True
    assert result.llm_invoked is True


def test_fts_unavailable_propagates_despite_dense_seams_injected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`FtsUnavailable` propagates unchanged even with `embedder`/
    `vector_store` injected (spec: FtsUnavailable still propagates despite
    dense degrade logic)."""
    bundle_dir = tmp_path / "bundle"

    def _raise_unavailable(_bundle_dir: Path) -> fts.FtsIndex:
        raise fts.FtsUnavailable("fts5 not compiled in")

    monkeypatch.setattr(fts, "build_index", _raise_unavailable)

    with pytest.raises(fts.FtsUnavailable):
        answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(),
            embedder=_FakeEmbedder(),
            vector_store=_FakeVectorStore(hits=[]),
        )


# --- Phase 3 (graph slice): two-stage seeded graph retrieval --------------


def test_graph_reachable_concept_absent_from_fts_and_dense_appears_via_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A concept reachable via graph proximity to the seeds -- absent from
    both FTS and dense hits -- appears in the final answer's citations via
    its `graph_hits` rank (spec: Graph contributes a concept absent from
    FTS and dense hits)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    _write_doc(
        bundle_dir / "concepts" / "graph-neighbor.md",
        title="Graph Neighbor",
        body="reachable only via the graph, not lexically or semantically",
    )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/stoicism", score=0.0)]
    )
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    fake_store = _FakeGraphStore(
        nodes=["concepts/stoicism", "concepts/graph-neighbor"],
        edges=[
            Edge(source_id="concepts/stoicism", target_id="concepts/graph-neighbor")
        ],
    )
    monkeypatch.setattr(
        sqlite_graph, "build_graph", lambda _bundle_dir: _FakeGraphStoreCM(fake_store)
    )
    llm = _FakeLLM(reply="cites the graph neighbor too")

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    cited_ids = {citation.concept_id for citation in result.citations}
    assert "concepts/graph-neighbor" in cited_ids


def test_seeds_come_from_the_initial_fuse_not_a_raw_union(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The seed set passed as `graph_rank`'s `seeds` equals the top
    `min(limit, 5)` `concept_id`s of the INITIAL `fuse(hits, vec_hits)`, not
    a raw union of FTS-only and dense-only top hits (spec: Seeds come from
    the initial fuse, not a raw union)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    fts_hits = [fts.FtsHit(concept_id=f"concepts/f{i}", score=0.0) for i in range(1, 7)]
    recording_index = _RecordingIndex(hits=fts_hits)
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    vector_store = _FakeVectorStore(
        hits=[VecHit(concept_id="concepts/d1", distance=0.0)]
    )
    embedder = _FakeEmbedder()
    expected_seeds = fusion.fuse(
        fts_hits, [VecHit(concept_id="concepts/d1", distance=0.0)]
    )[:5]
    recorded_seeds: list[list[str]] = []
    original_rank = graph_retrieve.graph_rank

    def _spy_graph_rank(
        store: GraphStore, seeds: list[str], *, limit: int
    ) -> list[GraphHit]:
        recorded_seeds.append(list(seeds))
        return original_rank(store, seeds, limit=limit)

    monkeypatch.setattr(graph_retrieve, "graph_rank", _spy_graph_rank)
    monkeypatch.setattr(
        sqlite_graph,
        "build_graph",
        lambda _bundle_dir: _FakeGraphStoreCM(_FakeGraphStore(nodes=[], edges=[])),
    )

    answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=_FakeLLM(),
        embedder=embedder,
        vector_store=vector_store,
    )

    assert recorded_seeds == [expected_seeds]
    assert expected_seeds != [hit.concept_id for hit in fts_hits[:5]]


def test_graph_hit_count_equals_raw_pool_size_before_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`graph_hit_count` equals the raw pool size returned by `graph_rank`
    before final-fusion truncation (spec: Graph counts reflect retrieval)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "seed.md", title="Seed", body="dichotomyzz seed"
    )
    for i in range(6):
        _write_doc(
            bundle_dir / "concepts" / f"n{i}.md", title=f"N{i}", body=f"neighbor {i}"
        )
    recording_index = _RecordingIndex(
        hits=[fts.FtsHit(concept_id="concepts/seed", score=0.0)]
    )
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    fake_store = _FakeGraphStore(
        nodes=["concepts/seed"] + [f"concepts/n{i}" for i in range(6)],
        edges=[
            Edge(source_id="concepts/seed", target_id=f"concepts/n{i}")
            for i in range(6)
        ],
    )
    monkeypatch.setattr(
        sqlite_graph, "build_graph", lambda _bundle_dir: _FakeGraphStoreCM(fake_store)
    )
    llm = _FakeLLM(reply="counts reflect the pool")

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm, limit=3)

    assert result.graph_hit_count == 6


def test_build_graph_raising_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`build_graph` raising any `Exception` sets `graph_degraded=True`,
    `graph_hit_count=0`, propagates no exception, and the FTS+dense answer
    is still produced (spec: Graph build failure degrades cleanly)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="answered despite the graph failure")

    def _raise_build_graph(_bundle_dir: Path) -> sqlite_graph.SqliteGraphStore:
        raise RuntimeError("boom")

    monkeypatch.setattr(sqlite_graph, "build_graph", _raise_build_graph)

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert result.graph_degraded is True
    assert result.graph_hit_count == 0
    assert result.llm_invoked is True
    assert result.answer == "answered despite the graph failure"


def test_edgeless_graph_is_not_a_degrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A graph projection that builds successfully but has zero edges
    yields `graph_hits=[]` and `graph_degraded=False` -- the build itself
    succeeded (spec: Edgeless bundle yields an empty graph list, not a
    failure)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    fake_store = _FakeGraphStore(
        nodes=["concepts/stoicism", "concepts/other"], edges=[]
    )
    monkeypatch.setattr(
        sqlite_graph, "build_graph", lambda _bundle_dir: _FakeGraphStoreCM(fake_store)
    )
    llm = _FakeLLM(reply="fine without graph edges")

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert result.graph_hit_count == 0
    assert result.graph_degraded is False
    assert result.llm_invoked is True


def test_no_seeds_skips_graph_build_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty initial fuse (zero hits from both retrievers) means no
    seeds exist -- `build_graph`/`graph_rank` are never called, and
    `graph_degraded=True`, `graph_hit_count=0` (spec: no seeds skips the
    build)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(hits=[])
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)
    build_calls: list[Path] = []

    def _recording_build_graph(target_bundle_dir: Path) -> _FakeGraphStoreCM:
        build_calls.append(target_bundle_dir)
        return _FakeGraphStoreCM(_FakeGraphStore(nodes=[], edges=[]))

    monkeypatch.setattr(sqlite_graph, "build_graph", _recording_build_graph)
    rank_calls: list[tuple[object, ...]] = []

    def _recording_graph_rank(
        store: GraphStore, seeds: list[str], *, limit: int
    ) -> list[GraphHit]:
        rank_calls.append((store, seeds, limit))
        return []

    monkeypatch.setattr(graph_retrieve, "graph_rank", _recording_graph_rank)
    llm = _FakeLLM()

    result = answer_mod.answer("q", bundle_dir=bundle_dir, llm=llm)

    assert build_calls == []
    assert rank_calls == []
    assert result.graph_degraded is True
    assert result.graph_hit_count == 0


def test_empty_question_never_calls_build_graph_or_graph_rank(
    tmp_path: Path,
) -> None:
    """A whitespace-only question short-circuits BEFORE any retrieval --
    `build_graph`/`graph_rank` are never called, alongside the existing
    `embedder.embed`/`vector_store.query` short-circuit assertions (spec:
    Empty Query Sets A Distinct No-Match Cause)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(hits=[])
    llm = _FakeLLM()

    result = answer_mod.answer(
        "   ",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
    )

    assert embedder.calls == []
    assert vector_store.calls == []
    assert llm.calls == []
    assert result.no_match_cause == "empty_query"
    assert result.graph_degraded is False
    assert result.graph_hit_count == 0


def test_graph_retrieval_is_deterministic_across_repeated_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same bundle and question, `answer()` called twice, produces
    identical `graph_hits` ordering (spied via `graph_retrieve.graph_rank`)
    and an identical final fused, limit-truncated `concept_id` list (spec:
    Personalized PageRank Is Deterministic)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control [Related](/concepts/related.md)",
    )
    _write_doc(
        bundle_dir / "concepts" / "related.md",
        title="Related",
        body="a related concept reachable via the graph",
    )
    recorded_ranks: list[list[GraphHit]] = []
    original_rank = graph_retrieve.graph_rank

    def _recording_rank(
        store: GraphStore, seeds: list[str], *, limit: int
    ) -> list[GraphHit]:
        result = original_rank(store, seeds, limit=limit)
        recorded_ranks.append(result)
        return result

    monkeypatch.setattr(graph_retrieve, "graph_rank", _recording_rank)

    first = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=_FakeLLM())
    second = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=_FakeLLM())

    assert len(recorded_ranks) == 2
    assert recorded_ranks[0] == recorded_ranks[1]
    assert [c.concept_id for c in first.citations] == [
        c.concept_id for c in second.citations
    ]


# --- Phase 6/7: title fallback -------------------------------------------


def test_missing_title_falls_back_to_concept_id(tmp_path: Path) -> None:
    """A concept with no frontmatter `title` cites with `concept_id` as its title."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "untitled.md").write_text(
        "---\ntype: Concept\ndescription: ''\n---\ndichotomyzz of control",
        encoding="utf-8",
    )
    llm = _FakeLLM()

    result = answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=llm)

    assert result.citations == [
        answer_mod.Citation(concept_id="concepts/untitled", title="concepts/untitled")
    ]


# --- Phase 8: typed exception propagation ---------------------------------


def test_fts_unavailable_propagates_unswallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`FtsUnavailable` raised while building the index is never caught here."""
    bundle_dir = tmp_path / "bundle"

    def _raise_unavailable(_bundle_dir: Path) -> fts.FtsIndex:
        raise fts.FtsUnavailable("fts5 not compiled in")

    monkeypatch.setattr(fts, "build_index", _raise_unavailable)

    with pytest.raises(fts.FtsUnavailable):
        answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=_FakeLLM())


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

    with pytest.raises(OllamaUnavailable):
        answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=_ExplodingLLM())


# --- Phase 9: layering guard ----------------------------------------------


# --- ingest-source-body: zero-change confirmation ------------------------


def test_query_retrieves_and_cites_ingested_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`openkos ingest` embeds a source's verbatim text into its Source
    concept body, and `answer()` retrieves and cites that concept with NO
    changes to `state/fts.py` or `retrieval/answer.py` -- embedding alone
    makes the content reachable via the existing generic body-indexing and
    body-feeding behavior (design's zero-change confirmation, scenario:
    query retrieves and cites ingested content)."""
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
    result = answer_mod.answer("flurbnorxal", bundle_dir=tmp_path / "bundle", llm=llm)

    assert result.answer != answer_mod.NO_MATCH
    assert any(
        citation.concept_id == "sources/protocol-notes" for citation in result.citations
    )
    assert len(llm.calls) == 1
    user_content = llm.calls[0][1]["content"]
    assert distinctive_phrase in user_content


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
