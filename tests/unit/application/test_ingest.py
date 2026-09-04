"""Filesystem-free unit tests for `openkos.application.ingest` (issue #918).

Slice 1 covers `DerivedPlan` and the three collision-detection helpers,
moved verbatim from `cli/main.py`. Slice 2 covers the typed contracts
(`DropKind`/`StagingDrop`/`StagedDerivedObjects`) and `stage_derived_objects`
itself, de-presented -- it returns typed disclosure data instead of calling
`typer.echo`, and propagates `OllamaError` rather than catching it (design:
"the backend exception propagates; the adapter catches `OllamaError`"). No
Typer runner and no bundle directory beyond a `tmp_path`-scoped collision
scan -- the whole point of the application layer (ADR-0018) is that this
module's behavior is reachable without driving a CLI command.
"""

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos import config
from openkos.application import ingest as ingest_service
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
) -> object:
    """Monkeypatch stand-in for `extract_concept`/`extract_concept_union`,
    mirroring `tests/unit/cli/test_ingest.py::_capturing_extractor` -- lets a
    test fabricate an exact `ExtractionOutcome` (objects + report) without
    driving the real extraction pipeline, so branches gated on `report`
    fields (judge status, sole-object-restates, unevidenced titles) are
    reachable without an LLM call."""
    report = concept_mod.ExtractionReport(
        produced=len(objects),
        retained=len(objects),
        judge_status=judge_status,
        sole_object_restates_source=sole_object_restates_source,
        unevidenced_titles=unevidenced_titles,
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
