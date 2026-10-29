"""Wiring of the meeting `Event` into both extraction paths (#1689)."""

import json
from collections.abc import Sequence

import pytest

from openkos.extraction import concept as concept_mod
from openkos.extraction import meeting_event as me
from openkos.llm.base import Message

_TITLE = "Weekly platform meeting"
_TEXT = (
    "Alice: Which chip should we use?\n"
    "Bob: The regular chip is cheaper.\n"
    "Alice: Okay, we'll go with the regular chip.\n"
    "Bob: Then the case is plastic.\n"
) * 3

_BODY = (
    "The team reviewed the platform work together. Took part: Alice, Bob. "
    "It produced the subjects recorded alongside it."
)
_COMPOSED = json.dumps({"description": "A weekly platform review.", "body": _BODY})


def _obj(type_: str, title: str) -> str:
    return json.dumps(
        {
            "type": type_,
            "title": title,
            "description": f"About {title}.",
            "body": "",
        }
    )


_EVENT = _obj("Event", "Platform meeting of October")


def _reply(*items: str) -> str:
    return "[" + ", ".join(items) + "]"


def _subjects(n: int) -> list[str]:
    return [_obj("Concept", f"Subject {i:02d}") for i in range(1, n + 1)]


class _LLM:
    """Extraction replies by system prompt; the compose call by its own."""

    def __init__(self, main: str, compose: str | Exception = _COMPOSED) -> None:
        self.main = main
        self.compose = compose
        self.compose_calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        system = messages[0]["content"]
        if system == me.SYSTEM_PROMPT:
            self.compose_calls += 1
            if isinstance(self.compose, Exception):
                raise self.compose
            return self.compose
        if system == concept_mod._SYSTEM_PROMPT:
            return self.main
        return "[]"


def _legacy(
    llm: _LLM, *, title: str = _TITLE, text: str = _TEXT
) -> concept_mod.ExtractionOutcome:
    return concept_mod.extract_concept(text, source_title=title, llm=llm)


def _union(
    llm: _LLM, *, title: str = _TITLE, text: str = _TEXT
) -> concept_mod.ExtractionOutcome:
    return concept_mod.extract_concept_union(text, source_title=title, llm=llm)


_PATHS = pytest.mark.parametrize("run", [_legacy, _union], ids=["legacy", "union"])


@_PATHS
def test_event_follows_the_subjects(run) -> None:  # type: ignore[no-untyped-def]
    llm = _LLM(_reply(*_subjects(3), _EVENT))
    outcome = run(llm)
    assert [(o.type, o.title) for o in outcome.objects] == [
        ("Concept", "Subject 01"),
        ("Concept", "Subject 02"),
        ("Concept", "Subject 03"),
        ("Event", "Platform meeting of October"),
    ]
    event = outcome.objects[-1]
    assert event.description == "A weekly platform review."
    assert event.body == _BODY
    assert llm.compose_calls == 1
    assert outcome.report.meeting_event_added_titles == ("Platform meeting of October",)
    assert outcome.report.retained == len(outcome.objects)
    assert outcome.report.produced == len(outcome.objects)
    assert outcome.report.optional_call_failures == ()


def test_legacy_event_sits_outside_the_cap() -> None:
    llm = _LLM(_reply(*_subjects(8), _EVENT))
    outcome = _legacy(llm)
    titles = [o.title for o in outcome.objects]
    assert titles == [f"Subject {i:02d}" for i in range(1, 7)] + [
        "Platform meeting of October"
    ]
    assert outcome.report.discarded_titles == ("Subject 07", "Subject 08")
    assert outcome.report.produced == 9
    assert outcome.report.retained == 7


def test_union_event_sits_outside_the_backstop() -> None:
    n = concept_mod._UNION_BACKSTOP + 2
    llm = _LLM(_reply(*_subjects(n), _EVENT))
    outcome = _union(llm)
    assert len(outcome.objects) == concept_mod._UNION_BACKSTOP + 1
    assert outcome.objects[-1].type == "Event"
    assert [o.title for o in outcome.objects[:-1]] == [
        f"Subject {i:02d}" for i in range(1, concept_mod._UNION_BACKSTOP + 1)
    ]


@_PATHS
def test_no_subject_no_event_and_no_call(run) -> None:  # type: ignore[no-untyped-def]
    llm = _LLM(_reply(_EVENT))
    outcome = run(llm)
    assert outcome.objects == []
    assert llm.compose_calls == 0
    assert outcome.report.meeting_event_added_titles == ()


@_PATHS
def test_only_an_event_is_a_candidate(run) -> None:  # type: ignore[no-untyped-def]
    llm = _LLM(_reply(*_subjects(2), _obj("Project", "Platform meeting project")))
    outcome = run(llm)
    assert [o.type for o in outcome.objects] == ["Concept", "Concept"]
    assert llm.compose_calls == 0


@_PATHS
def test_a_source_the_drop_does_not_apply_to_is_untouched(run) -> None:  # type: ignore[no-untyped-def]
    llm = _LLM(_reply(*_subjects(2), _EVENT))
    outcome = run(llm, title="Design notes", text="Prose about chips. " * 20)
    assert llm.compose_calls == 0
    assert outcome.report.meeting_event_added_titles == ()
    assert [o.type for o in outcome.objects] == ["Concept", "Concept", "Event"]
    assert outcome.objects[-1].description == "About Platform meeting of October."


@_PATHS
def test_a_failed_compose_keeps_the_subjects(run) -> None:  # type: ignore[no-untyped-def]
    llm = _LLM(_reply(*_subjects(2), _EVENT), compose=RuntimeError("boom"))
    outcome = run(llm)
    assert [o.type for o in outcome.objects] == ["Concept", "Concept"]
    assert outcome.report.meeting_event_added_titles == ()
    assert outcome.report.optional_call_failures == ("meeting_event: RuntimeError",)


@_PATHS
def test_a_refused_body_stores_no_event_and_no_failure(run) -> None:  # type: ignore[no-untyped-def]
    bad = json.dumps(
        {
            "description": "d",
            "body": "The team met for a long review of the work. "
            "Took part: Alice, Bob and the rest of the team.",
        }
    )
    llm = _LLM(_reply(*_subjects(2), _EVENT), compose=bad)
    outcome = run(llm)
    assert [o.type for o in outcome.objects] == ["Concept", "Concept"]
    assert outcome.report.optional_call_failures == ()


@_PATHS
def test_two_candidates_keep_the_better_grounded_title(run) -> None:  # type: ignore[no-untyped-def]
    weak = _obj("Event", "Quarterly offsite meeting")
    strong = _obj("Event", "Platform chip meeting")
    llm = _LLM(_reply(*_subjects(2), weak, strong))
    outcome = run(llm)
    assert [o.title for o in outcome.objects if o.type == "Event"] == [
        "Platform chip meeting"
    ]
    assert llm.compose_calls == 1


def test_the_source_title_twin_is_not_a_candidate() -> None:
    twin = _obj("Event", _TITLE)
    llm = _LLM(_reply(*_subjects(2), twin))
    outcome = _legacy(llm)
    assert [o.type for o in outcome.objects] == ["Concept", "Concept"]
    assert llm.compose_calls == 0


def test_the_subjects_are_what_they_were_without_the_event() -> None:
    plain = _legacy(_LLM(_reply(*_subjects(8))))
    with_event = _legacy(_LLM(_reply(*_subjects(8), _EVENT)))
    assert with_event.objects[:-1] == plain.objects


def test_the_event_is_not_marked_as_quoting_nothing() -> None:
    outcome = _legacy(_LLM(_reply(*_subjects(2), _EVENT)))
    assert "Platform meeting of October" not in outcome.report.unevidenced_titles


def test_union_decision_pass_subjects_keep_the_event_when_the_union_is_empty() -> None:
    from openkos.extraction import decision_pass as dp

    item = (
        '{"subject": "chip", "value": "Use the regular chip.",'
        ' "evidence": "we\'ll go with the regular chip"}'
    )

    class _Routing(_LLM):
        def chat(self, messages: Sequence[Message]) -> str:
            if messages[0]["content"] == dp.build_messages("")[0]["content"]:
                return "[" + item + "]"
            return super().chat(messages)

    llm = _Routing(_reply(_EVENT))
    outcome = concept_mod.extract_concept_union(
        _TEXT, source_title=_TITLE, llm=llm, transcript_decisions=True
    )
    assert [o.type for o in outcome.objects] == ["Decision", "Event"]
    assert outcome.report.meeting_event_added_titles == ("Platform meeting of October",)
    assert outcome.report.produced == outcome.report.retained == 2
