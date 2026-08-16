"""The cost table in `docs/cli.md` is a claim about how many model calls one
ingest makes, so it is pinned against the pipeline that actually makes them
(issue #700).

#700's own table was written by hand and was wrong the day it was published: it
claimed a six-page note costs ONE model call, when the union path asks the
identical prompt TWICE below the chunking threshold and then runs a judge. It
also predated [#714], which gave meeting-shaped sources their own, lower
threshold, so every transcript row understated the fan-out as well. A table
nobody can re-derive is the shipped-template failure this repository has already
paid for: prose that taught a stale default for a whole release because no test
read it.

THIS TEST OBSERVES THE FAN-OUT RATHER THAN RESTATING IT. It drives the real
`extract_concept_union` over a counting `LLMBackend` and asserts the documented
number against the calls that actually happen. An earlier draft recomputed the
arithmetic from the constants instead -- which would have caught a moved
constant, but not a changed fan-out shape (a third extraction pass, a second
judge call, a newly unconditional re-ask), and the fan-out is the thing the
table is really about. No network: the fake answers every call locally.

It deliberately asserts the ARITHMETIC only. Wall-clock seconds are machine- and
model-specific, are labelled in the document as one machine on one day, and are
not pinnable by any test.
"""

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.extraction import concept
from openkos.llm.base import Message

CLI_DOC = Path(__file__).resolve().parents[2] / "docs" / "cli.md"

_PAGE_CHARS = 3_000
"""The characters-per-page unit the documented table converts through."""

_DOCUMENTED_PAGES = (2, 5, 10, 15, 30, 100)
"""Every page count the table is expected to carry.

Pinned as an exact set rather than a minimum length: a `>= 5` guard tolerated
deleting exactly one row, and the row most worth deleting is the 100-page one,
whose fan-out is the only cell far from any threshold.

NO VALUE HERE MAY SIT ON A THRESHOLD. 4 pages is exactly
`_MEETING_CHUNK_THRESHOLD` and 6 is exactly `_CHUNK_THRESHOLD`, so on those the
branch taken depends on whether the filler below lands one character over or
under -- an artifact of the fixture rather than a fact about the product. Both
were removed from the table for that reason.
"""

_PROSE_TITLE = "Informe tecnico de plataforma"
_MEETING_TITLE = "Acta de la reunion semanal"

_REPLY = (
    "["
    '{"type": "Concept", "title": "Trazabilidad Documental", '
    '"description": "La practica de registrar el origen de cada dato.", '
    '"body": "La plataforma conserva el origen de cada dato registrado."},'
    '{"type": "Decision", "title": "Adopcion del Registro Diario", '
    '"description": "Se acordo registrar las decisiones cada dia.", '
    '"body": "El equipo acordo dejar constancia diaria de sus decisiones."}'
    "]"
)
"""TWO distinct Spanish objects, deliberately.

Two, because a single surviving candidate makes the judge a provable no-op that
`extract_concept_union` skips (#644) and makes the re-ask fire (#642) -- either
one would move the count for a reason the table is not about. Spanish, because
the chunked path runs `_drop_wrong_language_titles` against the source, and an
English title over Spanish filler would be dropped before the judge saw it.
"""

_ROW = re.compile(
    r"^\|\s*(?P<label>[^|]+?)\s*\|\s*(?P<pages>\d+)\s*\|\s*"
    r"(?P<prose>\d+) calls\s*\|\s*(?P<meeting>\d+) calls\s*\|$"
)


class _CountingLLM:
    """Structural `LLMBackend` that answers every call with `_REPLY`.

    The judge and participant passes get an extraction-shaped reply they cannot
    parse, so they degrade -- which is irrelevant here and deliberately so: each
    still costs exactly one call, and this test counts calls, not verdicts.
    """

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        return _REPLY


def _source_of(chars: int) -> str:
    """Prose-shaped Spanish filler of at least `chars` characters.

    Line-shaped on purpose: `_chunk_lines` splits on line boundaries, so a
    single unbroken string would not exercise the real window arithmetic.
    """
    line = "La plataforma registra la decision y su justificacion en el acta de hoy."
    lines: list[str] = []
    total = 0
    while total < chars:
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _observed_calls(chars: int, *, title: str) -> int:
    llm = _CountingLLM()
    concept.extract_concept_union(_source_of(chars), source_title=title, llm=llm)
    return llm.calls


def _documented_rows() -> list[tuple[str, int, int, int]]:
    """Every `| label | pages | N calls | N calls |` row of the cost table."""
    rows: list[tuple[str, int, int, int]] = []
    for line in CLI_DOC.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line.strip())
        if match is None:
            continue
        rows.append(
            (
                match["label"],
                int(match["pages"]),
                int(match["prose"]),
                int(match["meeting"]),
            )
        )
    return rows


def test_the_documented_table_carries_exactly_the_expected_rows() -> None:
    """A guard on the guard: without it, a renamed heading or a deleted row
    would leave the per-row assertions iterating a shorter list -- or an empty
    one -- and passing vacuously."""
    assert tuple(row[1] for row in _documented_rows()) == _DOCUMENTED_PAGES


@pytest.mark.parametrize("row", _documented_rows(), ids=lambda row: str(row[0]))
def test_documented_ingest_call_counts_match_the_pipeline(
    row: tuple[str, int, int, int],
) -> None:
    _, pages, documented_prose, documented_meeting = row
    chars = pages * _PAGE_CHARS

    assert documented_prose == _observed_calls(chars, title=_PROSE_TITLE)
    assert documented_meeting == _observed_calls(chars, title=_MEETING_TITLE)


def test_the_two_titles_this_test_relies_on_still_differ_in_meeting_shape() -> None:
    """The whole prose/meeting split is carried by these two titles. If either
    stopped classifying as the test assumes, both columns would silently
    measure the same shape and agree with a table that had drifted."""
    text = _source_of(2 * _PAGE_CHARS)

    assert not concept._is_meeting_shaped(_PROSE_TITLE, text)
    assert concept._is_meeting_shaped(_MEETING_TITLE, text)


def test_the_two_thresholds_the_table_names_are_the_shipped_ones() -> None:
    """The prose above the table states both thresholds and the window target
    in characters. Those three numbers decide every cell, so they are pinned by
    value as well as through the observed fan-out."""
    text = CLI_DOC.read_text(encoding="utf-8")

    assert f"above {concept._CHUNK_THRESHOLD:,} characters" in text
    assert f"one above {concept._MEETING_CHUNK_THRESHOLD:,}" in text
    assert f"`_CHUNK_TARGET` ({concept._CHUNK_TARGET:,} characters)" in text


def test_the_faq_states_the_same_window_size_as_the_code() -> None:
    """`docs/faq.md` answers the same question for a non-technical reader and
    repeats the window size in prose. It is a second copy of a shipped
    constant, so it needs the same guard: a reader who trusts the FAQ and a
    reader who trusts `cli.md` must not be told different numbers, and only
    one of the two was covered until this test existed."""
    faq = (CLI_DOC.parent / "faq.md").read_text(encoding="utf-8")

    assert f"about {concept._CHUNK_TARGET:,} characters" in faq
