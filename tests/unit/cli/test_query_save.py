"""Unit and integration tests for `query --save` (two-output-rule):
files the just-printed cited answer back as a new derived OKF concept.

Unit tests exercise `_stage_filed_answer` directly (Phase A staging, no
writes -- mirrors `_stage_derived_objects`'s test shape in
`test_ingest.py`). Integration tests drive the full `query --save` CLI path
through `CliRunner`, patching `openkos.cli.main.answer` exactly like
`test_query.py` does, so these tests are zero network, zero real Ollama
process, zero real FTS5/vector/graph index.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.cli import main
from openkos.cli.main import _stage_filed_answer, app
from openkos.graph import sqlite_graph
from openkos.retrieval.answer import NO_MATCH, AnswerResult, Citation
from openkos.state import fts, vectorstore
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import (
    changed_paths,
    confirm_after,
    echo_after,
    snapshot_with_mtime,
)
from tests.unit.vcs.conftest import isolate_git_identity

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


def _set_config_field(tmp_path: Path, old: str, new: str) -> None:
    """Patch a single line in the generated `openkos.yaml` (mirrors
    `test_ingest.py::_set_config_field`)."""
    config_path = tmp_path / "openkos.yaml"
    content = config_path.read_text(encoding="utf-8")
    assert old in content
    config_path.write_text(content.replace(old, new), encoding="utf-8")


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
    """Without `--title`/`--description`: the description defaults to the
    question, and the title FALLS BACK to the question when the answer's
    first sentence is unusable (here: 11 chars, below the declarative
    minimum) -- the pre-#570 default, kept as the safety net."""
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
    _write_concept(bundle_dir, "insights", "what-is-stoicism", title="Existing")
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


def test_stage_filed_answer_missing_citation_folds_confidential(
    tmp_path: Path,
) -> None:
    """A cited concept whose file is MISSING at save time folds to
    `confidential` -- the most-restrictive level, NOT skipped (fail-closed:
    "cannot verify sensitivity -> confidential", mirroring `okf._rank` /
    `blocks_llm_send`; skipping would under-classify a filed answer that may
    have synthesized confidential content)."""
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

    assert plan.sensitivity == "confidential"
    assert "sensitivity: confidential" in plan.content


def test_stage_filed_answer_sensitivity_survives_a_decomposed_citation_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#473: a citation id is NFC (`okf.concept_id_for` normalizes every id
    derived from a walked path) while the on-disk filename can still be NFD
    on a byte-exact filesystem (a bundle authored on HFS+, committed, and
    cloned onto ext4). The sensitivity re-read must resolve the document
    through `okf.concept_path_for` like every other reconstruction site --
    reading the NFC spelling directly misses, falls into the broad
    fail-closed `except`, and silently folds a READABLE citation's `private`
    to `confidential`.

    Byte-exact lookups are simulated (a name must appear verbatim in its
    parent's real directory listing) so the miss also reproduces on macOS,
    whose filesystems resolve either spelling insensitively."""
    nfc_stem = "café"  # precomposed e-acute, one code point
    nfd_stem = "café"  # e + combining acute, two code points
    assert nfc_stem != nfd_stem, "fixture must use two distinct spellings"
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", nfd_stem, title="Cafe")

    real_exists = Path.exists
    real_read_text = Path.read_text
    real_iterdir = Path.iterdir

    def _byte_exact(self: Path) -> bool:
        try:
            return self.name in {p.name for p in real_iterdir(self.parent)}
        except OSError:
            return False

    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: _byte_exact(self) if self.suffix == ".md" else real_exists(self),
    )

    def _byte_exact_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.suffix == ".md" and not _byte_exact(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _byte_exact_read_text)

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=[Citation(concept_id=f"concepts/{nfc_stem}", title="Cafe")],
        bundle_dir=bundle_dir,
        default_sensitivity="public",
        timestamp="2026-07-23T00:00:00Z",
    )

    assert plan.sensitivity == "private"
    assert "sensitivity: private" in plan.content


def test_stage_filed_answer_malformed_frontmatter_folds_confidential(
    tmp_path: Path,
) -> None:
    """A cited concept with MALFORMED frontmatter (unparseable YAML) does not
    crash `_stage_filed_answer` -- it is treated the same as unreadable and
    folds the running floor to `confidential` (fail-closed, mirrors
    `_assemble_context`'s broad `except Exception` in
    `retrieval/answer.py`)."""
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "malformed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: Concept\ntitle: [unterminated\n---\nbody\n", encoding="utf-8"
    )
    citations = [Citation(concept_id="concepts/malformed", title="Malformed")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
    )

    assert plan.sensitivity == "confidential"
    assert "sensitivity: confidential" in plan.content


def test_stage_filed_answer_all_readable_private_stays_private(
    tmp_path: Path,
) -> None:
    """When every cited concept is readable/parseable and `private`, the
    floor stays `private` -- fail-closed only applies to unreadable/
    unparseable citations, not to legitimately lower sensitivities."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism", sensitivity="private")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

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
        "retrieval: 1 FTS + 0 dense → 0 fused → LLM invoked → 1 cited\n"
    )
    assert (tmp_path / "bundle" / "index.md").read_text(
        encoding="utf-8"
    ) == index_before
    assert (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8") == log_before
    assert not (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).exists()


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
    concept_path = (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    )
    assert concept_path.is_file()
    content = concept_path.read_text(encoding="utf-8")
    assert "title: Stoicism teaches the dichotomy of control" in content
    assert "type: Insight" in content
    assert "Stoicism teaches the dichotomy of control." in content
    assert "provenance:\n- concepts/stoicism\n" in content
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "stoicism-teaches-the-dichotomy-of-control.md" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Filed answer**" in log_text
    assert "stoicism-teaches-the-dichotomy-of-control.md" in log_text
    assert "from query" in log_text
    # #640: the write-time refresh replaced the manual-reindex instruction.
    assert "indexed and searchable" in result.output


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
    assert not (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).exists()


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
    assert (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).is_file()


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
    assert (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).is_file()


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
    assert not (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).exists()
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
        tmp_path / "bundle",
        "insights",
        "stoicism-teaches-the-dichotomy-of-control",
        title="Existing",
    )
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    before = (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).read_text(encoding="utf-8")

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code != 0
    assert "already exists" in result.stderr
    assert (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).read_text(encoding="utf-8") == before


def test_query_save_malformed_citation_does_not_crash_and_files_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cited concept with malformed frontmatter no longer crashes `query
    --save` with a raw `yaml.YAMLError` traceback -- it exits cleanly and
    the filed concept is folded to `confidential` (fail-closed reliability
    fix; the on-disk citation may have drifted to malformed since the last
    reindex, and `query` reads a possibly-stale index)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    path = bundle_dir / "concepts" / "malformed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: Concept\ntitle: [unterminated\n---\nbody\n", encoding="utf-8"
    )
    citation = Citation(concept_id="concepts/malformed", title="Malformed")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    concept_path = (
        bundle_dir / "insights" / "stoicism-teaches-the-dichotomy-of-control.md"
    )
    assert concept_path.is_file()
    assert "sensitivity: confidential" in concept_path.read_text(encoding="utf-8")


def test_query_save_review_false_skips_the_prompt_like_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config `review: false` skips the confirmation prompt the same as
    `--auto`, without passing `--auto` (mirrors
    `test_ingest.py::test_review_false_skips_the_prompt_like_auto`; the
    docstring already claims `--auto` OR `review: false` bypasses the
    prompt, but only `--auto` was previously exercised for `query --save`)."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(tmp_path, "review: true", "review: false")
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).is_file()


def test_query_save_success_reports_searchable_not_a_manual_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful save used to hint at a manual `openkos reindex` (the
    original spec line "Successful filing hints at reindex") -- since #640
    the save itself refreshes the derived indexes, so the hint would be
    false; the success line reports the insight as indexed instead. The
    manual-reindex pointer survives only on the refresh's degrade path
    (covered by `test_write_time_refresh.py`)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "Run `openkos reindex` to make it searchable." not in result.output
    assert "indexed and searchable" in result.output


# -- #313: re-validate every write target after the confirm gate ------------

_SAVE_WRITE_TARGETS = ("bundle/index.md", "bundle/log.md")
"""The two `write_atomic` targets `query --save` passes to the drift guard.

Named once and spread into every case below so the roster lives in ONE place
in this module rather than in each `parametrize` list. It is NOT coupled to
the guard's mapping in `cli/main.py` -- nothing here introspects that dict --
so adding a target there and forgetting this tuple still leaves the suite
green. Closing that gap is what #327 is about; this constant only makes the
edit a one-liner once someone remembers to make it.

The answer document is a third write and deliberately not in the mapping;
`query`'s docstring says why."""


def _changed_under_bundle(
    before: Mapping[Path, object], after: Mapping[Path, object]
) -> set[Path]:
    """Paths under `bundle/` whose snapshot entry changed.

    Scoped to `bundle/` because opening the retrieval stores makes SQLite
    create and remove `-wal`/`-shm` sidecars beside `.openkos/*.db` on
    every run, and an unscoped comparison reports them as changes that have
    nothing to do with what the verb wrote. `query` is the verb whose TESTS
    hit this first, not the only guarded verb that opens the stores (#332):
    `ingest` opens and writes `vectors.db` through `_embed_after_ingest`,
    so its suite has the same sidecar problem waiting -- this helper stays
    here only until a second suite actually needs it. Every path
    `query --save` writes lives under `bundle/`, so nothing the assertion
    is about is excluded -- but note the converse: a write this verb should
    NOT be making, outside `bundle/`, would not be caught here either.
    """
    return {
        k for k in changed_paths(before, after) if k.parts and k.parts[0] == "bundle"
    }


def _matched_answer_on_a_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace with one cited concept and a stubbed matched answer, on a
    TTY -- the minimum that reaches `query --save`'s confirm gate with both
    write targets present."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    _simulate_tty(monkeypatch)


@pytest.mark.parametrize("target", _SAVE_WRITE_TARGETS)
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313: `query --save` renders the new `index.md` and `log.md` from a
    pre-prompt read and then writes those exact bytes, so an edit landing
    while the operator reads the preview was overwritten in full.

    `query` is the one guarded verb where the operator has just been shown
    an answer and is deciding whether to keep it -- a human-scale pause by
    construction, which is exactly when a second terminal lands an edit.
    """
    _matched_answer_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    # The answer document was never created: the guard precedes every write.
    assert not (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).exists()
    after = snapshot_with_mtime(tmp_path)
    assert _changed_under_bundle(before, after) == {Path(target)}


@pytest.mark.parametrize("target", _SAVE_WRITE_TARGETS)
def test_a_write_target_deleted_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A target that has since been DELETED is drift too: re-creating it
    from a snapshot the operator can no longer see is the same silent
    revert as overwriting it."""
    _matched_answer_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / target
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, target_path.unlink)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert not target_path.exists()
    after = snapshot_with_mtime(tmp_path)
    assert _changed_under_bundle(before, after) == {Path(target)}


@pytest.mark.parametrize("target", _SAVE_WRITE_TARGETS)
def test_a_crlf_rewrite_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306's constraint, re-pinned for `query --save`: `read_text` applies
    universal-newline translation, so a CRLF rewrite compares EQUAL to its
    own LF snapshot and the guard would wave it through -- then
    `fsio.write_atomic` (which opens with `newline=""`) puts the LF plan
    back over the operator's CRLF file.
    """
    _matched_answer_on_a_tty(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_bytes(concurrent))

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_bytes() == concurrent
    after = snapshot_with_mtime(tmp_path)
    assert _changed_under_bundle(before, after) == {Path(target)}


def test_targets_that_were_already_crlf_are_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: BOTH targets already CRLF at rest, untouched,
    must not be reported as drift -- otherwise `query --save` refuses
    forever on a CRLF workspace, naming a cause that never happened."""
    _matched_answer_on_a_tty(tmp_path, monkeypatch)
    for rel in _SAVE_WRITE_TARGETS:
        path = tmp_path / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr
    assert (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).is_file()


@pytest.mark.parametrize("target", _SAVE_WRITE_TARGETS)
def test_drift_on_the_unprompted_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313: the guard must run on `--auto` too.

    Every other drift test here reaches the gate through `typer.confirm`,
    so indenting the `_reject_drifted_targets` call into the
    `if not auto and cfg.review:` block would disable it for unattended
    runs and leave all of them green.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    target_path = tmp_path / target
    concurrent = "hand-edited while the preview printed\n"
    before = snapshot_with_mtime(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    assert _changed_under_bundle(before, after) == {Path(target)}


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `query --save` (#327 follow-up; the pin
    existed only in `test_relate.py`): the guard's baseline and the new
    catalog's source text must come from the ONE `_snapshot_read`
    observation -- under a two-read shape a writer landing between the
    text-read and the bytes-read becomes the guard's own baseline, and
    Phase B writes the catalog rendered from the EARLIER text, silently
    reverting the edit.

    The edit lands immediately after `index.md`'s snapshot returns (the
    first of the verb's two snapshots), the earliest a concurrent writer
    can now land relative to the plan; the guard's re-read must call it
    drift and refuse the whole run.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    target_path = tmp_path / "bundle" / "index.md"
    concurrent = "hand-edited the instant the snapshot returned\n"
    real_snapshot_read = main._snapshot_read
    fired = False

    def racing_snapshot_read(path: Path) -> tuple[bytes, str]:
        nonlocal fired
        snapshot = real_snapshot_read(path)
        if not fired and path == target_path:
            fired = True
            target_path.write_text(concurrent, encoding="utf-8")
        return snapshot

    before = snapshot_with_mtime(tmp_path)
    monkeypatch.setattr(main, "_snapshot_read", racing_snapshot_read)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert fired, "the racing wrapper never saw the index.md snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/index.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    # The answer document was never created: the guard precedes every write.
    assert not (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).exists()
    assert _changed_under_bundle(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/index.md")
    }


def test_the_answer_document_created_during_the_prompt_is_not_clobbered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#313 review, R3: pin the premise the guard's exclusion rests on.

    `plan.path` is left out of the guard's mapping because it goes through
    `fsio.write_exclusive`, which fails closed if something appeared at that
    path meanwhile. Nothing pinned that: the existing collision test plants
    the file BEFORE the run, so Phase A's staging rejects it and execution
    never reaches the write. Swapping `write_exclusive` for `write_atomic`
    left every test in this module green.

    This lands the collision inside the prompt window instead -- the only
    window the exclusion's justification is about -- and asserts the run
    fails closed with the operator's file intact.

    #333: fail-closed alone was still mechanism-vacuous. TWO mechanisms
    could satisfy "non-zero exit, file intact": the intended one
    (`write_exclusive` raising `FileExistsError` out of Phase B) and a
    wrong one (someone adds `plan.path` to the guard mapping AND downgrades
    the write to `write_atomic` -- the guard has no Phase-A bytes for a
    file that did not exist, but a future "treat missing as empty" tweak
    would make it refuse here too). So this pins WHICH mechanism fired:
    the Phase-B save-failure message with the colliding path named
    workspace-relative, NOT the guard's refusal phrase, and exit code
    exactly 1 -- the guard exits 3 since wave 4, so the wrong mechanism
    cannot keep this green.
    """
    _matched_answer_on_a_tty(tmp_path, monkeypatch)
    answer_path = (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    )
    concurrent = "filed by someone else while the prompt waited\n"

    def _create_it() -> None:
        answer_path.parent.mkdir(parents=True, exist_ok=True)
        answer_path.write_text(concurrent, encoding="utf-8")

    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, _create_it)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    # Exactly 1: the Phase-B failure ladder's exit code, never the drift
    # guard's retryable 3 (#333).
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    # The failure surfaced through the save-failure path, naming the
    # colliding path workspace-relative...
    assert "failed while saving the answer --" in result.stderr
    assert (
        "bundle/insights/stoicism-teaches-the-dichotomy-of-control.md" in result.stderr
    )
    # ...and NOT through the drift guard, which never saw this file.
    assert "refusing to write --" not in result.stderr
    # The other file's content survives: create-only is what protects it.
    assert answer_path.read_text(encoding="utf-8") == concurrent
    # And the catalog was not touched -- the answer document is written
    # FIRST, so failing there leaves `index.md`/`log.md` untouched.
    assert _changed_under_bundle(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/insights/stoicism-teaches-the-dichotomy-of-control.md")
    }


# -- #331: Phase-B failure reporting and auto-commit -------------------------


def _fail_write_atomic_on(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Make `write_atomic` raise exactly when asked to write `target`,
    delegating every other path to the real implementation.

    This replaces a call-INDEX version (#327, wave-4 R3): counting calls
    globally meant the tests encoded "call 1 is `index.md`, call 2 is
    `log.md`" as a positional fact about today's Phase B, so any future
    `write_atomic` added BEFORE Phase B would silently shift which write
    fails -- the test would then be injecting the fault somewhere it never
    intended while its name and docstring kept claiming the old target.
    Keying on the written path pins the fault to the file the test is
    actually about, however many writes come to precede it."""
    real_write_atomic = fsio.write_atomic

    def _write(path: Path, content: str) -> None:
        if path == target:
            raise OSError("simulated disk failure")
        real_write_atomic(path, content)

    monkeypatch.setattr("openkos.cli.main.fsio.write_atomic", _write)


def test_a_failure_at_the_answer_write_reports_no_path_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#331: the answer document is write 1 of 3, so a failure THERE means
    nothing landed -- and the message must say so with the same "No path
    was written." clause its siblings use, never leave the operator to
    guess which of the three paths exists."""
    _matched_answer_on_a_tty(tmp_path, monkeypatch)

    def _failing_exclusive(path: Path, content: str) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr("openkos.cli.main.fsio.write_exclusive", _failing_exclusive)
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos query: failed while saving the answer --" in result.stderr
    assert "No path was written." in result.stderr
    assert "Already written" not in result.stderr
    assert _changed_under_bundle(before, snapshot_with_mtime(tmp_path)) == set()


def test_a_failure_at_the_index_write_names_the_landed_answer_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#331: failing write 2 of 3 (`index.md`) leaves the answer document
    on disk but uncataloged. The failure message must name exactly what
    landed -- content-before-catalog ordering means the partial state is
    benign (nothing references a missing file), but only if the operator
    can see WHICH state they are in."""
    _matched_answer_on_a_tty(tmp_path, monkeypatch)
    _fail_write_atomic_on(monkeypatch, tmp_path / "bundle" / "index.md")
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    assert result.exit_code == 1
    assert "openkos query: failed while saving the answer --" in result.stderr
    assert (
        "Already written (left partially filed, not rolled back): "
        "bundle/insights/stoicism-teaches-the-dichotomy-of-control.md." in result.stderr
    )
    # The message claims "not rolled back"; prove it on disk.
    assert (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).is_file()
    assert _changed_under_bundle(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/insights/stoicism-teaches-the-dichotomy-of-control.md")
    }


def test_a_failure_at_the_log_write_names_the_answer_and_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#331: failing write 3 of 3 (`log.md`) leaves the answer document AND
    the new `index.md` entry on disk; only the audit line is missing. The
    landed list must name both, in write order."""
    _matched_answer_on_a_tty(tmp_path, monkeypatch)
    _fail_write_atomic_on(monkeypatch, tmp_path / "bundle" / "log.md")
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save"], input="y\n")

    assert result.exit_code == 1
    assert "openkos query: failed while saving the answer --" in result.stderr
    assert (
        "Already written (left partially filed, not rolled back): "
        "bundle/insights/stoicism-teaches-the-dichotomy-of-control.md, bundle/index.md."
        in result.stderr
    )
    assert (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "stoicism-teaches-the-dichotomy-of-control" in index_text
    assert _changed_under_bundle(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/insights/stoicism-teaches-the-dichotomy-of-control.md"),
        Path("bundle/index.md"),
    }


def test_query_save_success_autocommits_its_three_writes(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#331: `query --save` now auto-commits its Phase-B writes exactly like
    every other mutating verb (workspace-autocommit). It was the one
    mutating path without the safety net, for no documented reason -- the
    Slice-2 exclusion list names `reindex`, `init`, and read-only verbs
    only -- which left both a successful save AND a partial failure sitting
    uncommitted, invisible to the git-recovery story every sibling's
    docstring leans on."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)
    # The hand-planted cited concept is untracked; commit it so the ONLY
    # dirty paths after the save are the three the verb itself wrote.
    vcs_git.commit_paths(
        tmp_path,
        ["bundle/concepts/stoicism.md", "bundle/index.md"],
        "seed cited concept",
    )
    before = _commit_count(tmp_path)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert _commit_count(tmp_path) == before + 1
    assert (
        _last_commit_subject(tmp_path)
        == "openkos: query --save insights/stoicism-teaches-the-dichotomy-of-control"
    )
    assert _last_commit_files(tmp_path) == {
        "bundle/insights/stoicism-teaches-the-dichotomy-of-control.md",
        "bundle/index.md",
        "bundle/log.md",
    }
    assert vcs_git.is_clean(tmp_path) is True


def _commit_count(root: Path) -> int:
    result = vcs_git._run(["git", "log", "--format=%H"], cwd=root)
    return len([line for line in result.stdout.splitlines() if line])


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    return result.stdout.strip()


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(["git", "show", "--name-only", "--format=", "-1"], cwd=root)
    return {line for line in result.stdout.splitlines() if line}


# --- #569: --save discloses the inherited high-water mark -------------------


def test_query_save_preview_discloses_a_raised_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filed answer already inherited the high-water mark (ADR-0003);
    #569's gap was that the user was never TOLD. When the fold raises the
    sensitivity above the workspace default, the proposed-changes preview
    now names the inherited level on the concept line."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path / "bundle",
        "concepts",
        "secret",
        title="Secret",
        sensitivity="confidential",
    )
    citation = Citation(concept_id="concepts/secret", title="Secret", confidential=True)
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is secret?", "--save", "--auto"])

    assert result.exit_code == 0
    assert (
        "  + bundle/insights/stoicism-teaches-the-dichotomy-of-control.md (sensitivity: confidential, "
        "inherited from citations)" in result.stdout
    )
    content = (
        tmp_path
        / "bundle"
        / "insights"
        / "stoicism-teaches-the-dichotomy-of-control.md"
    ).read_text(encoding="utf-8")
    assert "sensitivity: confidential" in content


def test_query_save_preview_stays_silent_at_the_default_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the fold lands exactly on the workspace default, the concept
    line stays as before -- the disclosure exists for a RAISED mark, and
    printing it unconditionally would bury the one case that matters."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert (
        "  + bundle/insights/stoicism-teaches-the-dichotomy-of-control.md\n"
        in result.stdout
    )
    assert "inherited from citations" not in result.stdout


# --- Insight filing (issue #570) --------------------------------------------


def test_declarative_answer_title_promotes_a_usable_first_sentence() -> None:
    assert (
        main._declarative_answer_title(
            "Symmetric cryptography relies on modular arithmetic. More detail."
        )
        == "Symmetric cryptography relies on modular arithmetic"
    )


def test_declarative_answer_title_refuses_fragments_questions_and_prose() -> None:
    """Too short, too long, or itself a question -> `None`, so the caller
    falls back to the question (the pre-#570 default)."""
    assert main._declarative_answer_title("Yes.") is None
    assert main._declarative_answer_title("It depends.") is None
    assert main._declarative_answer_title("¿Qué es la criptografía simétrica?") is None
    assert main._declarative_answer_title("Is it modular arithmetic?") is None
    assert main._declarative_answer_title("word " * 40 + ".") is None


def test_stage_filed_answer_defaults_to_insight_with_declarative_title(
    tmp_path: Path,
) -> None:
    """Issue #570's core: the default filing is an `Insight` under
    `bundle/insights/`, titled by the answer's first sentence -- never a
    Concept whose ID is an interrogative sentence."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="Stoicism teaches the dichotomy of control. It divides.",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
    )

    assert plan.title == "Stoicism teaches the dichotomy of control"
    assert plan.description == "what is stoicism?"
    assert plan.link_dir == "insights"
    assert plan.section == "Insights"
    assert "type: Insight" in plan.content
    assert plan.slug == "stoicism-teaches-the-dichotomy-of-control"


def test_stage_filed_answer_classifiable_type_override_still_accepted(
    tmp_path: Path,
) -> None:
    """`--type Concept` remains valid -- Insight is the default, not a
    restriction on the buildable vocabulary."""
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
        doc_type="Concept",
    )

    assert plan.link_dir == "concepts"
    assert "type: Concept" in plan.content


def test_query_marks_insight_citations_as_synthesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cited Insight renders `[synthesis]` in the citation list, so the
    reader can tell which legs of the answer stand on a Source and which
    on an earlier model synthesis (issue #570)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = _fake_matched_answer(
        citations=[
            Citation(concept_id="insights/earlier-answer", title="Earlier"),
            Citation(concept_id="concepts/stoicism", title="Stoicism"),
        ]
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "→ insights/earlier-answer (Earlier) [synthesis]" in result.stdout
    assert "→ concepts/stoicism (Stoicism)" in result.stdout
    assert "concepts/stoicism (Stoicism) [synthesis]" not in result.stdout
    # Mixed citations: at least one leg reaches a Source, so no warning.
    assert "every citation is itself a filed synthesis" not in result.stderr


def test_query_warns_when_every_citation_is_a_synthesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When EVERY citation is an Insight, nothing beneath the answer
    reaches a Source -- the compounding case #570 warns about."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = _fake_matched_answer(
        citations=[
            Citation(concept_id="insights/answer-one", title="One"),
            Citation(concept_id="insights/answer-two", title="Two"),
        ]
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "every citation is itself a filed synthesis" in result.stderr
