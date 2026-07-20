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


def test_validate_relation_type_accepts_known_type_silently(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A known seeded type is accepted with no stderr note."""
    result = relations.validate_relation_type("references")

    assert result == "references"
    assert capsys.readouterr().err == ""


def test_validate_relation_type_warns_on_unknown_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown type is accepted (never raises) but WARNs to stderr
    (spec: "Unknown type accepted with WARN to stderr")."""
    result = relations.validate_relation_type("custom_relation")

    assert result == "custom_relation"
    err = capsys.readouterr().err
    assert "custom_relation" in err


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
