"""The imports layout leaf (okf-import, slice 4, design D2): the namespace
rule, the ids derived from it and the imported-concept predicate."""

import pytest

from openkos.bundle import imports


@pytest.mark.parametrize("namespace", ["acme", "a-b", "a1", "1a", "a" * 64])
def test_a_valid_namespace_has_no_reason(namespace: str) -> None:
    assert imports.namespace_reason(namespace) is None


def test_an_empty_namespace_is_refused() -> None:
    reason = imports.namespace_reason("")
    assert reason is not None
    assert "empty" in reason


@pytest.mark.parametrize("namespace", ["a/b", "..", ".", "a b", "café", "a_b", "a.b"])
def test_a_namespace_with_a_character_outside_the_slug_alphabet_is_refused(
    namespace: str,
) -> None:
    reason = imports.namespace_reason(namespace)
    assert reason is not None
    assert "lowercase ASCII letters, digits and single hyphens" in reason


def test_an_uppercase_namespace_is_refused() -> None:
    reason = imports.namespace_reason("Acme")
    assert reason is not None
    assert "lowercase" in reason


def test_a_double_hyphen_is_refused_and_named() -> None:
    reason = imports.namespace_reason("a--b")
    assert reason is not None
    assert "--" in reason


@pytest.mark.parametrize("namespace", ["-a", "a-"])
def test_a_leading_or_trailing_hyphen_is_refused_and_named(namespace: str) -> None:
    reason = imports.namespace_reason(namespace)
    assert reason is not None
    assert "start or end with a hyphen" in reason


def test_a_namespace_of_65_characters_is_refused_and_names_the_limit() -> None:
    reason = imports.namespace_reason("a" * 65)
    assert reason is not None
    assert "64" in reason


def test_the_prefix_and_the_anchor_id_are_derived_from_the_namespace() -> None:
    assert imports.namespace_prefix("acme") == "imports/acme"
    assert imports.anchor_id("acme", "private") == "imports/acme--private"


def test_the_staging_prefix_is_a_dot_directory_of_the_namespace() -> None:
    assert imports.staging_name_prefix("acme") == ".acme.openkos-import-"


@pytest.mark.parametrize(
    "concept_id", ["imports/x/y", "imports/x", "imports/x--private"]
)
def test_a_concept_under_imports_is_imported(concept_id: str) -> None:
    assert imports.is_imported_concept(concept_id) is True


@pytest.mark.parametrize(
    "concept_id",
    ["concepts/imports/x", "imports-x/y", "import/x", "", "imports", "Imports/x"],
)
def test_only_the_first_segment_makes_a_concept_imported(concept_id: str) -> None:
    assert imports.is_imported_concept(concept_id) is False


def test_two_valid_namespaces_never_collide_on_an_anchor() -> None:
    """`--` cannot occur in a slug, so `<a>--<label>` is never `<b>--<label>`
    and never another namespace's directory."""
    namespaces = ["a", "a-b", "b", "a-private", "private"]
    assert all(imports.namespace_reason(ns) is None for ns in namespaces)
    labels = ["public", "private", "confidential"]
    anchors = [imports.anchor_id(ns, label) for ns in namespaces for label in labels]
    directories = [imports.namespace_prefix(ns) for ns in namespaces]
    assert len(set(anchors)) == len(anchors)
    assert not set(anchors) & set(directories)
