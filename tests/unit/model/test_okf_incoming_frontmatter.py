"""Unit tests for `model/okf.py`'s incoming-frontmatter seam
(preserve-source-frontmatter, issue #1062, design.md Decisions 1-3):

- `parse_incoming_frontmatter` -- a fail-closed, bounded YAML-only parser
  for a leading `---` block this engine did NOT write (design.md Decision
  1's ordered ten-check table).
- The plain-data domain check and storage round-trip gate (Decision 2).
- `normalize_tags`/`union_tags`/`lift_incoming_frontmatter` (Decision 3),
  landing later in Phase 3 -- not this file's scope yet.

Every hostile fixture asserts its OWN specific status, never just "falsy",
per this module's fail-closed contract: a function that maps every hostile
input to `None` cannot be tested per cause.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import get_args

import pytest

from openkos.model import okf


def _frontmatter_text(block: str) -> str:
    """Wrap `block` in a well-formed leading `---` fence, as it would
    appear at the start of a real ingested file."""
    return f"---\n{block}\n---\n"


# --- `absent`: no usable leading fence at all (task 1.4) -------------------


class TestParseAbsentCases:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("title: x\nno leading fence here\n", id="no_leading_dashes"),
            pytest.param(
                "---\ntitle: x\nno closing fence anywhere\n",
                id="unterminated_leading_dashes",
            ),
            pytest.param("﻿---\ntitle: x\n---\n", id="leading_utf8_bom_before_dashes"),
            pytest.param("+++\ntitle = 'x'\n+++\n", id="leading_toml_fence"),
            pytest.param(';;;\n{"title": "x"}\n;;;\n', id="leading_json_fence"),
        ],
    )
    def test_parse_absent_cases(self, text: str) -> None:
        result = okf.parse_incoming_frontmatter(text)

        assert result == okf.IncomingFrontmatter(status="absent", mapping=None)


# --- `empty`: a real block that resolves to nothing (task 1.5) -------------


class TestParseEmptyCases:
    @pytest.mark.parametrize(
        "block",
        [
            pytest.param("", id="nothing_between_fences"),
            pytest.param("# just a comment", id="comment_only"),
            pytest.param("{}", id="empty_flow_mapping"),
        ],
    )
    def test_parse_empty_cases(self, block: str) -> None:
        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result == okf.IncomingFrontmatter(status="empty", mapping=None)


# --- `too-large`: the byte-size boundary (task 1.6) -------------------------


def _padded_yaml_block(total_bytes: int) -> str:
    """A single-key YAML mapping whose block text is EXACTLY `total_bytes`
    UTF-8 bytes, built entirely from ASCII (1 byte/char) so the arithmetic
    is exact."""
    prefix, suffix = "k: '", "'"
    overhead = len((prefix + suffix).encode("utf-8"))
    padding = total_bytes - overhead
    assert padding >= 0
    block = f"{prefix}{'a' * padding}{suffix}"
    assert len(block.encode("utf-8")) == total_bytes
    return block


class TestParseTooLargeBoundary:
    def test_exact_limit_parses(self) -> None:
        block = _padded_yaml_block(okf.INCOMING_FRONTMATTER_MAX_BYTES)

        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result.status == "parsed"
        assert result.mapping == {"k": "a" * (okf.INCOMING_FRONTMATTER_MAX_BYTES - 5)}

    def test_one_byte_over_limit_is_too_large(self) -> None:
        block = _padded_yaml_block(okf.INCOMING_FRONTMATTER_MAX_BYTES + 1)

        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result == okf.IncomingFrontmatter(status="too-large", mapping=None)


# --- `alias`: anchor/alias expansion guard (task 1.7) -----------------------
#
# Only an ACTUAL alias reference (an `AliasEvent`) is a hazard -- a bare,
# unreferenced anchor is harmless and must parse normally (this narrows
# design.md Decision 1 check #3's "any non-None anchor" wording to the
# case that actually matters: reflexive/amplifying alias REFERENCES, not
# anchor DEFINITIONS on their own).


class TestParseAliasCases:
    def test_billion_laughs_amplification_is_rejected(self) -> None:
        billion_laughs = (
            'a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]\n'
            "b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
            "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\n"
            "d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]\n"
            "e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]\n"
            "f: [*e,*e,*e,*e,*e,*e,*e,*e,*e]"
        )

        result = okf.parse_incoming_frontmatter(_frontmatter_text(billion_laughs))

        assert result == okf.IncomingFrontmatter(status="alias", mapping=None)

    def test_self_referencing_anchor_alias_pair_is_rejected(self) -> None:
        self_reference = "a: &a [*a]"

        result = okf.parse_incoming_frontmatter(_frontmatter_text(self_reference))

        assert result == okf.IncomingFrontmatter(status="alias", mapping=None)

    def test_lone_anchor_with_no_alias_is_not_rejected(self) -> None:
        lone_anchor = "key: &a value"

        result = okf.parse_incoming_frontmatter(_frontmatter_text(lone_anchor))

        assert result.status == "parsed"
        assert result.mapping == {"key": "value"}


# --- `too-deep`: nesting depth boundary (task 1.8) --------------------------


class TestParseTooDeepBoundary:
    def test_exact_max_depth_parses(self) -> None:
        # Root mapping = depth 1; each nested flow-list pair adds 1.
        nesting = okf.INCOMING_FRONTMATTER_MAX_DEPTH - 1
        block = f"k: {'[' * nesting}{']' * nesting}"

        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result.status == "parsed"

    def test_one_level_over_max_depth_is_too_deep(self) -> None:
        nesting = okf.INCOMING_FRONTMATTER_MAX_DEPTH
        block = f"k: {'[' * nesting}{']' * nesting}"

        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result == okf.IncomingFrontmatter(status="too-deep", mapping=None)


# --- `malformed`: broken syntax and refused constructors (task 1.9) --------


class TestParseMalformedCases:
    @pytest.mark.parametrize(
        "block",
        [
            pytest.param("a: 1\n  b: 2\n c: 3", id="bad_yaml_indentation"),
            pytest.param(
                "a: !!python/object/apply:os.system ['echo hi']",
                id="python_object_tag_refused_by_safe_loader",
            ),
            pytest.param("a: !foo bar", id="unknown_custom_tag"),
            pytest.param("a: 1\n...\nb: 2", id="two_yaml_documents_via_ellipsis"),
        ],
    )
    def test_parse_malformed_cases(self, block: str) -> None:
        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result == okf.IncomingFrontmatter(status="malformed", mapping=None)


# --- `not-a-mapping`: a well-formed but non-dict root (task 1.10) ----------


class TestParseNotAMappingCases:
    @pytest.mark.parametrize(
        "block",
        [
            pytest.param("- a\n- b", id="list_root"),
            pytest.param("just a plain scalar string", id="scalar_string_root"),
            pytest.param("42", id="scalar_int_root"),
        ],
    )
    def test_parse_not_a_mapping_cases(self, block: str) -> None:
        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result == okf.IncomingFrontmatter(status="not-a-mapping", mapping=None)


# --- `unsupported-value`: plain-data domain check (task 1.12) --------------
# design.md Decision 2: the WHOLE block fails, never a partial lift, when
# any value anywhere in the mapping falls outside the plain-data domain
# (str, bool, int, finite float, None, date, datetime, list, dict[str, ...]).


class TestParseUnsupportedValueCases:
    @pytest.mark.parametrize(
        "block",
        [
            pytest.param("a: !!binary |\n  aGVsbG8=", id="binary_bytes"),
            pytest.param("a: !!set\n  ? x", id="set_literal"),
            pytest.param("1: a", id="non_str_key_top_level"),
            pytest.param("a:\n  1: b", id="non_str_key_nested"),
            pytest.param("a: .nan", id="nan_float"),
            pytest.param("a: .inf", id="infinite_float"),
        ],
    )
    def test_parse_unsupported_value_cases(self, block: str) -> None:
        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result == okf.IncomingFrontmatter(
            status="unsupported-value", mapping=None
        )


# --- Round-trip gate: ordering vs. the domain check, and what survives -----
# (task 1.13)


class TestParseRoundTripGate:
    def test_nan_is_caught_by_the_domain_check_not_a_separate_status(self) -> None:
        """`.nan` fails the DOMAIN check (1.12) before the round-trip gate
        is even reached -- confirmed by asserting the SAME
        `unsupported-value` status the domain-check tests assert, not some
        other round-trip-specific status."""
        result = okf.parse_incoming_frontmatter(_frontmatter_text("a: .nan"))

        assert result.status == "unsupported-value"

    def test_dates_nested_maps_and_unicode_round_trip_and_parse(self) -> None:
        block = (
            "d: 2026-07-14\n"
            "dt: 2026-07-14 10:00:00\n"
            "nested:\n"
            "  k: v\n"
            "unicode: héllo wörld 日本語"
        )

        result = okf.parse_incoming_frontmatter(_frontmatter_text(block))

        assert result.status == "parsed"
        assert result.mapping == {
            "d": date(2026, 7, 14),
            # naive on purpose: an unquoted `dt:` scalar resolves to a
            # naive `datetime` via PyYAML's implicit timestamp resolver.
            "dt": datetime(2026, 7, 14, 10, 0),  # noqa: DTZ001
            "nested": {"k": "v"},
            "unicode": "héllo wörld 日本語",
        }

    def test_round_trip_gate_rejects_a_value_that_fails_to_survive(self) -> None:
        """A value that fails to round-trip through the real storage path
        must reject as `unsupported-value` -- not merely as a symptom of the
        domain check. Monkeypatch `dump_frontmatter` to corrupt a key so the
        reload can never equal the original mapping, and confirm the gate,
        not just the domain check, is load-bearing (task 1.13 mutation)."""

        def _corrupting_dump(
            metadata: dict[str, object], body: str = "", *, width: int | None = None
        ) -> str:
            corrupted = dict(metadata)
            corrupted[okf.SOURCE_FRONTMATTER_KEY] = {"corrupted": True}
            return real_dump_frontmatter(corrupted, body, width=width)

        real_dump_frontmatter = okf.dump_frontmatter
        okf.dump_frontmatter = _corrupting_dump
        try:
            result = okf.parse_incoming_frontmatter(_frontmatter_text("k: v"))
        finally:
            okf.dump_frontmatter = real_dump_frontmatter

        assert result == okf.IncomingFrontmatter(
            status="unsupported-value", mapping=None
        )


# --- Regression: no exception ever propagates (task 1.15) ------------------


class TestParseNeverRaises:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("---\na: '\udcff'\n---\n", id="lone_surrogate_escape"),
            pytest.param("---\na: 'embedded\x00nul'\n---\n", id="embedded_nul_byte"),
            pytest.param("---\n" + "a" * 200_000 + "\n---\n", id="huge_garbage_block"),
        ],
    )
    def test_parse_never_raises(self, text: str) -> None:
        result = okf.parse_incoming_frontmatter(text)

        assert result.status in get_args(okf.IncomingFrontmatterStatus)
