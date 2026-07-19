"""Unit tests for the object-type vocabulary registry.

`model/types.py` is the single source of truth for the object-type
vocabulary: every derived projection consumed by `extraction/concept.py`,
`model/okf.py`, `cli/main.py`, and `bundle/index.py` MUST equal today's
literal values -- this is the behavior-preservation proof for the PR1
extraction (design: "Behavior-preservation proof").
"""

from dataclasses import FrozenInstanceError

import pytest

from openkos.model import types


def test_registry_has_six_entries_no_place() -> None:
    """PR1 REGISTRY has exactly today's 6 types; Place is added only in PR2."""
    names = tuple(ot.name for ot in types.REGISTRY)
    assert names == (
        "Concept",
        "Entity",
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


def test_classifiable_types_matches_legacy_literal() -> None:
    """Matches the classifier's/builder's pre-refactor `{Concept, Entity,
    Person, Organization}` literal (spec: "Classifiable set matches legacy
    literal")."""
    assert frozenset({"Concept", "Entity", "Person", "Organization"}) == (
        types.CLASSIFIABLE_TYPES
    )


def test_type_to_link_dir_matches_legacy_literal() -> None:
    """Matches `cli/main.py`'s pre-refactor `_TYPE_TO_LINK_DIR` literal
    (spec: "Dir/section maps unchanged for pre-existing types")."""
    assert types.TYPE_TO_LINK_DIR == {
        "Concept": "concepts",
        "Entity": "entities",
        "Person": "people",
        "Organization": "organizations",
    }


def test_type_to_section_matches_legacy_literal() -> None:
    """Matches `cli/main.py`'s pre-refactor `_TYPE_TO_SECTION` literal."""
    assert types.TYPE_TO_SECTION == {
        "Concept": "Concepts",
        "Entity": "Entities",
        "Person": "People",
        "Organization": "Organizations",
    }


def test_classifiable_link_dirs_matches_idempotency_tuple() -> None:
    """Matches `cli/main.py`'s hand-written idempotency scan tuple
    (spec: "Idempotency scan dirs derive from the registry")."""
    assert types.CLASSIFIABLE_LINK_DIRS == (
        "concepts",
        "entities",
        "people",
        "organizations",
    )


def test_canonical_section_order_matches_legacy_literal() -> None:
    """Matches `bundle/index.py`'s pre-refactor `_CANONICAL_SECTION_ORDER`
    literal -- no Place, no reorder (spec: "Canonical order is insert-only")."""
    assert types.CANONICAL_SECTION_ORDER == (
        "Concepts",
        "Entities",
        "Decisions",
        "People",
        "Organizations",
        "Sources",
    )
