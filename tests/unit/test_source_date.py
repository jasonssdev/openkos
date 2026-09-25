"""Unit tests for `source_date.py`: deriving a Source's `event_date` from
user-controlled evidence (issue #1014c, ADR-0023, design.md Decision 1).

Both functions are PURE and never raise -- see the module docstring for the
full contract. Cases mirror `tasks.md` Slice 1's RED/GREEN pairs and
design.md's two parser tables.
"""

from datetime import date

import pytest

from openkos import source_date

# --- `parse_event_date`: the `--event-date` flag parser (task 1.1) --------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-07-14", date(2026, 7, 14)),
        # Calendar-invalid: shape matches, but February has no 30th.
        ("2026-02-30", None),
        # `date.fromisoformat` accepts ISO-8601 Basic format since Python
        # 3.11, but the required `[0-9]{4}-[0-9]{2}-[0-9]{2}` prefilter
        # rejects it -- there is no hyphen to match.
        ("20260714", None),
        # `date.fromisoformat` also accepts ISO-8601 week dates since
        # Python 3.11; the prefilter rejects the `W` and the wrong shape.
        ("2026-W28-2", None),
        # A leading space fails `fullmatch`.
        (" 2026-07-14", None),
        ("", None),
    ],
)
def test_parse_event_date(text: str, expected: date | None) -> None:
    assert source_date.parse_event_date(text) == expected


# --- `event_date_from_name`: file-name inference (task 1.2) ---------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("call-2026-07-14.txt", date(2026, 7, 14)),
        ("2026-07-14.md", date(2026, 7, 14)),
        # The date sits in the suffix -- this is why the parser reads the
        # whole name, not the stem.
        ("notes.2026-07-14", date(2026, 7, 14)),
        # The same date twice is one distinct date, not an ambiguity.
        ("a-2026-07-14_b-2026-07-14.txt", date(2026, 7, 14)),
        # `-` is not a digit, so the token still stands.
        ("2026-07-14-15.txt", date(2026, 7, 14)),
        # Non-ISO-ordered: does not match the `YYYY-MM-DD` token shape.
        ("03-04-2026.txt", None),
        # Token shape matches, but month 13 is not a real calendar month.
        ("2026-13-01.txt", None),
        # Leading digit-adjacent: the token is not cleanly bounded.
        ("12026-07-14.txt", None),
        # Trailing digit-adjacent: same reason.
        ("2026-07-140.txt", None),
        # Two distinct dated tokens: ambiguous.
        ("2026-07-14_2026-08-01.txt", None),
        # One invalid token anywhere means no date, even beside a valid one.
        ("2026-13-01_2026-07-14.txt", None),
        # No hyphens: does not match the token shape at all.
        ("20260714.txt", None),
        ("notes.txt", None),
        # Arabic-Indic digits are Unicode digits, not ASCII `[0-9]` --
        # the token regex requires ASCII digits, so no token is found. The
        # digits are deliberately non-ASCII, hence the noqa (ruff RUF001).
        ("٢٠٢٦-٠٧-١٤.txt", None),  # noqa: RUF001
    ],
)
def test_event_date_from_name(name: str, expected: date | None) -> None:
    assert source_date.event_date_from_name(name) == expected


# --- Purity: neither function ever raises (task 1.3) -----------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "x" * 10_000,
        "2026-07-14\x00.txt",
        "\x00\x00\x00",
        "2026-07-14" + "\ud800",  # surrogate-adjacent
        "2026-07-14" * 500,
    ],
)
def test_parsers_never_raise(text: str) -> None:
    source_date.parse_event_date(text)
    source_date.event_date_from_name(text)
