"""Shared prompt-assembly fragments for LLM chat seams (issue #812).

This module is a leaf: stdlib `typing` only, no import of `openkos.config`
or any other `openkos` module -- the same constraint `openkos.llm.base` and
`openkos.llm.parsing` hold, and the reason the two resolution suggesters
can use it without either of them growing a `config` import.

The prompt SIDE of what `parsing.py` does for replies, and it exists for
the same recorded reason: `parsing.py`'s docstring describes five seams
that each carried a byte-identical module-local copy of one behaviour, and
resolves that by exposing a public function they all share. A language
clause that must read identically in `resolution.edge_typing` and
`resolution.volatility_typing` is exactly that shape -- two copies of one
sentence, free to drift the day one of them is reworded.
"""

from typing import Final

RATIONALE_LANGUAGE_TEMPLATE: Final = 'Write the "rationale" in {language}.'
"""The clause appended to a rationale prompt's system turn when a workspace
pins `rationale_language` (issue #812).

Worded after `extraction.concept._LANGUAGE_ANCHOR`, this repo's one
precedent for pinning an output language: name the JSON field in quotes,
say it in one imperative sentence, and say nothing else. That anchor's
scoping rationale is followed too -- it is added ONLY on the path an
operator explicitly asked for, never to the default one, because a longer
prompt on a common path has already been measured here to lose its A/B.

`{language}` is interpolated verbatim from config. The engine has no
language registry to map it through, so the clause is only ever as good as
the name the operator typed -- see `config.Config.rationale_language` for
what that costs when the name is wrong."""


def with_rationale_language(system_prompt: str, language: str | None) -> str:
    """Return `system_prompt` with the pinned-language clause appended, or
    `system_prompt` ITSELF when `language` is `None`.

    The `None` branch returns the argument object unchanged rather than a
    rebuilt equal string, so "an unpinned workspace sends the pre-#812
    bytes" is a structural property of this function and not a promise a
    later edit could quietly weaken (pinned by
    `tests/unit/resolution/test_rationale_language.py`).

    Appended, never interleaved: the pinned arm of a measurement must
    differ from the baseline arm by exactly this clause, or a quality
    movement cannot be attributed to it.

    A BLANK `language` raises `ValueError` rather than pinning nothing.
    `None` and `"pinned"` are the only two states this function has, and
    `""` was silently a third: it built `Write the "rationale" in .`, an
    instruction the model would try to follow, under an arm labelled
    pinned. The check lives here, at the leaf every caller funnels through,
    rather than at each caller: `config.read_config` already refuses blank
    and strips padding, but it is not the only entry point --
    `evals/edge_typing/run_edge_typing_eval.py` forwards its `--rationale-
    language` argument straight through, so an empty string, or a shell
    expansion of an unset variable, arrives here as a pinned arm. One check
    at the leaf covers that runner and the next caller alike."""
    if language is None:
        return system_prompt
    if not language.strip():
        raise ValueError(f"rationale_language must name a language, got {language!r}")
    return f"{system_prompt}\n\n{RATIONALE_LANGUAGE_TEMPLATE.format(language=language)}"
