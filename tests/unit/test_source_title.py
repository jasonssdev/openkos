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


# --- Frontmatter skipping via the PUBLIC API (review finding: `_frontmatter_end`
# had no public-API coverage; these drive real leading `---` content through
# `derive_source_title` itself, not just the private helper) ---------------


class TestFrontmatterViaPublicApi:
    def test_well_formed_frontmatter_block_is_skipped(self) -> None:
        raw = "---\ntitle: Ignored\n---\n# Chapter One"

        assert source_title.derive_source_title(raw) == "Chapter One"

    def test_unclosed_leading_dashes_are_treated_as_ordinary_content(self) -> None:
        # No closing `---` anywhere: the leading line is evaluated as a
        # plain rule (b) candidate and rejected -- it starts with `-`, a
        # block-syntax prefix -- so the result is `None`, not a title.
        raw = "---\nnot a real frontmatter block"

        assert source_title.derive_source_title(raw) is None

    def test_frontmatter_probe_is_fence_blind_by_design(self) -> None:
        # A `---` inside a fenced code block within the leading probe's
        # bounded scan closes the frontmatter early -- matching
        # `python-frontmatter`'s own behavior (module docstring: "a named,
        # accepted inaccuracy, not an oversight"). Here the probe closes at
        # the fence-internal `---`, so the walk resumes mid-fence and the
        # real `# Chapter Two` heading is swallowed as fenced content.
        raw = "---\n```\n---\n```\n# Chapter Two\n"

        assert source_title.derive_source_title(raw) is None


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

    def test_hash_prefix_without_atx_space_is_rejected_as_block_syntax(self) -> None:
        # `##Subheading` (no space after the hashes) does NOT match
        # `_ATX_H1_RE`, so it reaches rule (b) as a plain candidate -- where
        # the `#` member of `_BLOCK_SYNTAX_PREFIXES` rejects it. Deleting
        # that member would let this line through untested.
        assert source_title.derive_source_title("##Subheading") is None

    def test_only_the_first_non_blank_line_is_considered_no_scanning_ahead(
        self,
    ) -> None:
        # The first non-blank candidate line ends in `.` and is rejected.
        # A LATER line ("A plausible line") would pass the predicate on its
        # own, but the walk evaluates only the first candidate and does not
        # scan forward to find one that qualifies.
        raw = "First line fails.\n\nA plausible line\n\nbody"

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


# --- `_FORBIDDEN_IN_TITLE` Unicode extension (review finding: the class was
# ASCII-only, so invisible/direction-altering code points like `U+202E`
# RIGHT-TO-LEFT OVERRIDE survived normalization and reached unescaped
# markdown/terminal sinks -- Trojan-Source-class spoofing, newly reachable
# now that `title` comes from file CONTENT, where before it needed a
# deliberately crafted FILENAME) -------------------------------------------


class TestForbiddenUnicodeControlCharacters:
    @pytest.mark.parametrize(
        "char",
        [
            "​",  # ZERO WIDTH SPACE
            "‌",  # ZERO WIDTH NON-JOINER
            "‍",  # ZERO WIDTH JOINER
            "‎",  # LEFT-TO-RIGHT MARK
            "‏",  # RIGHT-TO-LEFT MARK
        ],
    )
    def test_zero_width_and_directional_marks_are_rejected(self, char: str) -> None:
        raw = f"Title with {char} inside"

        assert source_title.derive_source_title(raw) is None

    @pytest.mark.parametrize(
        "char",
        [
            "‪",  # LEFT-TO-RIGHT EMBEDDING
            "‫",  # RIGHT-TO-LEFT EMBEDDING
            "‬",  # POP DIRECTIONAL FORMATTING
            "‭",  # LEFT-TO-RIGHT OVERRIDE
            "‮",  # RIGHT-TO-LEFT OVERRIDE -- the Trojan-Source vector
        ],
    )
    def test_bidi_embedding_and_override_controls_are_rejected(self, char: str) -> None:
        raw = f"Title with {char} inside"

        assert source_title.derive_source_title(raw) is None

    @pytest.mark.parametrize(
        "char",
        [
            "⁦",  # LEFT-TO-RIGHT ISOLATE
            "⁧",  # RIGHT-TO-LEFT ISOLATE
            "⁨",  # FIRST STRONG ISOLATE
            "⁩",  # POP DIRECTIONAL ISOLATE
        ],
    )
    def test_bidi_isolate_controls_are_rejected(self, char: str) -> None:
        raw = f"Title with {char} inside"

        assert source_title.derive_source_title(raw) is None

    def test_bom_is_rejected(self) -> None:
        raw = "Title with ﻿ inside"

        assert source_title.derive_source_title(raw) is None

    def test_arabic_letter_mark_is_rejected(self) -> None:
        """`U+061C` ARABIC LETTER MARK does the same job as the LEFT-TO-RIGHT
        and RIGHT-TO-LEFT marks already rejected above -- it is an invisible
        directional mark -- so a class that rejects those and not this one is
        inconsistent rather than principled. It sits far from them in the code
        chart, which is exactly why it was missed the first time."""
        raw = "Title with ؜ inside"

        assert source_title.derive_source_title(raw) is None

    @pytest.mark.parametrize(
        "char",
        ["\U000e0000", "\U000e0041", "\U000e007f"],
    )
    def test_unicode_tag_characters_are_rejected(self, char: str) -> None:
        """The `U+E0000`-`U+E007F` Tag block encodes an invisible copy of
        ASCII. A whole readable sentence can be smuggled inside a title that
        renders as clean text, which is why it is a known real-world vector
        for hiding instructions in text bound for an LLM -- and this title IS
        interpolated into the extraction prompt (`extraction/concept.py:189`).
        Parametrized across the block's exact first code point, a middle one,
        and its exact last one, so a mis-typed range endpoint fails here
        rather than shipping. The endpoints must be the literal boundaries:
        a case one code point inside the range cannot detect a range that
        starts or ends one code point short, which is the whole reason this
        test exists."""
        raw = f"Title with {char} inside"

        assert source_title.derive_source_title(raw) is None

    @pytest.mark.parametrize(
        "char",
        ["\u2028", "\u2029"],  # LINE SEPARATOR, PARAGRAPH SEPARATOR
    )
    def test_line_and_paragraph_separators_are_already_stripped_by_normalization(
        self, char: str
    ) -> None:
        # Unlike the members above, `str.isspace()` is `True` for both of
        # these, so step 1's `" ".join(candidate.split())` already destroys
        # them -- exactly the same "reachable vs unreachable" situation
        # `_FORBIDDEN_IN_TITLE`'s docstring already documents for
        # `\x1c-\x1f`. They are still listed in the class as defense in
        # depth (in case the normalization order ever changes), but this
        # test proves the OUTCOME today is "silently collapsed to a single
        # space", not "rejected by the forbidden-character check".
        raw = f"Title with {char} inside"

        assert source_title.derive_source_title(raw) == "Title with inside"

    @pytest.mark.parametrize(
        "text",
        [
            "Título con acentos",
            "深圳笔记本",
            "Notes 📝 for later",
            "Call with Maria — 2026",
        ],
        ids=["accented", "cjk", "emoji", "em-dash"],
    )
    def test_ordinary_non_ascii_text_is_still_not_rejected(self, text: str) -> None:
        assert source_title.derive_source_title(text) == text


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
