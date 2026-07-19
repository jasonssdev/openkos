"""Unit tests for the object-type vocabulary registry.

`model/types.py` is the single source of truth for the object-type
vocabulary: every derived projection consumed by `extraction/concept.py`,
`model/okf.py`, `cli/main.py`, and `bundle/index.py` MUST equal today's
literal values -- this is the behavior-preservation proof for the PR1
extraction (design: "Behavior-preservation proof"). This slice adds two
occurrent entries, `Event` and `Procedure`, inserted after `Place`, before
`Decision` -- every other pairwise order is unchanged (spec: "Canonical
order is insert-only")."""

from dataclasses import FrozenInstanceError

import pytest

from openkos.model import types


def test_registry_has_nine_entries_with_event_and_procedure() -> None:
    """REGISTRY has the 7 pre-existing types plus `Event` and `Procedure`,
    inserted immediately after `Place`, before `Decision` (design: Decision
    1)."""
    names = tuple(ot.name for ot in types.REGISTRY)
    assert names == (
        "Concept",
        "Entity",
        "Place",
        "Event",
        "Procedure",
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
    """`CLASSIFIABLE_TYPES` is the closed 7-value set: the 5 pre-existing
    classifiable types plus `Event` and `Procedure` (spec: "Type
    Classification Prefers Specific Types Over the Entity Fallback")."""
    assert (
        frozenset(
            {
                "Concept",
                "Entity",
                "Place",
                "Event",
                "Procedure",
                "Person",
                "Organization",
            }
        )
        == types.CLASSIFIABLE_TYPES
    )


def test_type_to_link_dir_includes_event_and_procedure() -> None:
    """`TYPE_TO_LINK_DIR` gains `Event -> events` and `Procedure ->
    procedures` keys; the 5 pre-existing entries are unchanged (spec: "Dir/
    section maps unchanged for pre-existing types")."""
    assert types.TYPE_TO_LINK_DIR == {
        "Concept": "concepts",
        "Entity": "entities",
        "Place": "places",
        "Event": "events",
        "Procedure": "procedures",
        "Person": "people",
        "Organization": "organizations",
    }


def test_type_to_section_includes_event_and_procedure() -> None:
    """`TYPE_TO_SECTION` gains `Event -> Events` and `Procedure ->
    Procedures` keys; the 5 pre-existing entries are unchanged."""
    assert types.TYPE_TO_SECTION == {
        "Concept": "Concepts",
        "Entity": "Entities",
        "Place": "Places",
        "Event": "Events",
        "Procedure": "Procedures",
        "Person": "People",
        "Organization": "Organizations",
    }


def test_classifiable_link_dirs_includes_events_and_procedures_in_registry_order() -> (
    None
):
    """`CLASSIFIABLE_LINK_DIRS` gains `"events"` and `"procedures"`,
    positioned after `"places"` (registry order), so the ingest idempotency
    scan covers `events/`/`procedures/` too (spec: "Idempotency scan dirs
    derive from the registry")."""
    assert types.CLASSIFIABLE_LINK_DIRS == (
        "concepts",
        "entities",
        "places",
        "events",
        "procedures",
        "people",
        "organizations",
    )


def test_canonical_section_order_inserts_events_and_procedures_after_places() -> None:
    """`CANONICAL_SECTION_ORDER` inserts `Events` then `Procedures`
    immediately after `Places`, before `Decisions` -- every other pair keeps
    its pre-existing relative order (spec: "Canonical order is
    insert-only")."""
    assert types.CANONICAL_SECTION_ORDER == (
        "Concepts",
        "Entities",
        "Places",
        "Events",
        "Procedures",
        "Decisions",
        "People",
        "Organizations",
        "Sources",
    )
