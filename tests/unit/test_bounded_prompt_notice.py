"""The bounded-prompt advisory names the calls that read an excerpt (#866).

The bound itself is pinned in `tests/unit/extraction/test_prompt_bounding.py`;
these pin the rendering. Advisory only: nothing degraded -- the whole point
of the bound is that the model kept its instructions -- but a call that read
an even-coverage excerpt instead of the full source is a fact about this
run's quality an operator must be able to see, per the same rule that
surfaces a recovered judge retry.
"""

from openkos.cli.main import _bounded_prompt_notice
from openkos.extraction.concept import (
    BOUNDED_CALL_JUDGE,
    OPTIONAL_CALL_PARTICIPANT_CAPTURE,
    OPTIONAL_CALL_REASK,
    ExtractionReport,
)


def test_no_bounded_calls_renders_nothing() -> None:
    assert _bounded_prompt_notice(ExtractionReport(retained=3)) is None


def test_every_bounded_call_is_named_in_spend_order() -> None:
    notice = _bounded_prompt_notice(
        ExtractionReport(
            retained=3,
            bounded_prompt_calls=(
                OPTIONAL_CALL_REASK,
                OPTIONAL_CALL_PARTICIPANT_CAPTURE,
                BOUNDED_CALL_JUDGE,
            ),
        )
    )

    assert notice is not None
    assert OPTIONAL_CALL_REASK in notice
    assert OPTIONAL_CALL_PARTICIPANT_CAPTURE in notice
    assert BOUNDED_CALL_JUDGE in notice
    assert notice.index(OPTIONAL_CALL_REASK) < notice.index(BOUNDED_CALL_JUDGE)


def test_the_notice_says_the_source_outgrew_the_window() -> None:
    """The line must name the CAUSE (source larger than the context window)
    and the REMEDY-shaped fact (an even-coverage excerpt was read), not just
    that something was 'bounded' -- an operator reading it decides whether
    to raise `context_window`, and needs both halves to decide."""
    notice = _bounded_prompt_notice(
        ExtractionReport(retained=3, bounded_prompt_calls=(BOUNDED_CALL_JUDGE,))
    )

    assert notice is not None
    assert "context window" in notice
    assert "excerpt" in notice
