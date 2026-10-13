"""Wiring of the transcript decision pass into `extract_concept_union`
behind `transcript_decisions` (#1348)."""

from collections.abc import Sequence

import pytest

from openkos.extraction import concept as concept_mod
from openkos.extraction import decision_pass as dp
from openkos.extraction import judge as judge_mod
from openkos.llm.base import BackendUnavailable, Message

_TITLE = "Weekly platform meeting"
_TEXT = (
    "Alice: Which chip should we use?\n"
    "Bob: The regular chip is cheaper.\n"
    "Alice: Okay, we'll go with the regular chip.\n"
    "Bob: Then the case is plastic.\n"
) * 3

_CONCEPT = (
    '{"type": "Concept", "title": "Chip choice", "description": "About chips.",'
    ' "body": "Chips were discussed."}'
)
_MAIN_DECISION = (
    '{"type": "Decision", "title": "Product Design Choices",'
    ' "description": "A topic bundle.", "body": "Alice: Which chip should we use?"}'
)
_CHIP_ITEM = (
    '{"subject": "chip", "value": "Use the regular chip.",'
    ' "evidence": "we\'ll go with the regular chip"}'
)
_CASE_ITEM = (
    '{"subject": "case", "value": "The case is plastic.",'
    ' "evidence": "the case is plastic"}'
)


class _RoutingLLM:
    """Answers by system prompt: the pass, the judge, and the main extraction."""

    def __init__(self, pass_reply: str | Exception, main: str) -> None:
        self.pass_reply = pass_reply
        self.main = main
        self.systems: list[str] = []

    def chat(self, messages: Sequence[Message]) -> str:
        system = messages[0]["content"]
        self.systems.append(system)
        if system == dp.build_messages("")[0]["content"]:
            if isinstance(self.pass_reply, Exception):
                raise self.pass_reply
            return self.pass_reply
        if system == judge_mod._JUDGE_SYSTEM_PROMPT:
            return '{"keep": ["Chip choice", "Product Design Choices"]}'
        if system == concept_mod._SYSTEM_PROMPT:
            return self.main
        return "[]"

    @property
    def pass_calls(self) -> int:
        return self.systems.count(dp.build_messages("")[0]["content"])


def _main(*items: str) -> str:
    return "[" + ", ".join(items) + "]"


def _run(
    llm: _RoutingLLM,
    *,
    on: bool,
    title: str = _TITLE,
    text: str = _TEXT,
) -> concept_mod.ExtractionOutcome:
    return concept_mod.extract_concept_union(
        text, source_title=title, llm=llm, transcript_decisions=on
    )


def _titles(outcome: concept_mod.ExtractionOutcome, type_: str) -> list[str]:
    return [o.title for o in outcome.objects if o.type == type_]


def test_key_off_never_calls_the_pass() -> None:
    llm = _RoutingLLM(_main(_CHIP_ITEM), _main(_CONCEPT, _MAIN_DECISION))
    outcome = _run(llm, on=False)
    assert llm.pass_calls == 0
    assert _titles(outcome, "Decision") == ["Product Design Choices"]


def test_key_defaults_off() -> None:
    llm = _RoutingLLM(_main(_CHIP_ITEM), _main(_CONCEPT, _MAIN_DECISION))
    outcome = concept_mod.extract_concept_union(_TEXT, source_title=_TITLE, llm=llm)
    assert llm.pass_calls == 0
    assert _titles(outcome, "Decision") == ["Product Design Choices"]


def test_key_on_non_meeting_source_never_calls_the_pass() -> None:
    llm = _RoutingLLM(_main(_CHIP_ITEM), _main(_CONCEPT, _MAIN_DECISION))
    outcome = _run(llm, on=True, title="Design notes", text="Prose about chips. " * 20)
    assert llm.pass_calls == 0
    assert _titles(outcome, "Decision") == ["Product Design Choices"]


def test_pass_decisions_replace_main_decisions_and_leave_other_types() -> None:
    llm = _RoutingLLM(_main(_CHIP_ITEM, _CASE_ITEM), _main(_CONCEPT, _MAIN_DECISION))
    outcome = _run(llm, on=True)
    assert llm.pass_calls == 1
    assert _titles(outcome, "Decision") == [
        "Use the regular chip",
        "The case is plastic",
    ]
    assert _titles(outcome, "Concept") == ["Chip choice"]
    assert outcome.report.decision_pass_added_titles == (
        "Use the regular chip",
        "The case is plastic",
    )


def test_pass_decisions_are_not_judged() -> None:
    llm = _RoutingLLM(_main(_CHIP_ITEM), _main(_CONCEPT, _MAIN_DECISION))
    outcome = _run(llm, on=True)
    judge_user = [m for m in llm.systems if m == judge_mod._JUDGE_SYSTEM_PROMPT]
    # one non-Decision candidate left: the judge is skipped, yet the pass
    # Decision is retained.
    assert judge_user == []
    assert "Use the regular chip" in _titles(outcome, "Decision")


def test_pass_decisions_survive_when_the_union_is_otherwise_empty() -> None:
    llm = _RoutingLLM(_main(_CHIP_ITEM), "[]")
    outcome = _run(llm, on=True)
    assert _titles(outcome, "Decision") == ["Use the regular chip"]


def test_pass_decisions_are_not_cut_by_the_backstop() -> None:
    many = _main(
        *(
            f'{{"type": "Concept", "title": "Topic {i:02d}", "description": "d",'
            f' "body": "b"}}'
            for i in range(22)
        )
    )
    llm = _RoutingLLM(_main(_CHIP_ITEM), many)
    outcome = _run(llm, on=True)
    assert len(_titles(outcome, "Concept")) == concept_mod._UNION_BACKSTOP
    assert _titles(outcome, "Decision") == ["Use the regular chip"]
    assert outcome.report.retained == len(outcome.objects)


@pytest.mark.parametrize("pass_reply", ["[]", BackendUnavailable("down"), "nonsense"])
def test_empty_or_failed_pass_keeps_main_decisions(
    pass_reply: str | Exception,
) -> None:
    llm = _RoutingLLM(pass_reply, _main(_CONCEPT, _MAIN_DECISION))
    outcome = _run(llm, on=True)
    assert llm.pass_calls == 1
    assert _titles(outcome, "Decision") == ["Product Design Choices"]
    assert outcome.report.decision_pass_added_titles == ()


def test_a_failed_pass_is_reported_not_raised() -> None:
    llm = _RoutingLLM(BackendUnavailable("down"), _main(_CONCEPT, _MAIN_DECISION))
    outcome = _run(llm, on=True)
    assert (
        f"{concept_mod.OPTIONAL_CALL_DECISION_PASS}: BackendUnavailable"
        in outcome.report.optional_call_failures
    )


def test_an_honest_empty_pass_reports_no_failure() -> None:
    llm = _RoutingLLM("[]", _main(_CONCEPT, _MAIN_DECISION))
    outcome = _run(llm, on=True)
    assert outcome.report.optional_call_failures == ()


def test_chunked_source_asks_once_per_window() -> None:
    speakers = ("Ana", "Beto", "Caro")
    lines = [f"{speakers[i % 3]}: turno {i:04d} " + "palabra " * 5 for i in range(700)]
    text = "\n".join(lines)
    windows = concept_mod._chunk_lines(text)
    assert len(windows) > 1
    llm = _RoutingLLM("[]", _main(_CONCEPT))
    _run(llm, on=True, title="Plain", text=text)
    assert llm.pass_calls == len(windows)


def test_estimate_counts_the_pass_windows_only_when_on_and_meeting_shaped() -> None:
    base = concept_mod.estimate_extraction_calls(_TEXT, source_title=_TITLE)
    on = concept_mod.estimate_extraction_calls(
        _TEXT, source_title=_TITLE, transcript_decisions=True
    )
    assert on.calls == base.calls + 1
    prose = concept_mod.estimate_extraction_calls(
        "Prose.", source_title="Notes", transcript_decisions=True
    )
    assert (
        prose.calls
        == concept_mod.estimate_extraction_calls("Prose.", source_title="Notes").calls
    )
    legacy = concept_mod.estimate_extraction_calls(
        _TEXT, source_title=_TITLE, union_judge=False, transcript_decisions=True
    )
    assert (
        legacy.calls
        == concept_mod.estimate_extraction_calls(
            _TEXT, source_title=_TITLE, union_judge=False
        ).calls
    )
