"""`max_extraction_calls` is an upper bound a consented ingest is held to (#1496).

The ordinary-path estimate (`estimate_extraction_calls`) is what a cost gate
announces; it is not a bound: retries, the judge's second attempt and the
decision pass's re-ask all depend on what the model replies. A quote that
promises "at most N calls" needs a number no reply can exceed, whatever title
the Source ends up with. These tests drive the real extractors over replies
built to spend as many calls as each mechanism allows and assert the observed
count never passes the bound.
"""

import itertools
from collections.abc import Sequence

import pytest

from openkos.extraction import concept, decision_pass, judge
from openkos.llm.base import BackendError, Message

_PROSE_TITLE = "Informe tecnico de plataforma"
_MEETING_TITLE = "Acta de la reunion semanal"

_SOLE = (
    '[{"type": "Concept", "title": "Trazabilidad Documental", '
    '"description": "La practica de registrar el origen de cada dato.", '
    '"body": "La plataforma conserva el origen de cada dato registrado."}]'
)
_TWO = (
    "["
    '{"type": "Concept", "title": "Trazabilidad Documental", '
    '"description": "La practica de registrar el origen de cada dato.", '
    '"body": "La plataforma conserva el origen de cada dato registrado."},'
    '{"type": "Decision", "title": "Adopcion del Registro Diario", '
    '"description": "Se acordo registrar las decisiones cada dia.", '
    '"body": "El equipo acordo dejar constancia diaria de sus decisiones."}'
    "]"
)


def _source_of(chars: int) -> str:
    line = "La plataforma registra la decision y su justificacion en el acta de hoy."
    lines: list[str] = []
    total = 0
    while total < chars:
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


class _Adversary:
    """Spends every call a mechanism allows: a window fails once and then
    answers, the legacy single call answers `[]` once, the judge and the
    decision pass never give a usable reply (so each retries)."""

    def __init__(self, reply: str) -> None:
        self.calls = 0
        self._reply = reply
        self._extraction_calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        system = messages[0].get("content") if messages else None
        if system == judge._JUDGE_SYSTEM_PROMPT:
            return "not json"
        if system == decision_pass._SYSTEM_PROMPT:
            return "not json"
        if system == concept._SYSTEM_PROMPT:
            # Windows run in order, so every odd extraction call is a window's
            # first attempt and every even one its retry.
            self._extraction_calls += 1
            if self._extraction_calls % 2:
                raise BackendError("transient")
        return self._reply


class _EmptyOnce:
    """The legacy single-call path's adversary: `[]`, then an answer."""

    def __init__(self, reply: str) -> None:
        self.calls = 0
        self._reply = reply

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        return "[]" if self.calls == 1 else self._reply


_CHUNKED = 40_000
_SHORT = 3_000


@pytest.mark.parametrize("title", [_PROSE_TITLE, _MEETING_TITLE])
@pytest.mark.parametrize("reply", [_SOLE, _TWO])
@pytest.mark.parametrize("decisions", [False, True])
@pytest.mark.parametrize("selector", [False, True])
def test_the_union_path_never_passes_the_bound_on_a_chunked_source(
    title: str, reply: str, decisions: bool, selector: bool
) -> None:
    text = _source_of(_CHUNKED)
    llm = _Adversary(reply)
    concept.extract_concept_union(
        text,
        source_title=title,
        llm=llm,
        transcript_decisions=decisions,
        selector_judge=selector,
    )
    bound = concept.max_extraction_calls(
        text,
        union_judge=True,
        transcript_decisions=decisions,
        selector_judge=selector,
    )
    assert llm.calls <= bound
    # The adversary really did spend the retries: one extra call per window.
    windows = concept.estimate_extraction_calls(
        text, source_title=title, union_judge=True
    ).windows
    assert llm.calls >= 2 * windows


@pytest.mark.parametrize("title", [_PROSE_TITLE, _MEETING_TITLE])
@pytest.mark.parametrize("reply", [_SOLE, _TWO])
@pytest.mark.parametrize("decisions", [False, True])
@pytest.mark.parametrize("selector", [False, True])
def test_the_union_path_never_passes_the_bound_on_a_short_source(
    title: str, reply: str, decisions: bool, selector: bool
) -> None:
    text = _source_of(_SHORT)

    class _NoRaise(_Adversary):
        # Below the threshold an extraction failure propagates; the most a
        # reply can spend there is the judge's and the pass's retries.
        def chat(self, messages: Sequence[Message]) -> str:
            system = messages[0].get("content") if messages else None
            if system == concept._SYSTEM_PROMPT:
                self.calls += 1
                return self._reply
            return super().chat(messages)

    llm = _NoRaise(reply)
    concept.extract_concept_union(
        text,
        source_title=title,
        llm=llm,
        transcript_decisions=decisions,
        selector_judge=selector,
    )
    assert llm.calls <= concept.max_extraction_calls(
        text,
        union_judge=True,
        transcript_decisions=decisions,
        selector_judge=selector,
    )


@pytest.mark.parametrize("title", [_PROSE_TITLE, _MEETING_TITLE])
@pytest.mark.parametrize("chars", [_SHORT, _CHUNKED])
def test_the_legacy_path_never_passes_the_bound(title: str, chars: int) -> None:
    text = _source_of(chars)
    llm: _Adversary | _EmptyOnce = (
        _EmptyOnce(_SOLE) if chars == _SHORT else _Adversary(_SOLE)
    )
    concept.extract_concept(text, source_title=title, llm=llm)
    assert llm.calls <= concept.max_extraction_calls(
        text, union_judge=False, transcript_decisions=False
    )


def test_the_bound_is_never_below_the_ordinary_estimate() -> None:
    for chars in (_SHORT, 13_000, 19_000, _CHUNKED):
        text = _source_of(chars)
        for title in (_PROSE_TITLE, _MEETING_TITLE):
            for union, decisions, selector in itertools.product(
                (False, True), repeat=3
            ):
                estimate = concept.estimate_extraction_calls(
                    text,
                    source_title=title,
                    union_judge=union,
                    transcript_decisions=decisions,
                    selector_judge=selector,
                ).calls
                bound = concept.max_extraction_calls(
                    text,
                    union_judge=union,
                    transcript_decisions=decisions,
                    selector_judge=selector,
                )
                assert bound >= estimate


def test_the_bound_on_a_short_source_is_the_sum_of_every_mechanism() -> None:
    """Two passes, the re-ask, the meeting Event's call with its re-ask (#1689),
    the participant pass and the decision pass with its retry (a short source is
    one window), and, with the selector judge on, both judge attempts: the
    meeting-shaped reading is the costlier one, and the bound takes it whatever
    the title. The judge's attempts are absent by default (ADR-0080)."""
    text = _source_of(_SHORT)
    assert concept.max_extraction_calls(
        text, union_judge=True, transcript_decisions=True, selector_judge=True
    ) == (2 + 1 + 2 + 1 + 2 + judge.JUDGE_ATTEMPTS)
    assert concept.max_extraction_calls(
        text, union_judge=True, transcript_decisions=True
    ) == (2 + 1 + 2 + 1 + 2)
    assert concept.max_extraction_calls(
        text, union_judge=False, transcript_decisions=False
    ) == (2 + 1 + 2)


def test_blank_text_needs_no_call() -> None:
    assert (
        concept.max_extraction_calls(
            "  \n", union_judge=True, transcript_decisions=True
        )
        == 0
    )
