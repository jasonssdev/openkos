"""Filesystem-free unit tests for `openkos.application.ingest` (issue #918).

Slice 1 covers `DerivedPlan` and the three collision-detection helpers,
moved verbatim from `cli/main.py`. Slice 2 covers the typed contracts
(`DropKind`/`StagingDrop`/`StagedDerivedObjects`) and `stage_derived_objects`
itself, de-presented -- it returns typed disclosure data instead of calling
`typer.echo`, and propagates `OllamaError` rather than catching it (design:
"the backend exception propagates; the adapter catches `OllamaError`"). Slice
3 covers the plan-composition core: `converged_reingest` (the #773
convergence gate), `compose_source_document`, and `compose_catalog_update`.
No Typer runner and no bundle directory beyond a `tmp_path`-scoped collision
scan -- the whole point of the application layer (ADR-0018) is that this
module's behavior is reachable without driving a CLI command.
"""

import dataclasses
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from openkos import config
from openkos.application import ingest as ingest_service
from openkos.bundle import index as bundle_index
from openkos.extraction import concept as concept_mod
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.model import okf
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY


def _plan(**overrides: object) -> ingest_service.DerivedPlan:
    fields: dict[str, object] = {
        "doc_type": "Concept",
        "section": "concepts",
        "link_dir": "concepts",
        "slug": "example-concept",
        "title": "Example Concept",
        "description": "An example concept for the test.",
        "path": Path("bundle/concepts/example-concept.md"),
        "content": "---\ntitle: Example Concept\n---\nbody\n",
    }
    fields.update(overrides)
    return ingest_service.DerivedPlan(**fields)  # type: ignore[arg-type]


def test_derived_plan_is_frozen_dataclass() -> None:
    plan = _plan()
    assert dataclasses.is_dataclass(plan)
    assert plan.disambiguated_from is None
    assert plan.type_alternative is None
    assert plan.sensitivity == ""
    assert plan.type_floor_raised is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.slug = "changed"  # type: ignore[misc]


def test_derived_plan_carries_disambiguation_and_sensitivity_fields() -> None:
    """Triangulation: a second construction with every optional field set,
    to prove the dataclass is not merely satisfied by its defaults."""
    plan = _plan(
        disambiguated_from="example-concept",
        type_alternative="Entity",
        sensitivity="internal",
        type_floor_raised=True,
    )
    assert plan.disambiguated_from == "example-concept"
    assert plan.type_alternative == "Entity"
    assert plan.sensitivity == "internal"
    assert plan.type_floor_raised is True


def test_collision_helpers_resolve_disambiguated_slug(tmp_path: Path) -> None:
    """`collision_family` finds the on-disk family, `family_owns_source`
    distinguishes same-source from foreign-source ownership, and
    `first_free_disambiguated_slug` hands back the first free numeric
    suffix -- the same three-step sequence `_stage_derived_objects` runs
    on a foreign-source collision."""
    link_dir = tmp_path / "concepts"
    link_dir.mkdir()
    (link_dir / "note.md").write_text(
        "---\nprovenance:\n  - sources/other-source\n---\nbody\n",
        encoding="utf-8",
    )

    family = ingest_service.collision_family(link_dir, "note")
    assert [path.name for path in family] == ["note.md"]

    assert ingest_service.family_owns_source(family, "this-source") is False
    assert ingest_service.family_owns_source(family, "other-source") is True

    next_slug = ingest_service.first_free_disambiguated_slug(family, "note", set())
    assert next_slug == "note-2"


def test_collision_helpers_skip_reserved_slugs_within_a_batch(tmp_path: Path) -> None:
    """Triangulation: a DIFFERENT code path -- no on-disk collision at all,
    but `reserved` already claims `note-2` for an earlier candidate in the
    same batch, so the loop must advance past it (design: batch-local
    `seen_slugs` guard)."""
    link_dir = tmp_path / "concepts"
    link_dir.mkdir()

    family = ingest_service.collision_family(link_dir, "note")
    assert family == []
    assert ingest_service.family_owns_source(family, "any-source") is False

    next_slug = ingest_service.first_free_disambiguated_slug(family, "note", {"note-2"})
    assert next_slug == "note-3"


# --- Slice 2: `DropKind` / `StagingDrop` / `StagedDerivedObjects` -----------


class _FakeLLM:
    """A structural `LLMBackend` -- mirrors `test_ingest.py` (cli)'s own
    `_FakeLLM`: records nothing beyond what the test needs, returns a fixed
    reply, or raises a fixed exception. Zero network, zero real Ollama
    process."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self, reply: str = "", *, raises: Exception | None = None) -> None:
        self.reply = reply
        self.raises = raises

    def chat(self, messages: Sequence[Message]) -> str:
        if self.raises is not None:
            raise self.raises
        return self.reply

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


def _default_cfg(**overrides: object) -> config.Config:
    """Minimal `config.Config` for direct `stage_derived_objects` calls --
    mirrors `tests/unit/cli/test_ingest.py::_default_cfg`."""
    fields: dict[str, object] = {
        "model": "qwen3:8b",
        "review": True,
        "default_sensitivity": "private",
        "freshness_window": "7d",
        "embedding_model": "bge-m3",
        "chat_timeout": config.DEFAULT_CHAT_TIMEOUT,
        "max_generation_tokens": config.DEFAULT_MAX_GENERATION_TOKENS,
        "context_window": config.DEFAULT_CONTEXT_WINDOW,
        "temperature": config.DEFAULT_TEMPERATURE,
        "seed": config.DEFAULT_SEED,
        "confidential_local_exemption": config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION,
        "volatility_windows": {},
        "type_tiers": {},
        "models": {},
        "union_judge": config.DEFAULT_UNION_JUDGE,
        "sufficiency_check": config.DEFAULT_SUFFICIENCY_CHECK,
        "concurrent_extraction": config.DEFAULT_CONCURRENT_EXTRACTION,
        "type_sensitivity_defaults": {},
        "rationale_language": config.DEFAULT_RATIONALE_LANGUAGE,
    }
    fields.update(overrides)
    return config.Config(**fields)  # type: ignore[arg-type]


def _stage_kwargs(tmp_path: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "raw_content": "Some raw notes about self-control.",
        "source_title": "Notes",
        "source_slug": "notes",
        "workspace_floor": "private",
        "stamp_sensitivity": "private",
        "timestamp": "2026-07-14T18:30:00Z",
        "bundle_dir": tmp_path / "bundle",
        "llm": _FakeLLM('{"extract": false}'),
        "cfg": _default_cfg(),
    }
    kwargs.update(overrides)
    return kwargs


def _concept_reply(title: str = "Stoic Dichotomy Of Control") -> str:
    return json.dumps(
        {
            "extract": True,
            "type": "Concept",
            "title": title,
            "description": (
                "A framework distinguishing what is and is not within our control."
            ),
            "body": "Elaboration on applying the framework day to day.",
        }
    )


def test_drop_kind_and_staging_drop_field_shapes() -> None:
    """`StagingDrop` carries a `DropKind`, the slug the decision was about,
    and the two conditional fields (design: Interfaces/Contracts)."""
    drop = ingest_service.StagingDrop(kind="empty-slug", slug="")
    assert drop.kind == "empty-slug"
    assert drop.disambiguated_to is None
    assert drop.error is None

    disambiguated = ingest_service.StagingDrop(
        kind="disambiguated", slug="note", disambiguated_to="note-2"
    )
    assert disambiguated.disambiguated_to == "note-2"

    failed = ingest_service.StagingDrop(kind="build-failed", slug="note", error="boom")
    assert failed.error == "boom"


def test_staged_derived_objects_field_shapes() -> None:
    """`StagedDerivedObjects` carries `plans`, `skip_reason`, `notices`,
    `report`, `drops`, and `lost_in_staging` (design: Interfaces/
    Contracts)."""
    outcome = ingest_service.StagedDerivedObjects(
        plans=(),
        skip_reason="no-extractable-text",
        notices=(),
        report=None,
        drops=(),
        lost_in_staging=0,
    )
    assert outcome.plans == ()
    assert outcome.skip_reason == "no-extractable-text"
    assert outcome.notices == ()
    assert outcome.report is None
    assert outcome.drops == ()
    assert outcome.lost_in_staging == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.lost_in_staging = 1  # type: ignore[misc]


def test_stage_derived_objects_returns_no_extractable_text_reason(
    tmp_path: Path,
) -> None:
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, raw_content="   ")  # type: ignore[arg-type]
    )
    assert outcome.plans == ()
    assert outcome.skip_reason == "no-extractable-text"
    assert outcome.report is None


def test_stage_derived_objects_returns_blocked_by_sensitivity_reason(
    tmp_path: Path,
) -> None:
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, workspace_floor="confidential")  # type: ignore[arg-type]
    )
    assert outcome.plans == ()
    assert outcome.skip_reason == "blocked-by-sensitivity"
    assert outcome.report is None


def test_stage_derived_objects_returns_no_concepts_found_reason(
    tmp_path: Path,
) -> None:
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM('{"extract": false}'))  # type: ignore[arg-type]
    )
    assert outcome.plans == ()
    assert outcome.skip_reason == "no-concepts-found"
    assert outcome.report is not None


def test_stage_derived_objects_returns_plans_on_success(tmp_path: Path) -> None:
    """Triangulation: a DIFFERENT code path -- a healthy extraction stages
    exactly one `DerivedPlan`, `skip_reason` is `None`."""
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(_concept_reply()))  # type: ignore[arg-type]
    )
    assert len(outcome.plans) == 1
    assert outcome.skip_reason is None
    assert outcome.report is not None
    assert outcome.drops == ()
    assert outcome.lost_in_staging == 0


def test_stage_derived_objects_emits_generated_and_stable_status(
    tmp_path: Path,
) -> None:
    """Task 2.10 (derived-object half): a staged `DerivedPlan`'s content
    carries `generated`/`status: stable`, mirroring the Source path, with no
    `timestamp` key."""
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(_concept_reply()))  # type: ignore[arg-type]
    )

    metadata, _ = okf.load_frontmatter(outcome.plans[0].content)

    assert metadata["generated"] == {
        "by": okf.engine_actor(),
        "at": "2026-07-14T18:30:00Z",
    }
    assert metadata["status"] == "stable"
    assert "timestamp" not in metadata


def test_stage_derived_objects_renders_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The service module MUST NOT call `typer.echo` or any other
    presentation call -- a healthy call produces zero captured output
    (spec: "The service module renders nothing")."""
    ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(_concept_reply()))  # type: ignore[arg-type]
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_stage_derived_objects_propagates_ollama_error(tmp_path: Path) -> None:
    """`OllamaError` from `llm.chat` propagates unwrapped -- the service
    catches nothing from `llm.chat` (design: "the backend exception
    propagates; the adapter catches `OllamaError`")."""
    with pytest.raises(OllamaUnavailable, match="boom"):
        ingest_service.stage_derived_objects(
            **_stage_kwargs(  # type: ignore[arg-type]
                tmp_path, llm=_FakeLLM(raises=OllamaUnavailable("boom"))
            )
        )


def test_stage_derived_objects_drops_empty_slug_candidate(tmp_path: Path) -> None:
    """A title that slugifies to `""` is dropped, recorded as an
    `empty-slug` `StagingDrop`, and counted in `lost_in_staging` (#843)."""
    reply = _concept_reply(title="***")
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=_FakeLLM(reply))  # type: ignore[arg-type]
    )
    assert outcome.plans == ()
    assert len(outcome.drops) == 1
    assert outcome.drops[0].kind == "empty-slug"
    assert outcome.lost_in_staging == 1
    assert okf.EXTRACTION_NOTICE_CANDIDATES_DROPPED in outcome.notices


def test_stage_derived_objects_disambiguates_a_foreign_source_collision(
    tmp_path: Path,
) -> None:
    """Triangulation: a DIFFERENT drop kind -- a foreign-source collision on
    disk disambiguates the candidate to `<slug>-2`, staged (not dropped),
    recorded as a `disambiguated` `StagingDrop` (design: Collision loop
    mechanics, #131)."""
    bundle_dir = tmp_path / "bundle"
    concepts_dir = bundle_dir / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "stoic-dichotomy-of-control.md").write_text(
        "---\nprovenance:\n  - sources/other-source\n---\nbody\n",
        encoding="utf-8",
    )

    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path, bundle_dir=bundle_dir, llm=_FakeLLM(_concept_reply())
        )
    )

    assert len(outcome.plans) == 1
    assert outcome.plans[0].slug == "stoic-dichotomy-of-control-2"
    assert outcome.plans[0].disambiguated_from == "stoic-dichotomy-of-control"
    assert len(outcome.drops) == 1
    assert outcome.drops[0].kind == "disambiguated"
    assert outcome.drops[0].slug == "stoic-dichotomy-of-control"
    assert outcome.drops[0].disambiguated_to == "stoic-dichotomy-of-control-2"


def _fake_extractor(
    objects: list[concept_mod.ExtractionResult],
    *,
    judge_status: str = "skipped",
    sole_object_restates_source: bool = False,
    unevidenced_titles: tuple[str, ...] = (),
    skipped_chunks: tuple[int, ...] = (),
) -> object:
    """Monkeypatch stand-in for `extract_concept`/`extract_concept_union`,
    mirroring `tests/unit/cli/test_ingest.py::_capturing_extractor` -- lets a
    test fabricate an exact `ExtractionOutcome` (objects + report) without
    driving the real extraction pipeline, so branches gated on `report`
    fields (judge status, sole-object-restates, unevidenced titles, skipped
    chunks) are reachable without an LLM call."""
    report = concept_mod.ExtractionReport(
        produced=len(objects),
        retained=len(objects),
        judge_status=judge_status,
        sole_object_restates_source=sole_object_restates_source,
        unevidenced_titles=unevidenced_titles,
        skipped_chunks=skipped_chunks,
    )

    def _extractor(*args: object, **kwargs: object) -> object:
        return concept_mod.ExtractionOutcome(objects=list(objects), report=report)

    return _extractor


def test_stage_derived_objects_carries_judge_unavailable_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triangulation: the `judge_status == "failed"` branch -- distinct from
    every other notice-building arm exercised above."""
    monkeypatch.setattr(
        ingest_service, "extract_concept", _fake_extractor([], judge_status="failed")
    )
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path)  # type: ignore[arg-type]
    )
    assert okf.EXTRACTION_NOTICE_JUDGE_UNAVAILABLE in outcome.notices


def test_stage_derived_objects_carries_judge_empty_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triangulation: a DIFFERENT judge-status branch (`"empty"`, not
    `"failed"`) -- the two are mutually exclusive by construction."""
    monkeypatch.setattr(
        ingest_service, "extract_concept", _fake_extractor([], judge_status="empty")
    )
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path)  # type: ignore[arg-type]
    )
    assert okf.EXTRACTION_NOTICE_JUDGE_EMPTY in outcome.notices


def test_stage_derived_objects_carries_chunk_partial_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triangulation: `report.skipped_chunks` feeds the notices tuple
    independently of the judge/sole-object/unevidenced branches (#1053)."""
    result = concept_mod.ExtractionResult(
        type="Concept", title="Stoic Practice", description="desc", body="body"
    )
    monkeypatch.setattr(
        ingest_service,
        "extract_concept",
        _fake_extractor([result], skipped_chunks=(2,)),
    )
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path)  # type: ignore[arg-type]
    )
    assert okf.EXTRACTION_NOTICE_CHUNK_PARTIAL in outcome.notices


def test_stage_derived_objects_carries_sole_object_and_unevidenced_notices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triangulation: `sole_object_restates_source` and `unevidenced_titles`
    both feed the `notices` tuple, independent of the judge pair above."""

    result = concept_mod.ExtractionResult(
        type="Concept", title="Stoic Practice", description="desc", body="body"
    )
    monkeypatch.setattr(
        ingest_service,
        "extract_concept",
        _fake_extractor(
            [result],
            sole_object_restates_source=True,
            unevidenced_titles=("Stoic Practice",),
        ),
    )
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path)  # type: ignore[arg-type]
    )
    assert okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES in outcome.notices
    assert okf.EXTRACTION_NOTICE_OBJECTS_WITHOUT_EVIDENCE in outcome.notices


def test_stage_derived_objects_drops_an_in_batch_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triangulation: a THIRD drop kind -- two candidates in the same reply
    slugify alike; the first is staged, the second is dropped and NOT
    counted in `lost_in_staging` (#884)."""

    first = concept_mod.ExtractionResult(
        type="Concept", title="Stoic Practice", description="d1", body="b1"
    )
    second = concept_mod.ExtractionResult(
        type="Concept", title="Stoic Practice", description="d2", body="b2"
    )
    monkeypatch.setattr(
        ingest_service, "extract_concept", _fake_extractor([first, second])
    )
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path)  # type: ignore[arg-type]
    )
    assert len(outcome.plans) == 1
    assert outcome.drops == (
        ingest_service.StagingDrop(kind="in-batch-collision", slug="stoic-practice"),
    )
    assert outcome.lost_in_staging == 0


def test_stage_derived_objects_collapses_a_run_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1230: two same-type objects with near-match titles that quote the
    same source line are one subject; the richer survives and the other is
    reported as a `run-duplicate` drop (not a staging loss)."""
    quote = "We agreed to validate the pilot with real meeting minutes."
    first = concept_mod.ExtractionResult(
        type="Decision",
        title="Use of real minutes for validation",
        description="d1",
        body=quote,
    )
    second = concept_mod.ExtractionResult(
        type="Decision",
        title="Agreement on the use of real minutes for validation",
        description="d2",
        body=f"{quote} Confirmed with the team.",
    )
    monkeypatch.setattr(
        ingest_service, "extract_concept", _fake_extractor([first, second])
    )
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, raw_content=f"Notes\n{quote}\n")  # type: ignore[arg-type]
    )
    assert [plan.slug for plan in outcome.plans] == [
        "agreement-on-the-use-of-real-minutes-for-validation"
    ]
    assert outcome.drops == (
        ingest_service.StagingDrop(
            kind="run-duplicate",
            slug="use-of-real-minutes-for-validation",
            kept_slug="agreement-on-the-use-of-real-minutes-for-validation",
        ),
    )
    assert outcome.lost_in_staging == 0


def test_stage_derived_objects_create_only_skip_on_same_source_collision(
    tmp_path: Path,
) -> None:
    """Triangulation: a FOURTH drop kind -- a same-source collision is a
    create-only no-op (`"already-exists"`), not counted in
    `lost_in_staging`, and stages NOTHING (design D5)."""
    bundle_dir = tmp_path / "bundle"
    concepts_dir = bundle_dir / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "stoic-dichotomy-of-control.md").write_text(
        "---\nprovenance:\n  - sources/notes\n---\nbody\n",
        encoding="utf-8",
    )
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path, bundle_dir=bundle_dir, llm=_FakeLLM(_concept_reply())
        )
    )
    assert outcome.plans == ()
    assert outcome.drops == (
        ingest_service.StagingDrop(
            kind="already-exists", slug="stoic-dichotomy-of-control"
        ),
    )
    assert outcome.lost_in_staging == 0


def test_stage_derived_objects_drops_a_build_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triangulation: the FIFTH drop kind -- `okf.build_concept` rejects a
    multi-line title that slipped past the extractor's own validation,
    dropping the candidate and counting it in `lost_in_staging` (#843)."""

    result = concept_mod.ExtractionResult(
        type="Concept",
        title="Stoic Practice",
        description="A description with an embedded\nnewline",
        body="body",
    )
    monkeypatch.setattr(ingest_service, "extract_concept", _fake_extractor([result]))
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path)  # type: ignore[arg-type]
    )
    assert outcome.plans == ()
    assert outcome.lost_in_staging == 1
    assert len(outcome.drops) == 1
    assert outcome.drops[0].kind == "build-failed"
    assert outcome.drops[0].error


# -- Phase 5 (preserve-source-frontmatter, issue #1062): derived tag
# propagation -- `stage_derived_objects(source_tags=)` (design.md
# Decision 6) --


def test_stage_derived_objects_threads_source_tags_to_every_build_concept_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 5.4: a fake extractor returns two DISTINCT candidates (so both
    stage, not just the first);
    `stage_derived_objects(..., source_tags=("alpha", "beta"))` stages BOTH
    candidates' plans with `tags: [alpha, beta]` reaching each `build_concept`
    call."""
    first = concept_mod.ExtractionResult(
        type="Concept", title="Stoic Practice", description="d1", body="b1"
    )
    second = concept_mod.ExtractionResult(
        type="Concept", title="Negative Visualization", description="d2", body="b2"
    )
    monkeypatch.setattr(
        ingest_service, "extract_concept", _fake_extractor([first, second])
    )

    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path, source_tags=("alpha", "beta")
        )
    )

    assert len(outcome.plans) == 2
    for plan in outcome.plans:
        metadata, _ = okf.load_frontmatter(plan.content)
        assert metadata["tags"] == ["alpha", "beta"]


def test_stage_derived_objects_carried_path_ignores_source_tags(
    tmp_path: Path,
) -> None:
    """Task 5.5: the `carried=` short-circuit (pre-extraction return)
    returns immediately regardless of `source_tags`, staging NO plans and
    calling NO `build_concept` -- a Source-only rewrite creates nothing, so
    tags never reach it. PRECONDITION: `carried` is genuinely set (a real
    `ConvergedReingest`) before asserting the short-circuit fired."""
    converged = ingest_service.ConvergedReingest(
        carried_notices=(), carried_status="no-concepts-found"
    )
    assert converged is not None  # precondition: carried is genuinely set

    stub_llm = _FakeLLM(raises=AssertionError("must not be called"))
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(  # type: ignore[arg-type]
            tmp_path,
            llm=stub_llm,
            carried=converged,
            source_tags=("alpha", "beta"),
        )
    )

    assert outcome.plans == ()
    assert outcome.skip_reason == "no-concepts-found"


# -- Slice 3: `converged_reingest`, `compose_source_document`,
# `compose_catalog_update` (issue #918, design: Interfaces/Contracts) --


def _prior_concept_text(**overrides: object) -> str:
    """A prior Source concept's rendered text -- built through the real
    `okf.build_source_concept` so `converged_reingest`/
    `compose_source_document` parse exactly what `ingest` itself would have
    written, never a hand-rolled YAML fixture."""
    fields: dict[str, object] = {
        "title": "Notes",
        "description": (
            "Raw source imported from 'notes.txt' as raw/notes.txt; full "
            "text embedded verbatim below, not yet extracted into concepts."
        ),
        "resource": "raw/notes.txt",
        "tags": [],
        "generated": okf.Generated(by="openkos/test", at="2026-07-01T00:00:00Z"),
        "sensitivity": "private",
        "provenance": ["raw/notes.txt"],
        "raw_content": "Some raw notes about self-control.",
        "origin_key": "deadbeef",
    }
    fields.update(overrides)
    return okf.build_source_concept(**fields)  # type: ignore[arg-type]


def test_converged_reingest_falls_through_on_absent_frontmatter() -> None:
    """Design: "unparseable frontmatter falls through" -- the FIRST of the
    three policy decisions `converged_reingest` owns, in the shape that does
    NOT raise: `okf.load_frontmatter` returns `({}, text)` for a document
    with no frontmatter block at all, so this falls through on the absent
    `origin_key` rather than through the parse guard. Kept as its own case
    precisely because it does not exercise that guard -- see
    `test_converged_reingest_falls_through_on_malformed_frontmatter_yaml`."""
    assert (
        ingest_service.converged_reingest("not frontmatter at all", re_extract=False)
        is None
    )


def test_converged_reingest_falls_through_on_malformed_frontmatter_yaml() -> None:
    """#942: the parse guard has to catch what `frontmatter.loads` actually
    raises. Malformed YAML surfaces from `okf.load_frontmatter` as the typed
    `okf.FrontmatterError`, so the guard catches that and delivers the
    documented fall-through instead of letting the error escape.

    `title: [unclosed` is a genuinely malformed mapping value -- distinct
    from a merely absent block, which `frontmatter.loads` tolerates."""
    malformed = "---\ntitle: [unclosed\norigin_key: deadbeef\n---\n\nBody.\n"
    with pytest.raises(okf.FrontmatterError):
        okf.load_frontmatter(malformed)
    assert ingest_service.converged_reingest(malformed, re_extract=False) is None


def test_converged_reingest_falls_through_on_legacy_source_no_origin_key() -> None:
    """Design: "a legacy Source with no `origin_key` falls through" -- the
    SECOND policy decision; the full regenerate path backfills the key."""
    text = _prior_concept_text(origin_key=None)
    assert ingest_service.converged_reingest(text, re_extract=False) is None


def test_converged_reingest_falls_through_on_retryable_debt() -> None:
    """Design: "retryable debt falls through" -- the THIRD policy decision,
    an `extraction_status: failed` marker (#187)."""
    text = _prior_concept_text(extraction_status=okf.EXTRACTION_STATUS_FAILED)
    assert ingest_service.converged_reingest(text, re_extract=False) is None


def test_converged_reingest_falls_through_on_judge_degrade_notice() -> None:
    """Triangulation: retryable debt is also #772's judge-degrade
    `extraction_notice` token, not only `extraction_status: failed`."""
    text = _prior_concept_text(
        extraction_notice=okf.EXTRACTION_NOTICE_JUDGE_UNAVAILABLE
    )
    assert ingest_service.converged_reingest(text, re_extract=False) is None


def test_converged_reingest_falls_through_on_chunk_partial_notice() -> None:
    """Triangulation: retryable debt also includes #1053's chunk-skip
    `extraction_notice` token -- a `BackendError`-family chunk failure is
    transient, so a plain re-ingest genuinely can answer differently, the
    same reasoning that already applies to the two judge tokens."""
    text = _prior_concept_text(extraction_notice=okf.EXTRACTION_NOTICE_CHUNK_PARTIAL)
    assert ingest_service.converged_reingest(text, re_extract=False) is None


def test_converged_reingest_falls_through_on_re_extract() -> None:
    """Triangulation: `--re-extract`'s deliberate redo always falls through,
    even for an otherwise-convergent Source."""
    text = _prior_concept_text()
    assert ingest_service.converged_reingest(text, re_extract=True) is None


def test_converged_reingest_returns_carried_notices_on_convergence() -> None:
    """The healthy convergent path: the PRIOR run's carried
    `extraction_notice` token comes back on `ConvergedReingest.
    carried_notices`, not `None` -- the summary counts what the Source
    CARRIES when the run ends (#805, item 1)."""
    text = _prior_concept_text(
        extraction_notice=okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES
    )
    result = ingest_service.converged_reingest(text, re_extract=False)
    assert result is not None
    assert result.carried_notices == (okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES,)


def test_converged_reingest_returns_empty_notices_on_a_clean_convergence() -> None:
    """Triangulation: a clean prior Source (no carried notice) converges
    with an EMPTY `carried_notices` tuple, not `None` and not omitted."""
    text = _prior_concept_text()
    result = ingest_service.converged_reingest(text, re_extract=False)
    assert result is not None
    assert result.carried_notices == ()


def test_compose_source_document_concept_text_none_means_no_prior_source() -> None:
    """`concept_text is None` <=> the adapter's `had_prior_source` was
    `False` (design: Interfaces/Contracts) -- a fresh ingest resolves
    straight to the config default, with no on-disk read-back."""
    plan = ingest_service.compose_source_document(
        raw_content="Some raw notes about self-control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    assert plan.on_disk_sensitivity is None
    assert plan.on_disk_title is None
    assert plan.resolved_sensitivity == "private"
    assert "Some raw notes about self-control." in plan.content


def test_compose_source_document_emits_generated_and_stable_status() -> None:
    """Task 2.10: the written Source's frontmatter carries `generated: {by:
    <engine_actor()>, at: <the same instant previously passed as
    timestamp>}` and `status: stable`, with no `timestamp` key
    (okf-v02-migration design.md Decision 5)."""
    plan = ingest_service.compose_source_document(
        raw_content="Some raw notes about self-control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )

    metadata, _ = okf.load_frontmatter(plan.content)

    assert metadata["generated"] == {
        "by": okf.engine_actor(),
        "at": "2026-07-14T18:30:00Z",
    }
    assert metadata["status"] == "stable"
    assert "timestamp" not in metadata


def test_compose_source_document_never_writes_provenance() -> None:
    """Issue #1076: `compose_source_document` no longer passes
    `provenance=[resource]` into `okf.build_source_concept` -- a fresh
    Source's `resource` field already names its one raw original, so the
    written frontmatter carries no `provenance` key at all."""
    plan = ingest_service.compose_source_document(
        raw_content="Some raw notes about self-control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )

    metadata, _ = okf.load_frontmatter(plan.content)

    assert "provenance" not in metadata


def test_compose_source_document_reads_back_on_disk_sensitivity() -> None:
    """Triangulation: a DIFFERENT code path -- an EXISTING prior Source
    read back at a higher sensitivity raises the resolved value to the
    high-water mark (issue #229), never lowers it."""
    prior = _prior_concept_text(sensitivity="confidential")
    plan = ingest_service.compose_source_document(
        raw_content="Some raw notes about self-control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=prior,
        cfg=_default_cfg(default_sensitivity="private"),
        timestamp="2026-07-14T18:30:00Z",
    )
    assert plan.on_disk_sensitivity == "confidential"
    assert plan.resolved_sensitivity == "confidential"


def test_compose_source_document_reads_back_on_disk_title() -> None:
    """The preview's retitle disclosure depends on `on_disk_title` being
    read back, never made sticky (`title` is still rebuilt from content
    every run)."""
    prior = _prior_concept_text(title="Old Title")
    plan = ingest_service.compose_source_document(
        raw_content="Some raw notes about self-control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=prior,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    assert plan.on_disk_title == "Old Title"


def test_compose_source_document_raises_on_malformed_prior_frontmatter() -> None:
    """Triangulation: a DIFFERENT code path -- genuinely malformed YAML (not
    merely a missing `---` block, which `frontmatter.loads` tolerates as
    empty metadata) makes `okf.load_frontmatter` raise `okf.FrontmatterError`,
    which `_read_source_sensitivity` translates to `ValueError` (design:
    "Where the on-disk read happens, and how it fails") -- a re-ingest MUST
    NOT degrade an unreadable classification to the config default."""
    malformed = "---\ntitle: [unclosed\n---\nbody\n"
    with pytest.raises(ValueError, match="could not be parsed"):
        ingest_service.compose_source_document(
            raw_content="Some raw notes about self-control.",
            source_stem="notes",
            source_display_path="notes.txt",
            source_document_display_path="bundle/sources/notes.md",
            resource="raw/notes.txt",
            origin_key="deadbeef",
            concept_text=malformed,
            cfg=_default_cfg(),
            timestamp="2026-07-14T18:30:00Z",
        )


def test_frontmatter_refusal_names_the_source_document_not_the_raw_source() -> None:
    """The refusal must name the file the operator has to open.

    `compose_source_document` takes TWO display paths and it is easy to see
    them as redundant: `source_display_path` (the raw source) feeds the Source
    document's description, while `source_document_display_path` feeds these
    frontmatter-parse refusals. Collapsing them into one value is exactly what
    happened during the #918 Slice-3 extraction, and every test stayed green,
    because the only assertion on this message matched `"could not be parsed"`
    -- a substring both wordings satisfy.

    So this asserts the SPECIFIC path and the word `frontmatter`. The two
    fixture paths are deliberately different; if they were equal, this test
    could not fail.
    """
    malformed = "---\ntitle: [unclosed\n---\nbody\n"

    with pytest.raises(ValueError, match="refusing to ingest") as excinfo:
        ingest_service.compose_source_document(
            raw_content="Some raw notes about self-control.",
            source_stem="notes",
            source_display_path="notes.txt",
            source_document_display_path="bundle/sources/notes.md",
            resource="raw/notes.txt",
            origin_key="deadbeef",
            concept_text=malformed,
            cfg=_default_cfg(),
            timestamp="2026-07-14T18:30:00Z",
        )

    message = str(excinfo.value)
    assert "'bundle/sources/notes.md' frontmatter" in message
    assert "notes.txt" not in message, (
        "the refusal named the raw source, which has no frontmatter at all"
    )


def test_compose_source_document_renders_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Same layering invariant `stage_derived_objects` proves (spec: "The
    service module renders nothing") -- zero captured output."""
    ingest_service.compose_source_document(
        raw_content="Some raw notes about self-control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_compose_source_document_binary_source_description() -> None:
    """Triangulation: a DIFFERENT code path -- `raw_content is None` (a
    binary/undecodable source) renders the "could not be embedded"
    description, never calls `source_title.derive_source_title`, and falls
    back straight to `source_titles.titleize(source_stem)`."""
    plan = ingest_service.compose_source_document(
        raw_content=None,
        source_stem="notes",
        source_display_path="notes.bin",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.bin",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    assert "could not be embedded" in plan.description
    assert plan.title == "notes"


# -- Phase 2 (preserve-source-frontmatter, issue #1062): `compose_source_
# document` parses and forwards incoming frontmatter (design.md Decisions
# 1, 2; Interfaces/Contracts) --


def test_compose_source_document_parses_and_forwards_frontmatter() -> None:
    """ingest-application-service: "A given frontmatter mapping ... reach
    the generated document" (frontmatter half); ingestion: "Valid incoming
    frontmatter yields source_frontmatter and lifted tags" (frontmatter
    half). Task 2.5. **RED today**: `compose_source_document` does not call
    `okf.parse_incoming_frontmatter` yet."""
    raw_content = "---\nauthor: Jane\n---\nSome raw notes about self-control."
    plan = ingest_service.compose_source_document(
        raw_content=raw_content,
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    metadata, _ = okf.load_frontmatter(plan.content)
    assert metadata[okf.SOURCE_FRONTMATTER_KEY] == {"author": "Jane"}


def test_compose_source_document_malformed_frontmatter_lifts_nothing() -> None:
    """ingestion: "Malformed incoming frontmatter yields neither, and ingest
    still succeeds". Task 2.6: pins that the CALL SITE never raises, not
    just the parser (1.11 already guarantees the parser itself never
    raises)."""
    raw_content = "---\ntitle: [unclosed\n---\nSome raw notes about self-control."
    plan = ingest_service.compose_source_document(
        raw_content=raw_content,
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    metadata, _ = okf.load_frontmatter(plan.content)
    assert okf.SOURCE_FRONTMATTER_KEY not in metadata
    assert plan.raw_content == raw_content


def test_compose_source_document_frontmatter_free_is_byte_identical() -> None:
    """ingest-application-service's byte-identity scenario at the
    service-composition level (task 2.2 pinned it at the builder level).
    Task 2.7: a source with NO leading frontmatter block produces a plan
    identical to independently calling `okf.build_source_concept` with no
    `source_frontmatter=` argument at all -- the same reference-building
    shape `compose_source_document` uses internally, so a wiring bug that
    always forwarded SOMETHING would be caught here."""
    raw_content = "Some raw notes about self-control."
    plan = ingest_service.compose_source_document(
        raw_content=raw_content,
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    expected = okf.build_source_concept(
        title=plan.title,
        description=plan.description,
        resource="raw/notes.txt",
        tags=[],
        generated=okf.Generated(by=okf.engine_actor(), at="2026-07-14T18:30:00Z"),
        sensitivity=plan.resolved_sensitivity,
        raw_content=raw_content,
        extraction_status=None,
        extraction_notice=(),
        origin_key="deadbeef",
        event_date=plan.event_date.value,
    )
    assert plan.content == expected
    assert okf.SOURCE_FRONTMATTER_KEY not in plan.content
    assert "provenance" not in okf.load_frontmatter(plan.content)[0]


def test_compose_source_document_never_parses_frontmatter_for_non_utf8_or_blank_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 1.16 (deferred to Phase 2, per this file's deferral note in
    `tasks.md`): a CALL-SITE contract, not `parse_incoming_frontmatter`'s
    own behavior -- the guard `compose_source_document` already applies
    before `source_title.derive_source_title` (`raw_content is None or not
    raw_content.strip()`) must ALSO gate the new parse call (2.8), reusing
    the SAME guard rather than a second one (task 1.17). Ingestion: "A
    source that is not valid UTF-8 is never parsed for frontmatter".

    PRECONDITION: the spy DOES fire for ordinary non-blank content first --
    proving it is wired into the path this test is about to gate, so an
    absent call site cannot vacuously pass."""
    calls: list[str] = []
    original = okf.parse_incoming_frontmatter

    def _spy(text: str) -> okf.IncomingFrontmatter:
        calls.append(text)
        return original(text)

    monkeypatch.setattr(okf, "parse_incoming_frontmatter", _spy)

    ingest_service.compose_source_document(
        raw_content="Some raw notes about self-control.",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    assert len(calls) == 1
    calls.clear()

    ingest_service.compose_source_document(
        raw_content=None,
        source_stem="notes",
        source_display_path="notes.bin",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.bin",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    assert calls == []

    ingest_service.compose_source_document(
        raw_content="   \n\t  ",
        source_stem="notes",
        source_display_path="notes.txt",
        source_document_display_path="bundle/sources/notes.md",
        resource="raw/notes.txt",
        origin_key="deadbeef",
        concept_text=None,
        cfg=_default_cfg(),
        timestamp="2026-07-14T18:30:00Z",
    )
    assert calls == []


def _source_plan(**overrides: object) -> ingest_service.SourceDocumentPlan:
    fields: dict[str, object] = {
        "raw_content": "Some raw notes about self-control.",
        "source_stem": "notes",
        "source_display_path": "notes.txt",
        # Deliberately DIFFERENT from `source_display_path`: the description
        # names the raw source, the frontmatter-refusal messages name the
        # Source document. A fixture that made them equal could not catch the
        # two being collapsed into one value again.
        "source_document_display_path": "bundle/sources/notes.md",
        "resource": "raw/notes.txt",
        "origin_key": "deadbeef",
        "concept_text": None,
        "cfg": _default_cfg(),
        "timestamp": "2026-07-14T18:30:00Z",
    }
    fields.update(overrides)
    return ingest_service.compose_source_document(**fields)  # type: ignore[arg-type]


def _staged(**overrides: object) -> ingest_service.StagedDerivedObjects:
    fields: dict[str, object] = {
        "plans": (),
        "skip_reason": None,
        "notices": (),
        "report": None,
        "drops": (),
        "lost_in_staging": 0,
    }
    fields.update(overrides)
    return ingest_service.StagedDerivedObjects(**fields)  # type: ignore[arg-type]


def test_compose_catalog_update_conditional_rerender_on_skip_reason() -> None:
    """Design: `compose_catalog_update` "owns the conditional re-render
    (skip_reason or notices)" -- a `skip_reason` stamps
    `EXTRACTION_STATUS_KEY` onto a FRESH build, never patches the
    already-built bytes."""
    source = _source_plan()
    staged = _staged(skip_reason="no-concepts-found")
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    assert update.concept_content != source.content
    metadata, _ = okf.load_frontmatter(update.concept_content)
    assert metadata.get(okf.EXTRACTION_STATUS_KEY) == "no-concepts-found"


def test_compose_catalog_update_rerender_never_writes_provenance() -> None:
    """Issue #1076: the conditional re-render path (`skip_reason`/`notices`)
    calls `okf.build_source_concept` a SECOND time, from scratch -- that
    fresh build must not reintroduce `provenance=[resource]` either."""
    source = _source_plan()
    staged = _staged(skip_reason="no-concepts-found")

    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )

    metadata, _ = okf.load_frontmatter(update.concept_content)
    assert "provenance" not in metadata


def test_compose_catalog_update_conditional_rerender_on_notices() -> None:
    """Triangulation: a DIFFERENT trigger -- `notices` alone (no
    `skip_reason`) also forces the fresh rebuild."""
    source = _source_plan()
    staged = _staged(notices=(okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES,))
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    metadata, _ = okf.load_frontmatter(update.concept_content)
    assert metadata.get(okf.EXTRACTION_NOTICE_KEY) == "sole-object-restates-source"


def test_compose_catalog_update_healthy_path_reuses_source_content() -> None:
    """Triangulation: neither `skip_reason` nor `notices` -- the healthy
    path reuses `source.content` VERBATIM (design: "never patch the
    already-built bytes"), never rebuilds it a second time."""
    source = _source_plan()
    staged = _staged()
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    assert update.concept_content == source.content


def test_compose_catalog_update_derived_plans_index_log_loop_incl_disambiguation_bullet() -> (
    None
):
    """Loops `staged.plans` in staging order, extending the SAME index/log
    diff with one bullet per derived object plus the durable disambiguation
    audit entry (#131) when `disambiguated_from` is set."""
    source = _source_plan()
    plan = _plan(disambiguated_from="notes-original", section="Concepts")
    staged = _staged(plans=(plan,))
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    assert plan.slug in update.new_index_text
    assert "Disambiguation" in update.new_log_text
    assert "notes-original" in update.new_log_text


def test_compose_catalog_update_skips_the_disambiguation_bullet_when_not_disambiguated() -> (
    None
):
    """Triangulation: a DIFFERENT code path in the SAME loop -- a second
    plan with `disambiguated_from is None` gets its ordinary bullets but no
    disambiguation audit entry, proving the `if` inside the loop is
    per-plan, not all-or-nothing."""
    source = _source_plan()
    disambiguated = _plan(slug="note-2", disambiguated_from="note", section="Concepts")
    ordinary = _plan(slug="other-note", section="Concepts")
    staged = _staged(plans=(disambiguated, ordinary))
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    assert update.new_log_text.count("**Disambiguation**") == 1
    assert "other-note" in update.new_index_text


def test_compose_catalog_update_regenerate_dedupes_the_source_index_entry() -> None:
    """Triangulation: a DIFFERENT code path -- `regenerate=True` removes the
    Source's own existing bullet BEFORE re-inserting it (design D3:
    "dedup before insert"), so a re-ingest never duplicates it."""
    source = _source_plan()
    staged = _staged()
    seeded_index = bundle_index.insert_source_entry(
        "---\nokf_version: '0.1'\n---\n",
        title=source.title,
        slug="notes",
        description=source.description,
    )
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text=seeded_index,
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=True,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    assert update.new_index_text.count("sources/notes.md") == 1
    assert "Re-ingest" in update.new_log_text


def test_compose_catalog_update_second_build_carries_source_frontmatter() -> None:
    """design.md Decision 7: the second, conditional `build_source_concept`
    call (today's `tags=[]` hard-code with no `source_frontmatter=`
    argument at all) must also carry `source_frontmatter`, mirroring
    `test_compose_catalog_update_preserves_event_date_on_marker_only_rebuild`'s
    shape for `event_date`. Task 2.9. **RED today**: the rebuilt
    `concept_content`'s frontmatter is absent even though the first build
    had it -- **MUTATION**: after 2.10 fixes it, reverting only this call
    site's `source_frontmatter=` argument must fail this test again,
    proving it exercises the SECOND build, not the first."""
    source = _source_plan(
        raw_content="---\nauthor: Jane\n---\nSome raw notes about self-control."
    )
    metadata, _ = okf.load_frontmatter(source.content)
    assert metadata[okf.SOURCE_FRONTMATTER_KEY] == {"author": "Jane"}
    staged = _staged(skip_reason="no-concepts-found")
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    # Sanity: the rebuild branch actually fired (matches the existing
    # `test_compose_catalog_update_conditional_rerender_on_skip_reason`).
    assert update.concept_content != source.content
    rebuilt_metadata, _ = okf.load_frontmatter(update.concept_content)
    assert rebuilt_metadata.get(okf.SOURCE_FRONTMATTER_KEY) == {"author": "Jane"}


# -- Phase 3 (preserve-source-frontmatter, issue #1062): tag lift and
# sensitivity fold (design.md Decision 3, Decision 7) --


def test_compose_source_document_fresh_ingest_tags_are_exactly_lifted() -> None:
    """ingestion: "No incoming tags key leaves the Source's tags unaffected"
    (fresh half) and "A YAML list of tag strings lifts each tag". Task 3.6.
    **RED today**: no tag lift is wired yet -- `plan.tags` does not exist."""
    plan = _source_plan(
        raw_content="---\ntags: [alpha, beta]\n---\nSome raw notes.",
        concept_text=None,
    )
    assert plan.tags == ("alpha", "beta")

    plan_no_tags = _source_plan(
        raw_content="Some raw notes about self-control.", concept_text=None
    )
    assert plan_no_tags.tags == ()


def test_compose_source_document_reingest_tags_are_union() -> None:
    """ingestion: "Re-ingest unions lifted tags with on-disk tags". Task
    3.7."""
    prior = _prior_concept_text(tags=["alpha"])
    plan = _source_plan(
        raw_content="---\ntags: [beta]\n---\nSome raw notes.",
        concept_text=prior,
    )
    assert plan.tags == ("alpha", "beta")


def test_compose_source_document_hand_added_tag_survives_reingest() -> None:
    """ingestion: "A hand-added tag survives re-ingest even when the
    incoming file's tags changed". Task 3.8."""
    prior = _prior_concept_text(tags=["alpha", "hand-added"])
    plan = _source_plan(
        raw_content="---\ntags: [beta]\n---\nSome raw notes.",
        concept_text=prior,
    )
    assert plan.tags == ("alpha", "hand-added", "beta")


class TestSensitivityFoldRaiseOnly:
    """Task 3.9: `test_compose_source_document_sensitivity_fold_raise_only`,
    covering design.md Decision 3's fold order -- an incoming sensitivity
    only ever RAISES the resolved value, never lowers it."""

    def test_incoming_sensitivity_raises_above_on_disk_and_config(self) -> None:
        prior = _prior_concept_text(sensitivity="private")
        plan = _source_plan(
            raw_content="---\nsensitivity: confidential\n---\nSome raw notes.",
            concept_text=prior,
            cfg=_default_cfg(default_sensitivity="private"),
        )
        assert plan.resolved_sensitivity == "confidential"

    def test_lower_incoming_sensitivity_never_lowers_the_resolved_value(self) -> None:
        prior = _prior_concept_text(sensitivity="confidential")
        plan = _source_plan(
            raw_content="---\nsensitivity: public\n---\nSome raw notes.",
            concept_text=prior,
            cfg=_default_cfg(default_sensitivity="private"),
        )
        assert plan.resolved_sensitivity == "confidential"

    def test_absent_incoming_sensitivity_is_byte_identical_to_pre_phase_3_fold(
        self,
    ) -> None:
        """PRECONDITION-style byte-identity: compute the pre-lift fold
        directly (on-disk + config only, no incoming frontmatter at all),
        and confirm a run with an incoming frontmatter block that carries NO
        `sensitivity` key resolves to the exact same value."""
        prior = _prior_concept_text(sensitivity="private")
        baseline_plan = _source_plan(
            raw_content="Some raw notes about self-control.",
            concept_text=prior,
            cfg=_default_cfg(default_sensitivity="confidential"),
        )
        plan = _source_plan(
            raw_content="---\nauthor: Jane\n---\nSome raw notes.",
            concept_text=prior,
            cfg=_default_cfg(default_sensitivity="confidential"),
        )
        assert plan.resolved_sensitivity == baseline_plan.resolved_sensitivity

    def test_explicit_null_incoming_sensitivity_is_also_byte_identical(self) -> None:
        """The null-vs-absent rule (design.md Decision 3): an explicit YAML
        `null` must not be folded as `None`, which would wrongly floor a
        `public` workspace to `private` (`_rank(None)`) -- the MUTATION this
        task targets."""
        prior = _prior_concept_text(sensitivity="public")
        baseline_plan = _source_plan(
            raw_content="Some raw notes about self-control.",
            concept_text=prior,
            cfg=_default_cfg(default_sensitivity="public"),
        )
        plan = _source_plan(
            raw_content="---\nsensitivity: null\n---\nSome raw notes.",
            concept_text=prior,
            cfg=_default_cfg(default_sensitivity="public"),
        )
        assert (
            plan.resolved_sensitivity == baseline_plan.resolved_sensitivity == "public"
        )


# -- Slice 2 (issue #1014c / ADR-0023): `resolve_event_date`, stored
# read-back, `compose_source_document`/`compose_catalog_update` threading,
# and the carried-marker short-circuit (design.md Decisions 4, 3, 6) --


@pytest.mark.parametrize(
    (
        "flag",
        "stored",
        "inferred",
        "expected_value",
        "expected_origin",
        "expected_previous",
        "expected_changed",
    ),
    [
        pytest.param(
            date(2026, 7, 14),
            okf.StoredEventDate(
                value=date(2026, 1, 1), malformed=False, raw="2026-01-01"
            ),
            date(2026, 3, 3),
            date(2026, 7, 14),
            "flag",
            date(2026, 1, 1),
            True,
            id="flag-wins-over-stored-and-inferred",
        ),
        pytest.param(
            None,
            okf.StoredEventDate(
                value=date(2026, 1, 1), malformed=False, raw="2026-01-01"
            ),
            date(2026, 3, 3),
            date(2026, 1, 1),
            "kept",
            date(2026, 1, 1),
            False,
            id="stored-value-kept-when-no-flag",
        ),
        pytest.param(
            None,
            None,
            date(2026, 3, 3),
            date(2026, 3, 3),
            "file name",
            None,
            True,
            id="inferred-fills-the-gap-when-stored-is-absent",
        ),
        pytest.param(
            None,
            okf.StoredEventDate(value=None, malformed=True, raw="14/07/2026"),
            date(2026, 3, 3),
            date(2026, 3, 3),
            "file name",
            None,
            True,
            id="inferred-fills-the-gap-when-stored-is-malformed",
        ),
        pytest.param(
            None,
            None,
            None,
            None,
            None,
            None,
            False,
            id="no-evidence-leaves-the-key-unset",
        ),
    ],
)
def test_resolve_event_date_precedence(
    flag: date | None,
    stored: okf.StoredEventDate | None,
    inferred: date | None,
    expected_value: date | None,
    expected_origin: str | None,
    expected_previous: date | None,
    expected_changed: bool,
) -> None:
    """design.md Decision 4's precedence table: `flag` set wins regardless
    of `stored`/`inferred`; otherwise a VALID stored value is `"kept"`;
    otherwise an inferred file-name date fills the gap; otherwise the
    result is unset. A malformed stored value counts as absent -- that is
    the mechanism that keeps it from being carried forward. `.changed` is
    `value != previous`, where `previous` is the stored value only when it
    is not malformed."""
    resolution = ingest_service.resolve_event_date(
        flag=flag, stored=stored, inferred=inferred
    )
    assert resolution.value == expected_value
    assert resolution.origin == expected_origin
    assert resolution.previous == expected_previous
    assert resolution.changed is expected_changed


@pytest.mark.parametrize(
    (
        "flag",
        "stored",
        "incoming",
        "inferred",
        "expected_value",
        "expected_origin",
    ),
    [
        pytest.param(
            date(2026, 8, 1),
            okf.StoredEventDate(
                value=date(2026, 1, 1), malformed=False, raw="2026-01-01"
            ),
            date(2026, 7, 14),
            date(2026, 3, 3),
            date(2026, 8, 1),
            "flag",
            id="flag-wins-over-stored-incoming-and-inferred",
        ),
        pytest.param(
            None,
            okf.StoredEventDate(
                value=date(2026, 1, 1), malformed=False, raw="2026-01-01"
            ),
            date(2026, 7, 14),
            date(2026, 3, 3),
            date(2026, 1, 1),
            "kept",
            id="stored-wins-over-incoming-and-inferred",
        ),
        pytest.param(
            None,
            None,
            date(2026, 7, 14),
            date(2026, 3, 3),
            date(2026, 7, 14),
            "frontmatter",
            id="incoming-wins-over-inferred-when-stored-absent",
        ),
        pytest.param(
            None,
            okf.StoredEventDate(value=None, malformed=True, raw="14/07/2026"),
            date(2026, 7, 14),
            date(2026, 3, 3),
            date(2026, 7, 14),
            "frontmatter",
            id="incoming-wins-over-inferred-when-stored-malformed",
        ),
        pytest.param(
            None,
            None,
            None,
            date(2026, 3, 3),
            date(2026, 3, 3),
            "file name",
            id="inferred-fills-the-gap-when-incoming-absent",
        ),
        pytest.param(
            None,
            None,
            None,
            None,
            None,
            None,
            id="no-evidence-leaves-the-key-unset",
        ),
    ],
)
def test_resolve_event_date_four_tier_precedence_table(
    flag: date | None,
    stored: okf.StoredEventDate | None,
    incoming: date | None,
    inferred: date | None,
    expected_value: date | None,
    expected_origin: str | None,
) -> None:
    """Task 4.5: design.md Decision 5's four-tier precedence: `flag` >
    validly `stored` (`kept`) > `incoming` (`frontmatter`) > `inferred`
    (`file name`) > unset. **RED today**: `TypeError` -- `resolve_event_
    date` has no `incoming` parameter yet."""
    resolution = ingest_service.resolve_event_date(
        flag=flag, stored=stored, incoming=incoming, inferred=inferred
    )
    assert resolution.value == expected_value
    assert resolution.origin == expected_origin


def test_resolve_event_date_created_key_never_consulted() -> None:
    """Task 4.6: an incoming mapping carrying `created` and no `date` key
    computes `incoming=None` via `okf.read_incoming_date` (which never
    reads `created`), so `resolve_event_date`'s chain falls through to
    inference/unset exactly as if no incoming frontmatter existed at all --
    an end-to-end confirmation of "created is ignored for this precedence"
    at the `resolve_event_date` call boundary. **RED today**: `TypeError`
    until 4.7 adds the `incoming` parameter."""
    incoming = okf.read_incoming_date({"created": "2026-01-01"})
    assert incoming is None

    resolution = ingest_service.resolve_event_date(
        flag=None, stored=None, incoming=incoming, inferred=date(2026, 3, 3)
    )
    assert resolution.value == date(2026, 3, 3)
    assert resolution.origin == "file name"


def test_compose_source_document_emits_event_date_when_given() -> None:
    """ingest-application-service spec: "A given event_date reaches the
    generated document" -- `event_date_flag` reaches `resolve_event_date`
    and then the built frontmatter unchanged; the service resolves nothing
    of its own beyond calling `resolve_event_date` (design Decision 4)."""
    plan = _source_plan(event_date_flag=date(2026, 7, 14))
    metadata, _ = okf.load_frontmatter(plan.content)
    assert metadata.get(okf.EVENT_DATE_KEY) == "2026-07-14"
    assert plan.event_date.value == date(2026, 7, 14)
    assert plan.event_date.origin == "flag"


def test_compose_source_document_is_byte_identical_when_event_date_is_none() -> None:
    """ingest-application-service spec: "A None event_date produces a
    byte-identical Source" -- the default call (no flag, no source_name)
    produces exactly the same bytes as an explicit `event_date_flag=None,
    source_name=None` call, and neither carries the key."""
    with_defaults = _source_plan()
    explicit_none = _source_plan(event_date_flag=None, source_name=None)
    assert with_defaults.content == explicit_none.content
    metadata, _ = okf.load_frontmatter(with_defaults.content)
    assert okf.EVENT_DATE_KEY not in metadata
    assert with_defaults.event_date.value is None
    assert with_defaults.event_date.origin is None


def test_compose_source_document_reads_back_stored_event_date() -> None:
    """design.md Decision 4: "Where the stored value is read back" -- a
    re-ingest whose `concept_text` carries a prior `event_date` surfaces it
    on `SourceDocumentPlan.event_date.previous`, mirroring `on_disk_
    sensitivity`/`on_disk_title`'s existing read-back shape. No flag and no
    `source_name` here, so the ONLY evidence is the stored value -- the
    resolution keeps it (`origin == "kept"`)."""
    prior = _prior_concept_text(event_date=date(2026, 7, 10))
    plan = _source_plan(concept_text=prior)
    assert plan.event_date.previous == date(2026, 7, 10)
    assert plan.event_date.origin == "kept"
    assert plan.event_date.value == date(2026, 7, 10)


def test_compose_source_document_reads_incoming_date_independently_of_lift() -> None:
    """Task 4.8 (tasks-phase decision 2): a fixture with incoming
    frontmatter carrying ONLY `date: 2026-07-14` (no `tags`, no
    `sensitivity`) still resolves `event_date` to `2026-07-14` --
    `compose_source_document` calls `okf.read_incoming_date` directly off
    the parsed mapping, not through `lift_incoming_frontmatter` (whose
    `event_date` field stays `None` in this change). **RED today**:
    `AssertionError` -- no call site exists yet."""
    plan = _source_plan(
        raw_content="---\ndate: 2026-07-14\n---\nSome raw notes.",
        concept_text=None,
    )
    assert plan.event_date.value == date(2026, 7, 14)
    assert plan.event_date.origin == "frontmatter"


def test_compose_catalog_update_preserves_event_date_on_marker_only_rebuild() -> None:
    """ingest-application-service spec: "A marker-only catalog rebuild
    keeps the resolved event_date" -- design.md Decision 3's SECOND
    `build_source_concept` call site, the one the proposal missed. A
    Source whose resolved `event_date` is set must not lose it when
    `compose_catalog_update` rebuilds solely to stamp a fresh
    `extraction_status`/`extraction_notice` marker."""
    source = _source_plan(event_date_flag=date(2026, 7, 14))
    staged = _staged(skip_reason="no-concepts-found")
    update = ingest_service.compose_catalog_update(
        source=source,
        staged=staged,
        slug="notes",
        resource="raw/notes.txt",
        index_text="---\nokf_version: '0.1'\n---\n",
        log_text="---\nokf_version: '0.1'\n---\n",
        regenerate=False,
        timestamp="2026-07-14T18:30:00Z",
        entry_date=date(2026, 7, 14),
    )
    # Sanity: the rebuild branch actually fired (matches the existing
    # `test_compose_catalog_update_conditional_rerender_on_skip_reason`).
    assert update.concept_content != source.content
    metadata, _ = okf.load_frontmatter(update.concept_content)
    assert metadata.get(okf.EVENT_DATE_KEY) == "2026-07-14"


def test_carried_extraction_status_fails_closed() -> None:
    """design.md Decision 6: `carried_extraction_status` mirrors
    `carried_extraction_notice`'s fail-closed membership check
    (`application/ingest.py:547-568`), narrowed to a SINGLE
    `okf.ExtractionStatus | None` rather than a tuple --
    `EXTRACTION_STATUS_KEY` has never carried more than one token (#187).
    A value inside `okf.EXTRACTION_STATUS_VALUES` narrows through;
    anything else -- a missing key, an unrecognised string, or a wrong
    type -- yields `None`, matching `carried_extraction_notice`'s posture
    that frontmatter is hand-editable and a later release's token must
    never crash an otherwise-healthy run."""
    for status in okf.EXTRACTION_STATUS_VALUES:
        assert (
            ingest_service.carried_extraction_status(
                {okf.EXTRACTION_STATUS_KEY: status}
            )
            == status
        )

    assert ingest_service.carried_extraction_status({}) is None
    assert (
        ingest_service.carried_extraction_status(
            {okf.EXTRACTION_STATUS_KEY: "quarantined"}
        )
        is None
    )
    assert (
        ingest_service.carried_extraction_status({okf.EXTRACTION_STATUS_KEY: True})
        is None
    )
    assert (
        ingest_service.carried_extraction_status({okf.EXTRACTION_STATUS_KEY: None})
        is None
    )


def test_stage_derived_objects_returns_carried_markers_without_llm_call(
    tmp_path: Path,
) -> None:
    """design.md Decision 6: the carried-marker short-circuit -- GIVEN a
    `ConvergedReingest` whose `carried_status` and `carried_notices` are
    both set, WHEN `stage_derived_objects(carried=...)` runs with a stub
    `LLMBackend` whose `chat` raises, THEN it returns the carried markers
    and the stub's `chat` is never invoked -- the SAME pre-extraction
    short-circuit shape as `test_stage_derived_objects_returns_no_
    extractable_text_reason`/`..._blocked_by_sensitivity_reason` above."""
    converged = ingest_service.ConvergedReingest(
        carried_notices=(okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES,),
        carried_status="no-concepts-found",
    )
    stub_llm = _FakeLLM(raises=AssertionError("must not be called"))
    outcome = ingest_service.stage_derived_objects(
        **_stage_kwargs(tmp_path, llm=stub_llm, carried=converged)  # type: ignore[arg-type]
    )
    assert outcome.plans == ()
    assert outcome.skip_reason == "no-concepts-found"
    assert outcome.notices == (okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES,)
    assert outcome.report is None
    assert outcome.drops == ()
    assert outcome.lost_in_staging == 0
