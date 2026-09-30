"""Unit tests for the relation-type vocabulary registry (Phase 1, task 1.1).

`model/relations.py` mirrors `model/types.py::REGISTRY`'s zero-dependency-
leaf shape: it is the single source of truth for the SEEDED relation-type
vocabulary (KOM:336 open vocabulary) -- an OPEN set, unlike
`types.py::CLASSIFIABLE_TYPES`'s CLOSED set.
"""

import ast
from pathlib import Path

import pytest

from openkos.model import relations

_MODULE_PATH = Path(relations.__file__)


def test_seeded_relation_types_has_eight_kom_defaults() -> None:
    """`SEEDED_RELATION_TYPES` is exactly KOM's 8 default relation types."""
    assert (
        frozenset(
            {
                "references",
                "depends_on",
                "derived_from",
                "related_to",
                "caused_by",
                "part_of",
                "member_of",
                "produced_by",
            }
        )
        == relations.SEEDED_RELATION_TYPES
    )


def test_derived_from_is_engine_owned() -> None:
    """`derived_from` MEANS provenance in OpenKOS, and the engine synthesizes
    it at graph projection from each doc's `provenance:` frontmatter
    (`graph/sqlite_graph.py`, #135). It is therefore the engine's to write,
    not a suggester's to propose (#380)."""
    assert frozenset({"derived_from"}) == relations.ENGINE_OWNED_RELATION_TYPES


def test_suggestable_is_the_seeded_vocabulary_minus_the_engine_owned_ones() -> None:
    """The suggestable set is DERIVED, never hand-listed -- a second literal
    list would drift from `REGISTRY` the first time a type is added."""
    assert relations.SUGGESTABLE_RELATION_TYPES == (
        relations.SEEDED_RELATION_TYPES - relations.ENGINE_OWNED_RELATION_TYPES
    )
    assert "derived_from" not in relations.SUGGESTABLE_RELATION_TYPES
    assert relations.SUGGESTABLE_RELATION_TYPES


def test_engine_owned_types_stay_in_the_seeded_kom_vocabulary() -> None:
    """#380 removes `derived_from` from what a MODEL may propose, not from the
    vocabulary itself. It is a KOM default, the engine writes it, and a human
    running `relate` must still be able to -- so it stays seeded, and
    `validate_relation_type` must not start calling it unknown."""
    assert "derived_from" in relations.SEEDED_RELATION_TYPES


def test_validate_does_not_treat_an_engine_owned_type_as_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The write path keeps accepting `derived_from` silently as a seeded
    type; narrowing the SUGGESTION vocabulary must not leak into `relate`."""
    assert relations.validate_relation_type("derived_from") == "derived_from"
    assert "is not a seeded relation type" not in capsys.readouterr().err


def test_module_has_zero_openkos_imports() -> None:
    """`model/relations.py` is a zero-dependency leaf, like `model/types.py`
    -- it must never import from another `openkos` module (design: "new leaf
    ... zero openkos imports, mirrors types.py")."""
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("openkos")
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith("openkos")


def test_validate_relation_type_never_writes_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Library code returns; the CLI decides what to print. Neither a seeded
    nor an unknown type may reach stderr from `validate_relation_type`."""
    assert relations.validate_relation_type("references") == "references"
    assert relations.validate_relation_type("custom_relation") == "custom_relation"
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_relation_type_note_is_none_for_a_seeded_type() -> None:
    assert relations.relation_type_note("references") is None
    assert relations.relation_type_note("  references  ") is None


def test_relation_type_note_carries_the_exact_advisory_text() -> None:
    """The note is the text the CLI prints verbatim on the `relate` path
    (spec: "Unknown type accepted with WARN to stderr")."""
    known = ", ".join(sorted(relations.SEEDED_RELATION_TYPES))
    assert relations.relation_type_note(" custom_relation ") == (
        f"openkos: note -- 'custom_relation' is not a seeded relation type "
        f"(known: {known})"
    )


def test_validate_relation_type_rejects_empty_type() -> None:
    """Empty type is rejected -- no write (spec: "Empty/whitespace type
    rejected")."""
    with pytest.raises(ValueError, match="non-empty"):
        relations.validate_relation_type("")


def test_validate_relation_type_rejects_whitespace_only_type() -> None:
    """Whitespace-only type is rejected -- no write."""
    with pytest.raises(ValueError, match="non-empty"):
        relations.validate_relation_type("   ")


def test_validate_relation_type_strips_surrounding_whitespace() -> None:
    """A type with surrounding whitespace is stripped before validation."""
    assert relations.validate_relation_type("  references  ") == "references"


def test_asymmetric_relation_types_are_exactly_the_direction_bearing_five() -> None:
    """The asymmetric set is the five types whose meaning flips when SOURCE
    and TARGET swap -- the set #613 measured direction confusion over and
    #624 quarantines behind per-item consent. `related_to` is symmetric by
    definition and `references` stays outside #624's scope."""
    assert (
        frozenset({"caused_by", "depends_on", "member_of", "part_of", "produced_by"})
        == relations.ASYMMETRIC_RELATION_TYPES
    )


def test_asymmetric_relation_types_are_a_subset_of_the_suggestable_vocabulary() -> None:
    """Every asymmetric type must remain suggestable: the set narrows HOW a
    suggestion is consented to, never what may be suggested -- mirroring
    `ENGINE_OWNED_RELATION_TYPES`' narrowing contract."""
    assert relations.ASYMMETRIC_RELATION_TYPES <= relations.SUGGESTABLE_RELATION_TYPES


def test_resolution_relation_types_membership() -> None:
    """`RESOLUTION_RELATION_TYPES` names exactly the three types `reconcile`
    writes, and is disjoint from every vocabulary an LLM may read from --
    `revises` stays out of the suggestable vocabulary (reconcile-command
    spec: "revises stays out of the suggestable vocabulary")."""
    assert (
        frozenset({"supersedes", "reconciled_with", "revises"})
        == relations.RESOLUTION_RELATION_TYPES
    )
    assert relations.RESOLUTION_RELATION_TYPES.isdisjoint(
        relations.SEEDED_RELATION_TYPES
    )
    assert relations.RESOLUTION_RELATION_TYPES.isdisjoint(
        relations.SUGGESTABLE_RELATION_TYPES
    )
    assert "revises" not in {rt.name for rt in relations.REGISTRY}
