"""Which headed sections of a source no derived object covers (issue #793).
**THE SHIPPED-BASELINE PREDICATE WAS MEASURED AND REFUTED** -- see below.

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

## Why the `quote` predicate is not shipped

Its covering test is `evidence.evidence_line` unchanged, with the SECTION as
the source, so "quoted" meant one thing across both signals. That reuse is
also what killed it. `evidence_line` tests VERBATIM quoting, and extraction
over discursive text PARAPHRASES. Measured over `qwen3:8b`:

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

## The seam a next attempt needs: a PREDICATE, not an aggregation

Both aggregations were tried and neither helps: counting sections floods a
44-heading transcript, and weighting by text (the table above) inverts. So
the covering test itself is what has to change, and `CoveragePredicate`
makes it pluggable.

The trap that shape exists to close: `coverage_report` used
`evidence_line` for TWO jobs at once -- the covering test AND the
checkability gate that skips a section no object could ever quote. Swap only
the first and the denominators stop meaning the same thing, so a new column
would not be comparable to the committed ones while looking like it was. A
predicate is therefore a NAMED PAIR, and each pair carries its own gate.

`quote` is the refuted baseline, preserved byte-for-byte behind the seam so
the numbers above stay reproducible. `overlap` is a candidate that has since
been swept over a threshold ladder and DOES separate -- the reported failure
high, healthy runs low -- but at B in [0.20, 0.25] rather than at the 0.5
`OVERLAP_COVERED_FRACTION` still holds, on 17 runs of one model, with the
window selected from two of the three arms it is reported from. That is a
measured window, not a validated default, and nothing here ships. The
constant's docstring carries the ladder; the README carries the arms and the
gap that has to close first.

Every point on that ladder is `overlap_predicate(B)`, which names the
predicate after the threshold it used. Sweeping by rebinding the constant
would print every rung under one column header, and the number would stop
being attributable to the value that produced it -- the same failure
`CoverageReport` refuses by carrying no predicate name at all.

Config-free: never imports `openkos.config` and never imports
`extraction.concept`, taking and returning plain strings.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
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
class CoveragePredicate:
    """A named PAIR: one covering test and the checkability gate that
    belongs to it. Never a bare function, and this is the whole point of
    the type.

    ## The invariant every pair must hold

    **A section this predicate cannot SCORE MEANINGFULLY is skipped, and
    enters neither total.**

    `checkable(body)` answers False for exactly those sections, and
    `coverage_report` then leaves them out of `uncovered`, out of
    `uncovered_chars` and out of `checkable_chars` alike. Two different
    failures are being prevented at once:

    - A finding nothing can clear. `quote` cannot cover a section holding
      no line that meets `evidence.py`'s four-word floor, no matter how
      good the extraction, so flagging one would report unclearable debt --
      the vacuity that floor exists to prevent, re-entering through the
      section list.
    - A number that is not a measurement. `overlap` on a two-content-word
      section can only ever score 0.0, 0.5 or 1.0, so the flag it produces
      is a coin toss on two words rather than a reading of coverage.

    The second is a WIDENING of the first, from "could never clear" to
    "could never score", and a reader comparing predicates should know it:
    a section `overlap` skips is not always one `overlap` could have
    failed on.

    ## Why the gate travels WITH the covering test

    `coverage_report` originally used `evidence_line` for both jobs. Swap
    the covering test alone -- to token overlap, to embedding similarity,
    to asking the model -- and the gate keeps asking "does this section
    contain a quotable line", which is a question about a DIFFERENT
    predicate. `checkable_chars` then measures one thing while `uncovered`
    measures another, the share stops being a share of what was tested,
    and the new column is silently not comparable to the committed ones.
    Pairing them in one frozen object is what makes that mistake require
    editing this file rather than merely calling it.

    `covers(texts, body)` is asked of ALL the object texts at once rather
    than one at a time, because a predicate is free to pool them: `quote`
    happens to be an `any()` over the texts, `overlap` unions their words,
    and forcing the one-at-a-time shape would have ruled the second out.
    """

    name: str
    covers: Callable[[Sequence[str], str], bool]
    checkable: Callable[[str], bool]
    describe: str
    covers_by_quoting: bool = False
    """Whether `covers` IS `evidence.evidence_line` -- the same rule
    `quoting_objects` attributes with.

    Declared rather than detected, because it decides whether a whole arm
    of this harness means anything. `leave_one_section_out` builds a loss by
    deleting the objects that QUOTE a section; if the covering test is that
    same rule, the section is uncovered afterwards by construction and every
    trial is a hit. The arm's first table printed `quote` at 100.0% over 36
    trials, and that number measured nothing.

    A predicate cannot be asked at runtime which rule it is -- `covers` is
    an opaque callable, and a probe that inferred the answer from behaviour
    would be guessing about the one thing that must not be guessed. So the
    predicate declares it, `leave_one_section_out` refuses on it, and adding
    a third predicate built on `evidence_line` requires setting this flag
    rather than remembering a caveat."""


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

    A report carries no predicate name. Which predicate produced it is the
    caller's to track and to LABEL, because the failure this file guards
    against is two predicates' numbers being read in one column.
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


# --------------------------------------------------------------------------
# Predicate 1 of 2: `quote` -- the shipped baseline, measured and refuted.
# --------------------------------------------------------------------------


def is_quotable(body: str) -> bool:
    """Whether `body` holds any line an object could demonstrably quote.

    `quote`'s checkability gate, and the reason it is asked of the section's
    own body against itself: a line qualifies here on exactly the terms
    `evidence.evidence_line` would need to match it, so the gate and the
    covering test cannot drift into disagreeing about which sections are
    checkable. That self-application IS the invariant on
    `CoveragePredicate`, spelled in the sharpest way this predicate allows:
    if a section cannot cover ITSELF, no object can cover it either.
    """
    return evidence_mod.evidence_line(body, body) is not None


def _quote_covers(texts: Sequence[str], body: str) -> bool:
    """Whether any of `texts` quotes a line of `body`.

    `evidence.evidence_line` unchanged, with the SECTION as the source.
    That reuse is the whole point of this predicate and not an
    optimisation: "quoted" has to mean one thing across both signals,
    including the reverse direction (an object that carries the source line
    and continues past it is quoting) and the four-word floor. An object
    credited with covering a section it only paraphrased would make this
    signal disagree with the one #801 already ships, on the same object, in
    the same run.

    A blank or whitespace-only text is filtered by nothing here on purpose:
    `evidence.evidence_line` already answers `None` for it, and a second
    guard spelling the same fact would be one no test could distinguish
    from its absence.
    """
    return any(evidence_mod.evidence_line(text, body) is not None for text in texts)


QUOTE: Final = CoveragePredicate(
    name="quote",
    covers=_quote_covers,
    checkable=is_quotable,
    describe="verbatim quoting, shipped extraction/evidence.py -- REFUTED (#793)",
    covers_by_quoting=True,
)
"""The predicate the committed numbers were measured under.

Kept unchanged, and pinned in the probe's self-test against
`results/runs-20260821T233809Z-qwen3-8b.json`, because it is the only thing
that makes a new column comparable to an old one. A refactor that quietly
altered it would invalidate the refutation this directory exists to record.
"""


# --------------------------------------------------------------------------
# Predicate 2 of 2: `overlap` -- measured, and REFUTED (2026-08-23). It
# separated on one model and does not survive a second: see the README's
# "The window does not survive a second model".
# --------------------------------------------------------------------------

OVERLAP_COVERED_FRACTION: Final = 0.5
"""The share of a section's distinct content words that must appear in the
objects for `overlap` to call it covered.

**This VALUE is still the original placeholder, and the measurement did not
land on it.** A threshold ladder over `qwen3:8b` (README, *Predicate 2*) put
the separating window at **B in [0.20, 0.25]**, not at 0.5. The value is
left at 0.5 deliberately: moving it is a shipping decision nobody has taken,
and the probe's self-test pins the README's `## Resumen` example against
this named constant, so a change here has to be made on purpose rather than
discovered in a sweep.

What the window means, and what it does NOT mean:

- Below it the reconstructed #793 failure is not named at all -- at 0.20 all
  five ablated runs name BOTH `## Storage` and `## Components`; at 0.15 none
  of the five names both, and at 0.10 the share collapses to ~0%.
- Above it a healthy discursive transcript explodes: 8 runs go from <=5%
  uncovered at 0.20 to 92% on most runs by 0.30-0.50, which is `quote`'s
  own failure mode arriving late.
- The window was **SELECTED by sweeping the same two arms it is reported
  from.** Only the third arm -- `kickoff`, 0% uncovered at every value in
  and around the window -- is genuinely out of sample, and only for
  over-firing. That makes this a MEASURED window, not a validated default.

**And that third arm did not survive a second model.** On `phi4:14b` the
same `kickoff` fixture OVER-FIRES at every rung including 0.15, with
`## Context` flagged in 60% of runs while it produced objects. There is no
value of this constant at which that model's healthy runs stay quiet, so
the window is not a property of the predicate -- it is a property of
`qwen3:8b` on two files. `overlap` is refuted on the criterion this
directory fixed before either predicate was written.

`OVERLAP_MIN_CONTENT_WORDS` was held at 4 for every point on that ladder.
It is swept now, and what it showed is that raising it does not improve the
signal, it deletes the sections the signal was failing on.

The ladder is not folklore: `overlap_predicate(threshold)` builds the
predicate for any point on it, and the README names the one command that
regenerates the published table from the committed sweep with no model calls.
"""

OVERLAP_MEASURED_WINDOW: Final = (0.20, 0.25)
"""The window the README's ladder measured on `qwen3:8b`, as `(low, high)`
inclusive. **It did not survive a second model, and is kept as a record of
what was measured rather than as a recommendation.**

Named here rather than written into prose twice, so `overlap_predicate`'s
`describe` -- which tells a reader whether the value in front of them is
inside it -- cannot drift from the README's own bounds. It is a MEASURED
window and not a default: `OVERLAP_COVERED_FRACTION` deliberately sits
outside it, and that constant carries why.

Not deleted along with the refutation, and not widened to swallow it. A
reader arriving at a `overlap@0.22` column needs to know that value was
once the best candidate and on which evidence, or the refutation reads as
though nobody ever had a reason. `describe` says `inside` for such a value
and then says it is refuted anyway; both halves are true and the second
does not make the first uninteresting.
"""

OVERLAP_MIN_CONTENT_WORDS: Final = 4
"""**UNCALIBRATED.** The number of DISTINCT content words a section must
carry before `overlap` will score it at all -- this predicate's
checkability gate.

Below it the fraction is not a measurement: a three-content-word section
can only score 0.0, 1/3, 2/3 or 1.0, so `OVERLAP_COVERED_FRACTION` is
decided by one word landing either side of a line, and a heading like
`## Notas` over `Pendiente.` would enter the totals on that basis.

Four rather than a figure chosen for feel: it echoes
`evidence._MIN_EVIDENCE_WORDS`, the floor `quote`'s own gate inherits, so
the two gates start from the same order of magnitude and a difference
between the columns is easier to attribute to the covering tests than to
mismatched thresholds. It is DISTINCT words, matching the fraction's own
denominator, so the gate reads as exactly "too few content words for the
fraction to mean anything".

That echo is a starting point and nothing measured it. Four distinct
content words is roughly an eight-word sentence, which is coarser than
`quote`'s per-LINE floor: the two gates do not skip the same sections, and
they are not supposed to -- see `CoveragePredicate`.

It stays UNCALIBRATED after the threshold ladder in the README, because
that ladder swept `OVERLAP_COVERED_FRACTION` alone and held this gate at 4
throughout. Every number published about `overlap` is a number about this
gate's 4, and none of them tests it.
"""

_TOKEN_RE: Final = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
"""A word: a run of letters or digits, hyphenated compounds kept whole.

Applied to text that `_fold` has already casefolded and stripped of
combining marks, so the class is decided on the folded form.

DIGITS ARE KEPT, deliberately. `MySQL 8`, `PostgreSQL 16`, `2026-03-15` --
the version and the date are exactly the facts #793 complains about losing,
and dropping numerals would make the predicate blind to the sections most
worth checking. Hyphens are kept inside a token for the same reason: a
split `2026-03-15` matches any document that mentions the year.

Underscore is excluded (`[^\\W_]` rather than `\\w`) because it is a
markup character in this corpus -- `__bold__`, snake_case identifiers
pasted into minutes -- not a letter joining two words.

Everything else is punctuation and drops out, which also disposes of
markdown markers: `-`, `*`, `>`, `#` and `|` cannot start a token. The
heading line is already excluded from `Section.body`, so no heading text
reaches here in the first place.
"""


def _fold(value: str) -> str:
    """`value` casefolded and stripped of combining marks.

    Accent-aware in the direction this corpus needs: `Información`,
    `informacion` and `INFORMACIÓN` all fold to `informacion`. Extraction
    output drops and mangles diacritics constantly -- it is generated text,
    not a copy -- and a predicate that treated an accent-stripped
    reproduction as a different word would report a faithful paraphrase as
    uncovered, which is the exact false positive that killed `quote`.

    The cost, stated rather than hidden: folding is indiscriminate, so `ñ`
    becomes `n` and `año`/`ano` and `campaña`/`campana` merge. That is
    accepted here because the failure it prevents is SYSTEMATIC (every
    accented word in every Spanish section) while the failure it introduces
    needs both members of a specific pair to meet inside one comparison.
    The alternative -- a whitelist of protected letters -- is a rule whose
    effect on any real measurement would be far below the noise a 17-run,
    one-model ladder over the constants above already carries.

    Casefold BEFORE decomposing: `İ` and `ß` need the casefold pass first,
    and running it afterwards would leave their marks to be dropped as
    though they were accents.
    """
    return "".join(
        char
        for char in unicodedata.normalize("NFD", value.casefold())
        if not unicodedata.combining(char)
    )


_STOPWORD_SOURCE: Final = (
    # Spanish. Written with their accents and folded at build time below, so
    # a future entry cannot be added in a form the tokenizer never produces.
    "a al algo algunas algunos ante antes aquel aquella aquello aquí así aun "
    "aunque cada como con contra cual cuales cuando de del desde donde dos e "
    "el ella ellas ello ellos en entre era eran es esa esas ese eso esos esta "
    "estaba están estas este esto estos fue fueron ha había han hasta hay la "
    "las le les lo los más me mi mientras mis mucho muy ni no nos nosotros "
    "nuestra nuestro o otra otras otro otros para pero poco por porque que "
    "quien se según ser si sido sin sobre son su sus también tan tanto te "
    "tiene tienen toda todas todo todos tras tu un una unas uno unos y ya yo "
    # English.
    "about after all also an and any are as at be been before being both but "
    "by can could did do does done during each few for from had has have he "
    "her here hers him his how i if in into is it its just may me might more "
    "most must my no nor not of on once only or other our ours out over own "
    "per said same she should so some such than that the their theirs them "
    "then there these they this those through to too under until up us very "
    "was we were what when where which while who whom why will with would "
    "you your yours"
)
"""The function words both languages of this corpus contribute.

ONE union list, not two selected by a detected language, and that is a
decision with a cost worth naming. Detecting the language of a section is
its own unreliable signal, and meeting minutes here mix the two inside a
single paragraph -- an English tool name in a Spanish sentence, an English
`## Action items` heading over Spanish bullets. A union needs no detector.

What the union costs: a handful of words that are content in one language
and function in the other are dropped from both -- English `son`, `me`,
`no`, `a`, `he`, `us`, `will`, `can`; Spanish `he`, `van`. Each is a word
whose presence or absence would rarely decide a section's fraction on its
own, and paying that to delete a language detector is the trade taken here.
"""

_STOPWORDS: Final[frozenset[str]] = frozenset(
    _fold(word) for word in _STOPWORD_SOURCE.split()
)
"""`_STOPWORD_SOURCE` in the exact form `content_words` will look tokens up
in -- folded through the same function, so the two cannot drift."""


def _is_content(token: str) -> bool:
    """Whether a folded token counts toward a section's content.

    A stopword does not. Neither does a lone letter: `team's` tokenizes to
    `team` and `s`, an initial in `M. Ruiz` tokenizes to `m`, and a
    lettered list marker `a)` to `a` -- all morphological or layout debris
    that would appear in almost any text and inflate the intersection
    without carrying a fact.

    A lone DIGIT is kept, for `MySQL 8`. That asymmetry is the point of
    spelling this as a function rather than a length check.
    """
    if token in _STOPWORDS:
        return False
    return not (len(token) == 1 and not token.isdigit())


def content_words(text: str) -> frozenset[str]:
    """The DISTINCT content words of `text`, folded.

    Distinct, not a multiset, and the fraction below inherits that
    denominator. Counting occurrences would let one repeated word carry a
    section: a `## Decisions` block naming `Helios` in every bullet would
    score covered on an object that mentioned `Helios` once. #793's
    question is which FACTS survived extraction, not which words were
    frequent.
    """
    return frozenset(
        token for token in _TOKEN_RE.findall(_fold(text)) if _is_content(token)
    )


def overlap_fraction(texts: Iterable[str], body: str) -> float:
    """The share of `body`'s distinct content words that appear anywhere in
    `texts`.

    The objects are UNIONED before the comparison, not tested one at a
    time. A section is routinely split across several objects -- a
    `## Decisions` block gives one Decision per bullet -- and asking each
    object to clear the threshold alone would flag exactly the sections
    extraction handled best.

    Answers 0.0 for a body with no content words rather than dividing. That
    body is one `OVERLAP.checkable` has already skipped, so the value is
    never read as a score; it exists so this function is total and can be
    called by hand while calibrating.
    """
    section = content_words(body)
    if not section:
        return 0.0
    produced: frozenset[str] = frozenset()
    for text in texts:
        produced |= content_words(text)
    return len(section & produced) / len(section)


def overlap_predicate(
    threshold: float = OVERLAP_COVERED_FRACTION,
    min_content_words: int = OVERLAP_MIN_CONTENT_WORDS,
) -> CoveragePredicate:
    """The `overlap` predicate at `threshold`, NAMED for the value it used.

    A factory, and the shape is forced by the report rather than chosen for
    taste. `CoveragePredicate` is frozen, `PREDICATES` is built from the
    predicates' own `name`s, and `summarize` heads each column with that
    name. A ladder reached by rebinding `OVERLAP_COVERED_FRACTION`, or by
    threading a threshold argument through `covers`, would leave every
    column headed `overlap` while holding numbers from different
    thresholds -- the one failure this whole file is built to refuse, since
    `CoverageReport` already declines to carry a predicate name for exactly
    that reason. Capturing the threshold in a closure and spelling it in
    both `name` and `describe` makes a mislabelled column impossible to
    produce without editing this function.

    The shipped default keeps the bare name `overlap`. It is the registry
    entry, it is what `--predicate overlap` selects, and it is the label
    every committed number in this directory was recorded under; renaming
    it would orphan them. Every other value announces itself as
    `overlap@B`.

    `.10g` rather than a percentage in the name: `{0.125:.0%}` renders
    `12%`, which is a threshold this predicate does not use, and a caller
    deduplicating columns by name would then merge two genuinely different
    ladder points into one. Ten significant digits is past anything a sweep
    would ask for.

    The hypothesis it implements is unchanged and is the one `quote` failed
    on: extraction over discursive text PARAPHRASES, so it reorders,
    rewords and reattributes while carrying the section's nouns forward.
    Word overlap survives that; a substring test does not.

    Raises `ValueError` outside `(0.0, 1.0]`. At 0.0 every gated section is
    covered whatever the objects say, so the predicate is vacuous rather
    than lenient; above 1.0 nothing can clear it; and a share of distinct
    content words has no reading outside that interval at all. A sweep that
    typo'd a bound would otherwise print a full column of 0% or 100% that
    looks like a measurement.
    """
    if not 0.0 < threshold <= 1.0:
        raise ValueError(
            f"overlap threshold must be in (0.0, 1.0], got {threshold!r}; it is "
            "a share of a section's distinct content words"
        )
    # Below 1 the gate admits a section with NO content words, whose overlap
    # fraction is a division no reading rescues -- vacuity rather than
    # leniency, the same failure the threshold's own `0.0` bound refuses.
    if min_content_words < 1:
        raise ValueError(
            f"overlap word gate must be >= 1, got {min_content_words!r}; below "
            "that it admits a section with nothing to measure"
        )
    rendered = f"{threshold:.10g}"
    low, high = OVERLAP_MEASURED_WINDOW
    inside = "inside" if low <= threshold <= high else "outside"

    def covers(texts: Sequence[str], body: str) -> bool:
        """Whether `texts` reproduce at least `threshold` of `body`'s
        distinct content words."""
        return overlap_fraction(texts, body) >= threshold

    def checkable(body: str) -> bool:
        """`overlap`'s gate at THIS factory's word floor -- the closure, not
        a module-level gate pinned to the default, so a swept value
        reaches the gate rather than only the label above it."""
        return len(content_words(body)) >= min_content_words

    # The bare registry name is reserved for the FULLY default predicate.
    # Both constants are swept now, and a name that tracked only the
    # threshold would print two different gates in one `overlap` column --
    # exactly the mislabelling this factory exists to make impossible. The
    # word gate joins the name as `/W`, and only when it is not the default,
    # so no committed column is renamed by this widening.
    default_threshold = threshold == OVERLAP_COVERED_FRACTION
    default_words = min_content_words == OVERLAP_MIN_CONTENT_WORDS
    if default_threshold and default_words:
        name = "overlap"
    elif default_words:
        name = f"overlap@{rendered}"
    else:
        name = f"overlap@{rendered}/{min_content_words}"

    return CoveragePredicate(
        name=name,
        covers=covers,
        checkable=checkable,
        describe=(
            f"content-word overlap >= {rendered} "
            f"(>= {min_content_words} content words) -- REFUTED (#793): "
            f"separated at {low:g}-{high:g} on 1 model, over-fires at every "
            f"rung on a second; THIS value is {inside} that dead window"
        ),
    )


OVERLAP: Final = overlap_predicate()
"""A candidate that was measured and REFUTED (2026-08-23), like `quote`
before it.

Swept over a threshold ladder and it separated -- at B in [0.20, 0.25]
rather than at the 0.5 this object is built with, on three arms totalling
17 runs of ONE model, with the window selected from two of those same three
arms. A second model (`phi4:14b`) then over-fired at every rung on the one
arm that was genuinely out of sample, and a leave-one-section-out arm found
it blind to a majority of constructed losses at the same window. Kept, not
deleted: it is the second refutation this directory exists to record, and a
third candidate needs to be comparable against it.

The DEFAULT instance, and the only one in `PREDICATES`. Any other point on
the ladder is `overlap_predicate(B)`, which names itself `overlap@B` so its
column cannot be read as this one's."""


PREDICATES: Final[dict[str, CoveragePredicate]] = {
    predicate.name: predicate for predicate in (QUOTE, OVERLAP)
}
"""Every predicate, by name, in report order -- the baseline first.

Built FROM the predicates rather than written out beside them, so a key
cannot disagree with the `name` the report prints under it.

Embedding similarity and asking the model are the two candidates NOT here.
Both are named in the README with the reason each was deferred; neither is
a gap somebody should fill without reading it, because both put a model
call on the path of a signal whose whole appeal was costing nothing.
"""


def coverage_report(
    texts: list[str] | tuple[str, ...],
    source_text: str,
    predicate: CoveragePredicate = QUOTE,
) -> CoverageReport:
    """Which sections of `source_text` `predicate` says no entry of `texts`
    covers, and how much of the source's checkable text that is.

    `texts` are the written texts of the objects derived from this source --
    the same strings `evidence.evidence_line` is asked about, so the two
    signals cannot disagree about what one object contributed.

    `predicate` defaults to `QUOTE`, the refuted baseline, so every existing
    caller and every committed number keeps its exact meaning. A caller
    that passes anything else is producing numbers in a DIFFERENT column and
    is responsible for labelling them as such -- see `CoveragePredicate`.

    One object can cover SEVERAL sections, and each is cleared. Objects are
    not attributed to a single section: extraction merges freely across a
    document, and forcing a one-to-one attribution would falsely report the
    sections an object covered second. The converse also holds and is the
    sharper half: a section is judged on `texts` against ITS OWN body, so an
    object that covers a different section of the same source clears that
    one and nothing else.

    A section `predicate.checkable` rejects is skipped entirely, entering
    NEITHER total -- see `CoveragePredicate` for the invariant and for why
    the gate travels with the covering test rather than being fixed here.
    On a source whose every section is skipped this returns nothing
    uncovered and a zero denominator, and that means "nothing to say", never
    "fully covered".
    """
    uncovered: list[str] = []
    uncovered_chars = 0
    checkable_chars = 0
    for section in split_sections(source_text):
        if not predicate.checkable(section.body):
            continue
        length = len(section.body.strip())
        checkable_chars += length
        if not predicate.covers(texts, section.body):
            uncovered.append(section.heading)
            uncovered_chars += length
    return CoverageReport(
        uncovered=tuple(uncovered),
        uncovered_chars=uncovered_chars,
        checkable_chars=checkable_chars,
    )


def uncovered_sections(
    texts: list[str] | tuple[str, ...],
    source_text: str,
    predicate: CoveragePredicate = QUOTE,
) -> tuple[str, ...]:
    """The headings `coverage_report` reports as uncovered -- the naming
    half alone, for callers that do not need the weights."""
    return coverage_report(texts, source_text, predicate).uncovered


@dataclass(frozen=True)
class LeaveOneOutRow:
    """One section's leave-one-out trial: what was removed, and whether the
    predicate noticed.

    `quoting` and `remaining` are counts rather than the texts themselves
    because this row is reported for PRIVATE sources too -- the discursive
    transcripts `--source` reads carry real names, and a row that carried
    object text would launder it into a report. Counts and booleans are the
    whole finding.

    `covered_before` is the control, and a row without it measures nothing:
    a section the predicate ALREADY flags cannot be made more flagged by
    deleting objects, so scoring it as a hit would credit the signal for a
    verdict it reached before the arm ran.
    """

    heading: str
    quoting: int
    remaining: int
    covered_before: bool
    named_after: bool


def quoting_objects(texts: Sequence[str], section_body: str) -> tuple[int, ...]:
    """Indices of `texts` that demonstrably QUOTE `section_body`, via
    `evidence.evidence_line` -- the shipped verbatim-quoting rule, not a
    second spelling of it.

    This is the arm's attribution mechanism, and it is deliberately NOT the
    predicate under test. Attributing by the predicate's own covering rule
    would make every leave-one-out row true by construction: remove exactly
    the objects that cover a section and the section is uncovered, which
    proves nothing about the predicate except that it is a function.

    Verbatim quoting is a strictly narrower claim than coverage, so a
    section can keep several non-quoting objects that still cover it. That
    gap is the whole measurement: it is where a real loss stays invisible.
    """
    return tuple(
        index
        for index, text in enumerate(texts)
        if evidence_mod.evidence_line(text, section_body) is not None
    )


@dataclass(frozen=True)
class LeaveOneOutReport:
    """The scan `leave_one_section_out` reads, WITH the sections it could not
    score and why.

    The rows alone are not an auditable measurement. Three of the four
    exclusions produce no row at all, so a table reading "5 trials" says the
    same thing whether one section was excluded or forty -- and this
    directory's own rule is that a bounded arm names what it dropped. These
    three counts are that disclosure, and they are what makes the word-gate
    ladder's `20 -> 15` legible as "five sections stopped being scorable"
    rather than as a smaller measurement of the same thing.

    Mirrors `coverage_report`/`uncovered_sections`: the report carries the
    weights, the bare accessor carries the naming half for callers that do
    not need them.
    """

    rows: tuple[LeaveOneOutRow, ...]
    unscorable: int
    """Sections `predicate.checkable` rejected -- the pair's own invariant."""
    unquoted: int
    """Sections no object quotes, so no loss could be constructed. The
    largest of the three on a discursive source, and the reason that arm has
    5 trials rather than 40."""
    total_removal: int
    """Sections every object quotes, where ablation would empty the list and
    any predicate flags everything."""


def leave_one_out_report(
    texts: Sequence[str],
    source_text: str,
    predicate: CoveragePredicate,
) -> LeaveOneOutReport:
    """One trial per section whose loss can be CONSTRUCTED: drop the objects
    quoting it, rescore, and record whether `predicate` then names it.

    The under-fire arm that needs no adjudicated ground truth, and therefore
    the one that reaches a discursive source. `--ablate` cuts a run down to
    the objects one REPORTED run produced, which pins it to a fixture
    somebody itemised by hand -- the README names that as its single biggest
    gap, because the only evidence a predicate catches a real loss comes
    from one terse bullet-shaped file. Here the loss is built per section
    from the run's own texts, so any source with headings can be measured
    and nothing is graded against what it should have found.

    Three sections are skipped, each for its own reason, and none of them is
    a hit the arm declined to count:

    - `predicate.checkable` rejects it -- the pair's own invariant; a
      section that cannot be SCORED cannot be scored here either.
    - No object quotes it. There is nothing to remove, so there is no
      constructed loss; counting the model's own silence would turn this
      into exactly the judgment the probe refuses to make.
    - Every object quotes it. Removing them empties the list, and a
      predicate handed no objects flags every section it can check, so the
      row would be true by construction -- the same vacuity
      `quoting_objects` avoids one level up.

    Raises `ValueError` on a predicate whose `covers_by_quoting` is set. That
    is not a limitation to work around: on such a predicate the trial is
    decided before it is run, and a refusal is the only honest output. See
    `CoveragePredicate.covers_by_quoting`.

    `predicate` is REQUIRED, unlike `coverage_report`'s, and the asymmetry is
    deliberate. This module's default everywhere else is `QUOTE`, because it
    is the baseline every committed number was measured under -- and `QUOTE`
    is exactly the predicate this function refuses. A default that always
    raised would be a trap dressed as a convenience, so there is none.

    Sections are scored BY POSITION, never by heading name: headings are not
    unique (`CoverageReport` documents the repeated-`## Notes` collapse that
    cost 95 characters of denominator), and a leave-one-out trial that
    round-tripped through a heading-keyed dict would score one `## Notes`
    against the other's objects.
    """
    if predicate.covers_by_quoting:
        raise ValueError(
            f"{predicate.name!r} covers by the same verbatim-quoting rule this "
            "arm attributes by, so deleting a section's quoting objects leaves "
            "it uncovered by construction and every trial is a hit; the arm "
            "has nothing to measure on it"
        )
    kept = list(texts)
    rows: list[LeaveOneOutRow] = []
    unscorable = unquoted = total_removal = 0
    for section in split_sections(source_text):
        if not predicate.checkable(section.body):
            unscorable += 1
            continue
        attributed = set(quoting_objects(kept, section.body))
        if not attributed:
            unquoted += 1
            continue
        remaining = [text for index, text in enumerate(kept) if index not in attributed]
        if not remaining:
            total_removal += 1
            continue
        rows.append(
            LeaveOneOutRow(
                heading=section.heading,
                quoting=len(attributed),
                remaining=len(remaining),
                covered_before=predicate.covers(kept, section.body),
                named_after=not predicate.covers(remaining, section.body),
            )
        )
    return LeaveOneOutReport(
        rows=tuple(rows),
        unscorable=unscorable,
        unquoted=unquoted,
        total_removal=total_removal,
    )


def leave_one_section_out(
    texts: Sequence[str],
    source_text: str,
    predicate: CoveragePredicate,
) -> tuple[LeaveOneOutRow, ...]:
    """The rows `leave_one_out_report` scans, without its exclusion counts --
    for callers that score rather than audit. Reads the report rather than
    repeating the walk, so the two can never disagree about what a row is."""
    return leave_one_out_report(texts, source_text, predicate).rows
