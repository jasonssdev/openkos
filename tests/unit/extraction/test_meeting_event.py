"""The meeting Event kept beside its subjects (#1689): the pure parts.

Pins the speaker roster (one definition of a speaker shared with the meeting
gate), the candidate choice, the participant check, the frozen compose prompt
and the fail-closed compose call. The wiring into both extraction paths is in
`test_meeting_event_wiring.py`.
"""

from collections.abc import Sequence

import pytest

from openkos.extraction import concept as concept_mod
from openkos.extraction import meeting_event as me
from openkos.llm import prompts
from openkos.llm.base import BackendUnavailable, Message


def _talk(speakers: Sequence[str], rounds: int = 3, *, fmt: str = "{}: ") -> str:
    text = "Weekly review.\n\n"
    for r in range(rounds):
        for who in speakers:
            text += fmt.format(who) + f"Remark {r + 1} from {who}.\n\n"
    return text


def _event(title: str, type_: str = "Event") -> concept_mod.ExtractionResult:
    return concept_mod.ExtractionResult(
        type=type_, title=title, description="d", body="b"
    )


# --- the roster -------------------------------------------------------------


def test_roster_reads_named_speakers_in_order_of_first_appearance() -> None:
    assert concept_mod.speaker_roster(_talk(["Ana", "Luis"])) == ("Ana", "Luis")


def test_roster_reads_ami_labels() -> None:
    text = "".join(
        f"{who}: Remark {i}.\n" for i in range(1, 7) for who in ("PM", "ID", "UI")
    )
    assert concept_mod._transcript_shaped_text(text)
    assert concept_mod.speaker_roster(text) == ("PM", "ID", "UI")


def test_a_note_with_recurring_todo_and_note_lines_has_no_roster() -> None:
    text = (
        "Meeting summary.\n\nTODO: send the report\nNOTE: budget is tight\n"
        "TODO: book the room\nNOTE: Ana is away\nTODO: call Luis\n"
        "Some prose line one.\nSome prose line two.\nSome prose line three.\n"
    )
    assert not concept_mod._transcript_shaped_text(text)
    assert concept_mod.speaker_roster(text) == ()


def test_roster_is_empty_for_prose_and_for_one_speaker() -> None:
    assert concept_mod.speaker_roster("Just a paragraph of prose.") == ()
    assert concept_mod.speaker_roster(_talk(["Ana"])) == ()


def test_roster_needs_recurrence_three_for_a_name() -> None:
    assert concept_mod.speaker_roster(_talk(["Ana", "Luis"], rounds=2)) == ()


def test_roster_drops_what_the_meeting_gate_rejects() -> None:
    # label rules shared with the gate: any digit, three or more capitals
    odd = _talk(["Ana", "Luis", "Room 12", "ERROR"])
    assert concept_mod.speaker_roster(odd) == ("Ana", "Luis")


def test_roster_and_gate_share_one_definition_of_a_speaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _talk(["Ana", "Luis", "Eva"], rounds=4)
    assert concept_mod._transcript_shaped_text(text)
    assert concept_mod.speaker_roster(text) == ("Ana", "Luis", "Eva")
    # One seam: remove the line reader and BOTH stop seeing speakers.
    monkeypatch.setattr(concept_mod, "_speaker_turn_label", lambda line: None)
    assert not concept_mod._transcript_shaped_text(text)
    assert concept_mod.speaker_roster(text) == ()


# --- the candidate ----------------------------------------------------------


def test_candidate_is_the_best_grounded_event() -> None:
    source = "The product design meeting covered the remote."
    low = _event("Quarterly offsite")
    high = _event("Product Design Meeting")
    assert me.pick_candidate([low, high], source) is high


def test_candidate_tie_goes_to_the_earlier_position() -> None:
    first, second = _event("Meeting one"), _event("Meeting two")
    assert me.pick_candidate([first, second], "nothing shared") is first


def test_candidate_ignores_other_types_and_empty_input() -> None:
    assert me.pick_candidate([_event("Design meeting", "Project")], "design") is None
    assert me.pick_candidate([], "design") is None


def test_title_grounding_is_the_share_of_words_in_the_source() -> None:
    assert me.title_grounding("Design review", "a design doc") == 0.5
    assert me.title_grounding("", "x") == 0.0


# --- the participant check --------------------------------------------------


def test_participant_items_follow_a_participation_verb_only() -> None:
    body = "Los participantes fueron revisados. Participaron PM, ID y el equipo."
    assert me.participant_items(body) == ["PM", "ID", "equipo"]


def test_absent_participants_are_those_the_source_lacks() -> None:
    source = "PM: hola\nID: si\n"
    body = "Took part: PM, ID and other members of the team."
    assert me.absent_participants(body, source) == ["other members of the team"]
    assert me.absent_participants("A body with no roll call.", source) == []


# --- the frozen prompt ------------------------------------------------------


def test_prompt_is_byte_identical_to_the_measured_one() -> None:
    assert prompts.prompt_hash(me.SYSTEM_PROMPT) == "4f93593ea5bea022"


def test_messages_hand_over_the_roster_and_the_subjects() -> None:
    msgs = me.build_messages("SRC", "Weekly", ["A", "B", "A"], ("Ana", "Luis"))
    assert msgs[0] == {"role": "system", "content": me.SYSTEM_PROMPT}
    user = msgs[1]["content"]
    assert user.startswith(concept_mod._LANGUAGE_ANCHOR)
    assert "MEETING TITLE: Weekly" in user
    assert "SPEAKERS IN THE SOURCE (complete list): Ana, Luis" in user
    assert "EXTRACTED OBJECTS:\n- A\n- B\n\n" in user
    assert user.endswith("SOURCE TEXT:\nSRC")


def test_messages_say_none_labelled_for_an_empty_roster() -> None:
    user = me.build_messages("S", "T", ["A"], ())[1]["content"]
    assert "(complete list): none labelled" in user


# --- the compose call -------------------------------------------------------

_GOOD = (
    '{"description": "A weekly review.", "body": "'
    + "The team met and reviewed the work. Took part: Ana, Luis. "
    + "It produced Remote design and Pricing, both recorded below in detail."
    + '"}'
)
_SOURCE = _talk(["Ana", "Luis"])


class _Script:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[Sequence[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _compose(llm: _Script, source: str = _SOURCE) -> me.MeetingEventOutcome:
    return me.compose(
        source,
        candidate=_event("Weekly review"),
        subject_titles=["Remote design", "Pricing"],
        llm=llm,
    )


def test_compose_builds_the_event_from_the_reply() -> None:
    llm = _Script(_GOOD)
    out = _compose(llm)
    assert out.failure is None
    assert out.result is not None
    assert (out.result.type, out.result.title) == ("Event", "Weekly review")
    assert out.result.description == "A weekly review."
    assert out.result.body.startswith("The team met")
    assert len(llm.calls) == 1


def test_compose_asks_once_more_on_an_unparseable_reply() -> None:
    llm = _Script('{"description": "x", "body": "cut off', _GOOD)
    out = _compose(llm)
    assert out.result is not None
    assert len(llm.calls) == 2
    assert llm.calls[0] == llm.calls[1]


def test_compose_gives_up_after_the_second_unusable_reply() -> None:
    llm = _Script("no json", '{"description": "x"}')
    out = _compose(llm)
    assert out.result is None
    assert out.failure is None
    assert len(llm.calls) == 2


def test_compose_does_not_retry_a_raised_call() -> None:
    llm = _Script(RuntimeError("boom"))
    out = _compose(llm)
    assert out.result is None
    assert out.failure == "meeting_event: RuntimeError"
    assert len(llm.calls) == 1


def test_compose_lets_backend_unavailable_degrade_like_any_failure() -> None:
    out = _compose(_Script(BackendUnavailable("down")))
    assert out.result is None
    assert out.failure == "meeting_event: BackendUnavailable"


def test_compose_rejects_a_body_naming_an_absent_participant() -> None:
    bad = (
        '{"description": "d", "body": "'
        + "The team met and reviewed the work at length today. "
        + "Took part: Ana, Luis and other members of the team."
        + '"}'
    )
    out = _compose(_Script(bad))
    assert out.result is None
    assert out.failure is None


@pytest.mark.parametrize("body", ["too short", "x" * 1501])
def test_compose_rejects_a_body_out_of_bounds(body: str) -> None:
    out = _compose(_Script('{"description": "d", "body": "' + body + '"}'))
    assert out.result is None


def test_compose_bounds_both_ends_inclusively() -> None:
    for size in (80, 1500):
        body = "x" * size
        out = _compose(_Script('{"description": "d", "body": "' + body + '"}'))
        assert out.result is not None


def test_compose_reports_a_bounded_prompt() -> None:
    llm = _Script(_GOOD)
    out = _compose(llm, "word " * 200_000 + _SOURCE)
    assert out.prompt_bounded is True


def test_call_name_is_the_report_constant() -> None:
    assert me.CALL_NAME == concept_mod.OPTIONAL_CALL_MEETING_EVENT == "meeting_event"


@pytest.mark.parametrize("value", ["null", "[1]", '{"a": 1}', "5"])
def test_a_non_string_description_falls_back_to_the_candidates(value: str) -> None:
    reply = '{"description": ' + value + ', "body": "' + "x" * 100 + '"}'
    out = _compose(_Script(reply))
    assert out.result is not None
    assert out.result.description == "d"


def test_a_blank_description_falls_back_to_the_candidates() -> None:
    reply = '{"description": "  ", "body": "' + "x" * 100 + '"}'
    out = _compose(_Script(reply))
    assert out.result is not None
    assert out.result.description == "d"


@pytest.mark.parametrize("value", ["null", "[1]", '{"a": 1}', "5"])
def test_a_non_string_body_is_unusable(value: str) -> None:
    out = _compose(_Script('{"description": "x", "body": ' + value + "}"))
    assert out.result is None
