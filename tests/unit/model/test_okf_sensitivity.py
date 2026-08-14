"""Unit tests for `okf.sensitivity_direction` (issue #185, ADR-0003/ADR-0008)
and `okf.raise_by` (issue #669, ADR-0015).

`sensitivity_direction` classifies a proposed sensitivity change against the
current (possibly dirty) frontmatter value, using the same fail-closed
ranking `combine_sensitivity` already relies on (`okf._rank`). It never
exposes the underlying integer rank -- only the three-way verdict a caller
needs to decide whether a downgrade gate applies.

`raise_by` is the pure, stdlib-only helper that steps a sensitivity `level`
up `offset` positions in `SENSITIVITY_ORDER`, clamped at the ceiling
(`confidential`). It reuses `okf._rank`'s existing fail-closed ranking, so a
missing/malformed/non-string `level` still resolves to a canonical member
rather than raising. A negative `offset` is refused -- a helper that raises
CAN'T become a downgrade vector (design D2).
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


@pytest.mark.parametrize(
    ("level", "offset", "expected"),
    [
        # public floor
        ("public", 0, "public"),
        ("public", 1, "private"),
        ("public", 2, "confidential"),
        # private floor
        ("private", 0, "private"),
        ("private", 1, "confidential"),
        ("private", 2, "confidential"),  # clamp at the ceiling
        # confidential floor
        ("confidential", 0, "confidential"),
        ("confidential", 1, "confidential"),  # clamp at the ceiling
        ("confidential", 2, "confidential"),  # clamp at the ceiling
    ],
)
def test_raise_by_steps_up_the_order_and_clamps(
    level: str, offset: int, expected: str
) -> None:
    """Each `SENSITIVITY_ORDER` floor x offset 0/1/2 produces the expected
    canonical member, clamping at `confidential` rather than overflowing."""
    assert okf.raise_by(level, offset) == expected


@pytest.mark.parametrize(
    ("level", "offset", "expected"),
    [
        (None, 0, "private"),  # `_rank`'s fail-closed floor for missing values
        (None, 1, "confidential"),
        ("", 0, "private"),  # blank string ranks as private per `_rank`
        (7, 0, "confidential"),  # non-string ranks as confidential per `_rank`
        ("top-secret", 0, "confidential"),  # unrecognized string ranks confidential
        ("top-secret", 1, "confidential"),  # already at the ceiling, clamps
    ],
)
def test_raise_by_reuses_rank_fail_closed_behavior(
    level: object, offset: int, expected: str
) -> None:
    """A missing, blank, non-string, or unrecognized `level` ranks through
    `_rank`'s existing fail-closed rules before the offset is applied."""
    assert okf.raise_by(level, offset) == expected


def test_raise_by_rejects_negative_offset() -> None:
    """A negative offset would LOWER the level -- `raise_by` refuses it as
    defence in depth, even though config-load validation (D1) already
    refuses it earlier."""
    with pytest.raises(ValueError, match=r"offset must be non-negative"):
        okf.raise_by("public", -1)
