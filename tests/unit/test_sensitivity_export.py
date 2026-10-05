"""The export boundary predicate (okf-export, #1301, ADR-0048).

`sensitivity.export_boundary` decides what may leave the device in an
`openkos export`: `public` by default, `private` only on the explicit opt-in,
`confidential` and every doubtful label never, and an object labelled below
its sources only on `--allow-below-source`.
"""

import inspect
from collections.abc import Mapping

import pytest

from openkos import sensitivity
from openkos.sensitivity import ExportReason


def _doc(
    sensitivity_value: object = "public",
    *,
    type_: object = "Concept",
    provenance: object = None,
    **extra: object,
) -> dict[str, object]:
    meta: dict[str, object] = {"type": type_}
    if sensitivity_value is not _ABSENT:
        meta["sensitivity"] = sensitivity_value
    if provenance is not None:
        meta["provenance"] = provenance
    meta.update(extra)
    return meta


_ABSENT = object()


def _boundary(
    docs: Mapping[str, Mapping[str, object] | None],
    *,
    include_private: bool = False,
    allow_below_source: bool = False,
) -> sensitivity.ExportBoundary:
    return sensitivity.export_boundary(
        docs, include_private=include_private, allow_below_source=allow_below_source
    )


def test_public_only_by_default() -> None:
    result = _boundary({"a": _doc("public"), "b": _doc("private")})
    assert result.allowed == frozenset({"a"})
    assert result.withheld == {"b": ExportReason.PRIVATE}


def test_private_leaves_on_the_explicit_opt_in() -> None:
    result = _boundary(
        {"a": _doc("public"), "b": _doc("private")}, include_private=True
    )
    assert result.allowed == frozenset({"a", "b"})
    assert result.withheld == {}


@pytest.mark.parametrize("include_private", [False, True])
@pytest.mark.parametrize("allow_below_source", [False, True])
def test_confidential_never_leaves(
    include_private: bool, allow_below_source: bool
) -> None:
    result = _boundary(
        {"c": _doc("confidential")},
        include_private=include_private,
        allow_below_source=allow_below_source,
    )
    assert result.allowed == frozenset()
    assert result.withheld == {"c": ExportReason.CONFIDENTIAL}


@pytest.mark.parametrize(
    "value", [_ABSENT, None, "", "   ", 3, ["public"], "secret", "Public"]
)
def test_a_doubtful_label_is_withheld_under_every_flag(value: object) -> None:
    result = _boundary(
        {"x": _doc(value)}, include_private=True, allow_below_source=True
    )
    assert result.allowed == frozenset()
    assert result.withheld == {"x": ExportReason.UNLABELLED}


def test_a_padded_canonical_label_is_still_ranked() -> None:
    # `okf._rank` strips; a padded `public` is the same label, not doubtful.
    result = _boundary({"x": _doc(" public ")})
    assert result.allowed == frozenset({"x"})


def test_an_unreadable_document_is_withheld() -> None:
    result = _boundary({"x": None}, include_private=True)
    assert result.withheld == {"x": ExportReason.UNREADABLE}


@pytest.mark.parametrize("type_", ["", "  ", None, 7])
def test_a_document_without_type_is_withheld(type_: object) -> None:
    result = _boundary({"x": _doc("public", type_=type_)})
    assert result.withheld == {"x": ExportReason.UNREADABLE}


def test_an_interrupted_ingest_is_withheld() -> None:
    result = _boundary(
        {"sources/s": _doc("public", type_="Source", ingest_pending=True)},
        include_private=True,
    )
    assert result.withheld == {"sources/s": ExportReason.INCOMPLETE}


def test_reasons_are_ordered_unreadable_before_label() -> None:
    # A confidential document that is also mid-ingest reports the earlier
    # reason; the predicate must not fall through to admitting it.
    result = _boundary(
        {"x": _doc("confidential", ingest_pending=True)}, include_private=True
    )
    assert result.withheld == {"x": ExportReason.INCOMPLETE}


# --- below-source (ADR-0048) -------------------------------------------------


def _below_source_bundle() -> dict[str, Mapping[str, object] | None]:
    return {
        "sources/s": _doc("confidential", type_="Source"),
        "concepts/a": _doc("private", provenance=["sources/s"]),
    }


def test_an_object_below_its_source_is_withheld_by_default() -> None:
    result = _boundary(_below_source_bundle(), include_private=True)
    assert "concepts/a" not in result.allowed
    assert result.withheld["concepts/a"] is ExportReason.BELOW_SOURCE
    assert result.below_source == ("concepts/a",)


def test_the_human_downgrade_is_honored_on_the_flag() -> None:
    result = _boundary(
        _below_source_bundle(), include_private=True, allow_below_source=True
    )
    assert result.allowed == frozenset({"concepts/a"})
    assert result.withheld == {"sources/s": ExportReason.CONFIDENTIAL}
    assert result.below_source == ("concepts/a",)


def test_an_equal_label_is_not_below() -> None:
    docs: dict[str, Mapping[str, object] | None] = {
        "sources/s": _doc("private", type_="Source"),
        "concepts/a": _doc("private", provenance=["sources/s"]),
    }
    result = _boundary(docs, include_private=True)
    assert result.allowed == frozenset({"sources/s", "concepts/a"})
    assert result.below_source == ()


def test_the_ancestor_walk_is_transitive() -> None:
    docs: dict[str, Mapping[str, object] | None] = {
        "sources/s": _doc("confidential", type_="Source"),
        "concepts/mid": _doc("confidential", provenance=["/sources/s.md"]),
        "concepts/top": _doc("public", provenance=["concepts/mid"]),
    }
    result = _boundary(docs, include_private=True)
    assert result.withheld["concepts/top"] is ExportReason.BELOW_SOURCE


def test_a_two_hop_downgrade_is_caught_even_when_the_middle_is_low() -> None:
    docs: dict[str, Mapping[str, object] | None] = {
        "sources/s": _doc("confidential", type_="Source"),
        "concepts/mid": _doc("public", provenance=["sources/s"]),
        "concepts/top": _doc("public", provenance=["concepts/mid"]),
    }
    result = _boundary(docs)
    assert result.withheld["concepts/top"] is ExportReason.BELOW_SOURCE
    assert result.withheld["concepts/mid"] is ExportReason.BELOW_SOURCE


@pytest.mark.parametrize("ancestor", [None, _doc(_ABSENT), _doc("weird")])
def test_a_doubtful_ancestor_ranks_confidential(
    ancestor: Mapping[str, object] | None,
) -> None:
    docs: dict[str, Mapping[str, object] | None] = {
        "sources/s": ancestor,
        "concepts/a": _doc("private", provenance=["sources/s"]),
    }
    result = _boundary(docs, include_private=True)
    assert result.withheld["concepts/a"] is ExportReason.BELOW_SOURCE


def test_raw_and_dangling_entries_contribute_nothing() -> None:
    docs: dict[str, Mapping[str, object] | None] = {
        "concepts/a": _doc("public", provenance=["raw/notes.txt", "sources/forgotten"]),
    }
    result = _boundary(docs)
    assert result.allowed == frozenset({"concepts/a"})
    assert result.below_source == ()


@pytest.mark.parametrize("provenance", ["sources/s", [3], [""], {"a": 1}])
def test_malformed_provenance_counts_as_below_source(provenance: object) -> None:
    docs: dict[str, Mapping[str, object] | None] = {
        "concepts/a": _doc("public", provenance=provenance),
    }
    result = _boundary(docs)
    assert result.withheld["concepts/a"] is ExportReason.BELOW_SOURCE


def test_a_provenance_cycle_terminates() -> None:
    docs: dict[str, Mapping[str, object] | None] = {
        "concepts/a": _doc("public", provenance=["concepts/b"]),
        "concepts/b": _doc("public", provenance=["concepts/a"]),
    }
    result = _boundary(docs)
    assert result.allowed == frozenset({"concepts/a", "concepts/b"})


def test_a_withheld_object_is_not_also_listed_below_source() -> None:
    # A private object without the opt-in is withheld for being private; the
    # below-source list names only objects the rule itself decided.
    result = _boundary(_below_source_bundle())
    assert result.withheld["concepts/a"] is ExportReason.PRIVATE
    assert result.below_source == ()


# --- the boundary cannot be widened ------------------------------------------


def test_no_parameter_can_admit_confidential() -> None:
    params = inspect.signature(sensitivity.export_boundary).parameters
    assert list(params) == ["docs", "include_private", "allow_below_source"]
