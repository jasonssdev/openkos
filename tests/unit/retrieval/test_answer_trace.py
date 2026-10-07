"""`AnswerResult.trace`: the observability payload behind `query --json` (#1345).

The trace carries data `answer()` already computes -- the context blocks as
sent, the per-channel ranks, the prompt hashes -- and never feeds back into
retrieval, assembly or synthesis. These tests pin what it reports; the
existing `test_answer.py` suite pins that nothing else moved.
"""

import hashlib
from pathlib import Path

import pytest

from openkos.retrieval import answer as answer_mod
from openkos.retrieval import fusion
from openkos.state import fts
from openkos.state.vectorstore import VecHit
from tests.unit.retrieval.test_answer import (
    _bundle_with_two,
    _FakeEmbedder,
    _FakeVectorStore,
    _ScriptedLLM,
    _WindowedLLM,
    _write_doc,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _answer(
    bundle_dir: Path,
    llm: _ScriptedLLM,
    *,
    sufficiency_check: bool = False,
    vector_store: _FakeVectorStore | None = None,
    limit: int = 5,
) -> answer_mod.AnswerResult:
    with fts.build_index(bundle_dir) as idx:
        return answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            embedder=_FakeEmbedder() if vector_store is not None else None,
            vector_store=vector_store,
            sufficiency_check=sufficiency_check,
            limit=limit,
        )


def test_context_blocks_reassemble_into_the_exact_user_prompt(
    tmp_path: Path,
) -> None:
    """Assembling `trace.context_blocks[*].text` the way the prompt does gives
    the user content that was sent, byte for byte, and its hash is reported."""
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("An answer.\nUSED: 2")

    result = _answer(bundle_dir, llm)

    assert result.trace is not None
    sent_system = str(llm.calls[-1][0]["content"])
    sent_user = str(llm.calls[-1][1]["content"])
    numbered = [f"[{b.index}] {b.text}" for b in result.trace.context_blocks]
    rebuilt = "CONTEXT:\n\n" + "\n\n".join(numbered) + "\n\nQUESTION:\ndichotomyzz"
    assert rebuilt == sent_user
    assert result.trace.user_sha256 == _sha(sent_user)
    assert result.trace.system_sha256 == _sha(sent_system)
    assert [b.index for b in result.trace.context_blocks] == [1, 2]
    assert [b.concept_id for b in result.trace.context_blocks] == result.context_ids
    assert result.trace.used_indices == (2,)
    assert result.trace.sufficiency_invoked is False
    assert result.trace.sufficiency_sha256 is None
    assert result.trace.sufficiency_raw_reply is None


def test_a_sufficiency_refusal_reports_the_check_and_not_the_synthesis_prompt(
    tmp_path: Path,
) -> None:
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("NONE")

    result = _answer(bundle_dir, llm, sufficiency_check=True)

    assert result.trace is not None
    assert result.no_match_cause == "insufficient_context"
    sent_system = str(llm.calls[0][0]["content"])
    sent_user = str(llm.calls[0][1]["content"])
    assert result.trace.sufficiency_invoked is True
    assert result.trace.sufficiency_raw_reply == "NONE"
    assert result.trace.sufficiency_sha256 == _sha(sent_system)
    assert result.trace.user_sha256 == _sha(sent_user)
    # Synthesis never ran, so its system prompt was never sent.
    assert result.trace.system_sha256 is None
    assert result.trace.used_indices == ()


def test_a_sufficiency_pass_reports_the_raw_reply_and_both_system_hashes(
    tmp_path: Path,
) -> None:
    bundle_dir = _bundle_with_two(tmp_path)
    llm = _ScriptedLLM("a quoted sentence", "Answer.\nUSED: 1")

    result = _answer(bundle_dir, llm, sufficiency_check=True)

    assert result.trace is not None
    assert result.trace.sufficiency_raw_reply == "a quoted sentence"
    assert result.trace.sufficiency_sha256 == _sha(str(llm.calls[0][0]["content"]))
    assert result.trace.system_sha256 == _sha(str(llm.calls[1][0]["content"]))
    assert result.trace.sufficiency_sha256 != result.trace.system_sha256


def test_retrieved_rows_carry_per_channel_ranks_and_the_rrf_score(
    tmp_path: Path,
) -> None:
    bundle_dir = _bundle_with_two(tmp_path)
    _write_doc(
        bundle_dir / "concepts" / "gamma.md", title="Gamma", body="unrelated words"
    )
    store = _FakeVectorStore(
        [VecHit("concepts/beta", 0.1), VecHit("concepts/gamma", 0.2)]
    )
    llm = _ScriptedLLM("Answer.")

    result = _answer(bundle_dir, llm, vector_store=store)

    assert result.trace is not None
    rows = {r.concept_id: r for r in result.trace.retrieved}
    assert rows["concepts/alpha"].dense_rank is None
    assert rows["concepts/gamma"].fts_rank is None
    assert rows["concepts/gamma"].dense_rank == 2
    assert rows["concepts/beta"].dense_rank == 1
    beta_fts = rows["concepts/beta"].fts_rank
    assert beta_fts is not None
    k = fusion.K_RRF
    assert rows["concepts/beta"].rrf_score == 1 / (k + 1) + 1 / (k + beta_fts)
    assert rows["concepts/gamma"].rrf_score == 1 / (k + 2)
    assert [r.rank for r in result.trace.retrieved] == [1, 2, 3]
    assert len(result.trace.retrieved) == 3
    assert rows["concepts/gamma"].title == "Gamma"


def test_a_budget_dropped_document_is_reported_as_omitted(tmp_path: Path) -> None:
    """The window leaves the one document no room: it is listed as omitted
    with the plain budget reason and no display suffix, and no prompt was
    assembled, so no user hash exists."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "sources" / "huge.md",
        doc_type="Source",
        title="Huge",
        body="\n".join(f"dichotomyzz line {n:04d} " + "y" * 60 for n in range(1_500)),
    )
    llm = _WindowedLLM(context_window=4_096, max_generation_tokens=None)

    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz", bundle_dir=bundle_dir, llm=llm, fts_index=idx
        )

    assert result.trace is not None
    assert result.trace.omitted == (
        answer_mod.OmittedTrace("sources/huge", "Huge", "context_budget"),
    )
    assert answer_mod.OMIT_REASON_BUDGET == "context_budget"
    assert result.trace.context_blocks == ()
    assert result.trace.user_sha256 is None
    assert result.trace.system_sha256 is None
    # Read and admitted, then dropped: still named in `retrieved`.
    assert [r.concept_id for r in result.trace.retrieved] == ["sources/huge"]


def test_an_omitted_earlier_version_has_its_own_reason_and_a_clean_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The history path drops a predecessor by the same budget but names it
    `... (earlier version)` in the display title; the trace keeps the title
    clean and moves the distinction into the closed `reason` enum."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "concepts" / "s.md",
        title="S",
        body="dichotomyzz s body",
        relations=[("concepts/p", "supersedes")],
    )
    _write_doc(
        bundle_dir / "concepts" / "p.md",
        title="P",
        body="p body",
        status="deprecated",
    )
    monkeypatch.setattr(
        answer_mod,
        "_bound_with_history",
        lambda labels, bodies, holders, **_kw: (
            list(bodies),
            [False] * len(bodies),
            [False, True],
        ),
    )
    omitted_citations: list[answer_mod.Citation] = []
    answer_mod._assemble_context(
        bundle_dir,
        ["concepts/s"],
        revision_history=True,
        omitted_titles_out=[],
        omitted_citations_out=omitted_citations,
    )
    assert [(c.concept_id, c.title, c.history) for c in omitted_citations] == [
        ("concepts/p", "P", "superseded")
    ]

    llm = _ScriptedLLM("Answer.")
    with fts.build_index(bundle_dir) as idx:
        result = answer_mod.answer(
            "dichotomyzz",
            bundle_dir=bundle_dir,
            llm=llm,
            fts_index=idx,
            revision_history=True,
        )

    assert result.omitted_titles == ["P (earlier version)"]
    assert result.trace is not None
    assert result.trace.omitted == (
        answer_mod.OmittedTrace("concepts/p", "P", "context_budget_earlier_version"),
    )
    # The earlier version is not a fused hit, so it is not a `retrieved` row.
    assert [r.concept_id for r in result.trace.retrieved] == ["concepts/s"]


def test_the_trace_does_not_take_part_in_equality() -> None:
    """Characterization tests compare whole `AnswerResult`s; a trace must not
    make two otherwise-equal results unequal."""
    plain = answer_mod.AnswerResult(
        answer="a",
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="zero_hits",
        skip_notices=[],
    )
    traced = answer_mod.AnswerResult(
        answer="a",
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="zero_hits",
        skip_notices=[],
        trace=answer_mod.AnswerTrace(),
    )
    assert traced == plain
