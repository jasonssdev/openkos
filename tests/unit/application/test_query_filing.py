"""Direct unit tests for `openkos.application.query`'s `--save` filing
composition (D3/D4, ADR-0018): `stage_filed_answer`, `FiledAnswerPlan`,
`grounding_unverified`, `synthesis_share_warrants_warning`, and
`scan_for_duplicates`.

Mirrors `test_query_service.py`'s posture for the read path: these exercise
the pure composition directly, never a live Ollama process or a CLI
invocation. `stage_filed_answer`'s EXHAUSTIVE behavioral scenarios (every
title-cascade case, the full sensitivity high-water-mark matrix, byte-exact
NFC/NFD citation lookups, etc.) stay in `tests/unit/cli/test_query_save.py`,
reached through a test-local alias to this module's `stage_filed_answer`
(issue #918 Slice 2, design task 7.3) -- this file adds the RED-first
scenarios task 6.1/6.3/6.5 name, plus a compact, non-duplicative set of
direct calls sufficient to exercise every branch of the moved title cascade
and `stage_filed_answer` itself at THIS layer's own coverage gate (task
8.2), reusing the exact known-good fixtures/expected values `test_query_save.py`
already pinned rather than re-deriving them."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos import config
from openkos.application import query as query_service
from openkos.retrieval.answer import AnswerResult, Citation


def _write_concept(
    bundle_dir: Path,
    link_dir: str,
    slug: str,
    *,
    title: str = "A cited concept",
    sensitivity: str | None = "private",
) -> None:
    path = bundle_dir / link_dir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}", "description: ''"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines.append("---")
    lines.append("body")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _workspace(tmp_path: Path) -> tuple[config.WorkspaceLayout, config.Config]:
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    return layout, config.read_config(tmp_path)


def _citation(concept_id: str) -> Citation:
    return Citation(concept_id=concept_id, title=concept_id)


def test_stage_filed_answer_refuses_empty_citations(tmp_path: Path) -> None:
    """Zero citations refuse at the service boundary (spec: "Zero citations
    refuse at the service boundary"), reported through `ValueError` --
    `query`'s own `except ValueError` maps this to the CLI's exit-1
    refusal; the service itself never prints or exits."""
    layout, cfg = _workspace(tmp_path)

    with pytest.raises(ValueError, match="nothing to file"):
        query_service.stage_filed_answer(
            question="what is stoicism?",
            answer_text="Stoicism is a school of ancient philosophy.",
            citations=[],
            bundle_dir=layout.bundle_dir,
            default_sensitivity=cfg.default_sensitivity,
            timestamp="2024-01-01T00:00:00Z",
            cfg=cfg,
        )


def test_stage_filed_answer_invalid_type_raises(tmp_path: Path) -> None:
    """An invalid `doc_type` raises `ValueError` -- `okf.build_concept`'s own
    classifiable-vocabulary gate, checked here first (fixture and assertion
    match `test_query_save.py::test_stage_filed_answer_type_override_validated`
    exactly)."""
    layout, cfg = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts", "stoicism")
    citations = [_citation("concepts/stoicism")]

    with pytest.raises(ValueError, match="type must be one of"):
        query_service.stage_filed_answer(
            question="what is stoicism?",
            answer_text="answer text",
            citations=citations,
            bundle_dir=layout.bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
            doc_type="NotAType",
            cfg=cfg,
        )


def test_stage_filed_answer_empty_slug_raises(tmp_path: Path) -> None:
    """A title made only of characters `slugify` strips yields an empty
    slug, which refuses (fixture mirrors
    `test_query_save.py::test_stage_filed_answer_empty_slug_raises`)."""
    layout, cfg = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts", "stoicism")
    citations = [_citation("concepts/stoicism")]

    with pytest.raises(ValueError, match="cannot derive"):
        query_service.stage_filed_answer(
            question="???",
            answer_text="answer text",
            citations=citations,
            bundle_dir=layout.bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
            title="???",
            cfg=cfg,
        )


def test_stage_filed_answer_collision_raises(tmp_path: Path) -> None:
    """A pre-existing file at the target slug path refuses (fixture mirrors
    `test_query_save.py::test_stage_filed_answer_collision_raises`)."""
    layout, cfg = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts", "stoicism")
    _write_concept(layout.bundle_dir, "insights", "stoicism", title="Existing")
    citations = [_citation("concepts/stoicism")]

    with pytest.raises(ValueError, match="already exists"):
        query_service.stage_filed_answer(
            question="what is stoicism?",
            answer_text="answer text",
            citations=citations,
            bundle_dir=layout.bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
            cfg=cfg,
        )


def test_stage_filed_answer_missing_citation_folds_confidential(tmp_path: Path) -> None:
    """A cited concept whose file is MISSING at save time folds the running
    sensitivity floor to `confidential` (the `except Exception` branch;
    fixture mirrors
    `test_query_save.py::test_stage_filed_answer_missing_citation_folds_confidential`)."""
    layout, cfg = _workspace(tmp_path)
    citations = [_citation("concepts/missing")]

    plan = query_service.stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=layout.bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        cfg=cfg,
    )

    assert plan.sensitivity == "confidential"
    assert "sensitivity: confidential" in plan.content


def test_stage_filed_answer_builds_a_plan_from_a_readable_citation(
    tmp_path: Path,
) -> None:
    """The ordinary success path: a readable cited concept's own
    `sensitivity` folds into the plan, and `provenance` equals the cited
    concept ids in order (fixture mirrors
    `test_query_save.py::test_stage_filed_answer_provenance_equals_cited_ids`)."""
    layout, cfg = _workspace(tmp_path)
    _write_concept(layout.bundle_dir, "concepts", "stoicism", title="Stoicism")
    citations = [_citation("concepts/stoicism")]

    plan = query_service.stage_filed_answer(
        question="what is stoicism?",
        answer_text="Stoicism teaches the dichotomy of control.",
        citations=citations,
        bundle_dir=layout.bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        cfg=cfg,
    )

    assert plan.sensitivity == "private"
    assert "provenance:\n- concepts/stoicism\n" in plan.content
    assert plan.type_floor_raised is False


def test_declarative_answer_title_promotes_a_usable_first_sentence() -> None:
    """Fixture mirrors
    `test_query_save.py::test_declarative_answer_title_promotes_a_usable_first_sentence`."""
    assert (
        query_service._declarative_answer_title(
            "Symmetric cryptography relies on modular arithmetic. More detail."
        )
        == "Symmetric cryptography relies on modular arithmetic"
    )


def test_declarative_answer_title_refuses_fragments_and_questions() -> None:
    """Covers both the length-bound branch and the question/markdown-start
    branch (fixture mirrors
    `test_query_save.py::test_declarative_answer_title_refuses_fragments_questions_and_prose`)."""
    assert query_service._declarative_answer_title("Yes.") is None
    assert query_service._declarative_answer_title("word " * 40 + ".") is None
    assert query_service._declarative_answer_title("Is it modular arithmetic?") is None


def test_question_subject_strips_definitional_scaffolding() -> None:
    """Covers the prefix-match break, the trailing-clause cut, and the
    article-strip branches (fixture mirrors
    `test_query_save.py::test_question_subject_strips_definitional_scaffolding`)."""
    assert (
        query_service._question_subject("¿qué es el Model Context Protocol?")
        == "Model Context Protocol"
    )
    assert query_service._question_subject("¿qué es MCP y para qué sirve?") == "MCP"
    assert (
        query_service._question_subject("what is the context window?")
        == "Context window"
    )


def test_question_subject_refuses_non_definitional_questions() -> None:
    """Covers the prefix `for/else` fallthrough and the no-letters residue
    guard (fixture mirrors
    `test_query_save.py::test_question_subject_refuses_non_definitional_questions`)."""
    assert query_service._question_subject("summarize the meeting") is None
    assert query_service._question_subject("¿qué es?") is None


def test_clause_answer_title_promotes_the_first_clause_of_an_overlong_opening() -> None:
    """Fixture mirrors
    `test_query_save.py::test_clause_answer_title_promotes_the_first_clause_of_an_overlong_opening`."""
    assert (
        query_service._clause_answer_title(
            "La relación entre la trazabilidad y la verdad contextual en "
            "sistemas RAG radica en que cada afirmación generada debe poder "
            "rastrearse hasta su fuente original."
        )
        == "La relación entre la trazabilidad y la verdad contextual en sistemas RAG"
    )


def test_clause_answer_title_defers_to_the_declarative_rung_within_the_ceiling() -> (
    None
):
    """Fixture mirrors
    `test_query_save.py::test_clause_answer_title_defers_to_the_declarative_rung_within_the_ceiling`."""
    within = "Symmetric cryptography relies on modular arithmetic. More detail."
    assert query_service._declarative_answer_title(within) is not None
    assert query_service._clause_answer_title(within) is None


def test_clause_answer_title_refuses_questions_and_markdown() -> None:
    """Fixture mirrors
    `test_query_save.py::test_clause_answer_title_refuses_questions_and_markdown`."""
    assert (
        query_service._clause_answer_title(
            "¿Por qué la trazabilidad importa tanto en un repositorio de "
            "conocimiento local, y qué pasa si falta?"
        )
        is None
    )


def test_clause_answer_title_refuses_a_residue_outside_the_bounds() -> None:
    """Covers both the too-short-residue and the no-cut-found branches
    (fixture mirrors
    `test_query_save.py::test_clause_answer_title_refuses_a_residue_outside_the_bounds`
    and `..._refuses_a_sentence_with_no_clause_boundary`)."""
    assert (
        query_service._clause_answer_title(
            "El MVP, entendido como la versión más pequeña del producto que "
            "ya entrega valor real al usuario, sirve para aprender."
        )
        is None
    )
    assert (
        query_service._clause_answer_title(
            "Rastrear afirmaciones generadas hasta fuentes originales "
            "inmutables mediante cadenas largas de procedencia declarada"
        )
        is None
    )


def test_clause_answer_title_refuses_a_residue_with_no_letters() -> None:
    """Fixture mirrors
    `test_query_save.py::test_clause_answer_title_refuses_a_residue_with_no_letters`."""
    cut = query_service._clause_answer_title(
        "2024 2025 2026 2027 2028 2029 2030 2031 2032, la trazabilidad "
        "quedó definida como la propiedad central del repositorio local."
    )
    assert cut is None


def test_grounding_unverified_true_when_unattributed() -> None:
    """`grounding_unverified` is the D4 policy predicate `query` gates the
    #774 unattributed-save confirmation on -- `True` exactly when the LLM
    ran and its own attribution line never confirmed the citation list."""
    result = AnswerResult(
        answer="x",
        citations=[],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        attribution="absent",
    )
    assert query_service.grounding_unverified(result) is True


def test_grounding_unverified_false_when_reported() -> None:
    result = AnswerResult(
        answer="x",
        citations=[],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        attribution="reported",
    )
    assert query_service.grounding_unverified(result) is False


def test_grounding_unverified_false_when_the_llm_never_ran() -> None:
    """A short-circuit result defaults `attribution` to `"absent"`, but the
    LLM never ran, so there is no grounding claim left to verify."""
    result = AnswerResult(
        answer="x",
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="empty_query",
        skip_notices=[],
    )
    assert query_service.grounding_unverified(result) is False


def test_synthesis_share_warrants_warning_at_threshold() -> None:
    """Half-or-more of the citations being filed syntheses (`insights/`)
    warrants the warning (issue #649) -- exactly at
    `_SYNTHESIS_SHARE_WARN_THRESHOLD`, not only above it."""
    citations = [_citation("insights/a"), _citation("concepts/b")]
    assert query_service.synthesis_share_warrants_warning(citations) is True


def test_synthesis_share_warrants_warning_below_threshold() -> None:
    citations = [
        _citation("insights/a"),
        _citation("concepts/b"),
        _citation("concepts/c"),
    ]
    assert query_service.synthesis_share_warrants_warning(citations) is False


def test_synthesis_share_warrants_warning_empty_citations() -> None:
    assert query_service.synthesis_share_warrants_warning([]) is False


class _FakeEmbedder:
    """Returns a queued vector per input text (mirrors
    `test_insight_identity._FakeEmbedder`)."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vectors[text] for text in texts]


class _RaisingEmbedder:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("backend down")


def _write_insight(bundle_dir: Path, slug: str, *, description: str) -> None:
    path = bundle_dir / "insights" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: Insight\ntitle: An Insight\n"
        f"description: {description}\nsensitivity: private\n---\nThe answer.",
        encoding="utf-8",
    )


def test_scan_for_duplicates_reports_unavailable_when_the_cache_cannot_open(
    tmp_path: Path,
) -> None:
    """A corrupt/unopenable `insight_questions.db` degrades to `cache=None`,
    which `near_duplicate_insights` reports as `unavailable=True` -- the
    scan is advisory and never blocks a save (design D3)."""
    layout, cfg = _workspace(tmp_path)
    _write_insight(layout.bundle_dir, "filed", description="a filed question?")
    layout.insight_questions_db_path.parent.mkdir(parents=True, exist_ok=True)
    layout.insight_questions_db_path.write_bytes(b"not a sqlite database")

    scan = query_service.scan_for_duplicates(
        "a new question?", layout=layout, cfg=cfg, embedder=_RaisingEmbedder()
    )

    assert scan.unavailable is True
    assert scan.candidates == []


def test_scan_for_duplicates_finds_a_positive_match(tmp_path: Path) -> None:
    """A filed insight whose source question embeds identically to the new
    one is reported as a candidate -- the ordinary, cache-cold path."""
    layout, cfg = _workspace(tmp_path)
    _write_insight(layout.bundle_dir, "why-stoicism", description="why stoicism?")
    embedder = _FakeEmbedder(
        {"what is stoicism?": [1.0, 0.0], "why stoicism?": [1.0, 0.0]}
    )

    scan = query_service.scan_for_duplicates(
        "what is stoicism?", layout=layout, cfg=cfg, embedder=embedder
    )

    assert scan.unavailable is False
    assert [c.concept_id for c in scan.candidates] == ["insights/why-stoicism"]
