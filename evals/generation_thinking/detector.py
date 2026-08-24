"""The repetition detector #830 proposes, as a pure function, so it can be
scored against real replies before anything is built on it.

#830's claim is precise and testable:

> That is the shape of a degenerate generation -- the model entering a
> repetition loop on a particular input -- not of a reply that is
> legitimately long. **Length is a proxy for it; repetition is the thing
> itself.**

This module implements "repetition" as the n-gram duplicate share the issue
names ("an n-gram repetition window over the streamed text"), and the probe
beside it scores that share on every reply the harness records. Nothing here
decides anything; it exists so the claim can be measured rather than
assumed.
"""

from __future__ import annotations

import re
from typing import Final

_WORD_RE: Final = re.compile(r"\S+")

DEFAULT_WINDOW: Final = 8
"""Words per n-gram.

Eight rather than a shorter window because a short one fires on ordinary
prose: `"the decision was made by the team"` is six words and recurs
honestly across sibling `Decision` objects. #830 names the false-cut count
as the number that kills a candidate, so the window starts where legitimate
repetition is least likely to reach.
"""


def repetition_share(text: str, window: int = DEFAULT_WINDOW) -> float:
    """Share of `text`'s `window`-word n-grams that are duplicates.

    `0.0` when every n-gram is distinct, approaching `1.0` as the text
    collapses into one repeated phrase. A text shorter than `window + 1`
    words scores `0.0`: there is no repetition to see, and inventing a
    score for it would put the shortest replies at whichever end of the
    ladder the arithmetic happened to fall.

    Deliberately computed over the reply CONTENT, which is what #830 says
    to stream and inspect. That choice is the measurement: see the README
    for what the content of a runaway reply turns out to contain.
    """
    if window < 1:
        # A non-positive window makes every n-gram the empty tuple, so any
        # text at all scores near 1.0 -- a confident maximum computed from
        # nothing. Refused rather than clamped, so a swept value that typo'd
        # its bound is a crash rather than a full column of false alarms.
        raise ValueError(f"window must be >= 1, got {window!r}")
    words = _WORD_RE.findall(text)
    if len(words) < window + 1:
        # `window + 1`, not `window`: a text of exactly `window` words holds
        # ONE n-gram, and one n-gram cannot repeat. Scoring it would divide
        # by a set of size 1 and return 0.0 anyway, but by arithmetic
        # accident rather than by rule -- and the boundary either side of
        # this line is pinned in the self-test.
        return 0.0
    grams = [tuple(words[i : i + window]) for i in range(len(words) - window + 1)]
    return 1.0 - len(set(grams)) / len(grams)
