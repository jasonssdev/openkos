"""Staging attaches a same-type, same-key candidate to the existing concept
instead of forking it as `<slug>-N` (attach-at-ingest, #1268)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import ingest as application_ingest
from openkos.application.ingest import AttachLookup, AttachTarget
from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import ExtractionResult
from openkos.model import okf
from openkos.resolution.normalize import normalize_key


class _NoLLM:
    def chat(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("attach must not call a model")


def _existing_text(
    *,
    type_: str = "Concept",
    title: str = "Skill",
    provenance: tuple[str, ...] = ("sources/a",),
    sensitivity: str = "public",
    version: int = 1,
    status: str = "stable",
) -> str:
    metadata: dict[str, object] = {
        "type": type_,
        "title": title,
        "description": "A reusable capability.",
        "tags": [],
        "status": status,
        "version": version,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": list(provenance),
        "generated": {"by": "openkos/1", "at": "2026-01-01T00:00:00Z"},
    }
    related = "\n".join(
        f"- [{p}](/{p}.md) — source this was extracted from" for p in provenance
    )
    return okf.dump_frontmatter(
        metadata, f"# {title}\n\nA reusable capability.\n\n## Related\n\n{related}\n"
    )


def _write(bundle: Path, concept_id: str, text: str) -> None:
    path = bundle / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _lookup(
    bundle: Path, texts: dict[str, str], *keys: tuple[str, str, str]
) -> AttachLookup:
    """A hand-built lookup: `keys` are `(type, normalized key, concept id)`."""
    matches: dict[tuple[str, str], list[str]] = {}
    for type_, key, concept_id in keys:
        matches.setdefault((type_, key), []).append(concept_id)
        _write(bundle, concept_id, texts[concept_id])
    return AttachLookup(
        find=lambda type_, title: tuple(matches.get((type_, normalize_key(title)), ())),
        read=lambda concept_id: AttachTarget(concept_id, texts[concept_id]),
    )


def _extractor(
    *results: ExtractionResult,
) -> Callable[..., concept_mod.ExtractionOutcome]:
    def run(*args: Any, **kwargs: Any) -> concept_mod.ExtractionOutcome:
        return concept_mod.ExtractionOutcome(
            objects=list(results),
            report=concept_mod.ExtractionReport(
                produced=len(results), retained=len(results), chunks=1, runs=1
            ),
        )

    return run


def _result(
    title: str = "Skill", type_: str = "Concept", body: str = "Ships scripts."
) -> ExtractionResult:
    return ExtractionResult(
        type=type_, title=title, description="Another description.", body=body
    )


def _stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    results: list[ExtractionResult],
    attach: AttachLookup | None,
    *,
    source_slug: str = "b",
    stamp_sensitivity: str = "public",
) -> application_ingest.StagedDerivedObjects:
    monkeypatch.setattr(
        application_ingest, "extract_concept_union", _extractor(*results)
    )
    (tmp_path / "openkos.yaml").write_text("model: x\n", encoding="utf-8")
    return application_ingest.stage_derived_objects(
        raw_content="Some notes about skills.",
        source_title="Second note",
        source_slug=source_slug,
        workspace_floor="private",
        stamp_sensitivity=stamp_sensitivity,
        timestamp="2026-02-01T00:00:00Z",
        bundle_dir=tmp_path / "bundle",
        llm=_NoLLM(),
        cfg=config.read_config(tmp_path),
        union_judge=True,
        attach=attach,
    )


def test_a_matching_candidate_becomes_an_attach_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {"concepts/skill": _existing_text()}
    lookup = _lookup(bundle, texts, ("Concept", "skill", "concepts/skill"))

    staged = _stage(tmp_path, monkeypatch, [_result()], lookup)

    (plan,) = staged.plans
    assert plan.attach_to == "concepts/skill"
    assert plan.slug == "skill"
    assert plan.path == bundle / "concepts" / "skill.md"
    meta, body = okf.load_frontmatter(plan.content)
    assert meta["provenance"] == ["sources/a", "sources/b"]
    assert meta["version"] == 2
    assert "## Update from Second note (sources/b)" in body
    assert plan.attach_version == 2
    assert [d.kind for d in staged.drops] == ["attached"]
    assert staged.drops[0].attached_to == "concepts/skill"
    assert staged.lost_in_staging == 0
    assert not (bundle / "concepts" / "skill-2.md").exists()


def test_no_lookup_reproduces_todays_disambiguation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "concepts/skill", _existing_text())

    staged = _stage(tmp_path, monkeypatch, [_result()], None)

    (plan,) = staged.plans
    assert plan.attach_to is None
    assert plan.slug == "skill-2"
    assert plan.disambiguated_from == "skill"


@pytest.mark.parametrize("excluded", ["Person", "Event"])
def test_excluded_types_keep_the_slug_collision_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, excluded: str
) -> None:
    bundle = tmp_path / "bundle"
    link_dir = {"Person": "people", "Event": "events"}[excluded]
    texts = {f"{link_dir}/skill": _existing_text(type_=excluded)}
    # Even a lookup that (wrongly) offered the match must not be consulted.
    lookup = _lookup(bundle, texts, (excluded, "skill", f"{link_dir}/skill"))

    staged = _stage(tmp_path, monkeypatch, [_result(type_=excluded)], lookup)

    (plan,) = staged.plans
    assert plan.attach_to is None
    assert plan.slug == "skill-2"


def test_a_different_type_does_not_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {"concepts/skill": _existing_text()}
    lookup = _lookup(bundle, texts, ("Concept", "skill", "concepts/skill"))

    staged = _stage(tmp_path, monkeypatch, [_result(type_="Project")], lookup)

    (plan,) = staged.plans
    assert plan.attach_to is None
    assert plan.path == bundle / "projects" / "skill.md"


def test_normalization_decides_the_match_not_the_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {"places/cafe-central": _existing_text(type_="Place", title="Café Central")}
    lookup = _lookup(bundle, texts, ("Place", "cafe central", "places/cafe-central"))

    staged = _stage(tmp_path, monkeypatch, [_result("Cafe central", "Place")], lookup)

    (plan,) = staged.plans
    assert plan.attach_to == "places/cafe-central"
    assert plan.path == bundle / "places" / "cafe-central.md"


def test_a_source_that_already_owns_a_match_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {
        "concepts/skill": _existing_text(),
        "concepts/skill-2": _existing_text(provenance=("sources/b",)),
    }
    lookup = _lookup(
        bundle,
        texts,
        ("Concept", "skill", "concepts/skill"),
        ("Concept", "skill", "concepts/skill-2"),
    )

    staged = _stage(tmp_path, monkeypatch, [_result()], lookup, source_slug="b")

    assert staged.plans == ()
    assert [d.kind for d in staged.drops] == ["already-exists"]


def test_a_family_attaches_to_its_canonical_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {
        "concepts/skill-3": _existing_text(),
        "concepts/skill": _existing_text(),
        "concepts/skill-2": _existing_text(),
    }
    lookup = _lookup(
        bundle,
        texts,
        ("Concept", "skill", "concepts/skill-3"),
        ("Concept", "skill", "concepts/skill-2"),
        ("Concept", "skill", "concepts/skill"),
    )

    staged = _stage(tmp_path, monkeypatch, [_result()], lookup, source_slug="d")

    (plan,) = staged.plans
    assert plan.attach_to == "concepts/skill"


def test_a_second_candidate_for_the_same_target_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {"places/cafe": _existing_text(type_="Place", title="Cafe")}
    lookup = _lookup(bundle, texts, ("Place", "cafe", "places/cafe"))

    staged = _stage(
        tmp_path,
        monkeypatch,
        [_result("Cafe", "Place"), _result("Café", "Place", body="Other.")],
        lookup,
    )

    assert [p.attach_to for p in staged.plans] == ["places/cafe"]
    assert [d.kind for d in staged.drops] == ["attached", "in-batch-collision"]


def test_attach_raises_sensitivity_to_the_high_water_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {"concepts/skill": _existing_text(sensitivity="public")}
    lookup = _lookup(bundle, texts, ("Concept", "skill", "concepts/skill"))

    staged = _stage(
        tmp_path, monkeypatch, [_result()], lookup, stamp_sensitivity="confidential"
    )

    meta, _ = okf.load_frontmatter(staged.plans[0].content)
    assert meta["sensitivity"] == "confidential"


def test_attach_never_lowers_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {"concepts/skill": _existing_text(sensitivity="confidential")}
    lookup = _lookup(bundle, texts, ("Concept", "skill", "concepts/skill"))

    staged = _stage(
        tmp_path, monkeypatch, [_result()], lookup, stamp_sensitivity="public"
    )

    meta, _ = okf.load_frontmatter(staged.plans[0].content)
    assert meta["sensitivity"] == "confidential"


def test_an_unparseable_match_is_ignored_and_the_slug_path_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    texts = {"concepts/skill": "---\n: : :\n---\n"}
    lookup = _lookup(bundle, texts, ("Concept", "skill", "concepts/skill"))

    staged = _stage(tmp_path, monkeypatch, [_result()], lookup)

    (plan,) = staged.plans
    assert plan.attach_to is None


# --- the lookup builder -------------------------------------------------------


def _write_doc(bundle: Path, concept_id: str, frontmatter: str) -> None:
    _write(bundle, concept_id, f"---\n{frontmatter}\n---\n\n# x\n")


def test_builder_groups_by_type_and_key_and_skips_excluded_types(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    _write_doc(bundle, "concepts/skill", "type: Concept\ntitle: Skill")
    _write_doc(bundle, "concepts/skill-2", "type: Concept\ntitle: Skill")
    _write_doc(bundle, "people/ana", "type: Person\ntitle: Ana")
    _write_doc(bundle, "events/launch", "type: Event\ntitle: Launch")

    lookup = application_ingest.build_attach_lookup(
        bundle, read=lambda concept_id: AttachTarget(concept_id, "")
    )

    assert lookup.find("Concept", "Skill") == ("concepts/skill", "concepts/skill-2")
    assert lookup.find("Person", "Ana") == ()
    assert lookup.find("Event", "Launch") == ()


def test_builder_never_offers_a_deprecated_concept(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _write_doc(
        bundle, "concepts/skill", "type: Concept\ntitle: Skill\nstatus: deprecated"
    )

    lookup = application_ingest.build_attach_lookup(
        bundle, read=lambda concept_id: AttachTarget(concept_id, "")
    )

    assert lookup.find("Concept", "Skill") == ()


def test_excluded_types_are_exactly_event_and_person() -> None:
    assert frozenset({"Event", "Person"}) == application_ingest.ATTACH_EXCLUDED_TYPES
