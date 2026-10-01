"""Deterministic within-run duplicate guard (#1230).

One ingest can emit two objects that are the same subject under different
titles -- two `Decision`s whose bodies quote the same source paragraph, two
`Procedure`s opening with the same sentence. The union judge keeps both
because it reads titles and descriptions, and `duplicates`/`adjudicate`
only catch the pair afterwards.

Two objects are collapsed only when ALL of these hold, so a bullet list of
distinct decisions (same type, similar titles, each quoting its own line)
is never touched:

1. the same `type`;
2. the titles are a LOW-confidence near-match under the shipped
   `similarity.is_near_match` (no new similarity measure);
3. both quote the SAME source line, per `evidence.evidence_line` (the
   object's own text, falling back to its description when the body is
   blank, as the builder does).

An object that quotes nothing is never collapsed: with no shared evidence
the pair is only a title resemblance, and a wrongly-merged pair is silent
data loss while a surviving near-duplicate is mergeable later.
"""

from dataclasses import dataclass

from openkos.extraction import evidence
from openkos.extraction.concept import ExtractionResult

from .normalize import normalize_key
from .similarity import is_near_match


@dataclass(frozen=True)
class RunCollapse:
    """Outcome of `collapse_run_duplicates`."""

    kept: list[ExtractionResult]
    """The surviving objects, in first-occurrence order."""
    collapsed: tuple[tuple[str, str], ...]
    """`(dropped_title, surviving_title)` per collapse, in encounter order."""


def _quoted_line(obj: ExtractionResult, source_text: str) -> str | None:
    line = evidence.evidence_line(obj.body.strip() or obj.description, source_text)
    return None if line is None else " ".join(line.casefold().split()).rstrip(".,;:!?")


def collapse_run_duplicates(
    objects: list[ExtractionResult], *, source_text: str
) -> RunCollapse:
    """Collapse same-run near-duplicates (see the module docstring).

    The survivor is the object with the longer body, a tie going to the
    first occurrence, and it keeps the first occurrence's slot -- the
    `_merge_union` rule, so output order stays deterministic. The whole
    object is kept, never field-mixed."""
    kept: list[ExtractionResult] = []
    quotes: list[str | None] = []
    collapsed: list[tuple[str, str]] = []
    for obj in objects:
        quote = _quoted_line(obj, source_text)
        match = None
        if quote is not None:
            for index, existing in enumerate(kept):
                if (
                    existing.type == obj.type
                    and quotes[index] == quote
                    and is_near_match(
                        normalize_key(existing.title), normalize_key(obj.title)
                    )
                ):
                    match = index
                    break
        if match is None:
            kept.append(obj)
            quotes.append(quote)
            continue
        incumbent = kept[match]
        if len(obj.body) > len(incumbent.body):
            kept[match] = obj
            collapsed.append((incumbent.title, obj.title))
        else:
            collapsed.append((obj.title, incumbent.title))
    return RunCollapse(kept=kept, collapsed=tuple(collapsed))
