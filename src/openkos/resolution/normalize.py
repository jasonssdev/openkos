"""Deterministic title-key normalization for HIGH-confidence exact matching.

`normalize_key` is the single seam every HIGH-tier comparison in
`candidates.py` goes through: two same-type documents whose titles
normalize to an identical key form a HIGH-confidence candidate (spec:
Exact Normalized-Key Match). Stdlib-only, no locale/ICU dependency.
"""

import re
import unicodedata
from collections.abc import Sequence


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


def is_suffix_family(base_id: str, other_id: str) -> bool:
    """Whether `other_id` is `base_id` plus an ingest-time `-N` disambiguator
    (digits only, same directory) -- the collision suffix `ingest` appends to
    a slug that is already taken (#1228)."""
    prefix = f"{base_id}-"
    return other_id.startswith(prefix) and other_id[len(prefix) :].isdecimal()


_TRAILING_NUMBER = re.compile(r"-(\d+)$")


def canonical_family_member(member_ids: Sequence[str]) -> str:
    """The member of a same-key group that keeps the entity's identity: the
    un-suffixed Concept ID (the one no other member is the base of), else the
    lowest `-N`, ties by id order (#1228's rule, shared by `merge` and by
    ingest's attach so the two cannot pick different survivors). Raises
    `ValueError` on an empty group."""
    if not member_ids:
        raise ValueError("a family needs at least one member")

    def rank(member_id: str) -> tuple[bool, int, str]:
        suffixed = any(is_suffix_family(other, member_id) for other in member_ids)
        match = _TRAILING_NUMBER.search(member_id)
        return (suffixed, int(match.group(1)) if match else 0, member_id)

    return min(member_ids, key=rank)
