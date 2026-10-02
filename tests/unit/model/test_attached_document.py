"""`okf.build_attached_document`: the deterministic revision an ingest attach
makes to an existing concept (attach-at-ingest, #1268)."""

from typing import Any

from openkos.model import okf

EXISTING_BODY = (
    "# Skill\n\nA reusable capability.\n\nLoaded on demand.\n\n"
    "## Related\n\n- [sources/a](/sources/a.md) — source this was extracted from\n"
)


def _existing(**overrides: Any) -> dict[str, object]:
    meta: dict[str, object] = {
        "type": "Concept",
        "title": "Skill",
        "description": "A reusable capability.",
        "tags": ["a"],
        "status": "stable",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": "public",
        "provenance": ["sources/a"],
        "sources": [{"id": "sources/a"}],
        "generated": {"by": "openkos/1", "at": "2026-01-01T00:00:00Z"},
    }
    meta.update(overrides)
    return meta


def _candidate(**overrides: Any) -> dict[str, object]:
    meta: dict[str, object] = {
        "type": "Concept",
        "title": "Skill",
        "description": "Another description.",
        "tags": ["b"],
        "status": "stable",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": "public",
        "provenance": ["sources/b"],
        "generated": {"by": "openkos/2", "at": "2026-02-01T00:00:00Z"},
        "type_alternative": "Entity",
    }
    meta.update(overrides)
    return meta


def _attach(
    existing: dict[str, object] | None = None,
    candidate: dict[str, object] | None = None,
    *,
    existing_body: str = EXISTING_BODY,
    candidate_body: str = "Skills can ship scripts.",
) -> tuple[dict[str, object], str]:
    return okf.build_attached_document(
        existing if existing is not None else _existing(),
        existing_body,
        candidate if candidate is not None else _candidate(),
        candidate_body,
        source_id="sources/b",
        source_title="Second note",
    )


def test_provenance_is_appended_and_sources_reprojected() -> None:
    meta, _ = _attach()
    assert meta["provenance"] == ["sources/a", "sources/b"]
    assert meta["sources"] == okf.project_sources(["sources/a", "sources/b"])


def test_version_is_incremented() -> None:
    meta, _ = _attach(_existing(version=1))
    assert meta["version"] == 2
    meta, _ = _attach(_existing(version=7))
    assert meta["version"] == 8


def test_missing_or_unusable_version_counts_as_one() -> None:
    base = _existing()
    del base["version"]
    assert _attach(base)[0]["version"] == 2
    assert _attach(_existing(version="x"))[0]["version"] == 2
    assert _attach(_existing(version=True))[0]["version"] == 2
    assert _attach(_existing(version=2.0))[0]["version"] == 2


def test_identity_fields_never_change() -> None:
    meta, body = _attach()
    assert meta["type"] == "Concept"
    assert meta["title"] == "Skill"
    assert meta["description"] == "A reusable capability."
    assert "id" not in meta
    assert body.startswith(EXISTING_BODY.split("## Related")[0])


def test_tags_are_unioned_and_type_alternative_is_not_imported() -> None:
    meta, _ = _attach()
    assert meta["tags"] == ["a", "b"]
    assert "type_alternative" not in meta


def test_sensitivity_is_the_high_water_mark_and_never_lowers() -> None:
    meta, _ = _attach(
        _existing(sensitivity="public"), _candidate(sensitivity="confidential")
    )
    assert meta["sensitivity"] == "confidential"
    meta, _ = _attach(
        _existing(sensitivity="confidential"), _candidate(sensitivity="public")
    )
    assert meta["sensitivity"] == "confidential"


def test_newer_side_supplies_freshness_and_generation() -> None:
    meta, _ = _attach(_existing(freshness="snapshot"), _candidate(freshness="timeless"))
    assert meta["freshness"] == "timeless"
    assert meta["generated"] == {"by": "openkos/2", "at": "2026-02-01T00:00:00Z"}


def test_no_merged_from_ledger_is_written() -> None:
    meta, _ = _attach()
    assert okf.MERGED_FROM_KEY not in meta


def test_existing_relations_are_kept() -> None:
    relations = [{"target": "concepts/z", "type": "related_to"}]
    meta, _ = _attach(_existing(relations=relations))
    assert meta["relations"] == relations


def test_body_section_lands_above_related_with_headings_demoted() -> None:
    _, body = _attach(candidate_body="Skills can ship scripts.\n\n## Detail\n\nmore")
    section = "## Update from Second note (sources/b)\n\n"
    assert section in body
    assert body.index(section) < body.index("## Related")
    assert "#### Detail" in body
    assert body.startswith(EXISTING_BODY.split("## Related")[0])


def test_related_gains_one_bullet_for_the_new_source() -> None:
    _, body = _attach()
    related = body.split("## Related", 1)[1]
    assert related.count("(/sources/a.md)") == 1
    assert "- [sources/b](/sources/b.md) — source this was extracted from" in related
    assert related.count("(/sources/b.md)") == 1


def test_contained_body_adds_no_section_but_still_revises() -> None:
    meta, body = _attach(candidate_body="Loaded   on demand.")
    assert "## Update from" not in body
    assert meta["provenance"] == ["sources/a", "sources/b"]
    assert meta["version"] == 2
    assert "(/sources/b.md)" in body


def test_empty_candidate_body_adds_no_section() -> None:
    _, body = _attach(candidate_body="  \n")
    assert "## Update from" not in body


def test_document_without_a_related_section_gets_one() -> None:
    _, body = _attach(existing_body="# Skill\n\nOnly prose.\n")
    assert body.index("## Update from") < body.index("## Related")
    assert "- [sources/b](/sources/b.md)" in body


def test_existing_text_is_never_rewritten_or_reordered() -> None:
    _, body = _attach()
    prefix = EXISTING_BODY.split("## Related")[0]
    assert body.startswith(prefix)
    assert "- [sources/a](/sources/a.md) — source this was extracted from" in body


def test_result_is_a_conformant_document(tmp_path: Any) -> None:
    meta, body = _attach()
    concepts = tmp_path / "concepts"
    concepts.mkdir()
    (concepts / "skill.md").write_text(
        okf.dump_frontmatter(meta, body), encoding="utf-8"
    )
    assert okf.check_conformance(tmp_path) == []


def test_inputs_are_not_mutated() -> None:
    existing = _existing()
    candidate = _candidate()
    before = (dict(existing), dict(candidate))
    _attach(existing, candidate)
    assert (existing, candidate) == before
