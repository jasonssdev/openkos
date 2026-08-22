"""Which headed sections of a source no derived object reproduces a line of
(issue #793). **MEASURED AND NOT SHIPPED** -- see the verdict below.

This lives under `evals/` rather than in `src/openkos/` for one reason: the
measurement refuted it. It is kept because the refutation is reusable and
the next attempt at #793 should not have to rediscover it, exactly as
`evals/judge_cold_start/` keeps the harness that falsified the cold-start
explanation.

## What it does

The source-side companion to shipped `extraction/evidence.py`. The two
answer opposite halves of one question:

- `evidence.evidence_line` asks, of an OBJECT, *does this quote anything?*
  An object that quotes nothing cannot support a citation (#801, shipped).
- `uncovered_sections` asks, of a SECTION, *did anything come out of this?*

The covering test is `evidence.evidence_line` unchanged, with the SECTION as
the source, so "quoted" would have meant one thing across both signals.

## Why it is not shipped

That reuse is also what killed it. `evidence_line` tests VERBATIM quoting,
and extraction over discursive text PARAPHRASES. Measured over `qwen3:8b`:

| source | uncovered share of checkable text | sections flagged |
| --- | --- | --- |
| `helios-overview`, 5 ok runs | 0.0% every run | 0 of 4 |
| `kickoff`, 4 ok runs | 0.0% every run | 0 of 4 |
| the failure #793 reports | 62.0% | 2 of 4 |
| a real 9-heading transcript, 3 runs | **98.0%, 31.3%, 97.6%** | **7, 6, 7 of 8** |

An ordinary meeting transcript scores HIGHER than the defect the signal was
built to catch, so no threshold separates them -- the distributions do not
merely overlap, they invert.

The mechanism, hand-checked: that transcript's `## Resumen` reads *"El
equipo definió el alcance del sistema y acordó usar minutas reales para
validar la arquitectura propuesta."* The run produced `Decision: Uso de
Minutas Reales para Validación`, which plainly covers it, and
`evidence_line` answers `None`. The section is flagged on a correct
extraction.

It works on terse, declarative, bullet-shaped sources, where extraction does
quote. It fails on meeting transcripts, which is the corpus openkos is for,
and nothing tells the two apart in advance.

## What a next attempt would have to change

The covering PREDICATE, not the aggregation. Both aggregations were tried
and neither helps: counting sections floods a 44-heading transcript, and
weighting by text (the table above) inverts. A fuzzy predicate -- token
overlap, embedding similarity, or asking the model -- is a different signal
needing its own calibration, and the fixtures and probe here are set up to
measure it.

Config-free: never imports `openkos.config` and never imports
`extraction.concept`, taking and returning plain strings.
"""

import re
from dataclasses import dataclass
from typing import Final

from openkos.extraction import evidence as evidence_mod

_HEADING_RE: Final = re.compile(r"^\s{0,3}#{1,6}\s+\S")
"""A line that OPENS a section: an ATX heading at the start of its line.

Anchored deliberately. A `#` inside a line is ordinary prose -- an issue
reference like `#793`, a colour, a comment marker in a quoted snippet -- and
treating one as a heading would split a paragraph into two sections, neither
of which a reader could find in the source by the name reported to them.

Up to three leading spaces because that is what CommonMark allows before an
ATX heading; a fourth makes it an indented code block, which is content
rather than structure. The trailing `\\S` requires the heading to name
something: `##` alone delimits nothing a report could refer to.
"""

PREAMBLE_HEADING: Final = "(preamble)"
"""How a section of body text that precedes the first heading is named.

Such a section is real -- a source that opens with prose and only later uses
headings keeps its opening paragraph here -- but it has no heading line to
be named by, and inventing one from the source's title would collide with a
genuine `# Title` section. The parenthesis marks it as a description rather
than text to grep for.
"""


@dataclass(frozen=True)
class Section:
    """One heading-delimited span of a source.

    `heading` is the heading LINE verbatim, `#` markers included, so a
    reader handed the name can grep the source for it and land on the
    section. `body` excludes that line: the heading is the section's name,
    not its content, and letting it count as content would let a section be
    "covered" by an object that merely echoed its title -- which is the
    restatement failure #585 and #801 already flag, arriving through a third
    door.
    """

    heading: str
    body: str


def split_sections(source_text: str) -> list[Section]:
    """`source_text` divided at its headings, in source order.

    Every heading opens a section regardless of level. Nesting is
    deliberately ignored: an `##` under an `#` is reported as its own
    section rather than folded into its parent, because the question this
    supports is "did anything come out of this stretch of the document",
    and a reader looking for the lost content wants the narrowest heading
    that names it. Rolling `## Storage` up into `# Overview` would let one
    object anywhere in the document mark the whole source covered.
    """
    sections: list[Section] = []
    heading = PREAMBLE_HEADING
    body: list[str] = []
    for line in source_text.splitlines():
        # `search` against an anchored pattern, not `match`: `match` anchors
        # implicitly, which would make the `^` redundant and leave the
        # anchor unkillable by any test -- a guard no test can fail is a
        # guard nobody can trust. One anchor, spelled where it is read.
        if _HEADING_RE.search(line):
            sections.append(Section(heading=heading, body="\n".join(body)))
            heading = line.strip()
            body = []
        else:
            body.append(line)
    sections.append(Section(heading=heading, body="\n".join(body)))
    # A leading preamble section exists only if something preceded the first
    # heading. Dropping the empty one here rather than skipping it at the
    # call site keeps "the sections of this source" meaning the same thing
    # to every caller.
    if (
        sections
        and sections[0].heading == PREAMBLE_HEADING
        and not sections[0].body.strip()
    ):
        sections = sections[1:]
    return sections


@dataclass(frozen=True)
class CoverageReport:
    """What one source's sections did, weighed by text.

    `uncovered` names the headings, in source order, ONE ENTRY PER
    UNCOVERED SECTION -- so a source with two `## Notes` sections, both
    uncovered, reports the heading twice. That is not a cosmetic choice:
    section weights must never round-trip through a dict keyed by heading,
    because headings are not unique. Doing so collapsed two sections into
    one entry and then charged the survivor's length once per occurrence,
    inflating `checkable_chars` to 230 where the truth was 135, silently and
    without a crash, in exactly the repeated-`## Notes` shape a meeting
    transcript produces. Both totals are accumulated in ONE pass over
    `split_sections`, so each section contributes its own length exactly
    once and the collision has nowhere to occur.
    """

    uncovered: tuple[str, ...]
    uncovered_chars: int
    checkable_chars: int

    @property
    def uncovered_share(self) -> float:
        """`uncovered_chars / checkable_chars`, or 0.0 when nothing is
        checkable.

        Counting SECTIONS treats a one-line `## Notes` and a four-paragraph
        `## Decisions` as equals, which is why a 44-heading transcript looks
        catastrophic under a count. #793's own complaint is about text --
        "half the document was not represented" -- so this is the quantity
        it names.

        A zero denominator answers 0.0, meaning "nothing to say", matching
        what an empty `uncovered` means on the same source. It is NOT a
        claim of full coverage.
        """
        return (
            self.uncovered_chars / self.checkable_chars if self.checkable_chars else 0.0
        )


def coverage_report(
    texts: list[str] | tuple[str, ...], source_text: str
) -> CoverageReport:
    """Which sections of `source_text` no entry of `texts` quotes, and how
    much of the source's checkable text that is.

    `texts` are the written texts of the objects derived from this source --
    the same strings `evidence.evidence_line` is asked about, so the two
    signals cannot disagree about what one object contributed.

    A section is COVERED when some text quotes some line of it, using
    `evidence.evidence_line` unchanged, with the SECTION as the source. That
    reuse is the whole point and not an optimisation: "quoted" has to mean
    one thing across both signals, including the reverse direction (an
    object that carries the source line and continues past it is quoting)
    and the four-word floor. An object credited with covering a section it
    only paraphrased would make this signal disagree with the one #801
    already ships, on the same object, in the same run.

    One object can cover SEVERAL sections, and each is cleared. Objects are
    not attributed to a single section: extraction merges freely across a
    document, and forcing a one-to-one attribution would falsely report the
    sections an object quoted second.

    A section with no line clearing the evidence floor is skipped entirely,
    entering neither total. It could not be covered by any object no matter
    how good the extraction, so flagging it would be a finding nothing can
    clear -- the vacuity the floor in `evidence.py` exists to prevent,
    re-entering through the section list. On a source whose every section is
    that thin this returns nothing uncovered and a zero denominator, and
    that means "nothing to say", never "fully covered".

    A blank or whitespace-only text is filtered by nothing here on purpose:
    `evidence.evidence_line` already answers `None` for it, and a second
    guard spelling the same fact would be one no test could distinguish
    from its absence.
    """
    uncovered: list[str] = []
    uncovered_chars = 0
    checkable_chars = 0
    for section in split_sections(source_text):
        if not is_quotable(section.body):
            continue
        length = len(section.body.strip())
        checkable_chars += length
        if not any(
            evidence_mod.evidence_line(text, section.body) is not None for text in texts
        ):
            uncovered.append(section.heading)
            uncovered_chars += length
    return CoverageReport(
        uncovered=tuple(uncovered),
        uncovered_chars=uncovered_chars,
        checkable_chars=checkable_chars,
    )


def uncovered_sections(
    texts: list[str] | tuple[str, ...], source_text: str
) -> tuple[str, ...]:
    """The headings `coverage_report` reports as uncovered -- the naming
    half alone, for callers that do not need the weights."""
    return coverage_report(texts, source_text).uncovered


def is_quotable(body: str) -> bool:
    """Whether `body` holds any line an object could demonstrably quote.

    Asked of the section's own body against itself: a line qualifies here on
    exactly the terms `evidence.evidence_line` would need to match it, so
    the two cannot drift into disagreeing about which sections are checkable.
    """
    return evidence_mod.evidence_line(body, body) is not None
