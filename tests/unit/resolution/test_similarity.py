"""Unit tests for `resolution/similarity.py`: the deterministic, stdlib-only
LOW-confidence near-match helper.

Locks the two spec-pinned constants -- `SIMILARITY_THRESHOLD` (0.75) and
`MIN_TOKEN_LENGTH` (3) -- and the subset-containment algorithm: every
(length >= `MIN_TOKEN_LENGTH`) token of the SMALLER title's token set must
have an equivalent token (identical, or `difflib.SequenceMatcher.ratio()`
>= `SIMILARITY_THRESHOLD`) in the larger title's token set.
"""

from difflib import SequenceMatcher

import pytest

from openkos.resolution import similarity
from openkos.resolution.similarity import is_near_match, near_match_score, tokenize


def test_similarity_threshold_is_0_75() -> None:
    """The pinned threshold constant."""
    assert similarity.SIMILARITY_THRESHOLD == 0.75


def test_min_token_length_is_3() -> None:
    """The pinned minimum token length constant."""
    assert similarity.MIN_TOKEN_LENGTH == 3


def test_stoic_stoicism_boundary_ratio_is_locked() -> None:
    """The exact boundary value the spec pins: `SequenceMatcher(None,
    "stoic", "stoicism").ratio()` is approximately 0.769, ABOVE the 0.75
    threshold -- this is the concrete case `is_near_match` must qualify."""
    ratio = SequenceMatcher(None, "stoic", "stoicism").ratio()
    assert ratio == pytest.approx(0.769, abs=0.01)
    assert ratio >= similarity.SIMILARITY_THRESHOLD


def test_tokenize_drops_tokens_shorter_than_min_length() -> None:
    """A 2-char token is dropped; a 3-char token is kept."""
    assert tokenize("a bb ccc dddd") == ("ccc", "dddd")


def test_tokenize_empty_key_yields_empty_tuple() -> None:
    """An empty normalized key tokenizes to nothing."""
    assert tokenize("") == ()


def test_is_near_match_positive_stoic_boundary() -> None:
    """'stoic' vs 'stoicism' qualifies as a near-match (locks the 0.769
    boundary case above the 0.75 threshold)."""
    assert is_near_match("stoic", "stoicism") is True


def test_is_near_match_subset_containment_stoicism_stoic_philosophy() -> None:
    """'stoicism' is fully covered by an equivalent token in 'stoic
    philosophy' (spec's worked example: Stoicism <-> Stoic Philosophy)."""
    assert is_near_match("stoicism", "stoic philosophy") is True


def test_is_near_match_negative_dissimilar_titles() -> None:
    """Two clearly dissimilar same-type titles do not qualify."""
    assert is_near_match("stoicism", "quantum electrodynamics") is False


def test_is_near_match_false_when_either_key_has_no_tokens() -> None:
    """A key that tokenizes to nothing (e.g. all-short or empty) never
    matches -- and never raises."""
    assert is_near_match("", "stoicism") is False
    assert is_near_match("a bb", "stoicism") is False


def test_near_match_score_returns_none_when_no_match() -> None:
    """`near_match_score` returns `None`, not a low float, for a
    non-qualifying pair."""
    assert near_match_score("stoicism", "quantum electrodynamics") is None


def test_near_match_score_returns_a_float_at_or_above_threshold_on_match() -> None:
    """A qualifying pair's score is a float >= `SIMILARITY_THRESHOLD`."""
    score = near_match_score("stoic", "stoicism")
    assert score is not None
    assert score >= similarity.SIMILARITY_THRESHOLD


def test_near_match_score_identical_keys_scores_1_0() -> None:
    """Identical normalized keys score the maximum, `1.0`."""
    assert near_match_score("stoicism", "stoicism") == 1.0


def test_is_near_match_is_deterministic() -> None:
    """Repeated calls on the same inputs return the same result."""
    assert is_near_match("stoicism", "stoic philosophy") == is_near_match(
        "stoicism", "stoic philosophy"
    )


def test_is_near_match_short_token_false_positive_is_accepted_by_design() -> None:
    """Known precision tradeoff -- LOW tier is high-recall; do NOT tighten
    without re-checking recall on 'Stoicism' <-> 'Stoic Philosophy'.
    Precision is the LLM-adjudication slice's job.

    'cats' vs 'carts and currency' are unrelated titles, yet 'cats' is a
    subset-containment near-match of 'carts and currency': the single
    token 'cats' has an equivalent token 'carts' in the larger set
    (`SequenceMatcher(None, "cats", "carts").ratio()` ~= 0.888, above the
    0.75 threshold). This is STRUCTURALLY identical to this module's own
    motivating GOOD example ('stoic'/'stoicism' ~= 0.769) -- no lexical
    rule distinguishes the two. This test locks the current (accepted)
    behavior so nobody silently "fixes" it and breaks recall on the
    Stoicism case.
    """
    ratio = SequenceMatcher(None, "cats", "carts").ratio()
    assert ratio == pytest.approx(0.888, abs=0.01)
    assert ratio >= similarity.SIMILARITY_THRESHOLD

    assert is_near_match("cats", "carts and currency") is True
    score = near_match_score("cats", "carts and currency")
    assert score is not None
    assert score >= similarity.SIMILARITY_THRESHOLD


def test_is_near_match_short_token_false_positive_run_ruin() -> None:
    """Same accepted tradeoff, reviewer's second example: 'run' vs 'ruin'
    (`SequenceMatcher(None, "run", "ruin").ratio()` ~= 0.857) qualifies as
    a near-match despite being unrelated words. Do NOT tighten the
    threshold to reject this -- see the module-level docstring in
    `similarity.py` and the 'cats'/'carts' test above."""
    ratio = SequenceMatcher(None, "run", "ruin").ratio()
    assert ratio >= similarity.SIMILARITY_THRESHOLD
    assert is_near_match("run", "ruin") is True


def test_is_near_match_false_just_below_threshold() -> None:
    """Boundary test on the NEGATIVE side of the `>= 0.75` cut (the
    positive side, ~=0.769, is already locked by
    `test_stoic_stoicism_boundary_ratio_is_locked`).

    'cart' vs 'charter': `SequenceMatcher(None, "cart", "charter").ratio()`
    ~= 0.727, just BELOW the 0.75 threshold -- so it must NOT qualify as a
    near-match. Exercises both sides of the cut with concrete ratios.
    """
    ratio = SequenceMatcher(None, "cart", "charter").ratio()
    assert ratio == pytest.approx(0.727, abs=0.01)
    assert ratio < similarity.SIMILARITY_THRESHOLD

    assert is_near_match("cart", "charter") is False
    assert near_match_score("cart", "charter") is None
