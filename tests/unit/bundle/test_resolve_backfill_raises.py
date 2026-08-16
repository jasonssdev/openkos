"""RED-first tests for `bundle.provenance.resolve_backfill_raises` -- the
pure, bundle-wide sweep core `backfill-sensitivity` needs (design D4/D5/D6;
PR3a scope: the sweep core only, no Typer command, no I/O). These are pure
`Mapping[str, str]` fixtures, no filesystem, no CLI, and MUST NOT call
`okf._rank` or `bundle.provenance.find_unresolvable_provenance` (design D8:
that signal belongs to `lint`'s `dangling` finding, never this sweep)."""

from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf


def _source(*, sensitivity: str | None, provenance: list[str] | None = None) -> str:
    metadata: dict[str, object] = {
        "type": "Source",
        "title": "Source",
        "provenance": provenance or [],
    }
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    return okf.dump_frontmatter(metadata, "body text")


def _source_with_status(*, sensitivity: str, extraction_status: str) -> str:
    metadata: dict[str, object] = {
        "type": "Source",
        "title": "Source",
        "sensitivity": sensitivity,
        "provenance": [],
        "extraction_status": extraction_status,
    }
    return okf.dump_frontmatter(metadata, "body text")


def _concept(*, sensitivity: str | None, provenance: list[str]) -> str:
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Doc",
        "provenance": provenance,
    }
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    return okf.dump_frontmatter(metadata, "body text")


class TestResolveBackfillRaises:
    def test_raises_every_descendant_below_its_source(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "concepts/zeta.md": _concept(
                sensitivity="public", provenance=["sources/a"]
            ),
            "concepts/alpha.md": _concept(
                sensitivity="public", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == [
            "concepts/alpha",
            "concepts/zeta",
        ]
        assert {entry.new_level for entry in raises} == {"confidential"}

    def test_descendant_already_at_or_above_is_never_lowered_or_touched(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="private"),
            "concepts/high.md": _concept(
                sensitivity="confidential", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert raises == []

    def test_idempotent_second_sweep_stages_nothing(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "concepts/zeta.md": _concept(
                sensitivity="public", provenance=["sources/a"]
            ),
        }
        first_pass = bundle_provenance.resolve_backfill_raises(files)
        assert len(first_pass) == 1

        raised_files = dict(files)
        raised_files["concepts/zeta.md"] = first_pass[0].content

        second_pass = bundle_provenance.resolve_backfill_raises(raised_files)

        assert second_pass == []

    def test_source_never_written_as_its_own_root(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert raises == []

    def test_failed_extraction_status_source_still_a_valid_root(self) -> None:
        files = {
            "sources/a.md": _source_with_status(
                sensitivity="confidential", extraction_status="failed"
            ),
            "concepts/zeta.md": _concept(
                sensitivity="public", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == ["concepts/zeta"]
        assert raises[0].new_level == "confidential"

    def test_source_that_is_a_descendant_of_another_source_is_raised(self) -> None:
        """D6: Source `B` cites higher-sensitivity Source `A` in its own
        `provenance`, making `B` a genuine member of `A`'s closure -- `B` is
        raised like any other descendant, `A` is untouched."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == ["sources/b"]
        assert raises[0].new_level == "confidential"

    def test_descendant_citing_two_ids_inside_same_source_closure_is_raised(
        self,
    ) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "concepts/mid.md": _concept(sensitivity="public", provenance=["sources/a"]),
            "concepts/leaf.md": _concept(
                sensitivity="public", provenance=["sources/a", "concepts/mid"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == [
            "concepts/leaf",
            "concepts/mid",
        ]

    def test_descendant_citing_two_unrelated_sources_is_now_raised(self) -> None:
        """BEHAVIOR CHANGED by issue #697; this test previously asserted
        `raises == []`.

        ADR-0012 deferred the multi-Source case ("stays reported, not
        resolved") and `lint` reported it as `multi-source-uncovered`. The
        sweep now folds in `resolve_cited_high_water_raises`, so the concept
        this closure rule cannot reach IS repaired -- see ADR-0016. The
        per-Source closure half is unchanged and still exercised by every
        other test in this class.
        """
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="confidential"),
            "concepts/both.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/b"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == ["concepts/both"]
        assert raises[0].new_level == "confidential"

    def test_the_closure_half_still_excludes_a_multi_source_descendant(self) -> None:
        """The per-Source closure producer's own semantics are UNCHANGED by
        #697 -- only the sweep that composes it gained a second producer. Kept
        as a separate guard so a future edit cannot loosen
        `provenance_closure`'s conservative subset rule and have the change
        hide behind the fold above."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="confidential"),
            "concepts/both.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/b"]
            ),
        }

        assert (
            bundle_provenance.resolve_source_raises(
                files, source_id="sources/a", level="confidential"
            )
            == []
        )

    def test_merge_by_max_never_via_rank(self) -> None:
        """Two Sources both claim the same descendant via nested closures
        (`sources/a` at `confidential`, `sources/b` at `private`, `b` inside
        `a`'s closure, `shared` cites both); the merged raise picks the
        higher-ranked `new_level` (`confidential`), keyed only by
        `okf.SENSITIVITY_ORDER`, never private `okf._rank`."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="private", provenance=["sources/a"]),
            "concepts/shared.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/b"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        by_id = {entry.concept_id: entry for entry in raises}
        assert by_id["concepts/shared"].new_level == "confidential"
        assert by_id["sources/b"].new_level == "confidential"

    def test_result_is_sorted_by_concept_id(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "concepts/zeta.md": _concept(
                sensitivity="public", provenance=["sources/a"]
            ),
            "concepts/alpha.md": _concept(
                sensitivity="public", provenance=["sources/a"]
            ),
            "concepts/mu.md": _concept(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == sorted(
            entry.concept_id for entry in raises
        )
        assert [entry.concept_id for entry in raises] == [
            "concepts/alpha",
            "concepts/mu",
            "concepts/zeta",
        ]

    def test_source_with_missing_sensitivity_ranks_fail_closed_as_private(
        self,
    ) -> None:
        """A Source's own `sensitivity` is normally always present
        (`okf.build_source_concept` requires it), but a hand-edited bundle
        could still lack it; `_rank(None)` floors at `private`
        (ADR-0003), so a descendant already at `private` is untouched."""
        files = {
            "sources/a.md": _source(sensitivity=None),
            "concepts/zeta.md": _concept(
                sensitivity="private", provenance=["sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert raises == []
