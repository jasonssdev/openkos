"""Quoted-evidence detection for one derived object's written text (issue
#801): the line of that text which the SOURCE actually writes, or `None`
when the text quotes nothing from it.

Config-free leaf, mirroring `extraction/judge.py`'s own rule (design D2):
this module never imports `openkos.config` and never imports
`extraction.concept` -- it takes and returns plain strings, so the
orchestrator in `concept.py` can call it without either module importing
the other. It has no notion of an `ExtractionResult`, which is deliberate:
WHICH text belongs to an object -- `body` or the `description` the builder
falls back to -- is `concept.py`'s question, and answering it here would
require exactly the import this leaf rule forbids.

What it is for. `ingest` wrote a `Decision` whose whole body restated its
own description (`The decision regarding who owns the schema migration
plan for Project Helios.`), while the source sentence it came from
(`- Priya owns the schema migration plan.`) was gone. The stored object
recorded that a decision EXISTS and dropped the only fact worth storing,
and then became a citation that provably cannot support the answer it was
attached to. Seven sibling objects from the same run each carried a quoted
line, so the invariant was nearly held already -- which is why #801 marks
the exception rather than degrading the pipeline around it.
"""

import re
from typing import Final

_MIN_EVIDENCE_WORDS: Final = 4
"""The word floor a candidate line must clear, counted AFTER normalization.

This is the whole difference between a check and a vacuous guard. A
one- or two-word line -- `Priya Nair`, `MySQL 8`, a bare heading -- appears
verbatim in almost any source that mentions its subject at all, so without
a floor essentially every object would report evidence, the notice would
never fire, and the surface would prove nothing while looking like it
proved something. That failure mode has a name in this repo (a zero-FP
verdict nothing could have failed) and it is the one this constant exists
to prevent.

Four rather than a larger number, verified against #801's own data rather
than chosen for feel: the `Person` object's evidence line in the reported
run is `Priya owns the schema migration plan.` at 6 words, and the
`Decision` object that DOES quote its source carries a comparable
sentence. A floor of 4 clears both with margin while still rejecting the
name-and-title fragments that make the check vacuous. Raising it starts
discarding real evidence from terse sources (a bullet list of short
commitments); lowering it walks back toward vacuity.

It also filters the degenerate case out of `_qualifying_lines` -- a blank
line, or a line that was nothing but markdown markers, normalizes to the
empty string, which is a substring of every source and would otherwise
report evidence for every object ever written.

It is NOT the only thing standing between that line and a match, and this
docstring claimed it was until the claim was measured. `evidence_line`'s
longest-wins tie-break rejects a zero-word line independently: `words >
best_words` is already false on the first comparison, because `best_words`
starts at 0. Mutate this constant to 0 and the blank-line test stays GREEN
while only the two tests that name the floor go red -- which is how the
false claim was caught, and which is why the blank-line test documents the
tie-break rather than this constant.

A separate `if not candidate` skip is still not wanted. It would be a
THIRD spelling of a rule two mechanisms already enforce, and a guard no
test can hold is a guard nobody can prove is doing the work.
"""

_LINE_PREFIX_RE: Final = re.compile(r"^\s*(?:>\s*|#{1,6}\s+|[-*+]\s+|\d+\.\s+)*")
"""Leading markdown structure to strip before comparing a line: blockquote
`>`, ATX heading `#`..`######`, unordered list `-`/`*`/`+`, and ordered
list `N.`.

Markers are LAYOUT, never content. A model writes its body as a bullet
list far more often than as prose, and whether the source's own line
carried the same marker is an accident of how the source was written --
comparing them would report a genuinely quoted line as invented, which is
the exact false positive this check cannot afford (it marks stored
objects, and an advisory that fires on correct runs is one the operator
learns to skip).

Repeated (`*`) so a nested opener (`> - ...`) is stripped whole rather
than one layer deep. A line consisting ONLY of markers strips to the empty
string, which the word floor then rejects.
"""


def _normalize(value: str) -> str:
    """Casefold and collapse all whitespace to single spaces.

    BYTE-FOR-BYTE the normalization `concept._quoted_verbatim` applies
    (`extraction/concept.py`, the `_drop_wrong_language_titles` helper it
    serves). Read that function before changing this one: the two surfaces
    must agree on what "quoted verbatim" MEANS, because the gate there
    spares a title for being quoted while the check here reports an object
    for quoting nothing, and a second, subtly different normalization is
    precisely the drift this note exists to prevent -- it would let one
    surface call a line quoted and the other call the same line absent,
    with no call site able to see the disagreement."""
    return " ".join(value.casefold().split())


_NEEDLE_TRAILING_PUNCTUATION: Final = ".,;:!?"
"""Sentence-final punctuation trimmed from the END of whichever line is
being looked FOR (never from the text being searched, and never from the
line reported back).

The sentence boundary is exactly where one text extends another, and the
terminator is the one character that differs there. `- Priya owns the
schema migration plan.` reproduced as `Priya owns the schema migration plan
for Project Helios.` shares every word, and a raw substring test still says
no -- solely because the source's `.` became ` for`. Without this trim the
two-directional comparison below would not match its own motivating
example.

Applied to the NEEDLE in both directions, so the rule is one sentence
rather than an exception on one arm: a quote's terminal punctuation belongs
to the sentence that ends there, not to the quoted span. Trimming the
HAYSTACK instead would be a different and much wider claim -- it would let
a needle run past the end of the line it matched."""


def _qualifying_lines(text: str) -> list[tuple[str, str, int]]:
    """Every line of `text` that could carry evidence, as
    `(stripped, normalized, word_count)`.

    "Line" is one entry of `text.splitlines()` with its leading markdown
    structure removed (`_LINE_PREFIX_RE`) and its surrounding whitespace
    stripped; a line must clear `_MIN_EVIDENCE_WORDS` to appear here. Both
    directions of `evidence_line`'s comparison draw their candidates from
    this one function, so the floor and the marker-stripping cannot end up
    applying to one side and not the other."""
    lines: list[tuple[str, str, int]] = []
    for raw_line in text.splitlines():
        candidate = _LINE_PREFIX_RE.sub("", raw_line).strip()
        normalized = _normalize(candidate)
        words = len(normalized.split())
        if words >= _MIN_EVIDENCE_WORDS:
            lines.append((candidate, normalized, words))
    return lines


def evidence_line(text: str, source_text: str) -> str | None:
    """The longest line `text` and `source_text` demonstrably share, or
    `None` when they share none.

    "Verbatim" is `_normalize`'s casefolded, whitespace-collapsed substring
    test -- the same one `concept._quoted_verbatim` uses, deliberately.
    Candidates on both sides come from `_qualifying_lines`, so both clear
    `_MIN_EVIDENCE_WORDS` -- see that constant for why the floor is the
    difference between this check and a vacuous one, and why it is also
    what disqualifies a blank line (whose empty normalization is a
    substring of everything).

    The test is TWO-DIRECTIONAL, and that is the part a later reader will
    want to "simplify" away. A line counts as evidence when EITHER:

    - (a) a body line is a substring of the normalized source, or
    - (b) a source line is a substring of a normalized body line.

    Arm (b) exists because arm (a) alone reports a CORRECT extraction as
    quoting nothing whenever the model quotes the source and carries the
    sentence further:

        source: `- Priya owns the schema migration plan.`
        body:   `Priya owns the schema migration plan for Project Helios.`

    Every word of the source line is reproduced, yet the body line is not a
    substring of the source -- and note that even arm (b) only reaches it
    because `_NEEDLE_TRAILING_PUNCTUATION` trims the source line's own `.`,
    which the extension replaced with ` for`. A one-directional test flags
    the object outright.
    That is the false positive `_LINE_PREFIX_RE` above says this check
    cannot afford, and here it is unclearable rather than merely annoying:
    the notice's token is deliberately excluded from
    `application.ingest.extraction_retry_due`, so a plain re-ingest of an unchanged
    source skips extraction entirely (#773's convergence short-circuit) and
    the marker never recomputes. The Source would sit under `openkos
    status`'s needs-attention permanently, with no command that clears it.

    Widening it does NOT dissolve the check. #801's real object restates
    its description in the model's own words, so neither direction matches
    and it is still flagged; the floor applies to arm (b)'s SOURCE line for
    exactly the reason it applies to arm (a)'s body line, since a long body
    line contains many short phrases and admitting on one of those is the
    vacuous guard arriving from the other side.

    When arm (b) matches, the SOURCE line is returned -- that is the text
    actually quoted, and it is what a reader following this notice would
    search the source for. Handing back the body's longer sentence would
    send them looking for a string the source does not contain.

    Returns the LONGEST qualifying line by word count across BOTH arms
    combined. Longest because the evidence reported should be the most
    substantive thing the object quotes, not an incidental aside that
    happens to match. Ties go to the first match in document order, with
    arm (a) settling a cross-arm tie -- the two arms read different
    documents, so "document order" alone cannot decide between them, and a
    notice naming a different line on each run over identical bytes would
    read as nondeterminism in extraction itself.

    The returned string is the marker-stripped line, not the raw one: that
    is the text that was actually compared, and echoing a bullet back to a
    reader who is about to search for it would be unhelpful at best.

    Deliberately returns the line rather than a bare `bool`. The caller
    (`concept._unevidenced_titles`) only needs the `None` case today, but a
    check that can SHOW its evidence is checkable by hand, and the same
    asymmetry `_names_absent_from_source` documents applies: a reader has
    to be able to disagree with the advisory by opening the source.
    """
    normalized_source = _normalize(source_text)
    body_lines = _qualifying_lines(text)
    best: str | None = None
    best_words = 0

    # Arm (a): a body line quoted whole from the source.
    for candidate, normalized, words in body_lines:
        needle = normalized.rstrip(_NEEDLE_TRAILING_PUNCTUATION)
        if words > best_words and needle in normalized_source:
            best = candidate
            best_words = words

    # Arm (b): a source line the body reproduced and then extended. Only
    # `body_lines` is searched, not every raw body line, and that costs
    # nothing: containment means the body line holds all of the source
    # line's words, so a body line below the floor cannot contain a source
    # line that is at or above it.
    for candidate, normalized, words in _qualifying_lines(source_text):
        needle = normalized.rstrip(_NEEDLE_TRAILING_PUNCTUATION)
        if words > best_words and any(
            needle in body_normalized for _, body_normalized, _ in body_lines
        ):
            best = candidate
            best_words = words

    return best
