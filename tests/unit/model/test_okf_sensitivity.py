"""Unit tests for `okf.sensitivity_direction` (issue #185, ADR-0003/ADR-0008).

`sensitivity_direction` classifies a proposed sensitivity change against the
current (possibly dirty) frontmatter value, using the same fail-closed
ranking `combine_sensitivity` already relies on (`okf._rank`). It never
exposes the underlying integer rank -- only the three-way verdict a caller
needs to decide whether a downgrade gate applies.
"""

import pytest

from openkos.model import okf


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("public", "private"),
        ("public", "confidential"),
        ("private", "confidential"),
    ],
)
def test_sensitivity_direction_raise(current: str, target: str) -> None:
    """A `target` strictly more restrictive than `current` is a raise."""
    assert okf.sensitivity_direction(current, target) == "raise"


@pytest.mark.parametrize("level", ["public", "private", "confidential"])
def test_sensitivity_direction_same(level: str) -> None:
    """An identical `current`/`target` pair is `same`, never `raise`/`lower`."""
    assert okf.sensitivity_direction(level, level) == "same"


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("private", "public"),
        ("confidential", "public"),
        ("confidential", "private"),
    ],
)
def test_sensitivity_direction_lower(current: str, target: str) -> None:
    """A `target` strictly less restrictive than `current` is a lower."""
    assert okf.sensitivity_direction(current, target) == "lower"


@pytest.mark.parametrize(
    "current",
    [None, "", "   ", 7, "top-secret"],
)
def test_sensitivity_direction_fail_closed_dirty_current_is_lower(
    current: object,
) -> None:
    """A missing, blank, or malformed `current` must rank fail-closed through
    `_rank` (private for missing/blank, confidential for anything else
    unrecognized) -- so a `target` below that floor classifies as `lower`,
    never `same`/`raise`. This is the security-load-bearing behavior a
    downgrade gate depends on (ADR-0003, ADR-0008)."""
    assert okf.sensitivity_direction(current, "public") == "lower"
