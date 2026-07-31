"""Unit tests for `source_title.py`: deriving a Source's title from its
decoded raw content.

Every branch of `derive_source_title`'s single walk and its shared validator
is reachable from a plain string, so this suite needs no filesystem, no
`tmp_path`, and no CLI `runner` -- see the design's "Branch reachability"
note. Cases are grouped to mirror `tasks.md` Phase 1's RED/GREEN pairs.
"""

import pytest

from openkos import source_title

# --- `_frontmatter_end`: bounded leading `---` probe (tasks 1.1/1.2) -------


class TestFrontmatterEnd:
    def test_no_leading_dashes_returns_zero(self) -> None:
        lines = ["# Title", "", "body"]

        assert source_title._frontmatter_end(lines) == 0

    def test_leading_dashes_with_later_closing_dashes_skips_block(self) -> None:
        lines = ["---", "title: x", "---", "# Title"]

        assert source_title._frontmatter_end(lines) == 3

    def test_leading_dashes_with_no_closing_dashes_is_treated_as_content(
        self,
    ) -> None:
        lines = ["---", "not a real frontmatter block"]

        assert source_title._frontmatter_end(lines) == 0


# --- Fence tracking inside the single walk (tasks 1.3/1.4) -----------------


class TestFenceTracking:
    def test_h1_outside_any_fence_is_accepted(self) -> None:
        assert source_title.derive_source_title("# Chapter One") == "Chapter One"

    def test_h1_inside_a_fence_is_ignored_real_h1_after_it_wins(self) -> None:
        raw = "```\n# Not a title\n```\n# Chapter One"

        assert source_title.derive_source_title(raw) == "Chapter One"

    def test_tilde_fence_also_masks_h1(self) -> None:
        raw = "~~~\n# Not a title\n~~~\n# Chapter One"

        assert source_title.derive_source_title(raw) == "Chapter One"

    def test_unclosed_fence_swallows_rest_of_document(self) -> None:
        raw = "```\n# Never seen\nstill inside"

        assert source_title.derive_source_title(raw) is None

    def test_fence_closed_only_by_its_own_marker(self) -> None:
        # A mismatched `~~~` inside a ``` fence does not close it, so the
        # fence stays open until its own marker recurs.
        raw = "```\n# Not a title\n~~~\n```\n# Chapter One"

        assert source_title.derive_source_title(raw) == "Chapter One"


# --- ATX H1 detection and normalization (tasks 1.5/1.6) ---------------------


class TestAtxH1Normalization:
    def test_plain_h1_is_returned(self) -> None:
        assert source_title.derive_source_title("# Introduction to Stoicism") == (
            "Introduction to Stoicism"
        )

    def test_trailing_hash_sequence_is_stripped(self) -> None:
        assert source_title.derive_source_title("# Title #") == "Title"

    def test_grade_a_hash_is_preserved(self) -> None:
        assert source_title.derive_source_title("# Grade A#") == "Grade A#"

    def test_c_hash_vs_f_hash_is_preserved(self) -> None:
        assert source_title.derive_source_title("# C# vs F#") == "C# vs F#"

    def test_whitespace_only_heading_is_rejected(self) -> None:
        assert source_title.derive_source_title("#    ") is None


# --- Rule (b): title-plausible predicate (tasks 1.7/1.8) -------------------


class TestTitlePlausiblePredicate:
    def test_followed_by_blank_line_is_accepted(self) -> None:
        raw = "Call with Maria Salazar\n\nbody"

        assert source_title.derive_source_title(raw) == "Call with Maria Salazar"

    def test_followed_by_eof_is_accepted(self) -> None:
        assert source_title.derive_source_title("Call with Maria Salazar") == (
            "Call with Maria Salazar"
        )

    @pytest.mark.parametrize("terminal", [".", ",", ";", ":"])
    def test_terminal_punctuation_is_rejected(self, terminal: str) -> None:
        raw = f"A plain sentence{terminal}"

        assert source_title.derive_source_title(raw) is None

    @pytest.mark.parametrize("prefix", ["- item", "* item", "> quote", "| cell"])
    def test_block_syntax_prefix_is_rejected(self, prefix: str) -> None:
        assert source_title.derive_source_title(prefix) is None

    def test_wrapped_prose_first_line_with_no_trailing_blank_is_rejected(self) -> None:
        raw = "This paragraph keeps going\non the next physical line"

        assert source_title.derive_source_title(raw) is None


# --- `_FORBIDDEN_IN_TITLE` (tasks 1.9/1.10) --------------------------------


class TestForbiddenCharacters:
    @pytest.mark.parametrize(
        "char",
        [
            "\x00",
            "\x01",
            "\x7f",
            "[",
            "]",
            "(",
            ")",
            "`",
            "*",
            "_",
            "<",
            ">",
            "|",
        ],
    )
    def test_forbidden_member_rejects_the_candidate(self, char: str) -> None:
        raw = f"Title with {char} inside"

        assert source_title.derive_source_title(raw) is None

    @pytest.mark.parametrize(
        "char",
        ["#", "&", '"', "'", ":", "-", "—"],
    )
    def test_permitted_character_is_not_rejected(self, char: str) -> None:
        raw = f"Title with {char} inside"

        assert source_title.derive_source_title(raw) == raw


# --- Length: 120 vs 121 chars post-normalization (tasks 1.11/1.12) ---------


class TestLengthBoundary:
    def test_exactly_120_chars_is_accepted(self) -> None:
        title = "A" * 120

        assert source_title.derive_source_title(title) == title

    def test_121_chars_is_rejected(self) -> None:
        title = "A" * 121

        assert source_title.derive_source_title(title) is None

    def test_length_measured_after_normalization_padding_does_not_count(self) -> None:
        raw = " " * 50 + ("A" * 120) + " " * 50

        assert source_title.derive_source_title(raw) == "A" * 120


# --- CRLF, no-cascade, and edge cases (tasks 1.13/1.14/1.15) ----------------


class TestCrlfAndCascadeAndEdgeCases:
    def test_crlf_source_is_accepted(self) -> None:
        raw = "Call with Maria Salazar\r\n\r\nbody"

        assert source_title.derive_source_title(raw) == "Call with Maria Salazar"

    def test_rejected_h1_returns_none_without_cascading_to_plain_line(self) -> None:
        raw = "# [Draft] Notes\n\nA plausible plain line\n\nbody"

        assert source_title.derive_source_title(raw) is None

    def test_empty_string_returns_none(self) -> None:
        assert source_title.derive_source_title("") is None

    def test_whitespace_only_document_returns_none(self) -> None:
        assert source_title.derive_source_title("   \n\n   ") is None

    def test_no_h1_and_no_plausible_line_returns_none(self) -> None:
        raw = "- a bullet\n> a quote\n"

        assert source_title.derive_source_title(raw) is None
