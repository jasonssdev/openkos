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

from openkos.extraction import concept, judge
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

_SOLE_REPLY = (
    "["
    '{"type": "Concept", "title": "Trazabilidad Documental", '
    '"description": "La practica de registrar el origen de cada dato.", '
    '"body": "La plataforma conserva el origen de cada dato registrado."}'
    "]"
)
"""ONE object, the first of `_REPLY`'s two, for the two conditional paths.

`_REPLY` deliberately returns two so the documented table measures the
unconditional fan-out. This is its counterpart: a sole returned object is
exactly the condition the re-ask fires on (#642) and, once the union dedups
the identical answers back to one candidate, exactly the condition that makes
the judge a provable no-op (#644).
"""

_ROW = re.compile(
    r"^\|\s*(?P<label>[^|]+?)\s*\|\s*(?P<pages>\d+)\s*\|\s*"
    r"(?P<prose>\d+) calls\s*\|\s*(?P<meeting>\d+) calls\s*\|$"
)


_JUDGE_REPLY = '{"keep": ["Trazabilidad Documental", "Adopcion del Registro Diario"]}'
"""A VALID judge selection, keeping both candidates.

Until #754 the judge got the extraction-shaped reply like every other call and
degraded, and the test recorded that as harmless: "each still costs exactly one
call". #754 made it matter -- a failing judge now costs TWO calls, because
`judge.select` retries once before declaring itself unavailable. Left as it
was, this table would have documented the JUDGE-FAILURE cost of every ingest
as if it were the ordinary one.

Keeping both candidates also keeps the fan-out identical to what the table
already documented: no candidate is dropped, so nothing downstream of the
judge changes shape.
"""


class _CountingLLM:
    """Structural `LLMBackend` that answers extraction-shaped calls with a
    fixed reply and the JUDGE with a valid selection.

    The judge is told apart by comparing the system turn against
    `judge._JUDGE_SYSTEM_PROMPT` itself, not by sniffing for a phrase: the
    live constant cannot drift from what production sends, while a substring
    guess would silently stop matching the day the prompt is reworded and
    quietly restore the judge-failure cost this class exists to avoid.

    The participant pass still receives the extraction-shaped reply and still
    degrades. That one IS harmless and stays: it is an extraction-shaped
    prompt, its reply is legitimately shaped for it, and it costs one call
    either way.
    """

    def __init__(self, reply: str = _REPLY, *, judge_reply: str = _JUDGE_REPLY) -> None:
        self.calls = 0
        self.judge_calls = 0
        self._reply = reply
        self._judge_reply = judge_reply

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        if messages and messages[0].get("content") == judge._JUDGE_SYSTEM_PROMPT:
            self.judge_calls += 1
            return self._judge_reply
        return self._reply


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


def _observe(
    chars: int, *, title: str, reply: str = _REPLY
) -> tuple[int, concept.ExtractionReport]:
    """One real `extract_concept_union` pass; returns its call count and the
    report that says which conditional branches ran."""
    llm = _CountingLLM(reply)
    outcome = concept.extract_concept_union(
        _source_of(chars), source_title=title, llm=llm
    )
    return llm.calls, outcome.report


def _observed_calls(chars: int, *, title: str) -> int:
    return _observe(chars, title=title)[0]


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


@pytest.mark.parametrize("pages", _DOCUMENTED_PAGES)
def test_the_two_titles_this_test_relies_on_still_differ_in_meeting_shape(
    pages: int,
) -> None:
    """The whole prose/meeting split is carried by these two titles. If either
    stopped classifying as the test assumes, both columns would silently
    measure the same shape and agree with a table that had drifted.

    Parametrized across every documented size rather than asserted at one
    (#740). Two pages is the ONLY row that stays unchunked in the meeting
    column, so checking it alone left the gate untested on exactly the rows
    where chunking makes it matter. `_is_meeting_shaped` reads the whole
    source, not a window -- `extract_concept_union` computes it once before
    it branches -- so a longer source is the only thing needed to cover them.
    """
    text = _source_of(pages * _PAGE_CHARS)

    assert not concept._is_meeting_shaped(_PROSE_TITLE, text)
    assert concept._is_meeting_shaped(_MEETING_TITLE, text)


@pytest.mark.parametrize(
    "title", [_PROSE_TITLE, _MEETING_TITLE], ids=["prose", "meeting"]
)
@pytest.mark.parametrize("pages", _DOCUMENTED_PAGES)
def test_a_sole_returned_object_fires_the_reask(pages: int, title: str) -> None:
    """`docs/cli.md` documents the re-ask as firing "only when the source
    returned a *sole* object", and until #740 nothing pinned it: the stub
    answers with two objects on every row, so the guard measured the table
    with this branch permanently off.

    The baseline arm is asserted alongside the triggering one. Without it the
    test would pass just as well against a re-ask that fired unconditionally,
    which is the failure mode the word "only" is there to forbid.

    Across every documented size AND both title columns. The trigger is
    evaluated per BRANCH, and the branches are different code: below the
    threshold the union merges two whole-source passes, above it
    `_dedup_merged` collapses one pass per window, and the meeting column
    crosses that threshold at a different size while adding a participant
    call. A guard at 2 pages of prose alone would have left the chunked half
    and the entire meeting column measured with this branch permanently off --
    which is the very gap this test exists to close."""
    _, sole = _observe(pages * _PAGE_CHARS, title=title, reply=_SOLE_REPLY)
    _, pair = _observe(pages * _PAGE_CHARS, title=title)

    assert sole.reask_runs == 1
    assert pair.reask_runs == 0


@pytest.mark.parametrize(
    "title", [_PROSE_TITLE, _MEETING_TITLE], ids=["prose", "meeting"]
)
@pytest.mark.parametrize("pages", _DOCUMENTED_PAGES)
def test_a_single_surviving_candidate_skips_the_judge(pages: int, title: str) -> None:
    """The table's other unpinned conditional: the judge is "skipped only when
    a single candidate survives the merge" (#644).

    Both arms again, for the same reason -- an always-skipped judge would
    satisfy the first assertion on its own, and that is precisely the bug that
    would silently halve the documented cost of every row. Across every size
    and both columns for the same reason as the re-ask above: what survives
    the merge is branch-specific."""
    _, sole = _observe(pages * _PAGE_CHARS, title=title, reply=_SOLE_REPLY)
    _, pair = _observe(pages * _PAGE_CHARS, title=title)

    assert sole.judge_status == "skipped"
    assert pair.judge_status != "skipped"


@pytest.mark.parametrize(
    "title", [_PROSE_TITLE, _MEETING_TITLE], ids=["prose", "meeting"]
)
@pytest.mark.parametrize("pages", _DOCUMENTED_PAGES)
def test_the_two_conditional_branches_cancel_in_the_documented_total(
    pages: int, title: str
) -> None:
    """Why the two tests above assert the REPORT and not the call count.

    On a sole returned object the re-ask adds a call and the skipped judge
    removes one, so the arm costs exactly what the two-object arm costs. A
    guard written against `llm.calls` would therefore go green whether the
    branches ran or not -- the same shape of vacuous pass as a metric that
    cannot vary. Pinned rather than left as prose, so that if the arithmetic
    ever stops cancelling, the reason these tests look indirect is re-read.

    Measured at every documented size and in both columns, not assumed from
    the smallest: the cancellation holds for prose at 2 and 5 pages (3 calls
    either way), at 10 (9) and at 30 (24), and for the meeting column at the
    same sizes (4, 6, 10, 25 -- one higher, and crossing into chunking
    earlier). Holding across two different branch shapes and two thresholds is
    what makes it a structural property rather than a coincidence of one row.

    Each component is asserted separately. A single tuple comparison is
    satisfied when only ONE of the two differs, so a regression that left the
    judge running on the sole-object arm while the re-ask still fired would
    keep the tuples unequal and slip through."""
    sole_calls, sole = _observe(pages * _PAGE_CHARS, title=title, reply=_SOLE_REPLY)
    pair_calls, pair = _observe(pages * _PAGE_CHARS, title=title)

    assert sole_calls == pair_calls
    assert sole.reask_runs != pair.reask_runs
    assert sole.judge_status != pair.judge_status


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


def test_a_failing_judge_costs_exactly_one_extra_call_for_the_retry() -> None:
    """#754's retry is a real cost, and it is bounded at one.

    The documented table measures the ORDINARY path, where the judge answers.
    This pins the other one: when the judge cannot be used, `select` asks a
    second time and then gives up. Asserted as a DIFFERENCE against the same
    source, so it cannot drift with the fan-out -- and asserted on the judge's
    own call count too, since a total that happened to rise by one for some
    other reason would satisfy a bare arithmetic check.
    """
    chars = 5 * _PAGE_CHARS

    healthy = _CountingLLM()
    concept.extract_concept_union(
        _source_of(chars), source_title=_PROSE_TITLE, llm=healthy
    )

    # An extraction-shaped array is not a judge object, so both attempts fail.
    failing = _CountingLLM(judge_reply=_REPLY)
    outcome = concept.extract_concept_union(
        _source_of(chars), source_title=_PROSE_TITLE, llm=failing
    )

    assert healthy.judge_calls == 1
    assert failing.judge_calls == judge.JUDGE_ATTEMPTS == 2
    assert failing.calls - healthy.calls == 1
    assert outcome.report.judge_status == "failed"


def test_the_ordinary_path_pays_nothing_for_the_retry() -> None:
    """The complement, and the one that matters most: a judge that answers on
    the first attempt must cost exactly what it cost before #754. A retry that
    also fired on success would double a call on every source in a batch."""
    llm = _CountingLLM()
    outcome = concept.extract_concept_union(
        _source_of(5 * _PAGE_CHARS), source_title=_PROSE_TITLE, llm=llm
    )

    assert outcome.report.judge_status == "ok"
    assert llm.judge_calls == 1


# --- issue #775: the cost-gate estimator is pinned against the same pipeline


@pytest.mark.parametrize("pages", _DOCUMENTED_PAGES)
@pytest.mark.parametrize("title", [_PROSE_TITLE, _MEETING_TITLE])
def test_estimate_matches_the_observed_ordinary_path(pages: int, title: str) -> None:
    """`estimate_extraction_calls` -- the number `ingest`'s batch cost gate
    announces (#775) -- must equal what the real union pipeline spends on the
    ordinary path (multi-candidate reply, judge answers first try), for every
    documented row. Observed, not re-derived: a changed fan-out shape must
    fail this, exactly as it fails the table test above."""
    text = _source_of(pages * _PAGE_CHARS)
    estimate = concept.estimate_extraction_calls(text, source_title=title)

    assert estimate.calls == _observed_calls(pages * _PAGE_CHARS, title=title)


def test_estimate_reports_windows_only_when_the_source_fans_out() -> None:
    """`windows` is 0 below the threshold (the gate has no split to name) and
    equals the real `_chunk_lines` window count above it -- the same number
    the per-chunk progress counter shows."""
    small = concept.estimate_extraction_calls(
        _source_of(2 * _PAGE_CHARS), source_title=_PROSE_TITLE
    )
    big_text = _source_of(10 * _PAGE_CHARS)
    big = concept.estimate_extraction_calls(big_text, source_title=_PROSE_TITLE)

    assert small.windows == 0
    assert big.windows == len(concept._chunk_lines(big_text))
    assert big.calls == big.windows + 1


def test_estimate_single_run_path_counts_extraction_passes_only() -> None:
    """`union_judge=False` restores the single-run path: one call below the
    threshold, one per window above, no judge and no participant pass."""
    small = concept.estimate_extraction_calls(
        _source_of(2 * _PAGE_CHARS), source_title=_PROSE_TITLE, union_judge=False
    )
    big_text = _source_of(10 * _PAGE_CHARS)
    big = concept.estimate_extraction_calls(
        big_text, source_title=_PROSE_TITLE, union_judge=False
    )

    assert small.calls == 1
    assert big.calls == len(concept._chunk_lines(big_text))
