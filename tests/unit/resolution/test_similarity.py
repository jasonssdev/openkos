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
from openkos.resolution.normalize import normalize_key
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


# --- Acronym expansion (#397) -----------------------------------------------


def test_acronym_matches_the_initials_of_a_word_run() -> None:
    """#397's exact miss: `Google ADK` and `ADK (Agent Development Kit)` share
    only the token `adk`, which the subset-containment rule rejects because
    `google` finds no near-match in the longer title. The acronym IS the
    expansion's initials, and that is the signal the lexical gate lacked."""
    assert (
        similarity.acronym_expansion_match("adk agent development kit", "google adk")
        == "adk"
    )


def test_acronym_match_is_symmetric() -> None:
    """Candidate detection compares unordered pairs, so which title is passed
    first must never change the verdict.

    Fixture changed by #641: this test used `mcp workflows` <-> `model
    context protocol`, which the head rule now correctly rejects (a title
    about MCP workflows is not a duplicate of the protocol) -- see
    `test_acronym_as_modifier_of_a_head_noun_is_not_a_duplicate`. Symmetry
    itself is unchanged and re-pinned on the surviving #397 pair."""
    forward = similarity.acronym_expansion_match(
        "google adk", "adk agent development kit"
    )
    backward = similarity.acronym_expansion_match(
        "adk agent development kit", "google adk"
    )
    assert forward == backward == "adk"


def test_acronym_run_may_start_anywhere_in_the_title() -> None:
    """The expansion is rarely the whole title -- in the measured case it sits
    inside `ADK (Agent Development Kit)` after the acronym itself. Anchoring
    to the first word would miss every real instance."""
    assert (
        similarity.acronym_expansion_match(
            "the agent development kit explained", "google adk"
        )
        == "adk"
    )


def test_near_match_shapes_are_not_acronym_matches() -> None:
    """`Stoicism`/`Stoic Philosophy` is the LOW tier's own documented case.
    This rule must not claim it -- overlapping tiers would double-report one
    pair and inflate the adjudication cost #382 is about."""
    assert similarity.acronym_expansion_match("stoicism", "stoic philosophy") is None


def test_unrelated_neighbouring_topics_do_not_match() -> None:
    """Measured false positives of the embedding-proximity alternative, which
    this rule exists to avoid paying for: topically adjacent, not the same
    thing."""
    assert (
        similarity.acronym_expansion_match("agent tools", "agent instructions") is None
    )
    assert similarity.acronym_expansion_match("mcp launching", "mcp origin") is None


def test_a_run_of_one_word_is_never_an_expansion() -> None:
    """An acronym abbreviates SEVERAL words. Matching a single word's initial
    would make every title starting with the same letter a candidate."""
    assert similarity.acronym_expansion_match("abc", "alpha") is None


def test_tokens_shorter_than_the_minimum_are_ignored() -> None:
    """A two-letter token carries too little signal: `ai` would match the
    initials of any two words beginning a-i, which on a corpus about agents
    is most of them."""
    assert (
        similarity.acronym_expansion_match("ai systems", "agent infrastructure") is None
    )


def test_identical_titles_are_not_reported_as_acronym_matches() -> None:
    """An exact match is the HIGH tier's business. Reporting it here too
    would put one pair in two tiers."""
    assert (
        similarity.acronym_expansion_match(
            "agent development kit", "agent development kit"
        )
        is None
    )


def test_empty_and_single_token_keys_never_match() -> None:
    """Mirrors `near_match_score`'s degrade posture: a key that normalizes to
    nothing yields no candidate rather than raising."""
    assert similarity.acronym_expansion_match("", "agent development kit") is None
    assert similarity.acronym_expansion_match("adk", "") is None


# --- #641: the acronym must be the HEAD of its title, not a modifier --------


def test_acronym_as_modifier_of_a_head_noun_is_not_a_duplicate() -> None:
    """#641's first e2e false positive: `mcp server` is a title ABOUT a
    server (head noun `server`), not about the protocol itself. The `mcp`
    token modifies the head, so the pair must not be proposed as a
    duplicate of `model context protocol`."""
    assert (
        similarity.acronym_expansion_match("mcp server", "model context protocol")
        is None
    )


def test_acronym_modifying_a_plural_head_is_not_a_duplicate() -> None:
    """#641's second e2e false positive: `scoping mcp servers` is about
    scoping servers (head `servers`); `mcp` sits mid-title as a modifier."""
    assert (
        similarity.acronym_expansion_match(
            "scoping mcp servers", "model context protocol"
        )
        is None
    )


def test_acronym_after_a_preposition_is_inside_a_modifier_phrase() -> None:
    """#641's third e2e false positive, and a DISTINCT rejection path from
    the last-token rule: in `skill in mcp` the acronym IS the last token,
    but it follows the preposition `in`, so it belongs to a prepositional
    modifier phrase -- the title's head is `skill`. Without the boundary
    truncation, the last-token rule alone would wrongly accept this pair."""
    assert (
        similarity.acronym_expansion_match("skill in mcp", "model context protocol")
        is None
    )


def test_spanish_connective_also_bounds_the_head() -> None:
    """Same shape in Spanish: `servidor de mcp` is a title about a server
    (head `servidor`); `mcp` follows the connective `de`. Spanish
    head-initial compounds are handled by this boundary, not by reversing
    head direction."""
    assert (
        similarity.acronym_expansion_match("servidor de mcp", "model context protocol")
        is None
    )


def test_head_position_acronym_still_matches() -> None:
    """#641 must not lose #397's original true positive: in `google adk`
    the acronym is the compound's HEAD (English compounds are head-final),
    so the pair with its expansion still matches."""
    assert (
        similarity.acronym_expansion_match("google adk", "adk agent development kit")
        == "adk"
    )


def test_single_token_title_is_trivially_its_own_head() -> None:
    """#641 must not lose the known expansion twin: a bare `mcp` title has
    nothing but the acronym, which is therefore its head."""
    assert similarity.acronym_expansion_match("mcp", "model context protocol") == "mcp"


def test_one_qualifying_direction_is_enough() -> None:
    """The head test is evaluated PER DIRECTION and any qualifying
    direction keeps the pair. `google adk` qualifies via its own `adk`
    head against the expansion's initials; the expansion-side title
    (head `kit`, with `adk` merely its first token) never needs to pass
    the head test for the other side's token."""
    assert (
        similarity.acronym_expansion_match("adk agent development kit", "google adk")
        == "adk"
    )


def test_multiple_qualifying_acronyms_return_the_smallest() -> None:
    """Determinism is unchanged by the head rule: when BOTH directions
    qualify (each title's head is the initials of a run in the other),
    the lexicographically smallest acronym is reported."""
    assert (
        similarity.acronym_expansion_match(
            "model context protocol adk", "agent development kit mcp"
        )
        == "adk"
    )


# --- #555: short-token dropping must not manufacture single-token titles ----


def test_dropping_short_tokens_never_reduces_a_multi_word_title_to_one_token() -> None:
    """'ai agent' -> ("agent",) was the bug (#555): the discarded token was
    the one that DISTINGUISHED the title, and the manufactured single-token
    set then subset-matched anything sharing that one generic token at a
    displayed 1.000. With the guard, 'ai' must also find an equivalent --
    and it has none in 'agent observability'."""
    assert near_match_score("ai agent", "agent observability") is None


def test_claude_md_does_not_match_claude_code() -> None:
    """#555's second evidence pair: 'claude md' -> ("claude",) contained in
    ("claude", "code") at 1.000. 'md' must also find an equivalent."""
    assert near_match_score("claude md", "claude code") is None


def test_retained_short_tokens_still_catch_a_genuine_near_duplicate() -> None:
    """The guard RETAINS short tokens instead of rejecting the pair
    outright, so a genuine near-duplicate whose distinguishing token is
    short is still caught: 'ai agent' vs 'ai agents' matches on both
    tokens ('ai' identically, 'agent'/'agents' above threshold)."""
    score = near_match_score("ai agent", "ai agents")
    assert score is not None
    assert score >= similarity.SIMILARITY_THRESHOLD


def test_all_short_multi_word_titles_can_still_match_each_other() -> None:
    """A multi-word title made ENTIRELY of short tokens ('ai ml') used to
    tokenize to `()` and never match anything; retained, it can match a
    qualifying counterpart."""
    assert near_match_score("ai ml", "ai and ml") is not None


def test_single_short_word_title_still_never_matches() -> None:
    """The guard applies only to MULTI-word titles: a single short word
    still tokenizes to nothing and never matches (unchanged behavior --
    stray single letters carry no reliable signal)."""
    assert is_near_match("ai", "ai observability") is False


def test_genuine_single_token_subset_containment_is_unchanged() -> None:
    """The motivating case survives the guard untouched: a genuinely
    single-token title ('stoicism') was never REDUCED to one token, so it
    still subset-matches 'stoic philosophy'."""
    assert near_match_score("stoicism", "stoic philosophy") == pytest.approx(
        0.769, abs=0.01
    )


# --- #755: function words must not decide a containment ---------------------


def test_function_words_do_not_block_a_genuine_near_duplicate() -> None:
    """#755's reported pair. Two concepts on one subject, from one source,
    went unreported and `curate` then filed a `part_of` between them.

    NOTE the mechanism, because the issue states a different one. It claims
    the unmatched `del` makes the rule return `None`. It does not: both token
    sets have four members, the tie makes the LEFT set the required one, and
    what actually blocks is `decisiones` -- a genuine content word absent
    from the right title. Excluding `del` fixes the pair by changing WHICH
    set is smaller, so containment is asked in the direction that can
    succeed. Function words must not count toward SIZE, and size is what
    picks the direction."""
    score = near_match_score(
        "arquitectura de sistemas de extraccion de decisiones",
        "arquitectura del sistema de extraccion",
    )
    assert score is not None
    assert score >= similarity.SIMILARITY_THRESHOLD


def test_a_function_word_is_never_a_match_target_for_a_content_word() -> None:
    """The mirror defect, found while measuring: a function word left in the
    LARGER set is a lexical target a content word can match against, which
    MANUFACTURES a near-match between unrelated titles.

    `SequenceMatcher(None, "model", "del").ratio()` is exactly 0.750 -- on
    the threshold -- so `model` matched `del` and these two unrelated titles
    were reported as duplicates. Excluding function words from scoring, not
    only from the required set, is what removes it."""
    assert SequenceMatcher(None, "model", "del").ratio() == 0.75
    assert (
        near_match_score(
            "integracion del knowledge source project", "knowledge object model"
        )
        is None
    )


def test_excluding_function_words_never_manufactures_a_single_token_title() -> None:
    """#555's floor, applied to this filter -- the trap the issue names.

    'the app' carries two tokens, one of them a function word. Dropping it
    outright leaves `("app",)`, which subset-matches anything containing a
    similar token at a displayed 1.000: precisely the LOW-tier false positive
    #555 removed, reintroduced through a different door. So the exclusion
    RETAINS the function words whenever dropping would leave a multi-word
    title with fewer than two tokens."""
    assert near_match_score("the app", "app store") is None


def test_accented_function_words_are_matched_after_normalization() -> None:
    """The lexicon is normalized through `normalize_key`, the same function
    that produced the keys being compared.

    Three entries -- `más`, `según`, `también` -- exist ONLY in accented form
    in `LANGUAGE_FUNCTION_WORDS`, while `normalize_key` strips accents. A
    raw-set membership test would silently never fire for them, and the
    lexicon would be quietly three words smaller than it reads."""
    assert "mas" in similarity.MATCH_FUNCTION_WORDS
    assert "segun" in similarity.MATCH_FUNCTION_WORDS
    assert "tambien" in similarity.MATCH_FUNCTION_WORDS
    assert "del" in similarity.MATCH_FUNCTION_WORDS
    assert "with" in similarity.MATCH_FUNCTION_WORDS


def test_the_lexicon_is_the_shipped_one_not_a_second_copy() -> None:
    """One source of truth (owner ruling on #755): this module must reuse
    `extraction.concept.LANGUAGE_FUNCTION_WORDS` rather than grow a private
    list that diverges the first time someone adds a word to one side."""
    from openkos.extraction.concept import LANGUAGE_FUNCTION_WORDS

    assert {normalize_key(w) for w in LANGUAGE_FUNCTION_WORDS} == (
        similarity.MATCH_FUNCTION_WORDS
    )


def test_content_bearing_titles_are_unaffected_by_the_exclusion() -> None:
    """A pair with no function words on either side scores exactly as before
    -- the filter must be inert where it has nothing to remove."""
    assert near_match_score("stoicism", "stoic philosophy") == pytest.approx(
        0.769, abs=0.01
    )
    assert near_match_score("ai agent", "ai agents") == pytest.approx(0.909, abs=0.01)
