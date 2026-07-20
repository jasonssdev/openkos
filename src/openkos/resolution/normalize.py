"""Deterministic title-key normalization for HIGH-confidence exact matching.

`normalize_key` is the single seam every HIGH-tier comparison in
`candidates.py` goes through: two same-type documents whose titles
normalize to an identical key form a HIGH-confidence candidate (spec:
Exact Normalized-Key Match). Stdlib-only, no locale/ICU dependency.
"""

import unicodedata


def normalize_key(title: str) -> str:
    """Normalize `title` into a stable comparison key.

    Order (design): (1) `unicodedata.normalize("NFKD", ...)` decomposes
    accented characters into a base character plus combining marks; (2)
    combining marks are dropped, removing the diacritic while keeping the
    base letter (e.g. "Café" -> "Cafe"); (3) `casefold()` neutralizes case
    more aggressively than `lower()` (correct for Unicode-aware matching);
    (4) every non-alphanumeric character (punctuation, symbols) is mapped
    to a space; (5) whitespace is collapsed to single spaces and stripped
    from both ends. An empty or punctuation-only title normalizes to `""`,
    never raises.
    """
    decomposed = unicodedata.normalize("NFKD", title)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    spaced = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(spaced.split())
