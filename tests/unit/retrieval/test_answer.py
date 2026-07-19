"""Unit tests for `retrieval/answer.py`: the cited answer library.

`answer()` composes three archived seams end-to-end: `state.fts.build_index`
(retrieve), a per-hit guarded `okf.load_frontmatter` re-read (assemble), and
an injected `llm.LLMBackend` (answer). All tests use a `tmp_path` bundle and
a structural fake `LLMBackend` -- zero network, zero real Ollama process.
"""

import ast
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.retrieval import answer as answer_mod
from openkos.state import fts

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


# --- Phase 1: scaffold -------------------------------------------------


def test_citation_is_a_frozen_dataclass() -> None:
    """`Citation` carries `concept_id` and `title`, and is immutable."""
    citation = answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")

    assert citation.concept_id == "concepts/stoicism"
    assert citation.title == "Stoicism"
    with pytest.raises(dataclasses.FrozenInstanceError):
        citation.title = "Other"  # type: ignore[misc]


def test_answer_result_is_a_frozen_dataclass() -> None:
    """`AnswerResult` carries `answer` text, a `citations` list, and retrieval
    metadata (`fts_hit_count`, `llm_invoked`, `no_match_cause`,
    `skip_notices`), and is immutable."""
    citation = answer_mod.Citation(concept_id="concepts/stoicism", title="Stoicism")
    result = answer_mod.AnswerResult(
        answer="the reply",
        citations=[citation],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
    )

    assert result.answer == "the reply"
    assert result.citations == [citation]
    assert result.fts_hit_count == 1
    assert result.llm_invoked is True
    assert result.no_match_cause == "none"
    assert result.skip_notices == []
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


def test_caller_omits_limit_search_called_with_five(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`limit` defaults to 5 and is forwarded unchanged to `FtsIndex.search`."""
    bundle_dir = tmp_path / "bundle"
    recording_index = _RecordingIndex(hits=[])
    monkeypatch.setattr(fts, "build_index", lambda _bundle_dir: recording_index)

    answer_mod.answer("dichotomyzz", bundle_dir=bundle_dir, llm=_FakeLLM())

    assert recording_index.calls == [("dichotomyzz", 5)]


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
    collapses into `"zero_hits"` or `"all_unreadable"`."""
    hits = [fts.FtsHit(concept_id="concepts/stoicism", score=1.0)]

    assert answer_mod._classify_no_match("   ", hits) == "empty_query"
    assert answer_mod._classify_no_match("", hits) == "empty_query"
    assert answer_mod._classify_no_match("real question", []) == "zero_hits"
    assert answer_mod._classify_no_match("real question", hits) == "all_unreadable"


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
