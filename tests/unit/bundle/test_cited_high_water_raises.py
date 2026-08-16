"""RED-first tests for `bundle.provenance.resolve_cited_high_water_raises` --
the pure fixpoint that maintains ADR-0003's high-water mark for a concept
whose provenance spans MORE THAN ONE Source (issue #697).

ADR-0012 deferred exactly this case: a concept in no single Source's closure
was reported by `lint` and never repaired by the sweep. The mark is applied at
birth (`cli/main.py::_stage_filed_answer` folds `combine_sensitivity` over each
CITED concept) and was never maintained afterwards, so raising one cited Source
left every multi-source insight below its own inputs.

These are pure `Mapping[str, str]` fixtures: no filesystem, no CLI. They MUST
NOT call `okf._rank` (ADR-0003 keeps it private) -- fail-closed ranking is
asserted through `okf.combine_sensitivity`'s observable behavior instead.
"""

from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf


def _source(*, sensitivity: str | None, provenance: list[str] | None = None) -> str:
    metadata: dict[str, object] = {
        "type": "Source",
        "title": "Source",
        "provenance": provenance if provenance is not None else ["raw/notes.txt"],
    }
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    return okf.dump_frontmatter(metadata, "body text")


def _concept(
    *, sensitivity: object, provenance: list[str], type: str = "Concept"
) -> str:
    """`sensitivity` is typed `object`, not `str | None`, so a test can plant
    the DIRTY frontmatter value (`42`, a list) that `_levels_by_id` keeps raw;
    `None` still means "omit the key entirely"."""
    metadata: dict[str, object] = {
        "type": type,
        "title": "Doc",
        "provenance": provenance,
    }
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    return okf.dump_frontmatter(metadata, "body text")


def _sensitivity_of(content: str) -> object:
    metadata, _ = okf.load_frontmatter(content)
    return metadata.get("sensitivity")


class TestResolveCitedHighWaterRaises:
    def test_multi_source_insight_is_raised_to_its_cited_high_water_mark(self) -> None:
        """The exact shape #697 reports: an insight born `private` while every
        citation was `private`, with one cited Source raised afterwards."""
        files = {
            "sources/transcription1.md": _source(sensitivity="confidential"),
            "sources/transcription2.md": _source(sensitivity="private"),
            "insights/relacion.md": _concept(
                sensitivity="private",
                provenance=["sources/transcription1", "sources/transcription2"],
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert [entry.concept_id for entry in raises] == ["insights/relacion"]
        assert raises[0].current == "private"
        assert raises[0].new_level == "confidential"
        assert _sensitivity_of(raises[0].content) == "confidential"

    def test_single_source_descendant_is_covered_too(self) -> None:
        """The fixpoint reads DIRECT provenance, so it also reaches what the
        per-Source closure sweep already covers. Both producers staging the
        same raise is harmless -- `resolve_backfill_raises` merges by max."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "concepts/one.md": _concept(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert [entry.concept_id for entry in raises] == ["concepts/one"]
        assert raises[0].new_level == "confidential"

    def test_a_raise_propagates_transitively_through_a_chain(self) -> None:
        """THE FIXPOINT, in ADVERSARIAL ORDER.

        `insights/top` cites `concepts/mid`, which cites the raised Source.
        The insertion order below is deliberate and load-bearing: the walk
        iterates `files` order, so listing the DEPENDENT (`top`) BEFORE its
        dependency (`mid`) means a single pass reads `mid` at its stale level,
        raises only `mid`, and leaves `top` behind -- the same maintenance gap
        one level up. Listed the other way round, one pass happens to suffice
        and the test proves nothing; mutating `changed = True` to `False`
        confirmed exactly that, so the order is the assertion.
        """
        files = {
            "insights/top.md": _concept(
                sensitivity="public", provenance=["concepts/mid", "sources/b"]
            ),
            "concepts/mid.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/b"]
            ),
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="public"),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        by_id = {entry.concept_id: entry for entry in raises}
        assert set(by_id) == {"concepts/mid", "insights/top"}
        assert by_id["concepts/mid"].new_level == "confidential"
        assert by_id["insights/top"].new_level == "confidential"

    def test_a_three_link_chain_converges_from_the_worst_order(self) -> None:
        """Deepest-first insertion, so a single pass repairs exactly one link
        of a three-link chain. Guards the loop against being replaced by any
        fixed number of passes, not just one."""
        files = {
            "insights/c.md": _concept(
                sensitivity="public", provenance=["insights/b", "sources/pub"]
            ),
            "insights/b.md": _concept(
                sensitivity="public", provenance=["insights/a", "sources/pub"]
            ),
            "insights/a.md": _concept(
                sensitivity="public", provenance=["sources/secret", "sources/pub"]
            ),
            "sources/secret.md": _source(sensitivity="confidential"),
            "sources/pub.md": _source(sensitivity="public"),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert [entry.concept_id for entry in raises] == [
            "insights/a",
            "insights/b",
            "insights/c",
        ]
        assert {entry.new_level for entry in raises} == {"confidential"}

    def test_already_at_or_above_the_mark_is_never_staged(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="private"),
            "sources/b.md": _source(sensitivity="public"),
            "insights/high.md": _concept(
                sensitivity="confidential", provenance=["sources/a", "sources/b"]
            ),
        }

        assert bundle_provenance.resolve_cited_high_water_raises(files) == []

    def test_never_lowers_a_concept_above_its_citations(self) -> None:
        """Raise-only by construction: a concept deliberately classified above
        everything it cites keeps its level."""
        files = {
            "sources/a.md": _source(sensitivity="public"),
            "sources/b.md": _source(sensitivity="public"),
            "insights/manual.md": _concept(
                sensitivity="confidential", provenance=["sources/a", "sources/b"]
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert raises == []

    def test_a_source_is_never_staged_by_this_sweep(self) -> None:
        """A Source's level is operator-set truth, never derived from what it
        cites. A Source citing a confidential concept is left alone even though
        the fold would otherwise raise it."""
        files = {
            "concepts/secret.md": _concept(sensitivity="confidential", provenance=[]),
            "sources/a.md": _source(
                sensitivity="public", provenance=["concepts/secret"]
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert [entry.concept_id for entry in raises] == []

    def test_an_unresolvable_citation_leaves_the_concept_unstaged(self) -> None:
        """Matches `lint`'s multi-source rule and ADR-0012's design D8: a
        dangling provenance id is `check_dangling_provenance`'s signal, and
        raising a whole bundle off one dangling ref would be a blast radius no
        preview makes safe."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "insights/partial.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/ghost"]
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert raises == []

    def test_a_concept_with_no_provenance_is_never_staged(self) -> None:
        files = {
            "concepts/orphan.md": _concept(sensitivity="public", provenance=[]),
        }

        assert bundle_provenance.resolve_cited_high_water_raises(files) == []

    def test_a_missing_sensitivity_is_ranked_fail_closed_not_skipped(self) -> None:
        """ADR-0003: an absent `sensitivity` floors at `private`, so a concept
        with no level at all is still raised toward a confidential citation."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="public"),
            "insights/blank.md": _concept(
                sensitivity=None, provenance=["sources/a", "sources/b"]
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert [entry.concept_id for entry in raises] == ["insights/blank"]
        assert raises[0].new_level == "confidential"

    def test_a_present_but_dirty_sensitivity_fails_closed_and_propagates(self) -> None:
        """The companion to the MISSING-value case above: a cited concept whose
        `sensitivity` is present but DIRTY (`42`, from hand-edited frontmatter).
        ADR-0003 floors any non-string at `confidential`, so the dirty concept
        is raised to it AND carries that level to everything citing it.

        THE LAST ASSERTION IS THE LOAD-BEARING ONE, and it is not the one #736
        asked for. `_levels_by_id`'s docstring justifies keeping the value raw
        by claiming a `str` coercion "would turn a dirty `int` into a string
        that no longer fails closed the same way". Measured against every value
        YAML frontmatter can produce (`int`, `bool`, `list`, `dict`, `float`,
        `date`, blank and unrecognized strings), that claim is FALSE: `_rank`
        floors an unrecognized STRING at `confidential` too, so `42` and `"42"`
        rank identically and a `new_level` assertion alone cannot fail under
        the coercion it exists to forbid.

        What the coercion would really destroy is REPORTING: `DescendantRaise.
        current` is the value a preview shows the operator as what is on disk,
        and coercing would show `'42'` where the file holds `42`. Pinning the
        raw type is therefore the assertion that actually goes red."""
        files = {
            "sources/a.md": _source(sensitivity="public"),
            "concepts/dirty.md": _concept(sensitivity=42, provenance=["sources/a"]),
            "insights/citing.md": _concept(
                sensitivity="public", provenance=["concepts/dirty"]
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert [entry.concept_id for entry in raises] == [
            "concepts/dirty",
            "insights/citing",
        ]
        assert [entry.new_level for entry in raises] == ["confidential"] * 2
        assert _sensitivity_of(raises[1].content) == "confidential"
        assert raises[0].current == 42
        assert isinstance(raises[0].current, int)

    def test_a_provenance_cycle_terminates(self) -> None:
        """Two concepts citing each other. The fold is monotone and bounded by
        the id universe, so the fixpoint halts instead of spinning."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "concepts/x.md": _concept(
                sensitivity="public", provenance=["concepts/y", "sources/a"]
            ),
            "concepts/y.md": _concept(
                sensitivity="public", provenance=["concepts/x", "sources/a"]
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert {entry.concept_id for entry in raises} == {"concepts/x", "concepts/y"}
        assert {entry.new_level for entry in raises} == {"confidential"}

    def test_results_are_sorted_by_concept_id(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="public"),
            "insights/zeta.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/b"]
            ),
            "insights/alpha.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/b"]
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        assert [entry.concept_id for entry in raises] == [
            "insights/alpha",
            "insights/zeta",
        ]

    def test_idempotent_second_pass_stages_nothing(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="public"),
            "insights/one.md": _concept(
                sensitivity="public", provenance=["sources/a", "sources/b"]
            ),
        }
        first_pass = bundle_provenance.resolve_cited_high_water_raises(files)
        assert len(first_pass) == 1

        raised = dict(files)
        raised["insights/one.md"] = first_pass[0].content

        assert bundle_provenance.resolve_cited_high_water_raises(raised) == []

    def test_content_preserves_every_other_frontmatter_field(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="public"),
            "insights/one.md": okf.dump_frontmatter(
                {
                    "type": "Insight",
                    "title": "Kept",
                    "description": "kept too",
                    "tags": ["x"],
                    "sensitivity": "public",
                    "provenance": ["sources/a", "sources/b"],
                },
                "# Kept\n\nbody\n",
            ),
        }

        raises = bundle_provenance.resolve_cited_high_water_raises(files)

        metadata, body = okf.load_frontmatter(raises[0].content)
        assert metadata["sensitivity"] == "confidential"
        assert metadata["title"] == "Kept"
        assert metadata["description"] == "kept too"
        assert metadata["tags"] == ["x"]
        # Compared against the INPUT's own round-trip, not a literal: the
        # load/dump pair normalizes the body's trailing newline, and pinning
        # the literal here would assert `okf`'s formatting rather than this
        # sweep's preservation of it (`resolve_source_raises` does the same).
        _, original_body = okf.load_frontmatter(files["insights/one.md"])
        assert body == original_body


class TestBackfillSweepFoldsInTheCitedHighWaterMark:
    def test_the_bundle_wide_sweep_now_reaches_a_multi_source_concept(self) -> None:
        """#697: the whole point. Before this change the per-Source closure
        rule excluded it and `resolve_backfill_raises` returned nothing."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="private"),
            "insights/spanning.md": _concept(
                sensitivity="private", provenance=["sources/a", "sources/b"]
            ),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == ["insights/spanning"]
        assert raises[0].new_level == "confidential"

    def test_both_producers_agree_and_merge_by_max(self) -> None:
        """A single-Source descendant is staged by BOTH the closure sweep and
        the cited fold. The merge keeps one record at the highest level."""
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "concepts/one.md": _concept(sensitivity="public", provenance=["sources/a"]),
        }

        raises = bundle_provenance.resolve_backfill_raises(files)

        assert [entry.concept_id for entry in raises] == ["concepts/one"]
        assert raises[0].new_level == "confidential"

    def test_sweep_stays_idempotent_after_the_fold(self) -> None:
        files = {
            "sources/a.md": _source(sensitivity="confidential"),
            "sources/b.md": _source(sensitivity="private"),
            "insights/spanning.md": _concept(
                sensitivity="private", provenance=["sources/a", "sources/b"]
            ),
        }
        first_pass = bundle_provenance.resolve_backfill_raises(files)
        raised = dict(files)
        for entry in first_pass:
            raised[f"{entry.concept_id}.md"] = entry.content

        assert bundle_provenance.resolve_backfill_raises(raised) == []
