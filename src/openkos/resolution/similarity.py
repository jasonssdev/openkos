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


MIN_ACRONYM_LENGTH: Final[int] = 3
"""Shortest token treated as a candidate acronym. Two letters carry too
little signal -- `ai` matches the initials of any two words beginning a-i,
which on a corpus about agents is most of them. Both measured real cases
(`adk`, `mcp`) are three letters."""


def _initialisms(key: str) -> set[str]:
    """Every contiguous run of two or more words in `key`, as its initials.

    Runs may start anywhere: in the one case this rule was built from, the
    expansion sits INSIDE the title that also carries the acronym (`ADK
    (Agent Development Kit)`), so anchoring to the first word would miss
    every real instance. Runs of one word are excluded by construction --
    an acronym abbreviates several words, and matching a single initial
    would make every title sharing a first letter a candidate.
    """
    words = key.split()
    found: set[str] = set()
    for start in range(len(words)):
        for end in range(start + 2, len(words) + 1):
            initials = "".join(word[0] for word in words[start:end])
            if len(initials) >= MIN_ACRONYM_LENGTH:
                found.add(initials)
    return found


def acronym_expansion_match(key_a: str, key_b: str) -> str | None:
    """Return the acronym linking `key_a` and `key_b`, or `None` (#397).

    Two normalized titles match when a token of one IS the initials of a
    contiguous word run in the other -- `google adk` against `adk agent
    development kit`, or `mcp workflows` against `model context protocol`.
    Symmetric: the pair is unordered, so which key arrives first never
    changes the verdict. When several acronyms qualify, the
    lexicographically smallest is returned, so the reported trigger is
    deterministic.

    This exists because subset containment (`near_match_score`) structurally
    cannot see this shape. Every token of the smaller title must find a
    near-match in the larger one, so `google adk` fails on `google` and the
    pair never reaches the adjudicator at all -- a recall failure in the
    gate, not a judgment failure downstream.

    Case is irrelevant here even though the acronym is usually written in
    caps: the comparison is a token's LETTERS against a word run's
    initials, which survives `normalize.normalize_key`'s casefold intact.
    Working on normalized keys is what lets this tier reuse the existing
    shared prelude rather than threading raw titles through it.

    Deliberately narrow. Measured over one real 19-document bundle against
    the 18 same-type pairs an embedding-proximity tier would have added,
    this rule fired on exactly two -- the genuine duplicate #397 reports and
    the `MCP`/`Model Context Protocol` expansion twin recorded as a known
    residue elsewhere -- and on nothing else. That precision is the whole
    point: the proximity alternative surfaced the same true positive but
    cost 18 adjudication calls to deliver it, because embedding distance
    measures topical relatedness rather than identity.

    Returns `None` for identical keys: an exact match is the HIGH tier's
    business, and reporting it here would put one pair in two tiers.
    """
    if key_a == key_b:
        return None
    tokens_a = {token for token in key_a.split() if len(token) >= MIN_ACRONYM_LENGTH}
    tokens_b = {token for token in key_b.split() if len(token) >= MIN_ACRONYM_LENGTH}
    matches = (tokens_a & _initialisms(key_b)) | (tokens_b & _initialisms(key_a))
    return min(matches) if matches else None
