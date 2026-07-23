"""Unit and integration tests for `query --save` (two-output-rule):
files the just-printed cited answer back as a new derived OKF concept.

Unit tests exercise `_stage_filed_answer` directly (Phase A staging, no
writes -- mirrors `_stage_derived_objects`'s test shape in
`test_ingest.py`). Integration tests drive the full `query --save` CLI path
through `CliRunner`, patching `openkos.cli.main.answer` exactly like
`test_query.py` does, so these tests are zero network, zero real Ollama
process, zero real FTS5/vector/graph index.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import _stage_filed_answer, app
from openkos.graph import sqlite_graph
from openkos.retrieval.answer import NO_MATCH, AnswerResult, Citation
from openkos.state import fts, vectorstore

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke`
    call (mirrors `test_ingest.py::_simulate_tty`)."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialize a workspace and backfill empty derived stores, so `query`'s
    three index seams are healthy by default (mirrors
    `test_query.py::_init_workspace`)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db").close()
    bundle_dir = tmp_path / "bundle"
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    sqlite_graph.write_graph_store(tmp_path / ".openkos" / "graph.db", bundle_dir)


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


def _fake_matched_answer(
    *,
    answer: str = "Stoicism teaches the dichotomy of control.",
    citations: list[Citation] | None = None,
) -> AnswerResult:
    return AnswerResult(
        answer=answer,
        citations=[] if citations is None else citations,
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
    )


# --- Unit tests: _stage_filed_answer ----------------------------------------


def test_stage_filed_answer_provenance_equals_cited_ids(tmp_path: Path) -> None:
    """`provenance` on the built concept equals `[c.concept_id for c in
    citations]`, in citation order (spec: "`--save` Files The Cited Answer
    As A New Concept")."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism", title="Stoicism")
    _write_concept(bundle_dir, "concepts", "epictetus", title="Epictetus")
    citations = [
        Citation(concept_id="concepts/stoicism", title="Stoicism"),
        Citation(concept_id="concepts/epictetus", title="Epictetus"),
    ]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="Stoicism teaches the dichotomy of control.",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
    )

    assert "provenance:\n- concepts/stoicism\n- concepts/epictetus\n" in plan.content


def test_stage_filed_answer_title_description_default_to_question(
    tmp_path: Path,
) -> None:
    """Without `--title`/`--description`, both default to the question
    (spec: "Default filing uses the question as title/description")."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
    )

    assert plan.title == "what is stoicism?"
    assert plan.description == "what is stoicism?"


def test_stage_filed_answer_title_description_overrides_apply(
    tmp_path: Path,
) -> None:
    """`--title`/`--description` overrides take precedence over the question
    (spec: "`--title`, `--description`, `--type` override defaults")."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        title="Stoicism, briefly",
        description="A short primer.",
    )

    assert plan.title == "Stoicism, briefly"
    assert plan.description == "A short primer."


def test_stage_filed_answer_type_override_validated(tmp_path: Path) -> None:
    """An invalid `--type` raises `ValueError` (build_concept's own
    classifiable-vocabulary gate, reused rather than duplicated)."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    with pytest.raises(ValueError, match="type must be one of"):
        _stage_filed_answer(
            question="what is stoicism?",
            answer_text="answer text",
            citations=citations,
            bundle_dir=bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
            doc_type="NotAType",
        )


def test_stage_filed_answer_valid_type_override_applies(tmp_path: Path) -> None:
    """A valid `--type` override (e.g. `Procedure`) is honored end-to-end."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        doc_type="Procedure",
    )

    assert plan.link_dir == "procedures"
    assert plan.section == "Procedures"
    assert "type: Procedure" in plan.content


def test_stage_filed_answer_zero_citations_raises(tmp_path: Path) -> None:
    """Zero citations refuses with `ValueError` (spec: "Zero citations
    refuse to file")."""
    bundle_dir = tmp_path / "bundle"

    with pytest.raises(ValueError, match="nothing to file"):
        _stage_filed_answer(
            question="what is stoicism?",
            answer_text="answer text",
            citations=[],
            bundle_dir=bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
        )


def test_stage_filed_answer_empty_slug_raises(tmp_path: Path) -> None:
    """A title made only of characters `_slugify` strips yields an empty
    slug, which refuses (mirrors `_stage_derived_objects`'s empty-slug
    drop)."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    with pytest.raises(ValueError, match="cannot derive"):
        _stage_filed_answer(
            question="???",
            answer_text="answer text",
            citations=citations,
            bundle_dir=bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
            title="???",
        )


def test_stage_filed_answer_collision_raises(tmp_path: Path) -> None:
    """A pre-existing file at the target slug path refuses (design: "Slug
    collision handling (mirror ingest)")."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    _write_concept(bundle_dir, "concepts", "what-is-stoicism", title="Existing")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    with pytest.raises(ValueError, match="already exists"):
        _stage_filed_answer(
            question="what is stoicism?",
            answer_text="answer text",
            citations=citations,
            bundle_dir=bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
        )


def test_stage_filed_answer_sensitivity_floor_default(tmp_path: Path) -> None:
    """An unreadable cited concept is skipped; the running floor (seeded at
    `cfg.default_sensitivity`) holds (design: "unreadable -> skipped, floor
    holds -- fail-safe")."""
    bundle_dir = tmp_path / "bundle"
    citations = [Citation(concept_id="concepts/missing", title="Missing")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
    )

    assert plan.sensitivity == "private"
    assert "sensitivity: private" in plan.content


def test_stage_filed_answer_confidential_citation_is_high_water_mark(
    tmp_path: Path,
) -> None:
    """A confidential cited concept (surfaced under `--include-confidential`)
    yields a confidential plan (spec: "Confidential citation propagates
    confidentiality")."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "secret", sensitivity="confidential")
    citations = [Citation(concept_id="concepts/secret", title="Secret")]

    plan = _stage_filed_answer(
        question="what is the secret?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
    )

    assert plan.sensitivity == "confidential"
    assert "sensitivity: confidential" in plan.content


# --- Integration tests: `query --save` --------------------------------------


def test_query_purity_without_save_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`query` WITHOUT `--save` produces byte-identical stdout+stderr vs the
    pre-existing read-only path, and creates no new file/index/log entry
    (spec: "Read-Only Purity Without `--save`")."""
    _init_workspace(tmp_path, monkeypatch)
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    index_before = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    log_before = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Stoicism teaches the dichotomy of control.\n"
        "\n"
        "Citations:\n"
        "  → concepts/stoicism (Stoicism)\n"
    )
    assert result.stderr == (
        "retrieval: 1 FTS + 0 dense + 0 graph → 0 fused → LLM invoked → 1 cited\n"
    )
    assert (tmp_path / "bundle" / "index.md").read_text(
        encoding="utf-8"
    ) == index_before
    assert (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8") == log_before
    assert not (tmp_path / "bundle" / "concepts" / "what-is-stoicism.md").exists()


def test_query_save_writes_concept_index_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--save` on a matched answer with citations writes the concept file
    (body=answer, title=question, type=Concept, provenance=cited ids), adds
    the `index.md` bullet, and appends the "Filed answer" log line (spec:
    "`--save` Files The Cited Answer As A New Concept")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "concepts" / "what-is-stoicism.md"
    assert concept_path.is_file()
    content = concept_path.read_text(encoding="utf-8")
    assert "title: what is stoicism?" in content
    assert "type: Concept" in content
    assert "Stoicism teaches the dichotomy of control." in content
    assert "provenance:\n- concepts/stoicism\n" in content
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "what-is-stoicism.md" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Filed answer**" in log_text
    assert "what-is-stoicism.md" in log_text
    assert "from query" in log_text
    assert "reindex" in result.output.lower()


def test_query_save_overrides_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--title`/`--description`/`--type` overrides propagate to the written
    concept; an invalid `--type` exits non-zero with no write (spec:
    "`--title`, `--description`, `--type` override defaults")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(
        app,
        [
            "query",
            "what is stoicism?",
            "--save",
            "--auto",
            "--title",
            "Stoicism Primer",
            "--description",
            "A short primer.",
            "--type",
            "Procedure",
        ],
    )

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / "procedures" / "stoicism-primer.md"
    assert concept_path.is_file()
    content = concept_path.read_text(encoding="utf-8")
    assert "title: Stoicism Primer" in content
    assert "description: A short primer." in content
    assert "type: Procedure" in content


def test_query_save_invalid_type_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(
        app,
        ["query", "what is stoicism?", "--save", "--auto", "--type", "NotAType"],
    )

    assert result.exit_code != 0
    assert not (tmp_path / "bundle" / "concepts" / "what-is-stoicism.md").exists()


def test_query_save_zero_citations_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero-citation matched answer with `--save` refuses, exits non-zero,
    no write (spec: "Zero citations refuse to file")."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = _fake_matched_answer(citations=[])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    index_before = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code != 0
    assert "nothing to file" in result.stderr
    assert (tmp_path / "bundle" / "index.md").read_text(
        encoding="utf-8"
    ) == index_before


def test_query_save_no_match_never_reaches_save_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--save` on a NO_MATCH result never reaches the save block at all --
    the early `no_match_cause` return precedes it (design: "Purity")."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="zero_hits",
        skip_notices=[],
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is nothing?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "nothing to file" not in result.stderr


def test_query_save_preview_and_confirm_on_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preview is shown; a TTY without `--auto` requires confirmation before
    write (spec: "TTY confirms before writing")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    assert result.exit_code == 0
    assert "proposed changes" in result.output.lower()
    assert (tmp_path / "bundle" / "concepts" / "what-is-stoicism.md").is_file()


def test_query_save_auto_bypasses_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` bypasses the confirmation prompt (spec: "`--auto` or
    `review: false` bypasses the prompt")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert (tmp_path / "bundle" / "concepts" / "what-is-stoicism.md").is_file()


def test_query_save_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-TTY without `--auto` refuses to write, exits non-zero, bundle
    unchanged (spec: "Non-TTY without `--auto` refuses to write")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    index_before = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"])

    assert result.exit_code != 0
    assert "--auto" in result.stderr
    assert not (tmp_path / "bundle" / "concepts" / "what-is-stoicism.md").exists()
    assert (tmp_path / "bundle" / "index.md").read_text(
        encoding="utf-8"
    ) == index_before


def test_query_save_slug_collision_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing file at the target slug path refuses, exits non-zero,
    no write (design: "Slug collision handling (mirror ingest)")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    _write_concept(
        tmp_path / "bundle", "concepts", "what-is-stoicism", title="Existing"
    )
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    before = (tmp_path / "bundle" / "concepts" / "what-is-stoicism.md").read_text(
        encoding="utf-8"
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code != 0
    assert "already exists" in result.stderr
    assert (tmp_path / "bundle" / "concepts" / "what-is-stoicism.md").read_text(
        encoding="utf-8"
    ) == before


def test_query_save_prints_reindex_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful save prints a hint to run `openkos reindex` (spec:
    "Successful filing hints at reindex")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "openkos reindex" in result.output
