"""Unit tests for `retrieval/answer.py`: the cited answer library.

`answer()` composes four INJECTED seams end-to-end (Slice 5, PR3, design D4):
a read-only `fts_index` handle (lexical), an injected `Embedder` +
`VectorStore` (dense, optional), a read-only `graph_index` handle +
`retrieval.graph_retrieve.graph_rank` (seeded PPR, optional/degrading), and
an injected `llm.LLMBackend` (answer). `answer()` no longer builds
`fts_index`/`graph_index` itself -- tests inject either a real
`fts.build_index(bundle_dir)`/`sqlite_graph.build_graph(bundle_dir)` handle
(via a `with` block) for genuine end-to-end coverage, or a lightweight
structural fake for isolated/degrade-path coverage. All tests use a
`tmp_path` bundle and a structural fake `LLMBackend` -- zero network, zero
real Ollama process.
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
    """A minimal `GraphStore` fixture over an explicit node/edge list --
    injected directly as `graph_index=...`, no build/context-manager step."""

    def __init__(self, nodes: list[str], edges: list[Edge]) -> None:
        self._nodes = nodes
        self._edges = edges

    def nodes(self) -> list[str]:
        return self._nodes

    def edges(self) -> list[Edge]:
        return self._edges

    def neighbors(self, concept_id: str) -> list[str]:
        return [edge.target_id for edge in self._edges if edge.source_id == concept_id]


class _SpyGraphStore:
    """A `GraphStore` fixture that raises if any query method is ever
    called -- proves `answer()` never reads an injected `graph_index` it
    has no seeds for, or on the empty-question short-circuit."""

    def nodes(self) -> list[str]:
        raise AssertionError("graph_index.nodes() should never be called here")

    def edges(self) -> list[Edge]:
        raise AssertionError("graph_index.edges() should never be called here")

    def neighbors(self, concept_id: str) -> list[str]:
        raise AssertionError("graph_index.neighbors() should never be called here")


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
    assert f"{stoicism_block}\n\n{epictetus_block}" in user_content
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
    `fts_index.search`, `embedder.embed`, `vector_store.query`, and any
    query surface of `graph_index` are ALL untouched, `llm.chat` is never
    invoked, and `no_match_cause` is `"empty_query"` (follow-up #1,
    strengthened: spies on all four injected handles, not just two;
    query-answer: Whitespace-only question touches no injected handle)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    fts_index = _SpyFtsIndex()
    embedder = _FakeEmbedder()
    vector_store = _FakeVectorStore(hits=[])
    graph_index = _SpyGraphStore()
    llm = _FakeLLM()

    result = answer_mod.answer(
        "   ",
        bundle_dir=bundle_dir,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        fts_index=fts_index,
        graph_index=graph_index,
    )

    assert fts_index.calls == 0
    assert embedder.calls == []
    assert vector_store.calls == []
    assert llm.calls == []
    assert result.no_match_cause == "empty_query"
    assert result.answer == answer_mod.NO_MATCH
    assert result.fts_hit_count == 0
    assert result.llm_invoked is False
    assert result.graph_degraded is False
    assert result.graph_hit_count == 0


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


# --- Phase 3 (graph slice): two-stage seeded graph retrieval --------------


def test_graph_reachable_concept_absent_from_fts_and_dense_appears_via_graph(
    tmp_path: Path,
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
    fake_store = _FakeGraphStore(
        nodes=["concepts/stoicism", "concepts/graph-neighbor"],
        edges=[
            Edge(source_id="concepts/stoicism", target_id="concepts/graph-neighbor")
        ],
    )
    llm = _FakeLLM(reply="cites the graph neighbor too")

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        graph_index=fake_store,
    )

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

    answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=_FakeLLM(),
        embedder=embedder,
        vector_store=vector_store,
        fts_index=recording_index,
        graph_index=_FakeGraphStore(nodes=[], edges=[]),
    )

    assert recorded_seeds == [expected_seeds]
    assert expected_seeds != [hit.concept_id for hit in fts_hits[:5]]


def test_graph_hit_count_equals_raw_pool_size_before_truncation(
    tmp_path: Path,
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
    fake_store = _FakeGraphStore(
        nodes=["concepts/seed"] + [f"concepts/n{i}" for i in range(6)],
        edges=[
            Edge(source_id="concepts/seed", target_id=f"concepts/n{i}")
            for i in range(6)
        ],
    )
    llm = _FakeLLM(reply="counts reflect the pool")

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        graph_index=fake_store,
        limit=3,
    )

    assert result.graph_hit_count == 6


def test_graph_rank_raising_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`graph_rank` raising any `Exception` sets `graph_degraded=True`,
    `graph_hit_count=0`, propagates no exception, and the FTS+dense answer
    is still produced (spec: Graph build failure degrades cleanly).
    `graph_rank` is the ACTUAL call site `_graph_search` wraps in a
    try/except now that there is no per-call `build_graph` step."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="answered despite the graph failure")

    def _raise_graph_rank(
        store: GraphStore, seeds: list[str], *, limit: int
    ) -> list[GraphHit]:
        raise RuntimeError("boom")

    monkeypatch.setattr(graph_retrieve, "graph_rank", _raise_graph_rank)

    result = answer_mod.answer(
        "dichotomyzz",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=_RecordingIndex(
            hits=[fts.FtsHit(concept_id="concepts/stoicism", score=0.0)]
        ),
        graph_index=_FakeGraphStore(nodes=["concepts/stoicism"], edges=[]),
    )

    assert result.graph_degraded is True
    assert result.graph_hit_count == 0
    assert result.llm_invoked is True
    assert result.answer == "answered despite the graph failure"


def test_edgeless_graph_is_not_a_degrade(tmp_path: Path) -> None:
    """A graph projection that opened successfully but has zero edges
    yields `graph_hits=[]` and `graph_degraded=False` -- the handle itself
    opened fine (spec: Edgeless bundle yields an empty graph list, not a
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
    llm = _FakeLLM(reply="fine without graph edges")

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            graph_index=fake_store,
        )

    assert result.graph_hit_count == 0
    assert result.graph_degraded is False
    assert result.llm_invoked is True


def test_no_seeds_skips_graph_read_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty initial fuse (zero hits from both retrievers) means no
    seeds exist -- `graph_rank` is never called and the injected
    `graph_index` is never read -- `graph_degraded=True`,
    `graph_hit_count=0` (spec: no seeds skips the build)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    recording_index = _RecordingIndex(hits=[])
    rank_calls: list[tuple[object, ...]] = []

    def _recording_graph_rank(
        store: GraphStore, seeds: list[str], *, limit: int
    ) -> list[GraphHit]:
        rank_calls.append((store, seeds, limit))
        return []

    monkeypatch.setattr(graph_retrieve, "graph_rank", _recording_graph_rank)
    llm = _FakeLLM()

    result = answer_mod.answer(
        "q",
        bundle_dir=bundle_dir,
        llm=llm,
        fts_index=recording_index,
        graph_index=_SpyGraphStore(),
    )

    assert rank_calls == []
    assert result.graph_degraded is True
    assert result.graph_hit_count == 0


def test_absent_graph_index_degrades_cleanly(tmp_path: Path) -> None:
    """`graph_index=None` (the default -- workspace never ran `reindex`, or
    the CLI resolved an unopenable/corrupt store to `None`) sets
    `graph_degraded=True`, `graph_hit_count=0`, and FTS/dense retrieval
    still produce a final answer (query-answer: Absent graph handle
    degrades cleanly)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "stoicism.md",
        title="Stoicism",
        body="dichotomyzz of control",
    )
    llm = _FakeLLM(reply="answered without any graph index")

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            graph_index=None,
        )

    assert result.graph_degraded is True
    assert result.graph_hit_count == 0
    assert result.llm_invoked is True
    assert result.answer == "answered without any graph index"


def test_graph_retrieval_is_deterministic_across_repeated_calls(
    tmp_path: Path,
) -> None:
    """Same bundle and question, `answer()` called twice, produces
    identical `graph_hits` ordering and an identical final fused,
    limit-truncated `concept_id` list (spec: Personalized PageRank Is
    Deterministic)."""
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

    with (
        fts.build_index(bundle_dir) as idx_one,
        sqlite_graph.build_graph(bundle_dir) as store_one,
    ):
        first = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(),
            fts_index=idx_one,
            graph_index=store_one,
        )
    with (
        fts.build_index(bundle_dir) as idx_two,
        sqlite_graph.build_graph(bundle_dir) as store_two,
    ):
        second = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=_FakeLLM(),
            fts_index=idx_two,
            graph_index=store_two,
        )

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
