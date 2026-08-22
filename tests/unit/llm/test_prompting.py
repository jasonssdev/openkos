"""Unit tests for `openkos.llm.prompting`: the pinned rationale-language
clause (#812) that both curate rationale prompts append.

The seam tests -- that the clause actually reaches each suggester's system
turn, and that an unset language assembles the pre-#812 bytes -- live in
`tests/unit/resolution/test_rationale_language.py`, against both prompts
under one parametrize. What is guarded HERE is the leaf's own contract: it
is the single place every caller funnels through, so it is where "pinned
means a language was actually named" has to hold.

WHY THE LEAF AND NOT EACH CALLER. `with_rationale_language` branches on
`language is None`, so before #812's review every other non-`None` value
counted as pinned -- including `""`. `read_config` never produces one (it
refuses blank and strips padding), but it is not the only entry point:
`evals/edge_typing/run_edge_typing_eval.py` hands `args.rationale_language`
straight through, so `--rationale-language ""`, or a shell expansion of an
unset variable, reached this function as a pinned arm. Guarding the runner
would have fixed that one caller and left the next one -- a second harness,
a library caller, a notebook -- free to build the same degenerate clause.
The guard therefore sits at the leaf, where it costs one check and covers
every path, and where the failure is loud at the point the prompt is built
rather than silent in a report that says an arm was pinned to nothing.
"""

import pytest

from openkos.llm import prompting

_SYSTEM = "System rubric.\n\nSecond paragraph."


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t"])
def test_a_blank_pinned_language_is_refused(blank: str) -> None:
    """A pinned language that names no language raises, never builds.

    The alternative -- returning the clause with an empty slot -- is the
    worst outcome available: `Write the "rationale" in .` is a real
    instruction the model will try to follow, the arm would be LABELLED
    pinned in `results/`, and it would be indistinguishable afterwards from
    a baseline run that simply spent fewer tokens."""
    with pytest.raises(ValueError, match="rationale_language"):
        prompting.with_rationale_language(_SYSTEM, blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_the_refusal_names_the_value_it_refused(blank: str) -> None:
    """`got ''` -- the repr, matching `read_config`'s own message shape.

    A blank value is invisible in an error that only says "blank": the
    operator's shell expanded a variable to nothing, and the repr is the
    one rendering that shows it."""
    with pytest.raises(ValueError, match="rationale_language") as excinfo:
        prompting.with_rationale_language(_SYSTEM, blank)

    assert repr(blank) in str(excinfo.value)


def test_a_named_language_still_appends_its_clause() -> None:
    """The guard refuses blanks and nothing else.

    Pinned as the complement of the two tests above: a refusal that also
    swallowed real values would make every one of them pass while removing
    the feature."""
    assert prompting.with_rationale_language(_SYSTEM, "Spanish") == (
        f'{_SYSTEM}\n\nWrite the "rationale" in Spanish.'
    )


def test_an_unset_language_returns_the_prompt_itself() -> None:
    """`None` is not blank, and must stay the identity branch.

    The byte-identity promise is structural -- the same object back, not a
    rebuilt equal string -- and the new guard must not move that check to
    after the branch, where `None.strip()` would raise on the one path that
    has to be untouched."""
    assert prompting.with_rationale_language(_SYSTEM, None) is _SYSTEM
