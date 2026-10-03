"""retire-superseded-sources, task 3.1: the pure helpers that detach a
forgotten Source's GENERATED references from a surviving concept, and remove
a `supersedes` relation from the superseding Source.

Everything here is text-in/text-out and matches only the exact shapes the
engine itself wrote; anything else is left untouched so `forget` still
blocks on it."""

import pytest

from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import relations as bundle_relations
from openkos.model import okf

_GENERATED = okf.Generated(by="openkos-test", at="2026-01-01T00:00:00Z")


def _concept(provenance: list[str], body: str = "Body text.", **extra: object) -> str:
    text = okf.build_concept(
        type="Concept",
        title="Shared",
        description="A shared concept.",
        body=body,
        provenance=provenance,
        sensitivity="private",
        generated=_GENERATED,
    )
    if not extra:
        return text
    metadata, doc_body = okf.load_frontmatter(text)
    metadata.update(extra)
    return okf.dump_frontmatter(metadata, doc_body)


def test_detach_removes_the_provenance_entry_the_sources_entry_and_the_bullet() -> None:
    text = _concept(["sources/v1", "sources/v2"])

    detached = bundle_provenance.detach_generated_source_references(
        text, source_id="sources/v1"
    )

    metadata, body = okf.load_frontmatter(detached)
    assert metadata["provenance"] == ["sources/v2"]
    assert metadata[okf.SOURCES_KEY] == [
        {"id": "sources/v2", "resource": "/sources/v2.md"}
    ]
    assert "sources/v1" not in body
    assert "- [sources/v2](/sources/v2.md) — source this was extracted from" in body


def test_detach_is_exactly_the_concept_built_without_that_source() -> None:
    """Byte-identical to what the builder would have written for the
    remaining provenance: nothing else in the document moves."""
    both = _concept(["sources/v1", "sources/v2"])
    only_v2 = _concept(["sources/v2"])

    detached = bundle_provenance.detach_generated_source_references(
        both, source_id="sources/v1"
    )

    assert detached == only_v2


def test_detach_keeps_sensitivity_and_version_untouched() -> None:
    text = _concept(["sources/v1", "sources/v2"], sensitivity="confidential", version=3)

    detached = bundle_provenance.detach_generated_source_references(
        text, source_id="sources/v1"
    )

    metadata, _ = okf.load_frontmatter(detached)
    assert metadata["sensitivity"] == "confidential"
    assert metadata["version"] == 3


def test_detach_matches_the_dot_md_spelling_of_a_provenance_entry() -> None:
    text = _concept(["sources/v1.md", "sources/v2"])

    detached = bundle_provenance.detach_generated_source_references(
        text, source_id="sources/v1"
    )

    metadata, _ = okf.load_frontmatter(detached)
    assert metadata["provenance"] == ["sources/v2"]


def test_detach_refuses_to_empty_the_provenance() -> None:
    text = _concept(["sources/v1"])

    with pytest.raises(ValueError, match="empty"):
        bundle_provenance.detach_generated_source_references(
            text, source_id="sources/v1"
        )


def test_detach_leaves_a_hand_edited_bullet_in_place() -> None:
    """A bullet whose phrase was changed by hand is not the generated shape:
    it is NOT removed, so the caller's re-scan still sees the link and
    forget keeps blocking on it."""
    text = _concept(["sources/v1", "sources/v2"]).replace(
        "- [sources/v1](/sources/v1.md) — source this was extracted from",
        "- [sources/v1](/sources/v1.md) — my own notes on the original",
    )

    detached = bundle_provenance.detach_generated_source_references(
        text, source_id="sources/v1"
    )

    assert "- [sources/v1](/sources/v1.md) — my own notes on the original" in detached


def test_detach_leaves_a_hand_written_prose_link_in_place() -> None:
    text = _concept(
        ["sources/v1", "sources/v2"], body="See [the original](/sources/v1.md)."
    )

    detached = bundle_provenance.detach_generated_source_references(
        text, source_id="sources/v1"
    )

    assert "See [the original](/sources/v1.md)." in detached


def test_detach_of_an_unnamed_source_is_a_no_op() -> None:
    text = _concept(["sources/v2"])

    assert (
        bundle_provenance.detach_generated_source_references(
            text, source_id="sources/v1"
        )
        == text
    )


def test_remove_relation_drops_one_edge_and_keeps_others() -> None:
    text = okf.dump_frontmatter(
        {
            "type": "Source",
            "title": "V2",
            okf.RELATIONS_KEY: [
                {"target": "sources/v1", "type": "supersedes"},
                {"target": "concepts/x", "type": "references"},
            ],
        },
        "Body.\n",
    )

    result = bundle_relations.remove_relation(
        text, target_id="sources/v1", rel_type="supersedes"
    )

    metadata, body = okf.load_frontmatter(result)
    assert metadata[okf.RELATIONS_KEY] == [
        {"target": "concepts/x", "type": "references"}
    ]
    assert body.strip() == "Body."


def test_remove_last_relation_omits_the_relations_key() -> None:
    text = okf.dump_frontmatter(
        {
            "type": "Source",
            "title": "V2",
            okf.RELATIONS_KEY: [{"target": "sources/v1", "type": "supersedes"}],
        },
        "Body.\n",
    )

    result = bundle_relations.remove_relation(
        text, target_id="sources/v1", rel_type="supersedes"
    )

    metadata, _ = okf.load_frontmatter(result)
    assert okf.RELATIONS_KEY not in metadata


def test_remove_relation_absent_edge_is_a_no_op() -> None:
    text = okf.dump_frontmatter({"type": "Source", "title": "V2"}, "Body.\n")

    assert (
        bundle_relations.remove_relation(
            text, target_id="sources/v1", rel_type="supersedes"
        )
        == text
    )
