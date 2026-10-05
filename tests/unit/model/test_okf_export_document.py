"""Exported frontmatter (okf-export, #1301): the OKF seam's half of
`openkos export` -- what an exported document's frontmatter keeps, filters,
re-projects and strips."""

import pytest

from openkos.model import okf

_ALLOWED = frozenset({"concepts/kept", "sources/kept"})


def _doc(metadata: dict[str, object], body: str = "# Title\n\nBody.\n") -> str:
    return okf.dump_frontmatter(metadata, body)


def _base(**extra: object) -> dict[str, object]:
    meta: dict[str, object] = {
        "type": "Concept",
        "title": "A",
        "sensitivity": "public",
        "status": "stable",
    }
    meta.update(extra)
    return meta


# --- export_frontmatter -------------------------------------------------------


def test_a_relation_into_a_withheld_object_is_removed() -> None:
    meta = _base(
        relations=[
            {"target": "concepts/kept", "type": "related_to"},
            {"target": "concepts/gone", "type": "related_to"},
        ]
    )
    result = okf.export_frontmatter(meta, _ALLOWED)
    assert result[okf.RELATIONS_KEY] == [
        {"target": "concepts/kept", "type": "related_to"}
    ]


def test_relations_key_is_removed_when_none_remain() -> None:
    meta = _base(relations=[{"target": "concepts/gone", "type": "supersedes"}])
    assert okf.RELATIONS_KEY not in okf.export_frontmatter(meta, _ALLOWED)


def test_malformed_relations_are_dropped_whole() -> None:
    # Fail closed: a list that cannot be decoded cannot be filtered entry by
    # entry, and may hold a withheld target.
    meta = _base(relations="concepts/gone")
    assert okf.RELATIONS_KEY not in okf.export_frontmatter(meta, _ALLOWED)


def test_untouched_relations_keep_their_original_value() -> None:
    original = [{"target": "concepts/kept", "type": "related_to"}]
    meta = _base(relations=original)
    assert okf.export_frontmatter(meta, _ALLOWED)[okf.RELATIONS_KEY] is original


def test_provenance_is_filtered_and_sources_reprojected() -> None:
    meta = _base(
        provenance=["sources/kept", "/sources/gone.md", "raw/notes.txt"],
        sources=[
            {"id": "sources/kept", "resource": "/sources/kept.md"},
            {"id": "sources/gone", "resource": "/sources/gone.md"},
        ],
    )
    result = okf.export_frontmatter(meta, _ALLOWED)
    assert result["provenance"] == ["sources/kept", "raw/notes.txt"]
    assert result[okf.SOURCES_KEY] == okf.project_sources(
        ["sources/kept", "raw/notes.txt"]
    )


def test_a_hand_edited_sources_entry_does_not_survive() -> None:
    # `sources` is a projection: export recomputes it, never trusts it.
    meta = _base(
        provenance=["sources/kept"],
        sources=[
            {"id": "sources/kept", "resource": "/sources/kept.md"},
            {"id": "sources/gone", "resource": "/sources/gone.md"},
        ],
    )
    result = okf.export_frontmatter(meta, _ALLOWED)
    assert result[okf.SOURCES_KEY] == [
        {"id": "sources/kept", "resource": "/sources/kept.md"}
    ]


def test_provenance_and_sources_are_removed_when_nothing_remains() -> None:
    meta = _base(
        provenance=["sources/gone"],
        sources=[{"id": "sources/gone", "resource": "/sources/gone.md"}],
    )
    result = okf.export_frontmatter(meta, _ALLOWED)
    assert "provenance" not in result
    assert okf.SOURCES_KEY not in result


@pytest.mark.parametrize("provenance", ["sources/gone", [3, "sources/kept"]])
def test_malformed_provenance_keeps_only_safe_entries(provenance: object) -> None:
    result = okf.export_frontmatter(_base(provenance=provenance), _ALLOWED)
    kept = result.get("provenance")
    assert kept in (None, ["sources/kept"])


def test_no_sources_key_is_invented() -> None:
    result = okf.export_frontmatter(_base(provenance=["sources/kept"]), _ALLOWED)
    assert okf.SOURCES_KEY not in result


@pytest.mark.parametrize("key", [okf.ORIGIN_KEY_KEY, okf.MERGED_FROM_KEY])
def test_machine_local_and_ledger_keys_are_stripped(key: str) -> None:
    meta = _base(**{key: "anything"})
    assert key not in okf.export_frontmatter(meta, _ALLOWED)


def test_every_other_key_is_kept_unchanged() -> None:
    meta = _base(
        freshness="timeless",
        generated={"at": "2026-07-05T20:00:00Z", "by": "openkos/legacy"},
        version=2,
        tags=["a"],
        resource="raw/notes.txt",
        source_frontmatter={"author": "me"},
        custom_extension={"x": [1, 2]},
    )
    assert okf.export_frontmatter(meta, _ALLOWED) == meta


def test_the_input_mapping_is_never_mutated() -> None:
    meta = _base(origin_key="abc", provenance=["sources/gone"])
    snapshot = dict(meta)
    okf.export_frontmatter(meta, _ALLOWED)
    assert meta == snapshot


# --- export_document ----------------------------------------------------------


def test_an_untouched_document_is_returned_as_the_same_object() -> None:
    text = _doc(_base(provenance=["sources/kept"]))
    result = okf.export_document(
        text, allowed=_ALLOWED, superseded=False, walk_complete=True
    )
    assert result.text is text
    assert not result.status_projected


def test_a_changed_document_keeps_its_body() -> None:
    text = _doc(_base(origin_key="abc"), body="# A\n\nSee [x](/concepts/kept.md).\n")
    result = okf.export_document(
        text, allowed=_ALLOWED, superseded=False, walk_complete=True
    )
    meta, body = okf.load_frontmatter(result.text)
    assert okf.ORIGIN_KEY_KEY not in meta
    assert body == okf.load_frontmatter(text)[1]


def test_a_superseded_concept_is_exported_deprecated_in_memory() -> None:
    text = _doc(_base())
    result = okf.export_document(
        text, allowed=_ALLOWED, superseded=True, walk_complete=True
    )
    meta, _ = okf.load_frontmatter(result.text)
    assert meta["status"] == "deprecated"
    assert okf.has_valid_export_marker(meta)
    assert result.status_projected


def test_a_stale_export_is_withdrawn_on_a_complete_walk() -> None:
    text = _doc(_base(status="deprecated", status_derived_from="supersedes"))
    result = okf.export_document(
        text, allowed=_ALLOWED, superseded=False, walk_complete=True
    )
    meta, _ = okf.load_frontmatter(result.text)
    assert meta["status"] == "stable"
    assert result.status_projected


def test_an_incomplete_walk_never_withdraws() -> None:
    text = _doc(_base(status="deprecated", status_derived_from="supersedes"))
    result = okf.export_document(
        text, allowed=_ALLOWED, superseded=False, walk_complete=False
    )
    assert result.text is text
    assert not result.status_projected


def test_a_blocked_draft_is_not_rewritten() -> None:
    text = _doc(_base(status="draft"))
    result = okf.export_document(
        text, allowed=_ALLOWED, superseded=True, walk_complete=True
    )
    assert result.text is text
