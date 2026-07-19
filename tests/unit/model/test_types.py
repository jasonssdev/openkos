"""Unit tests for the object-type vocabulary registry.

`model/types.py` is the single source of truth for the object-type
vocabulary: every derived projection consumed by `extraction/concept.py`,
`model/okf.py`, `cli/main.py`, and `bundle/index.py` MUST equal today's
literal values -- this is the behavior-preservation proof for the PR1
extraction (design: "Behavior-preservation proof"). This slice adds the
KOM "why" tier as classifiable: `Project` is inserted after `Decision`,
and `Decision` itself flips from ordering-only to fully classifiable
(design: Decision 1) -- every other pairwise order is unchanged (spec:
"Canonical order is insert-only")."""

from dataclasses import FrozenInstanceError

import pytest

from openkos.model import types


def test_registry_has_ten_entries_with_project() -> None:
    """REGISTRY has the 9 pre-existing types plus `Project`, inserted
    immediately after `Decision`, before `Person` (design: Decision 1)."""
    names = tuple(ot.name for ot in types.REGISTRY)
    assert names == (
        "Concept",
        "Entity",
        "Place",
        "Event",
        "Procedure",
        "Decision",
        "Project",
        "Person",
        "Organization",
        "Source",
    )


def test_object_type_is_frozen() -> None:
    """`ObjectType` is immutable -- the registry is a fixed, tamper-proof vocabulary."""
    entry = types.REGISTRY[0]
    with pytest.raises(FrozenInstanceError):
        entry.name = "Mutated"  # type: ignore[misc]


def test_source_is_the_only_non_classifiable_type() -> None:
    """`Source` is the SOLE ordering-only, non-classifiable registry entry:
    `Decision` flipped to classifiable (design: Decision 1), so every other
    type -- including `Decision` and the newly added `Project` -- is
    `llm_classifiable` (spec: "Classification Prompt Presents the Full
    9-Type Vocabulary")."""
    by_name = {ot.name: ot for ot in types.REGISTRY}
    assert by_name["Decision"].llm_classifiable is True
    assert by_name["Decision"].link_dir == "decisions"
    assert by_name["Project"].llm_classifiable is True
    assert by_name["Project"].link_dir == "projects"
    non_classifiable = {ot.name for ot in types.REGISTRY if not ot.llm_classifiable}
    assert non_classifiable == {"Source"}


def test_classifiable_types_matches_widened_set() -> None:
    """`CLASSIFIABLE_TYPES` is the closed 9-value set: the 7 pre-existing
    classifiable types plus `Decision` and `Project` (spec: "Type
    Classification Prefers Specific Types Over the Entity Fallback")."""
    assert (
        frozenset(
            {
                "Concept",
                "Entity",
                "Place",
                "Event",
                "Procedure",
                "Decision",
                "Project",
                "Person",
                "Organization",
            }
        )
        == types.CLASSIFIABLE_TYPES
    )


def test_type_to_link_dir_includes_decision_and_project() -> None:
    """`TYPE_TO_LINK_DIR` gains `Decision -> decisions` and `Project ->
    projects` keys; the 7 pre-existing entries are unchanged (spec: "Dir/
    section maps unchanged for pre-existing types")."""
    assert types.TYPE_TO_LINK_DIR == {
        "Concept": "concepts",
        "Entity": "entities",
        "Place": "places",
        "Event": "events",
        "Procedure": "procedures",
        "Decision": "decisions",
        "Project": "projects",
        "Person": "people",
        "Organization": "organizations",
    }


def test_type_to_section_includes_decision_and_project() -> None:
    """`TYPE_TO_SECTION` gains `Decision -> Decisions` and `Project ->
    Projects` keys; the 7 pre-existing entries are unchanged."""
    assert types.TYPE_TO_SECTION == {
        "Concept": "Concepts",
        "Entity": "Entities",
        "Place": "Places",
        "Event": "Events",
        "Procedure": "Procedures",
        "Decision": "Decisions",
        "Project": "Projects",
        "Person": "People",
        "Organization": "Organizations",
    }


def test_classifiable_link_dirs_includes_decisions_and_projects_in_registry_order() -> (
    None
):
    """`CLASSIFIABLE_LINK_DIRS` gains `"decisions"` and `"projects"`,
    positioned after `"procedures"` (registry order), so the ingest
    idempotency scan covers `decisions/`/`projects/` too (spec: "Idempotency
    scan dirs derive from the registry")."""
    assert types.CLASSIFIABLE_LINK_DIRS == (
        "concepts",
        "entities",
        "places",
        "events",
        "procedures",
        "decisions",
        "projects",
        "people",
        "organizations",
    )


def test_canonical_section_order_inserts_project_after_decisions() -> None:
    """`CANONICAL_SECTION_ORDER` inserts `Projects` immediately after
    `Decisions` -- every other pair keeps its pre-existing relative order
    (spec: "Canonical order is insert-only")."""
    assert types.CANONICAL_SECTION_ORDER == (
        "Concepts",
        "Entities",
        "Places",
        "Events",
        "Procedures",
        "Decisions",
        "Projects",
        "People",
        "Organizations",
        "Sources",
    )
