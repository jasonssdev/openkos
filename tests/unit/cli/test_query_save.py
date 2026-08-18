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

from openkos import config, fsio
from openkos.cli import main
from openkos.cli.main import _stage_filed_answer, app
from openkos.graph import sqlite_graph
from openkos.resolution import insight_identity
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


def _opt_in_person_offset(tmp_path: Path) -> None:
    """Opt this workspace in to `type_sensitivity_defaults: {Person: 1}`.

    The packaged default is EMPTY since #756 -- sensitivity is the
    operator's call and no type is born above the floor unless they say so.
    The offset MECHANISM is unchanged and still has to be proven, so the
    tests that exercise it now configure it the way a real operator would
    instead of leaning on a shipped value that no longer exists.
    """
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\ntype_sensitivity_defaults:\n  Person: 1\n",
        encoding="utf-8",
    )


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


def _default_cfg(**overrides: object) -> config.Config:
    """Build a minimal `config.Config` for direct `_stage_filed_answer`
    calls (issue #669) -- mirrors `test_ingest.py::_default_cfg`'s
    hand-built-Config pattern exactly, so the shipped `{"Person": 1}`
    type-sensitivity-offset mapping applies by default; pass
    `type_sensitivity_defaults={}` to opt out entirely."""
    fields: dict[str, object] = {
        "model": "qwen3:8b",
        "review": True,
        "default_sensitivity": "private",
        "freshness_window": "7d",
        "embedding_model": "bge-m3",
        "chat_timeout": config.DEFAULT_CHAT_TIMEOUT,
        "max_generation_tokens": config.DEFAULT_MAX_GENERATION_TOKENS,
        "context_window": config.DEFAULT_CONTEXT_WINDOW,
        "confidential_local_exemption": config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION,
        "volatility_windows": {},
        "type_tiers": {},
        "models": {},
        "union_judge": config.DEFAULT_UNION_JUDGE,
        "sufficiency_check": config.DEFAULT_SUFFICIENCY_CHECK,
        "concurrent_extraction": config.DEFAULT_CONCURRENT_EXTRACTION,
        "type_sensitivity_defaults": {"Person": 1},
    }
    fields.update(overrides)
    return config.Config(**fields)  # type: ignore[arg-type]


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
        cfg=_default_cfg(),
    )

    assert "provenance:\n- concepts/stoicism\n- concepts/epictetus\n" in plan.content


def test_stage_filed_answer_title_description_default_to_question(
    tmp_path: Path,
) -> None:
    """Without `--title`/`--description`: the description defaults to the
    question, and when the answer's first sentence is unusable (here: 11
    chars, below the declarative minimum) the title falls to the QUESTION
    SUBJECT (#646) -- `what is stoicism?` names `stoicism`, so the filed
    identity is the subject, never the interrogative sentence."""
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
        cfg=_default_cfg(),
    )

    assert plan.title == "Stoicism"
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
        cfg=_default_cfg(),
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
            cfg=_default_cfg(),
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
        cfg=_default_cfg(),
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
            cfg=_default_cfg(),
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
            cfg=_default_cfg(),
        )


def test_stage_filed_answer_collision_raises(tmp_path: Path) -> None:
    """A pre-existing file at the target slug path refuses (design: "Slug
    collision handling (mirror ingest)")."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    # `what is stoicism?` + an unusable first sentence titles the filing
    # `Stoicism` (#646's subject rung), so THAT is the colliding slug.
    _write_concept(bundle_dir, "insights", "stoicism", title="Existing")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    with pytest.raises(ValueError, match="already exists"):
        _stage_filed_answer(
            question="what is stoicism?",
            answer_text="answer text",
            citations=citations,
            bundle_dir=bundle_dir,
            default_sensitivity="private",
            timestamp="2026-07-23T00:00:00Z",
            cfg=_default_cfg(),
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
        cfg=_default_cfg(),
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
        cfg=_default_cfg(),
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
        cfg=_default_cfg(),
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
        cfg=_default_cfg(),
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
        cfg=_default_cfg(),
    )

    assert plan.sensitivity == "confidential"
    assert "sensitivity: confidential" in plan.content


# --- type-sensitivity-defaults: `--save` birth seam (issue #669) ------------


def test_stage_filed_answer_type_default_raises_above_the_floor(
    tmp_path: Path,
) -> None:
    """`--type Person` births above the cited-concept high-water-mark given
    the shipped `{"Person": 1}` mapping (spec: `query-command` "A
    type-defaulted filed answer is saved above the cited high-water-mark").

    **Twin-rule guard** (design D6): this is the second of the two
    independent birth-seam site tests -- it must fail if ONLY the `--save`
    call site (`_stage_filed_answer`) reverts to
    `sensitivity=cited_high_water_mark`, independent of WU3's ingest-site
    test in `test_ingest.py`."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism", sensitivity="public")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="public",
        timestamp="2026-07-23T00:00:00Z",
        doc_type="Person",
        cfg=_default_cfg(default_sensitivity="public"),
    )

    assert plan.sensitivity == "private"
    assert plan.type_floor_raised is True
    assert "sensitivity: private" in plan.content


def test_stage_filed_answer_citation_high_water_mark_wins_over_type_default(
    tmp_path: Path,
) -> None:
    """A higher citation high-water-mark still wins over the type-defaulted
    floor -- `combine_sensitivity`'s high-water-mark is never bypassed by
    the offset (design D3: "the high-water-mark still wins outright
    whenever a source is more sensitive than that")."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "secret", sensitivity="confidential")
    citations = [
        Citation(concept_id="concepts/secret", title="Secret", confidential=True)
    ]

    plan = _stage_filed_answer(
        question="what is the secret?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="public",
        timestamp="2026-07-23T00:00:00Z",
        doc_type="Person",
        cfg=_default_cfg(default_sensitivity="public"),
    )

    assert plan.sensitivity == "confidential"
    # The high-water-mark, not the type default, produced `confidential`
    # here -- `resolved == cited_high_water_mark`, so the type default did
    # not raise anything.
    assert plan.type_floor_raised is False


def test_stage_filed_answer_cfg_is_required(tmp_path: Path) -> None:
    """#685 item 2: `cfg` has no default -- the pre-#669 `cfg=None` shape
    silently skipped the type-default security raise for any future caller
    that forgot the parameter. Requiring it turns that silent policy hole
    into a `TypeError` at the call site (and mypy at review time)."""
    import inspect

    param = inspect.signature(_stage_filed_answer).parameters["cfg"]

    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_stage_filed_answer_empty_mapping_is_untouched_by_the_type_default(
    tmp_path: Path,
) -> None:
    """An empty `type_sensitivity_defaults` mapping is the supported
    opt-out (#685 item 2 retired the `cfg=None` shape): the
    cited high-water-mark alone decides, no type-default raise applied."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism", sensitivity="public")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    plan = _stage_filed_answer(
        question="what is stoicism?",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="public",
        timestamp="2026-07-23T00:00:00Z",
        doc_type="Person",
        cfg=_default_cfg(type_sensitivity_defaults={}),
    )

    assert plan.sensitivity == "public"
    assert plan.type_floor_raised is False


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
    # issue #669, design D4: the `!` consequence line prints whenever the
    # resolved level is `confidential`, regardless of which branch (citation
    # inheritance here, the type default in the sibling tests below)
    # produced it.
    assert (
        "  ! confidential: excluded from query, contradictions, and "
        "suggest-relations against a non-local backend." in result.stdout
    )


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
    assert "excluded from query" not in result.stdout


# --- type-sensitivity-defaults: `--save` preview + success advisory (#669) --


def test_query_save_preview_names_the_type_default_when_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three-way preview branch (design D4): the type-default cause
    outranks the citation cause -- names the raise with `raised by the
    {Type} type default`, distinct from the citation-inherited wording,
    and stays silent on the confidential-consequence line at a non-
    confidential raised level."""
    _init_workspace(tmp_path, monkeypatch)
    _opt_in_person_offset(tmp_path)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    _write_concept(
        tmp_path / "bundle",
        "concepts",
        "stoicism",
        title="Stoicism",
        sensitivity="public",
    )
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(
        app, ["query", "what is stoicism?", "--save", "--auto", "--type", "Person"]
    )

    assert result.exit_code == 0
    assert "(sensitivity: private, raised by the Person type default)" in result.stdout
    assert "inherited from citations" not in result.stdout
    assert "excluded from query" not in result.stdout


def test_query_save_preview_confidential_consequence_via_type_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `!` consequence line prints when the TYPE DEFAULT (not citation
    inheritance) is the route that landed the resolved level on
    `confidential` -- the consequence belongs to the level, not the cause
    (design D4)."""
    _init_workspace(tmp_path, monkeypatch)
    _opt_in_person_offset(tmp_path)
    _write_concept(
        tmp_path / "bundle",
        "concepts",
        "stoicism",
        title="Stoicism",
        sensitivity="private",
    )
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(
        app, ["query", "what is stoicism?", "--save", "--auto", "--type", "Person"]
    )

    assert result.exit_code == 0
    assert (
        "(sensitivity: confidential, raised by the Person type default)"
        in result.stdout
    )
    assert (
        "  ! confidential: excluded from query, contradictions, and "
        "suggest-relations against a non-local backend." in result.stdout
    )


def test_query_save_success_message_names_the_type_default_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec Requirement 6 / `query-command` spec: the success message
    (immediately after `filed answer as ...`) carries the born-above-floor
    advisory, naming count + type + level, with the #569 consequence line
    at `confidential` -- fires even under `--auto` (the preview block is
    printed unconditionally in this codebase, but the success message must
    never depend on it either way)."""
    _init_workspace(tmp_path, monkeypatch)
    _opt_in_person_offset(tmp_path)
    _write_concept(
        tmp_path / "bundle",
        "concepts",
        "stoicism",
        title="Stoicism",
        sensitivity="private",
    )
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(
        app, ["query", "what is stoicism?", "--save", "--auto", "--type", "Person"]
    )

    assert result.exit_code == 0
    stdout = result.stdout
    filed_idx = stdout.index("openkos query: filed answer as")
    advisory_idx = stdout.index(
        "openkos query: 1 concept was born above the workspace sensitivity "
        "floor by type default (Person -> confidential)."
    )
    consequence_idx = stdout.index(
        "openkos query: confidential concepts are excluded from query, "
        "contradictions, and suggest-relations against a non-local backend "
        "(#569)."
    )
    assert filed_idx < advisory_idx < consequence_idx


def test_query_save_success_message_silent_when_nothing_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No per-type-default advisory when the filed type has no configured
    offset (the default `--type Insight` here is not in the shipped
    `{"Person": 1}` mapping)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    citation = Citation(concept_id="concepts/stoicism", title="Stoicism")
    fake_result = _fake_matched_answer(citations=[citation])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "type default" not in result.stdout


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


def test_question_subject_strips_definitional_scaffolding() -> None:
    """#646: the deterministic subject rung -- a definitional question's
    interrogative scaffolding strips away, the leading article strips, a
    trailing chained interrogative clause strips, and the first letter is
    capitalized so the subject reads as a title."""
    assert (
        main._question_subject("¿qué es el Model Context Protocol?")
        == "Model Context Protocol"
    )
    assert main._question_subject("¿qué es MCP y para qué sirve?") == "MCP"
    assert main._question_subject("what is the context window?") == "Context window"
    assert main._question_subject("¿para qué sirve el índice FTS?") == "Índice FTS"
    assert main._question_subject("¿cómo funciona la ingesta?") == "Ingesta"
    assert main._question_subject("what are embeddings?") == "Embeddings"


def test_question_subject_refuses_non_definitional_questions() -> None:
    """A question whose shape the rung does not recognize returns `None`
    so the ladder falls through to the question verbatim -- guessing a
    subject out of an open question would title the filing wrong."""
    assert main._question_subject("¿qué decidimos sobre el almacenamiento?") is None
    assert main._question_subject("summarize the meeting") is None
    assert main._question_subject("¿qué es?") is None
    assert main._question_subject("what is   ?") is None


def test_clause_connectors_are_space_delimited() -> None:
    """The cut filter keeps `str.find` results `> 0`, which is only a correct
    spelling of "found" because a leading space makes index 0 unreachable.

    Raised by the readability lens on this change: the guard reads as if it
    were excluding position 0, when what it excludes is `-1`. Rather than
    rewrite the filter, pin the invariant it rests on -- an entry added
    without its spaces would go silently inert, never loudly wrong, and that
    is the failure mode a test earns its keep against."""
    for connector in main._CLAUSE_CONNECTORS:
        assert connector.startswith(" "), connector
        assert connector.endswith(" "), connector


def test_clause_answer_title_promotes_the_first_clause_of_an_overlong_opening() -> None:
    """#696: a real Spanish opening runs past the declarative ceiling, so
    rung 1 refuses and -- when the question names no subject either -- the
    filing used to be named after the question. The clause rung cuts the
    same sentence at its first clause boundary and promotes that."""
    assert (
        main._clause_answer_title(
            "La relación entre la trazabilidad y la verdad contextual en "
            "sistemas RAG radica en que cada afirmación generada debe poder "
            "rastrearse hasta su fuente original."
        )
        == "La relación entre la trazabilidad y la verdad contextual en sistemas RAG"
    )


def test_clause_answer_title_defers_to_the_declarative_rung_within_the_ceiling() -> (
    None
):
    """A sentence rung 1 can already promote is NOT this rung's business.

    This is what keeps the change additive: every answer that resolves at
    rung 1 today must reach this helper and be refused, or the filings whose
    titles are already correct would start being cut short."""
    within = "Symmetric cryptography relies on modular arithmetic. More detail."
    assert main._declarative_answer_title(within) is not None
    assert main._clause_answer_title(within) is None


def test_clause_answer_title_cuts_at_the_earliest_competing_boundary() -> None:
    """`min(cuts)` picks the EARLIEST of a comma and up to seventeen
    connectors, and nothing else pinned that choice.

    Raised by the reliability lens on this change: every other test supplies
    a sentence with one cut point, so the tie-break was untested. Both cases
    below carry two real boundaries at once, and in each the LATER one would
    also pass the length bounds — so a `max`, or a first-match-wins over the
    connector tuple's own order, produces a different, still-plausible title
    and every other test stays green."""
    # A comma at 44 and ` porque ` at 62: the comma wins.
    assert (
        main._clause_answer_title(
            "La trazabilidad de cada afirmación generada, que es la base del "
            "sistema, importa porque sin ella nada se verifica jamás."
        )
        == "La trazabilidad de cada afirmación generada"
    )
    # Two connectors, no comma: ` es la ` precedes ` porque `, and ` es la `
    # sits later in `_CLAUSE_CONNECTORS` than ` porque `, so a tuple-order
    # scan would cut at the wrong one.
    assert (
        main._clause_answer_title(
            "La verdad contextual del sistema es la propiedad central porque "
            "sostiene cada afirmación que el modelo genera al responder."
        )
        == "La verdad contextual del sistema"
    )


def test_clause_answer_title_refuses_questions_and_markdown() -> None:
    """The over-ceiling test alone would admit a long question or a markdown
    opening, both of which rung 1 refuses for a reason that has nothing to do
    with length."""
    assert (
        main._clause_answer_title(
            "¿Por qué la trazabilidad importa tanto en un repositorio de "
            "conocimiento local, y qué pasa si falta?"
        )
        is None
    )
    assert (
        main._clause_answer_title(
            "- La trazabilidad es la propiedad que permite rastrear cada "
            "afirmación hasta su fuente original de origen."
        )
        is None
    )


def test_clause_answer_title_refuses_a_residue_outside_the_bounds() -> None:
    """The cut inherits rung 1's own bounds: a two-word left part names as
    little as a fragment does, and a clause that is still a paragraph is
    still a paragraph."""
    assert (
        main._clause_answer_title(
            "El MVP, entendido como la versión más pequeña del producto que "
            "ya entrega valor real al usuario, sirve para aprender."
        )
        is None
    )


def test_clause_answer_title_cut_index_survives_a_length_changing_lowercase() -> None:
    """The cut index must be measured against the string it slices.

    `str.lower()` is not length-preserving: `"İ"` lowers to TWO codepoints,
    so an index taken from `candidate.lower()` drifts one position right per
    such character. Two review lenses found this independently on this
    change. With three of them ahead of the connector the drift is three
    characters, which cuts inside the preceding word rather than at the
    clause boundary."""
    cut = main._clause_answer_title(
        "İİİstanbul y su repositorio local de conocimiento compartido "
        "es la base sobre la que se apoya toda la trazabilidad declarada."
    )
    assert cut == "İİİstanbul y su repositorio local de conocimiento compartido"


def test_clause_answer_title_refuses_a_residue_over_the_ceiling() -> None:
    """The UPPER arm of the residue bound, which the too-short case cannot
    reach (reliability lens, this change).

    The cut here lands past `_DECLARATIVE_TITLE_MAX_CHARS`, so the residue is
    still prose. Dropping the upper bound would file a paragraph as the
    permanent Concept ID -- exactly what the ceiling exists to prevent, and
    the too-short test would stay green throughout."""
    cut = main._clause_answer_title(
        "La trazabilidad de cada afirmación generada por el sistema hasta su "
        "fuente original inmutable y verificable es la propiedad central."
    )
    assert cut is None


def test_clause_answer_title_refuses_a_residue_with_no_letters() -> None:
    """The `isalpha` guard, driven for real (reliability lens, this change).

    A residue of digits and punctuation names nothing, and would slug to
    something unusable. Reachable because the earliest cut can land after a
    purely numeric opening."""
    cut = main._clause_answer_title(
        "2024 2025 2026 2027 2028 2029 2030 2031 2032, la trazabilidad "
        "quedó definida como la propiedad central del repositorio local."
    )
    assert cut is None


def test_clause_answer_title_refuses_a_sentence_with_no_clause_boundary() -> None:
    """No comma and no connector means no defensible cut, so the ladder
    falls through to the question verbatim exactly as it does today."""
    assert (
        main._clause_answer_title(
            "Rastrear afirmaciones generadas hasta fuentes originales "
            "inmutables mediante cadenas largas de procedencia declarada"
        )
        is None
    )


def test_stage_filed_answer_still_falls_through_to_the_question_verbatim(
    tmp_path: Path,
) -> None:
    """All THREE rungs refuse and the pre-#570 safety net still catches.

    Raised by the reliability lens on this change: the clause rung was
    spliced in directly above the terminal `or question`, and no test drove
    a filing all the way through it to that fallback. Without this, the
    safety net could be deleted and the suite would stay green for every
    question shape that happens to resolve earlier.

    The answer here overruns the declarative ceiling (so rung 1 refuses) and
    carries neither a comma nor any `_CLAUSE_CONNECTORS` entry (so the clause
    rung refuses), and the question is not a definitional scaffold (so the
    subject rung refuses)."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "trazabilidad")
    citations = [Citation(concept_id="concepts/trazabilidad", title="Trazabilidad")]
    question = "¿qué decidimos sobre el almacenamiento?"
    answer_text = (
        "Rastrear afirmaciones generadas hasta fuentes originales inmutables "
        "mediante cadenas largas de procedencia declarada por cada objeto"
    )
    assert main._declarative_answer_title(answer_text) is None
    assert main._question_subject(question) is None
    assert main._clause_answer_title(answer_text) is None

    plan = _stage_filed_answer(
        question=question,
        answer_text=answer_text,
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        cfg=_default_cfg(),
    )

    assert plan.title == question
    assert plan.slug == "qué-decidimos-sobre-el-almacenamiento"


def test_stage_filed_answer_prefers_the_subject_over_the_clause(
    tmp_path: Path,
) -> None:
    """MEASURED ORDERING (evals/query_title/): the clause rung sits BELOW the
    subject rung, not above it.

    Placed above, `¿qué es la trazabilidad?` over a long Spanish opening cut
    to `La trazabilidad` -- article and all -- where the shipped subject rung
    gives the cleaner `Trazabilidad`. A clause cut is a degraded declarative;
    a recognized definitional subject beats it every time."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "trazabilidad")
    citations = [Citation(concept_id="concepts/trazabilidad", title="Trazabilidad")]

    plan = _stage_filed_answer(
        question="¿qué es la trazabilidad?",
        answer_text=(
            "La trazabilidad es la propiedad que permite rastrear cada "
            "afirmación generada por el sistema hasta la fuente original."
        ),
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        cfg=_default_cfg(),
    )

    assert plan.title == "Trazabilidad"
    assert plan.slug == "trazabilidad"


def test_stage_filed_answer_uses_the_clause_when_the_question_names_no_subject(
    tmp_path: Path,
) -> None:
    """#696's own evidence question, end to end: `¿por qué es importante...?`
    is not a definitional scaffold, so the subject rung refuses too, and the
    filing used to take the question verbatim as its permanent Concept ID."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "trazabilidad")
    citations = [Citation(concept_id="concepts/trazabilidad", title="Trazabilidad")]

    plan = _stage_filed_answer(
        question="¿por qué es importante la trazabilidad en un sistema de conocimiento?",
        answer_text=(
            "La trazabilidad es importante en un sistema de conocimiento "
            "porque sin ella una respuesta correcta y una inventada son "
            "indistinguibles para quien la lee."
        ),
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        cfg=_default_cfg(),
    )

    assert plan.title == "La trazabilidad es importante en un sistema de conocimiento"
    assert plan.slug == "la-trazabilidad-es-importante-en-un-sistema-de-conocimiento"
    assert plan.description == (
        "¿por qué es importante la trazabilidad en un sistema de conocimiento?"
    )


def test_stage_filed_answer_uses_the_subject_when_the_sentence_is_unusable(
    tmp_path: Path,
) -> None:
    """#646's production shape exactly: a long Spanish answer whose first
    sentence exceeds the declarative ceiling used to fall back to the
    QUESTION VERBATIM (`¿qué es el Model Context Protocol?` became the
    permanent Concept ID). The subject rung now files it under the
    subject."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "mcp")
    citations = [Citation(concept_id="concepts/mcp", title="MCP")]
    long_first_sentence = (
        "El Model Context Protocol es un protocolo abierto que permite a "
        "los modelos de lenguaje conectarse con herramientas externas y "
        "estandariza la integración entre clientes y servidores. Más "
        "detalle después."
    )

    plan = _stage_filed_answer(
        question="¿qué es el Model Context Protocol?",
        answer_text=long_first_sentence,
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        cfg=_default_cfg(),
    )

    assert plan.title == "Model Context Protocol"
    assert plan.description == "¿qué es el Model Context Protocol?"
    assert plan.slug == "model-context-protocol"


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
        cfg=_default_cfg(),
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
    restriction on the buildable vocabulary. The question here is one the
    #646 subject rung does NOT recognize: with `--type Concept` the filing
    targets `concepts/`, where a subject-titled `Stoicism` would collide
    with the cited fixture concept itself."""
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts", "stoicism")
    citations = [Citation(concept_id="concepts/stoicism", title="Stoicism")]

    plan = _stage_filed_answer(
        question="summarize stoicism",
        answer_text="answer text",
        citations=citations,
        bundle_dir=bundle_dir,
        default_sensitivity="private",
        timestamp="2026-07-23T00:00:00Z",
        doc_type="Concept",
        cfg=_default_cfg(),
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


def test_query_warns_proportionally_at_half_synthesis_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#649: the all-or-nothing guard never fired while the share climbed.
    At >= 0.5 synthesis share the proportional warning names the count --
    here 2 of 4 -- before the base drifts to compounding on model output."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = _fake_matched_answer(
        citations=[
            Citation(concept_id="insights/answer-one", title="One"),
            Citation(concept_id="insights/answer-two", title="Two"),
            Citation(concept_id="concepts/stoicism", title="Stoicism"),
            Citation(concept_id="sources/notes", title="Notes"),
        ]
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "2 of 4 citations are themselves filed syntheses" in result.stderr
    assert "every citation is itself a filed synthesis" not in result.stderr


def test_query_below_the_synthesis_share_threshold_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 of 5 (the #649 evidence case) is visible via the `[synthesis]`
    markers but does not warn -- the threshold keeps the advisory rare
    enough to stay read."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = _fake_matched_answer(
        citations=[
            Citation(concept_id="insights/answer-one", title="One"),
            Citation(concept_id="concepts/stoicism", title="Stoicism"),
            Citation(concept_id="concepts/epictetus", title="Epictetus"),
            Citation(concept_id="sources/notes", title="Notes"),
            Citation(concept_id="sources/course", title="Course"),
        ]
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "filed syntheses" not in result.stderr
    assert "every citation is itself a filed synthesis" not in result.stderr


# --- the answer that reports drawing on nothing (#753) ----------------------


def _fake_unsupported_answer(
    *, answer: str = "A general essay about RAG systems."
) -> AnswerResult:
    """An answer the model reported drawing on NO context block.

    Distinct from a no-match: retrieval succeeded and `llm.chat` ran, so
    there is real prose to show -- it just stands on nothing in the bundle.
    That is #753's specimen, and before the citation fix it arrived carrying
    five citations."""
    return AnswerResult(
        answer=answer,
        citations=[],
        fts_hit_count=5,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        # DELIBERATELY divergent: five concepts survived the fuse, but only
        # three reached the prompt (two were skipped at the guarded re-read).
        # The warning must name the three the model was actually shown.
        fused_count=5,
        context_block_count=3,
        attribution="reported",
    )


def test_query_warns_when_the_answer_reports_drawing_on_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answer standing on no cited concept says so, on stderr.

    Without this the reply prints bare: prose, no `Citations:` block, and no
    reason for its absence -- which reads like a rendering bug rather than
    the finding it is. #753's whole rationale is that a visible failure is
    correctable while borrowed authority is not, so the honest case must
    ANNOUNCE itself rather than merely omit a section.
    """
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _fake_unsupported_answer()
    )

    result = runner.invoke(app, ["query", "what is retrieval augmented generation?"])

    assert result.exit_code == 0
    assert "A general essay about RAG systems." in result.stdout
    assert "Citations:" not in result.stdout
    assert "drew on none of the 3 concepts placed in its context" in result.stderr
    # The count must come from the blocks SENT, never from the fuse total --
    # `fused_count` is 5 here and naming it would overstate what the model had.
    assert "none of the 5" not in result.stderr


def test_the_no_support_warning_is_silent_when_the_model_never_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that never attributed gets today's behavior, warning included.

    `attribution="absent"` means the citation list was NOT decided by the
    answer, so an empty one there says nothing about support -- it means
    retrieval returned nothing readable. Warning on it would claim a finding
    the run never made, and would fire on every backend that ignores the
    instruction.
    """
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer="An answer.",
        citations=[],
        fts_hit_count=5,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        fused_count=5,
        attribution="absent",
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "drew on none of the" not in result.stderr


# --- the pre-synthesis sufficiency check reaches the CLI (#760) --------------


def test_query_renders_the_insufficient_context_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal explains that the bundle does not COVER the question.

    Deliberately different wording from `zero_hits`. Retrieval succeeded
    here — concepts were found and read, then judged unable to answer — so
    telling the user to "try different wording" would send them rephrasing a
    question the bundle simply does not cover. The message also names the
    opt-out, because a user who disagrees with the refusal needs a way past
    it that is not "give up".
    """
    _init_workspace(tmp_path, monkeypatch)
    fake = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=4,
        llm_invoked=False,
        no_match_cause="insufficient_context",
        skip_notices=[],
        fused_count=4,
        context_block_count=4,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *a, **k: fake)

    result = runner.invoke(app, ["query", "¿qué es la cuantización de pesos?"])

    assert result.exit_code == 0
    assert "does not cover it" in result.stdout
    assert "sufficiency_check: false" in result.stdout
    # NOT the zero-hit instruction: concepts WERE found.
    assert "Try different wording" not in result.stdout


def test_query_passes_the_configured_sufficiency_check_to_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cfg.sufficiency_check` reaches `answer()`, rather than being defaulted
    at the call site.

    The library default is `False` and the workspace default is `True`, so a
    call site that forgot to thread the value would silently ship the check
    OFF while `openkos.yaml` documented it ON — a divergence no config test
    can see, because both halves would still be individually correct.
    """
    _init_workspace(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    def _spy(*args: object, **kwargs: object) -> AnswerResult:
        seen.update(kwargs)
        return _fake_matched_answer()

    monkeypatch.setattr("openkos.cli.main.answer", _spy)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert seen["sufficiency_check"] is config.DEFAULT_SUFFICIENCY_CHECK


def test_query_honours_sufficiency_check_false_from_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who turns the key off gets it off, end to end."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\nsufficiency_check: false\n",
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def _spy(*args: object, **kwargs: object) -> AnswerResult:
        seen.update(kwargs)
        return _fake_matched_answer()

    monkeypatch.setattr("openkos.cli.main.answer", _spy)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert seen["sufficiency_check"] is False


# --- near-duplicate disclosure in the --save preview (#762) ------------------


def test_the_save_preview_discloses_a_possible_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resembling filed insight is shown BEFORE the confirmation gate.

    The slug is the permanent Concept ID, so a duplicate filed here is
    permanent; the preview is the last moment noticing it is free. Advisory
    by design — the line describes, and the human already confirming decides.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer",
        lambda *a, **k: _fake_matched_answer(
            citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")]
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.main.insight_identity.near_duplicate_insights",
        lambda *a, **k: [
            insight_identity.NearDuplicate(
                concept_id="insights/why-stoicism-matters",
                title="Why Stoicism Matters",
                question="why does stoicism matter?",
                similarity=0.9612,
            )
        ],
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "possible duplicate of bundle/insights/why-stoicism-matters.md" in (
        result.stdout
    )
    assert "why does stoicism matter?" in result.stdout
    assert "0.96 similar" in result.stdout


def test_the_disclosure_is_advisory_and_the_save_still_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disclosed duplicate does NOT refuse or alter the filing.

    The measured margin is +0.0745 over two subject families — enough to
    show a person, nowhere near enough to act on. A version that refused
    would turn a thin signal into a hard gate, which is exactly the mistake
    #760 refused when it declined to ship a distance threshold.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer",
        lambda *a, **k: _fake_matched_answer(
            citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")]
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.main.insight_identity.near_duplicate_insights",
        lambda *a, **k: [
            insight_identity.NearDuplicate(
                concept_id="insights/other",
                title="Other",
                question="q?",
                similarity=0.99,
            )
        ],
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    filed = sorted((tmp_path / "bundle" / "insights").glob("*.md"))
    assert len(filed) == 1


def test_no_duplicate_means_no_disclosure_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary save is unchanged — no noise on the common path."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer",
        lambda *a, **k: _fake_matched_answer(
            citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")]
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.main.insight_identity.near_duplicate_insights",
        lambda *a, **k: [],
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "possible duplicate" not in result.stdout


def test_the_disclosure_is_asked_about_the_question_being_filed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lookup runs on the QUESTION, not the answer or the derived title.

    `evals/query_identity/` measured all three: the answer body (-0.0620)
    and the title (-0.1579) both OVERLAP, and only the source question
    separates. Passing the wrong one would still produce plausible-looking
    output while reproducing the defect the measurement rejected.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path / "bundle", "concepts", "stoicism", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer",
        lambda *a, **k: _fake_matched_answer(
            citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")]
        ),
    )
    seen: dict[str, object] = {}

    def _spy(question: str, **kwargs: object) -> list[object]:
        seen["question"] = question
        seen.update(kwargs)
        return []

    monkeypatch.setattr(
        "openkos.cli.main.insight_identity.near_duplicate_insights", _spy
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert seen["question"] == "what is stoicism?"
    assert seen["bundle_dir"] == tmp_path / "bundle"


def test_a_sufficiency_refusal_reports_the_llm_as_refused_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retrieval summary must not call a paid call `skipped`.

    `skipped` is what a zero-hit short-circuit gets, and that one truly never
    reaches the backend. A sufficiency refusal DID: one chat call was made
    and its latency paid, so reusing the same word hides a cost the operator
    is being charged and makes two different events read identically.
    """
    _init_workspace(tmp_path, monkeypatch)
    fake = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=4,
        llm_invoked=False,
        no_match_cause="insufficient_context",
        skip_notices=[],
        fused_count=4,
        context_block_count=4,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *a, **k: fake)

    result = runner.invoke(app, ["query", "¿qué es la cuantización?"])

    assert result.exit_code == 0
    assert "LLM refused" in result.stderr
    assert "LLM skipped" not in result.stderr


def test_a_zero_hit_short_circuit_still_reports_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same distinction, so `refused` cannot swallow it."""
    _init_workspace(tmp_path, monkeypatch)
    fake = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="zero_hits",
        skip_notices=[],
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *a, **k: fake)

    result = runner.invoke(app, ["query", "nothing matches this"])

    assert result.exit_code == 0
    assert "LLM skipped" in result.stderr
    assert "LLM refused" not in result.stderr
