"""Section-coverage detection (issue #793) -- an EVAL module, not shipped.

`evals/section_coverage/section_coverage.py` answers, for one source, which
of its headed sections no derived object reproduces a line of. The
measurement refuted it as a shippable signal (its module docstring carries
the table), so it lives under `evals/` and nothing in `src/openkos/` calls
it.

It is executed here anyway, on `test_harness_report.py`'s precedent and for
the same reason: the probe that measured it is only trustworthy if its
machinery is, and a refuted signal whose implementation was never exercised
cannot be told apart from one that never worked. These tests pin the SIGNAL;
whether it separates on real extraction output is the probe's question and
no test here can answer it.

Loaded by path, without putting `evals/` on `sys.path` -- the module is
written to have no import side effects, exactly as `harness_report` is.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "section_coverage" / "section_coverage.py"


_FIXTURES_PATH = _REPO_ROOT / "evals" / "section_coverage" / "section_fixtures.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


coverage = _load("section_coverage", _MODULE_PATH)
section_fixtures = _load("section_fixtures", _FIXTURES_PATH)


class TestSplitSections:
    """Heading-delimited spans, named by their heading line."""

    def test_each_heading_opens_a_section_named_by_its_own_line(self) -> None:
        sections = coverage.split_sections(
            "# Title\nintro line here\n\n## Storage\nstorage line here\n"
        )

        assert [section.heading for section in sections] == ["# Title", "## Storage"]

    def test_a_section_body_excludes_its_own_heading_line(self) -> None:
        sections = coverage.split_sections("## Storage\nstorage line here\n")

        assert "## Storage" not in sections[0].body
        assert "storage line here" in sections[0].body

    def test_body_before_the_first_heading_becomes_the_preamble_section(self) -> None:
        sections = coverage.split_sections(
            "loose opening line here\n\n# Title\ntitled line here\n"
        )

        assert sections[0].heading == coverage.PREAMBLE_HEADING
        assert "loose opening line here" in sections[0].body

    def test_a_source_with_no_heading_at_all_is_one_preamble_section(self) -> None:
        sections = coverage.split_sections("just a flat line of prose here\n")

        assert len(sections) == 1
        assert sections[0].heading == coverage.PREAMBLE_HEADING

    def test_every_heading_level_opens_a_section(self) -> None:
        sections = coverage.split_sections(
            "# One\nbody line for one\n"
            "### Three\nbody line for three\n"
            "###### Six\nbody line for six\n"
        )

        assert [section.heading for section in sections] == [
            "# One",
            "### Three",
            "###### Six",
        ]

    def test_a_heading_marker_mid_line_does_not_open_a_section(self) -> None:
        """Only a line that STARTS with the marker is a heading. Prose that
        mentions one -- a document describing its own structure does it
        constantly -- would otherwise split a paragraph into two sections,
        neither of which a reader could find in the source.

        The marker here is followed by a space, so it satisfies everything
        the pattern asks for EXCEPT its position. A case like `#793`, which
        has no space, is rejected by the shape and would leave the anchor
        untested.
        """
        sections = coverage.split_sections(
            "# Title\nthe run reported that ## Storage produced nothing\n"
        )

        assert [section.heading for section in sections] == ["# Title"]

    def test_up_to_three_spaces_still_opens_a_section(self) -> None:
        """CommonMark's own allowance. A fourth space makes it an indented
        code block -- content, not structure -- and the next test pins that
        side of the boundary."""
        sections = coverage.split_sections("   ## Indented\nbody line of prose here\n")

        assert [section.heading for section in sections] == ["## Indented"]

    def test_four_leading_spaces_is_content_not_a_heading(self) -> None:
        sections = coverage.split_sections(
            "# Title\n    ## NotAHeading\nbody line of prose here\n"
        )

        assert [section.heading for section in sections] == ["# Title"]


class TestUncoveredSections:
    """Which sections no object's text quotes."""

    def test_a_section_whose_line_an_object_quotes_is_covered(self) -> None:
        source = "# Title\nintro line about the platform\n\n## Storage\nit uses MySQL 8 as its datastore\n"

        uncovered = coverage.uncovered_sections(
            ["it uses MySQL 8 as its datastore"], source
        )

        assert "## Storage" not in uncovered

    def test_a_section_no_object_quotes_is_reported(self) -> None:
        source = "# Title\nintro line about the platform\n\n## Storage\nit uses MySQL 8 as its datastore\n"

        uncovered = coverage.uncovered_sections(
            ["intro line about the platform"], source
        )

        assert uncovered == ("## Storage",)

    def test_sections_are_reported_in_source_order(self) -> None:
        source = (
            "# Title\nfirst body line of prose\n"
            "## Second\nsecond body line of prose\n"
            "## Third\nthird body line of prose\n"
        )

        assert coverage.uncovered_sections([], source) == (
            "# Title",
            "## Second",
            "## Third",
        )

    def test_one_object_covering_two_sections_clears_both(self) -> None:
        """The same object can quote lines from more than one section, and
        each of those sections is covered. Attributing an object to exactly
        one section would leave the other falsely reported."""
        source = "## First\nline one of the first part\n## Second\nline two of the second part\n"

        text = "line one of the first part\nline two of the second part"

        assert coverage.uncovered_sections([text], source) == ()

    def test_a_section_with_no_quotable_line_is_never_reported(self) -> None:
        """A heading whose body cannot clear the evidence floor could never
        be covered by any object, so reporting it would be noise that no
        extraction could ever clear -- the same vacuity trap the floor in
        `evidence.py` exists to prevent."""
        source = "# Title\na line long enough to be quoted here\n\n## Notes\nTBD\n"

        assert coverage.uncovered_sections([], source) == ("# Title",)

    def test_a_heading_with_an_empty_body_is_never_reported(self) -> None:
        source = "## Empty\n\n## Filled\na line long enough to be quoted here\n"

        assert coverage.uncovered_sections([], source) == ("## Filled",)

    def test_a_source_whose_every_section_is_unquotable_reports_nothing(self) -> None:
        """Not an error and not a finding: there is nothing this signal can
        say about such a source, and saying every section is uncovered would
        be exactly the vacuous verdict the floor rules out."""
        assert coverage.uncovered_sections([], "## A\nTBD\n\n## B\nnone\n") == ()

    def test_an_object_that_quotes_nothing_covers_nothing(self) -> None:
        """#801's object -- text that reproduces no source line -- is
        precisely the one that must not be credited with covering the
        section it was nominally derived from."""
        source = "## Storage\nit uses MySQL 8 as its primary datastore\n"

        uncovered = coverage.uncovered_sections(
            ["The decision regarding which datastore the platform adopted."], source
        )

        assert uncovered == ("## Storage",)

    def test_the_quoting_test_is_the_shipped_evidence_predicate(self) -> None:
        """Coverage must mean exactly what #801's evidence line means, or a
        reader holding both signals gets two different answers to one
        question. Reverse-direction quoting is the sharpest case: the object
        carries the source line and continues past it."""
        source = "## Ownership\nPriya owns the schema migration plan\n"

        text = "Priya owns the schema migration plan for Project Helios."

        assert coverage.uncovered_sections([text], source) == ()

    def test_an_empty_object_list_leaves_every_quotable_section_reported(self) -> None:
        source = "# Title\na line long enough to be quoted here\n"

        assert coverage.uncovered_sections([], source) == ("# Title",)

    def test_a_blank_object_text_is_not_credited_with_coverage(self) -> None:
        source = "# Title\na line long enough to be quoted here\n"

        assert coverage.uncovered_sections(["", "   "], source) == ("# Title",)


class TestSectionWeights:
    """The per-section text weights the published share is computed from.

    Read through `coverage_report`, never through a heading-keyed mapping:
    headings are not unique, and exposing one was the collision that
    inflated the denominator (see `TestDuplicateHeadings`).
    """

    def test_a_section_is_weighed_by_its_stripped_body(self) -> None:
        report = coverage.coverage_report([], "## A\na body of six plain words\n")

        assert report.checkable_chars == len("a body of six plain words")

    def test_the_heading_line_is_not_counted_as_body(self) -> None:
        """A long heading over a short body must not read as a well-covered
        section: the heading is the name, not the content."""
        report = coverage.coverage_report(
            [],
            "## A heading far longer than the body beneath it\nfour short words here\n",
        )

        assert report.checkable_chars == len("four short words here")

    def test_the_blank_line_after_a_heading_is_not_content(self) -> None:
        report = coverage.coverage_report([], "## A\n\n\nfour plain words here\n")

        assert report.checkable_chars == len("four plain words here")

    def test_an_unquotable_section_enters_neither_total(self) -> None:
        """It could never be covered, so counting it in the denominator
        would make a perfectly extracted source report a non-zero share
        forever."""
        report = coverage.coverage_report(
            [], "## A\nTBD\n## B\na longer body of words\n"
        )

        assert (report.checkable_chars, report.uncovered) == (
            len("a longer body of words"),
            ("## B",),
        )


class TestUncoveredShare:
    def test_the_share_is_the_ratio_of_uncovered_to_checkable(self) -> None:
        report = coverage.CoverageReport(("## A",), 276, 445)

        assert report.uncovered_share == pytest.approx(0.62, abs=0.005)

    def test_nothing_checkable_answers_zero_rather_than_dividing(self) -> None:
        """0.0 means "nothing to say" here, matching what an empty
        `uncovered` means on the same source. It is NOT a claim of full
        coverage, and it must not raise."""
        assert coverage.CoverageReport((), 0, 0).uncovered_share == 0.0

    def test_everything_uncovered_is_one(self) -> None:
        assert coverage.CoverageReport(("## A",), 445, 445).uncovered_share == 1.0


class TestThePublishedNumber:
    """The 62.0% this harness reported to issue #793, recomputed from the
    committed fixture rather than from a stored result.

    The refutation in the README rests on that figure sitting far below the
    98.0%/31.3%/97.6% a real transcript scored. If the arithmetic behind it
    drifts, the published conclusion silently stops following from the code,
    and no other test would notice.
    """

    def test_the_reported_failure_scores_sixty_two_percent(self) -> None:
        helios = section_fixtures.HELIOS_OVERVIEW
        # Every section EXCEPT the two the reported run lost is covered, so
        # the share is exactly what that run would have scored.
        covered = [
            section.body
            for section in coverage.split_sections(helios.text)
            if section.heading not in helios.must_fire
        ]

        report = coverage.coverage_report(covered, helios.text)

        assert report.uncovered == helios.must_fire
        assert (report.uncovered_chars, report.checkable_chars) == (276, 445)
        assert report.uncovered_share == pytest.approx(0.620, abs=0.001)


class TestDuplicateHeadings:
    """Two sections can carry the SAME heading.

    A meeting transcript with a `## Notes` or `## Action Items` block per
    agenda item is the ordinary case, not a corner one. Weighing sections
    through a dict keyed by heading text collapsed those into one entry and
    then summed the survivor once per occurrence, silently inflating the
    denominator of the published share -- 230 where the truth was 135, with
    no crash. The fix is to never key section weights by heading at all.
    """

    _SRC = (
        "## Notes\nshort body line here\n"
        "## Notes\n" + ("a much longer body line here " * 4).strip() + "\n"
    )

    def test_both_sections_survive_the_split(self) -> None:
        sections = coverage.split_sections(self._SRC)

        assert [s.heading for s in sections] == ["## Notes", "## Notes"]

    def test_checkable_chars_counts_each_section_once(self) -> None:
        sections = coverage.split_sections(self._SRC)
        truth = sum(len(s.body.strip()) for s in sections)

        report = coverage.coverage_report([], self._SRC)

        assert report.checkable_chars == truth

    def test_uncovered_chars_counts_each_section_once(self) -> None:
        sections = coverage.split_sections(self._SRC)
        truth = sum(len(s.body.strip()) for s in sections)

        report = coverage.coverage_report([], self._SRC)

        assert report.uncovered_chars == truth

    def test_covering_only_one_of_two_same_named_sections_charges_only_it(
        self,
    ) -> None:
        """The sharpest case: the share must reflect WHICH of the two was
        covered, not merely that the heading appeared somewhere."""
        sections = coverage.split_sections(self._SRC)
        longer = max(sections, key=lambda s: len(s.body.strip()))

        report = coverage.coverage_report([longer.body], self._SRC)

        assert report.uncovered_chars == len("short body line here")

    def test_a_duplicated_heading_is_reported_once_per_uncovered_section(
        self,
    ) -> None:
        report = coverage.coverage_report([], self._SRC)

        assert report.uncovered == ("## Notes", "## Notes")
