"""Unit tests for `prompt_budget.py`: the ONE whole-text prompt bound.

The bound shipped for the ingest path in #866 (`extraction/concept.py`'s
`_bounded_prompt_source`) and the retrieval path needed the identical
behaviour for #882. Copying it would have produced exactly the two-renderer
drift #883 had just closed elsewhere, so the arithmetic and the excerpt live
here and both paths call in. These tests own the shared contract; the two
callers' own tests own their wiring.

Pure string/integer arithmetic over arguments -- zero I/O, zero network.
"""

from typing import Final

import pytest

from openkos import prompt_budget

_MARKER: Final = prompt_budget.ELISION_MARKER


class _PinnedBackend:
    """A backend advertising a pinned context window, like `OllamaClient`."""

    def __init__(self, window: object, reply_reserve: object = None) -> None:
        self.context_window = window
        self.max_generation_tokens = reply_reserve


class _BareBackend:
    """A backend advertising neither attribute -- every test fake in the
    suite, and any third-party `LLMBackend` implementation."""


class _ThrowingBackend:
    """A backend whose `context_window` property RAISES.

    #866 closed this twice in one review cycle: unguarded it aborted the
    pipeline, and guarded around the whole body it silently skipped the
    bound and sent the full oversized prompt. Both are failures; the read
    alone is what may degrade."""

    @property
    def context_window(self) -> int:
        raise RuntimeError("backend exploded")

    @property
    def max_generation_tokens(self) -> int:
        raise RuntimeError("backend exploded")


def test_planning_window_reads_the_backends_pinned_window() -> None:
    assert prompt_budget.planning_window(_PinnedBackend(40_960)) == 40_960


def test_planning_window_defaults_when_the_backend_advertises_nothing() -> None:
    assert (
        prompt_budget.planning_window(_BareBackend())
        == prompt_budget.PLANNING_CONTEXT_WINDOW
    )


@pytest.mark.parametrize("window", [None, 0, -1, True, False, "12288", 12288.0])
def test_planning_window_defaults_on_every_unusable_value(window: object) -> None:
    """`True` is an `int` in Python and would otherwise plan a 1-token
    window -- the exact shape `config.read_config` rejects for the same
    reason."""
    assert (
        prompt_budget.planning_window(_PinnedBackend(window))
        == prompt_budget.PLANNING_CONTEXT_WINDOW
    )


def test_planning_window_degrades_rather_than_propagating_a_raising_property() -> None:
    assert (
        prompt_budget.planning_window(_ThrowingBackend())
        == prompt_budget.PLANNING_CONTEXT_WINDOW
    )


def test_reply_reserve_reads_the_backends_pinned_ceiling() -> None:
    assert prompt_budget.reply_reserve(_PinnedBackend(40_960, 1_024)) == 1_024


def test_reply_reserve_defaults_when_the_backend_advertises_nothing() -> None:
    assert prompt_budget.reply_reserve(_BareBackend()) == prompt_budget.REPLY_RESERVE


def test_reply_reserve_degrades_rather_than_propagating_a_raising_property() -> None:
    assert (
        prompt_budget.reply_reserve(_ThrowingBackend()) == prompt_budget.REPLY_RESERVE
    )


def test_budget_chars_is_window_minus_reserve_over_tokens_per_char_minus_overhead() -> (
    None
):
    budget = prompt_budget.budget_chars(
        planning=12_288, generation_reserve_tokens=2_048, overhead_chars=1_000
    )
    assert budget == int((12_288 - 2_048) / prompt_budget.TOKENS_PER_CHAR) - 1_000


def test_budget_chars_floors_at_zero_rather_than_going_negative() -> None:
    """A small pinned window under a large overhead must send an EMPTY text
    portion, never a negative budget that a slice would read as 'from the
    end'."""
    assert (
        prompt_budget.budget_chars(
            planning=1_000, generation_reserve_tokens=900, overhead_chars=10_000
        )
        == 0
    )


def test_chunk_lines_is_lossless() -> None:
    text = "alpha\nbravo\ncharlie\ndelta\necho"
    assert "\n".join(prompt_budget.chunk_lines(text, 12)) == text


def test_chunk_lines_never_splits_inside_a_line() -> None:
    """A line longer than the target becomes its own oversized window,
    whole: a truncated utterance is not extractable content."""
    long_line = "x" * 50
    windows = prompt_budget.chunk_lines(f"a\n{long_line}\nb", 10)
    assert long_line in windows


def test_bounded_text_returns_a_fitting_text_byte_identical_and_unflagged() -> None:
    """The bound must not change model input on a prompt that already
    fits."""
    text = "alpha\nbravo"
    bounded, was_bounded = prompt_budget.bounded_text(
        text, budget=1_000, windows=prompt_budget.chunk_lines(text, 10)
    )
    assert bounded == text
    assert was_bounded is False


def test_bounded_text_excerpt_fits_the_budget_and_is_flagged() -> None:
    text = "\n".join(f"line {n:03d} " + "y" * 40 for n in range(200))
    windows = prompt_budget.chunk_lines(text, 200)
    bounded, was_bounded = prompt_budget.bounded_text(
        text, budget=2_000, windows=windows
    )
    assert was_bounded is True
    assert len(bounded) <= 2_000


def test_bounded_text_keeps_both_ends_not_just_the_head() -> None:
    """Even coverage, not a head cut. The server-side truncation this
    replaces is precisely a one-ended view, so an excerpt that reproduced it
    would buy nothing."""
    text = "\n".join(f"line {n:03d} " + "y" * 40 for n in range(200))
    windows = prompt_budget.chunk_lines(text, 200)
    bounded, _ = prompt_budget.bounded_text(text, budget=3_000, windows=windows)
    assert "line 000" in bounded
    assert "line 199" in bounded


def test_bounded_text_marks_every_elision_so_the_model_is_told_text_is_missing() -> (
    None
):
    text = "\n".join(f"line {n:03d} " + "y" * 40 for n in range(200))
    windows = prompt_budget.chunk_lines(text, 200)
    bounded, _ = prompt_budget.bounded_text(text, budget=3_000, windows=windows)
    assert _MARKER in bounded


def test_bounded_text_maximizes_the_window_count_that_fits() -> None:
    """Coverage is the whole value of the excerpt, and nothing else in this
    module pins it: keeping both ends, marking elisions and fitting the
    budget are all satisfied by a two-window excerpt that throws away most
    of the room it was given.

    40 windows of ~100 chars against a 2,000-char budget: `k` mostly
    non-adjacent windows cost about `100k + len(marker) * (k - 1)`, so ~13
    fit and 2 is nowhere near the largest count that does. Asserted as a
    floor rather than an exact count so the test pins the PROPERTY, not this
    arithmetic."""
    windows = [f"w{n:03d}" + "y" * 94 for n in range(40)]
    text = "\n".join(windows)
    bounded, was_bounded = prompt_budget.bounded_text(
        text, budget=2_000, windows=windows
    )
    assert was_bounded is True
    included = sum(1 for n in range(40) if f"w{n:03d}" in bounded)
    assert included >= 12, f"only {included} of 40 windows survived"


def test_bounded_text_is_deterministic() -> None:
    text = "\n".join(f"line {n:03d} " + "y" * 40 for n in range(200))
    windows = prompt_budget.chunk_lines(text, 200)
    first, _ = prompt_budget.bounded_text(text, budget=3_000, windows=windows)
    second, _ = prompt_budget.bounded_text(text, budget=3_000, windows=windows)
    assert first == second


def test_bounded_text_hard_truncates_when_not_even_two_windows_fit() -> None:
    text = "\n".join("z" * 100 for _ in range(20))
    windows = prompt_budget.chunk_lines(text, 100)
    bounded, was_bounded = prompt_budget.bounded_text(text, budget=50, windows=windows)
    assert was_bounded is True
    assert len(bounded) == 50
    assert bounded == windows[0][:50]


def test_bounded_text_sends_an_empty_portion_when_overhead_swallowed_the_budget() -> (
    None
):
    """Strictly safer than the decapitated prompt an over-budget send would
    produce: the call keeps its instructions."""
    text = "\n".join("z" * 100 for _ in range(20))
    windows = prompt_budget.chunk_lines(text, 100)
    bounded, was_bounded = prompt_budget.bounded_text(text, budget=0, windows=windows)
    assert bounded == ""
    assert was_bounded is True


def test_bounded_text_never_exceeds_the_budget_across_many_sizes() -> None:
    """There is deliberately no floor ABOVE the budget: a floor that sends
    more than fits re-creates the overflow this module exists to close."""
    text = "\n".join(f"line {n:03d} " + "y" * 40 for n in range(200))
    windows = prompt_budget.chunk_lines(text, 200)
    for budget in range(0, 4_000, 137):
        bounded, _ = prompt_budget.bounded_text(text, budget=budget, windows=windows)
        assert len(bounded) <= budget, f"budget={budget}"


def test_fair_shares_leaves_a_fitting_set_untouched() -> None:
    assert prompt_budget.fair_shares([10, 20, 30], budget=1_000) == [10, 20, 30]


def test_fair_shares_splits_an_over_budget_set_evenly() -> None:
    assert prompt_budget.fair_shares([100, 100, 100], budget=30) == [10, 10, 10]


def test_fair_shares_redistributes_what_small_blocks_do_not_need() -> None:
    """A 5-char block must not sit on a 100-char share while a 500-char
    block is cut to 100 -- the whole point of sharing one window."""
    shares = prompt_budget.fair_shares([5, 5, 500], budget=300)
    assert shares[0] == 5
    assert shares[1] == 5
    assert shares[2] == 290
    assert sum(shares) <= 300


def test_fair_shares_never_exceeds_the_budget() -> None:
    for budget in range(0, 500, 17):
        shares = prompt_budget.fair_shares([5, 5, 500, 60, 1], budget=budget)
        assert sum(shares) <= budget, f"budget={budget}"


def test_fair_shares_of_an_empty_set_is_empty() -> None:
    assert prompt_budget.fair_shares([], budget=100) == []


def test_fair_shares_never_gives_a_block_more_than_its_own_size() -> None:
    """A zero-length body is ordinary -- a concept that is all frontmatter --
    and handing it a char both breaks this contract and STARVES a block that
    had a use for it. Measured before the fix: `[0, 100]` against a budget of
    1 returned `[1, 0]`, spending the only available char on the empty
    block."""
    assert prompt_budget.fair_shares([0, 100], budget=1) == [0, 1]
    for budget in range(0, 40):
        sizes = [0, 0, 7, 100, 0]
        shares = prompt_budget.fair_shares(sizes, budget=budget)
        for size, share in zip(sizes, shares, strict=True):
            assert share <= size, f"budget={budget}: {share} > {size}"


def test_fair_shares_is_order_independent() -> None:
    """The same documents, re-ranked, must be allowed the same amounts --
    otherwise a retrieval re-rank silently changes how much of each document
    the model is shown.

    Measured before the fix, with the leftover chars handed out by position:
    sizes `[7, 9, 100]` at a budget of 11 returned `[4, 4, 3]`, while the
    reverse ordering returned `[3, 4, 4]`."""
    sizes = [7, 9, 100]
    reverse = list(reversed(sizes))
    for budget in range(0, 130):
        forward = prompt_budget.fair_shares(sizes, budget=budget)
        backward = list(reversed(prompt_budget.fair_shares(reverse, budget=budget)))
        assert forward == backward, f"budget={budget}: {forward} != {backward}"
        assert sum(forward) <= budget
