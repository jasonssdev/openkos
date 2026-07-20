"""Unit tests for `resolution/normalize.py`: the deterministic title-key
normalizer HIGH-confidence exact matching keys on.

Normalization order (design): (1) `unicodedata.normalize("NFKD", ...)`;
(2) drop combining marks; (3) casefold; (4) map non-alphanumeric characters
to a space; (5) collapse whitespace (also strips leading/trailing).
"""

import pytest

from openkos.resolution.normalize import normalize_key


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Case-folding
        ("Stoicism", "stoicism"),
        ("STOICISM", "stoicism"),
        # Whitespace collapse (internal + surrounding)
        ("cafe   society", "cafe society"),
        ("  Cafe Society  ", "cafe society"),
        ("Cafe\tSociety\n", "cafe society"),
        # Punctuation strip
        ("Café Society!", "cafe society"),
        ("Stoic-Philosophy", "stoic philosophy"),
        ("Stoic, Philosophy.", "stoic philosophy"),
        # Diacritics removal via NFKD
        ("Café", "cafe"),
        ("Café Société", "cafe societe"),
        ("naïve", "naive"),
    ],
)
def test_normalize_key_rules(title: str, expected: str) -> None:
    """Each rule (case-fold, whitespace collapse, punctuation strip,
    diacritics removal) is applied, in combination, to produce a stable
    comparison key."""
    assert normalize_key(title) == expected


def test_normalize_key_differently_formatted_titles_are_identical() -> None:
    """The spec's HIGH-tier scenario: 'Café Society' and 'cafe   society'
    normalize to the SAME key."""
    assert normalize_key("Café Society") == normalize_key("cafe   society")


def test_normalize_key_is_deterministic() -> None:
    """Calling `normalize_key` twice on the same input yields the same key."""
    title = "Café   Society!!"
    assert normalize_key(title) == normalize_key(title)


def test_normalize_key_empty_string_is_empty() -> None:
    """An empty title normalizes to an empty key, never raises."""
    assert normalize_key("") == ""


def test_normalize_key_punctuation_only_is_empty() -> None:
    """A title with no alphanumeric content normalizes to an empty key."""
    assert normalize_key("!!! --- ...") == ""
