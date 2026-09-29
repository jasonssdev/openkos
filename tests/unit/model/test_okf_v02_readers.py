"""Unit tests for the OKF v0.2 dual-reader accessors (okf-v02-migration,
issue #1064, Phase 1/PR1): `generation_time`, `_parse_instant`, and
`declares_deprecated`.

These are the ONLY way any module reads a concept's generation time or own
lifecycle status (design.md Decision 6). They accept v0.1, v0.2, and mixed
documents.
"""

from datetime import date, datetime

import pytest

from openkos.model import okf


def test_generation_time_resolves_generated_at_when_present() -> None:
    """A v0.2 concept -- `generated.at` present, no `timestamp` -- resolves
    via `generated.at` (okf-format-migration: "A v0.2 concept resolves via
    `generated.at`")."""
    metadata = {"generated": {"at": "2026-07-14T09:00:00+00:00"}}

    result = okf.generation_time(metadata)

    assert result == datetime.fromisoformat("2026-07-14T09:00:00+00:00")


def test_generation_time_falls_back_to_legacy_timestamp_when_generated_absent() -> None:
    """A legacy concept -- only `timestamp`, no `generated` key -- resolves
    via `timestamp` (okf-format-migration: "A legacy concept resolves via
    `timestamp`"); a concept with neither key resolves to `None`."""
    legacy = {"timestamp": "2026-05-01T12:00:00+00:00"}
    neither: dict[str, object] = {}

    assert okf.generation_time(legacy) == datetime.fromisoformat(
        "2026-05-01T12:00:00+00:00"
    )
    assert okf.generation_time(neither) is None


def test_generation_time_generated_present_never_falls_back() -> None:
    """`generated.at` takes precedence when both keys are present, even when
    the values disagree (okf-format-migration: "`generated.at` takes
    precedence when both are present"); a present-but-malformed `generated`
    resolves to `None` WITHOUT falling back to the sibling `timestamp` --
    this kills a fallback that fires whenever `generated.at` fails to parse
    instead of only when `generated` is absent."""
    both = {
        "generated": {"at": "2026-01-01T00:00:00+00:00"},
        "timestamp": "2020-01-01T00:00:00+00:00",
    }
    assert okf.generation_time(both) == datetime.fromisoformat(
        "2026-01-01T00:00:00+00:00"
    )

    malformed_not_mapping = {
        "generated": "not-a-mapping",
        "timestamp": "2020-01-01T00:00:00+00:00",
    }
    assert okf.generation_time(malformed_not_mapping) is None

    malformed_missing_at = {
        "generated": {},
        "timestamp": "2020-01-01T00:00:00+00:00",
    }
    assert okf.generation_time(malformed_missing_at) is None

    malformed_unparseable_at = {
        "generated": {"at": "not-a-timestamp"},
        "timestamp": "2020-01-01T00:00:00+00:00",
    }
    assert okf.generation_time(malformed_unparseable_at) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2026, 7, 14, 9, 0, 0),  # noqa: DTZ001 -- naive on purpose: a
            # hand-written unquoted `at:`/`timestamp:` scalar YAML resolves to a
            # naive `datetime` just as readily as an aware one; `_parse_instant`
            # must pass it through unchanged either way
            datetime(2026, 7, 14, 9, 0, 0),  # noqa: DTZ001 -- same naive value, expected unchanged
        ),
        (
            "2026-07-14T09:00:00+00:00",
            datetime.fromisoformat("2026-07-14T09:00:00+00:00"),
        ),
        (date(2026, 7, 14), None),
        (12345, None),
        (["not", "a", "timestamp"], None),
        ("not-a-timestamp", None),
    ],
)
def test_parse_instant_accepts_datetime_str_and_rejects_the_rest(
    value: object, expected: datetime | None
) -> None:
    """`_parse_instant` widens `_parse_timestamp` to accept a YAML-resolved
    `datetime` object as-is (design.md Decision 6 -- today's
    `_parse_timestamp` rejects a `datetime`), parses an ISO-8601 string, and
    returns `None` for a bare `date`, a non-string/non-datetime, or an
    unparseable string."""
    assert okf._parse_instant(value) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("deprecated", True),
        ("active", False),
        ("stable", False),
        ("draft", False),
        (None, False),
        ("some-unknown-value", False),
    ],
)
def test_declares_deprecated_status_value_table(
    status: str | None, expected: bool
) -> None:
    """Only the exact literal `"deprecated"` marks a concept deprecated
    (okf-format-migration: "A legacy `active` concept is not deprecated", "A
    `stable` concept is not deprecated", "An absent status is not
    deprecated") -- this kills any value other than the exact literal
    marking a concept deprecated."""
    metadata: dict[str, object] = {} if status is None else {"status": status}

    assert okf.declares_deprecated(metadata) is expected
