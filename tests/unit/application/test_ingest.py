"""Filesystem-free unit tests for `openkos.application.ingest` (issue #918).

Slice 1 covers `DerivedPlan` and the three collision-detection helpers,
moved verbatim from `cli/main.py`. No Typer runner and no bundle
directory beyond a `tmp_path`-scoped collision scan -- the whole point of
the application layer (ADR-0018) is that this module's behavior is
reachable without driving a CLI command.
"""

import dataclasses
from pathlib import Path

import pytest

from openkos.application import ingest as ingest_service


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
