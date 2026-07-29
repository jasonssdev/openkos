"""Characterization tests for `bundle.provenance.resolve_source_raises` and
`bundle.provenance.find_unresolvable_provenance` -- pinned BEFORE the
extraction out of `set_sensitivity_cmd`'s inline scan (`main.py:3339-3411`,
design D1/D7/D8). Every assertion here mirrors byte-identical behavior the
extraction must preserve; these are pure `Mapping[str, str]` fixtures, no
filesystem, no CLI."""

from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf


def _doc(*, sensitivity: str | None, provenance: list[str]) -> str:
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Doc",
        "provenance": provenance,
    }
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    return okf.dump_frontmatter(metadata, "body text")


class TestResolveSourceRaises:
    def test_raises_descendants_sorted_by_concept_id(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/zeta.md": _doc(sensitivity="public", provenance=["sources/a"]),
            "concepts/alpha.md": _doc(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        assert [entry.concept_id for entry in raises] == [
            "concepts/alpha",
            "concepts/zeta",
        ]

    def test_content_is_byte_identical_to_dump_frontmatter(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/zeta.md": _doc(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        raise_ = raises[0]
        metadata, body = okf.load_frontmatter(files["concepts/zeta.md"])
        metadata["sensitivity"] = "confidential"
        assert raise_.current == "public"
        assert raise_.new_level == "confidential"
        assert raise_.content == okf.dump_frontmatter(metadata, body)

    def test_source_root_itself_is_excluded(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        assert raises == []

    def test_descendant_at_or_above_target_is_not_staged(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/high.md": _doc(
                sensitivity="confidential", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="private"
        )

        assert raises == []

    def test_dirty_current_is_ranked_fail_closed(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/dirty.md": _doc(
                sensitivity="not-a-level", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="private"
        )

        assert len(raises) == 1
        assert raises[0].current == "not-a-level"
        assert raises[0].new_level == "confidential"

    def test_only_root_provenance_closure_members_are_staged(self) -> None:
        """A concept citing an unrelated Source is never staged (design D6's
        conservative subset rule, unchanged by the extraction)."""
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "sources/b.md": _doc(sensitivity="public", provenance=[]),
            "concepts/only-b.md": _doc(sensitivity="public", provenance=["sources/b"]),
        }

        raises = bundle_provenance.resolve_source_raises(
            files, source_id="sources/a", level="confidential"
        )

        assert raises == []


class TestFindUnresolvableProvenance:
    def test_reports_dangling_entry(self) -> None:
        files = {
            "concepts/a.md": _doc(sensitivity="public", provenance=["sources/missing"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == [("concepts/a", "sources/missing")]

    def test_known_extra_ids_excludes_entry(self) -> None:
        files = {
            "concepts/a.md": _doc(sensitivity="public", provenance=["sources/target"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(
            files, known_extra_ids={"sources/target"}
        )

        assert result == []

    def test_order_is_files_order_then_provenance_list_order_no_dedupe(self) -> None:
        files = {
            "concepts/b.md": _doc(
                sensitivity="public",
                provenance=[
                    "sources/missing-1",
                    "sources/missing-1",
                    "sources/missing-2",
                ],
            ),
            "concepts/a.md": _doc(
                sensitivity="public", provenance=["sources/missing-3"]
            ),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == [
            ("concepts/b", "sources/missing-1"),
            ("concepts/b", "sources/missing-1"),
            ("concepts/b", "sources/missing-2"),
            ("concepts/a", "sources/missing-3"),
        ]

    def test_resource_shaped_entry_is_unresolvable(self) -> None:
        """A raw resource path (e.g. `raw/<resource>`) never normalizes to a
        bundle id, so it always reports as unresolvable (design D6/D8)."""
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=["raw/report.pdf"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == [("sources/a", "raw/report.pdf")]

    def test_resolved_entries_are_silent(self) -> None:
        files = {
            "sources/a.md": _doc(sensitivity="public", provenance=[]),
            "concepts/a.md": _doc(sensitivity="public", provenance=["sources/a"]),
        }

        result = bundle_provenance.find_unresolvable_provenance(files)

        assert result == []
