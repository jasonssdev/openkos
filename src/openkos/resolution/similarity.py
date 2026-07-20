"""Deterministic, stdlib-only near-match helper for LOW-confidence candidates.

Compares two NORMALIZED titles (see `normalize.normalize_key`) token by
token via `difflib.SequenceMatcher` -- never a model/LLM, never a
third-party dependency. Two same-type titles qualify as a LOW-confidence
near-match when every (length >= `MIN_TOKEN_LENGTH`) token of the SMALLER
title's token set has an equivalent token (identical, or a
`SequenceMatcher.ratio()` at or above `SIMILARITY_THRESHOLD`) in the
larger title's token set. This subset-containment rule catches reorders,
typos, and a shorter title fully contained in a longer one (e.g.
"Stoicism" inside "Stoic Philosophy") -- a whole-string ratio alone would
miss that case due to the length difference.
"""

from difflib import SequenceMatcher
from typing import Final

SIMILARITY_THRESHOLD: Final[float] = 0.75
"""Minimum per-token `SequenceMatcher.ratio()` for two tokens to be
considered equivalent. Locked by `SequenceMatcher(None, "stoic",
"stoicism").ratio()` ~= 0.769, which must qualify as a near-match."""

MIN_TOKEN_LENGTH: Final[int] = 3
"""Tokens shorter than this are dropped before comparison: too short to
carry a reliable similarity signal (e.g. stray single letters)."""


def tokenize(key: str) -> tuple[str, ...]:
    """Split a normalized key on whitespace, dropping tokens shorter than
    `MIN_TOKEN_LENGTH`. An empty key (or one with only short tokens)
    tokenizes to `()`."""
    return tuple(token for token in key.split() if len(token) >= MIN_TOKEN_LENGTH)


def near_match_score(key_a: str, key_b: str) -> float | None:
    """The near-match score for `key_a`/`key_b`, or `None` if they do not
    qualify as a LOW-confidence near-match.

    Tokenizes both keys, then checks SUBSET containment: every token of
    the SMALLER token set must have an equivalent token (per-token
    `SequenceMatcher.ratio()` >= `SIMILARITY_THRESHOLD`) in the larger
    set's tokens. The returned score is the WEAKEST per-token best-match
    ratio among the smaller set's tokens -- `1.0` when every token repeats
    identically. Returns `None` (never raises) when either key tokenizes
    to nothing, or when any token in the smaller set has no equivalent in
    the larger set.

    ACCEPTED precision tradeoff, BY DESIGN: LOW tier is intentionally
    high-recall, not high-precision. Because the rule only checks
    per-token ratio, a short single-token title can false-positive
    against an unrelated longer title that happens to contain a
    lexically similar token -- e.g. "cats" vs "carts and currency"
    ("cats"/"carts" ratio ~= 0.888) is STRUCTURALLY identical to this
    module's own motivating case, "stoicism" ⊂ "stoic philosophy"
    ("stoic"/"stoicism" ratio ~= 0.769). No lexical rule can separate the
    two without also rejecting the Stoicism case, so do NOT tighten
    `SIMILARITY_THRESHOLD` or `MIN_TOKEN_LENGTH` to "fix" this -- it would
    just trade one false positive for a false negative on the case this
    algorithm exists to catch. LOW-tier candidates are a read-only review
    queue (never auto-merged); precision here is deliberately deferred to
    LLM adjudication in a later slice.
    """
    tokens_a = tokenize(key_a)
    tokens_b = tokenize(key_b)
    if not tokens_a or not tokens_b:
        return None
    smaller, larger = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    )
    weakest = 1.0
    for small_token in smaller:
        best = max(
            SequenceMatcher(None, small_token, large_token).ratio()
            for large_token in larger
        )
        if best < SIMILARITY_THRESHOLD:
            return None
        weakest = min(weakest, best)
    return weakest


def is_near_match(key_a: str, key_b: str) -> bool:
    """`True` when `key_a`/`key_b` qualify as a LOW-confidence near-match
    (see `near_match_score`)."""
    return near_match_score(key_a, key_b) is not None
