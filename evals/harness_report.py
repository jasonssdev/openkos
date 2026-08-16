"""The one line every harness report carries to identify its arm.

#738 made the generation ceiling and the context window part of an arm's
identity rather than trivia -- they were unpinned before #691, so a stored run
that does not name them cannot be told apart from one measured under the old,
unbounded conditions. #740 put them into the human-readable report as well,
because the `runs-*.json` beside it is not what a person opens.

WHY THIS IS A MODULE AND NOT THREE F-STRINGS. #740 could only guard the line
by asserting that the two identifier NAMES appeared somewhere in each runner's
report-building source: the runners cannot be imported by the unit suite,
since every one of them mutates `sys.path` at module scope and imports a bare
`fixtures` module that only resolves because of it. So nothing executed the
f-string, and an unresolved name or a malformed format string would have
passed the suite and surfaced only after a paid eval run wrote the artifact.
#742 named this extraction the real fix.

This module therefore has NO `sys.path` surgery and NO imports beyond the
standard library, so `tests/unit/test_harness_report.py` can load it straight
from its path and render the line for real.

Kept deliberately small. The rest of each report -- the metric table, the
per-row tables -- genuinely differs between harnesses, and abstracting over
those differences would hide them.
"""

from __future__ import annotations

from collections.abc import Iterable

_SEPARATOR = " · "


def arm_identity_line(
    *,
    max_generation_tokens: int,
    context_window: int,
    extra: Iterable[str] = (),
) -> str:
    """The markdown line naming the settings this arm was measured under.

    `extra` is `Iterable[str]` rather than `Sequence[str]`: it is
    materialized once below, so a generator is accepted and rendered
    rather than silently drained. It carries already-formatted segments
    for harnesses whose identity
    needs more than the two universal fields -- `evals/ingest_concurrency/`
    adds the host and the server's `OLLAMA_NUM_PARALLEL`, without which one of
    its arms cannot be told apart from a silently serialized one. They are
    pre-formatted rather than typed because each harness bolds and quotes what
    matters to it, and a schema over that would be a bigger abstraction than
    the thing it describes.

    Returns ONE line: the result is spliced into a `lines` list that is later
    joined with newlines, so an embedded newline would silently become two
    report rows.

    Raises `ValueError` on a bare string, an empty segment, or a segment
    carrying a newline. All three render something plausible and wrong into a
    measurement artifact nobody re-reads, which is the failure mode this
    module exists to stop:

    - a bare `str` SATISFIES `Sequence[str]`, so `extra="host \\`h\\`"` would be
      iterated character by character and every character would become its own
      separator-joined segment;
    - an empty segment renders a doubled separator, and a caller building
      segments conditionally is the likely source;
    - a line break splits this into two report rows, contradicting the
      one-line guarantee the moment the caller joins the list. Carriage
      return counts: Python's own line splitting treats a lone `\\r` as a
      break, so checking only `\\n` would let one through.
    """
    if isinstance(extra, str):
        raise ValueError(
            "extra must be a sequence of segments, not a bare string: a `str` "
            "satisfies `Sequence[str]` and would be split into one segment "
            "per character"
        )
    # Materialized once: `extra` is read twice below, and a generator would
    # be drained by the validation loop and then splat to nothing, silently
    # dropping every caller-supplied field from the line.
    segments_extra = tuple(extra)
    for index, segment in enumerate(segments_extra):
        if not isinstance(segment, str):
            raise ValueError(
                f"extra segment {index} is {type(segment).__name__}, not str; "
                "the documented contract is ValueError for every malformed "
                "segment, and `.strip()` would raise AttributeError instead"
            )
        if not segment.strip():
            raise ValueError(
                f"extra segment {index} is empty; it would render a doubled "
                f"{_SEPARATOR!r} separator"
            )
        if "\n" in segment or "\r" in segment:
            raise ValueError(
                f"extra segment {index} contains a line break; it would render as "
                "two report rows, and this function promises one line"
            )

    segments = [
        f"Generation ceiling `{max_generation_tokens}`",
        f"context window `{context_window}`",
        *segments_extra,
    ]
    return _SEPARATOR.join(segments) + "."
