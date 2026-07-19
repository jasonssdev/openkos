"""Unit tests for the object-type vocabulary registry.

`model/types.py` is the single source of truth for the object-type
vocabulary: every derived projection consumed by `extraction/concept.py`,
`model/okf.py`, `cli/main.py`, and `bundle/index.py` MUST equal today's
literal values -- this is the behavior-preservation proof for the PR1
extraction (design: "Behavior-preservation proof"). PR2 adds one `Place`
entry, inserted after `Entity` -- every other pairwise order is unchanged
(spec: "Canonical order is insert-only")."""

from dataclasses import FrozenInstanceError

import pytest

from openkos.model import types


def test_registry_has_seven_entries_with_place() -> None:
    """PR2 REGISTRY has the 6 pre-existing types plus `Place`, inserted
    immediately after `Entity` (design: Decision 1, PR2 result)."""
    names = tuple(ot.name for ot in types.REGISTRY)
    assert names == (
        "Concept",
        "Entity",
        "Place",
        "Decision",
        "Person",
        "Organization",
        "Source",
    )


def test_object_type_is_frozen() -> None:
    """`ObjectType` is immutable -- the registry is a fixed, tamper-proof vocabulary."""
    entry = types.REGISTRY[0]
    with pytest.raises(FrozenInstanceError):
        entry.name = "Mutated"  # type: ignore[misc]


def test_decision_and_source_are_not_llm_classifiable() -> None:
    """`Decision`/`Source` are ordering-only registry entries (design: Decision 3)."""
    by_name = {ot.name: ot for ot in types.REGISTRY}
    assert by_name["Decision"].llm_classifiable is False
    assert by_name["Decision"].link_dir is None
    assert by_name["Source"].llm_classifiable is False


def test_classifiable_types_matches_widened_set() -> None:
    """`CLASSIFIABLE_TYPES` is the closed 5-value set: the PR1 4 pre-existing
    types plus `Place` (spec: "Classifiable set matches legacy literal",
    widened by "Place is a classifiable type")."""
    assert frozenset({"Concept", "Entity", "Place", "Person", "Organization"}) == (
        types.CLASSIFIABLE_TYPES
    )


def test_type_to_link_dir_includes_place() -> None:
    """`TYPE_TO_LINK_DIR` gains a `Place -> places` key; the 4 pre-existing
    entries are unchanged (spec: "Dir/section maps unchanged for
    pre-existing types")."""
    assert types.TYPE_TO_LINK_DIR == {
        "Concept": "concepts",
        "Entity": "entities",
        "Place": "places",
        "Person": "people",
        "Organization": "organizations",
    }


def test_type_to_section_includes_place() -> None:
    """`TYPE_TO_SECTION` gains a `Place -> Places` key; the 4 pre-existing
    entries are unchanged."""
    assert types.TYPE_TO_SECTION == {
        "Concept": "Concepts",
        "Entity": "Entities",
        "Place": "Places",
        "Person": "People",
        "Organization": "Organizations",
    }


def test_classifiable_link_dirs_includes_places_in_registry_order() -> None:
    """`CLASSIFIABLE_LINK_DIRS` gains `"places"`, positioned after
    `"entities"` (registry order), so the ingest idempotency scan covers
    `places/` too (spec: "Idempotency scan dirs derive from the registry")."""
    assert types.CLASSIFIABLE_LINK_DIRS == (
        "concepts",
        "entities",
        "places",
        "people",
        "organizations",
    )


def test_canonical_section_order_inserts_places_after_entities() -> None:
    """`CANONICAL_SECTION_ORDER` inserts `Places` immediately after
    `Entities`, before `Decisions` -- every other pair keeps its pre-existing
    relative order (spec: "Canonical order is insert-only")."""
    assert types.CANONICAL_SECTION_ORDER == (
        "Concepts",
        "Entities",
        "Places",
        "Decisions",
        "People",
        "Organizations",
        "Sources",
    )
