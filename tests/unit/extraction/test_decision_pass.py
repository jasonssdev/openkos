"""The transcript decision pass core (#1348): pure admission rules plus one
LLM-backed function."""

from collections.abc import Sequence

import pytest

from openkos.extraction import decision_pass as dp
from openkos.llm.base import BackendGenerationCapped, BackendUnavailable, Message
from openkos.resolution.decision_subject import quoted_verbatim
from openkos.resolution.normalize import normalize_key

WINDOW_A = (
    "Alice: Which chip should we use?\n"
    "Bob: The regular chip is cheaper.\n"
    "Alice: Okay, we'll go with the regular chip.\n"
    "Bob: The case could be wood or plastic.\n"
)
WINDOW_B = (
    "Carol: Let's settle the case.\n"
    "Bob: Fine, the case will be plastic and coated in rubber.\n"
)


class _FakeLLM:
    def __init__(self, replies: Sequence[object]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, str)
        return reply


def _item(subject: str, value: str, evidence: str) -> str:
    return f'{{"subject": "{subject}", "value": "{value}", "evidence": "{evidence}"}}'


def test_valid_item_is_admitted_as_a_decision() -> None:
    reply = (
        "["
        + _item("chip", "Use the regular chip.", "we'll go with the regular chip")
        + "]"
    )
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([reply]))
    assert result.failure is None
    assert [r.type for r in result.results] == ["Decision"]
    assert result.results[0].title == "Use the regular chip"
    assert result.results[0].description == "chip: Use the regular chip."


def test_non_verbatim_evidence_is_dropped() -> None:
    reply = (
        "["
        + _item("chip", "Use the regular chip.", "they chose the regular chip")
        + "]"
    )
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([reply]))
    assert result.results == []
    assert result.failure is None


def test_evidence_must_come_from_its_own_window() -> None:
    reply = (
        "[" + _item("case", "The case is plastic.", "the case will be plastic") + "]"
    )
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([reply]))
    assert result.results == []


def test_malformed_json_is_a_failure_not_a_raise() -> None:
    llm = _FakeLLM(["I think they chose a chip.", "Still prose."])
    result = dp.run_decision_pass([WINDOW_A], llm)
    assert result.results == []
    assert result.failure == "decision_pass: unparseable reply"


def test_backend_error_is_a_failure_not_a_raise() -> None:
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([BackendUnavailable("down")]))
    assert result.results == []
    assert result.failure == "decision_pass: BackendUnavailable"
    assert "down" not in (result.failure or "")


def test_empty_array_is_zero_items_not_a_failure() -> None:
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM(["[]"]))
    assert result.results == []
    assert result.failure is None


def test_fenced_empty_array_is_not_a_failure() -> None:
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM(["```json\n[]\n```"]))
    assert result.failure is None


_CHIP = (
    "[" + _item("chip", "Use the regular chip.", "we'll go with the regular chip") + "]"
)


def test_unparseable_window_is_asked_once_more() -> None:
    llm = _FakeLLM(["prose, no json", _CHIP])
    result = dp.run_decision_pass([WINDOW_A], llm)
    assert [r.title for r in result.results] == ["Use the regular chip"]
    assert result.failure is None
    assert len(llm.calls) == 2
    assert llm.calls[0] == llm.calls[1]


def test_generation_error_window_is_asked_once_more() -> None:
    llm = _FakeLLM([BackendGenerationCapped("cap"), _CHIP])
    result = dp.run_decision_pass([WINDOW_A], llm)
    assert len(result.results) == 1
    assert result.failure is None
    assert len(llm.calls) == 2


def test_failed_retry_reports_the_retrys_cause_and_keeps_other_windows() -> None:
    good = "[" + _item("case", "The case is plastic.", "the case will be plastic") + "]"
    llm = _FakeLLM(["prose", BackendGenerationCapped("cap"), good])
    result = dp.run_decision_pass([WINDOW_A, WINDOW_B], llm)
    assert [r.title for r in result.results] == ["The case is plastic"]
    assert result.failure == "decision_pass: BackendGenerationCapped"
    assert len(llm.calls) == 3


def test_unavailable_backend_is_not_retried() -> None:
    llm = _FakeLLM([BackendUnavailable("down"), _CHIP])
    result = dp.run_decision_pass([WINDOW_A], llm)
    assert result.failure == "decision_pass: BackendUnavailable"
    assert len(llm.calls) == 1


def test_valid_empty_reply_is_not_retried() -> None:
    llm = _FakeLLM(["[]", _CHIP])
    result = dp.run_decision_pass([WINDOW_A], llm)
    assert result.results == []
    assert result.failure is None
    assert len(llm.calls) == 1


def test_parsed_reply_with_no_verbatim_items_is_not_retried() -> None:
    bad = "[" + _item("chip", "Use the regular chip.", "they never said this") + "]"
    llm = _FakeLLM([bad, _CHIP])
    result = dp.run_decision_pass([WINDOW_A], llm)
    assert result.results == []
    assert result.failure is None
    assert len(llm.calls) == 1


def test_one_failed_window_does_not_lose_the_others() -> None:
    good = "[" + _item("case", "The case is plastic.", "the case will be plastic") + "]"
    llm = _FakeLLM([BackendUnavailable("x"), good])
    result = dp.run_decision_pass([WINDOW_A, WINDOW_B], llm)
    assert [r.title for r in result.results] == ["The case is plastic"]
    assert result.failure == "decision_pass: BackendUnavailable"


def test_dedup_by_normalized_subject_first_wins_across_windows() -> None:
    first = (
        "["
        + _item(
            "Remote  Case", "The case is wood.", "The case could be wood or plastic"
        )
        + "]"
    )
    second = (
        "["
        + _item("remote case", "The case is plastic.", "the case will be plastic")
        + "]"
    )
    result = dp.run_decision_pass([WINDOW_A, WINDOW_B], _FakeLLM([first, second]))
    assert [r.description for r in result.results] == ["Remote Case: The case is wood."]


def test_cap_bounds_pass_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dp, "MAX_PASS_DECISIONS", 2)
    items = ",".join(
        _item(
            f"topic {n}", f"Chosen value number {n}.", "we'll go with the regular chip"
        )
        for n in range(5)
    )
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([f"[{items}]"]))
    assert [r.title for r in result.results] == [
        "Chosen value number 0",
        "Chosen value number 1",
    ]


def test_body_is_value_plus_quoted_evidence_and_nothing_else() -> None:
    reply = (
        "["
        + _item("chip", "Use the regular chip.", "we'll go with the regular chip")
        + "]"
    )
    body = dp.run_decision_pass([WINDOW_A], _FakeLLM([reply])).results[0].body
    assert "Use the regular chip." in body
    assert "we'll go with the regular chip" in body
    for other in ("Which chip should we use?", "wood or plastic", "cheaper"):
        assert other not in body


def test_items_missing_fields_are_dropped() -> None:
    reply = '[{"subject": "chip", "value": "", "evidence": "we\'ll go with the regular chip"}, {"subject": 3}]'
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([reply]))
    assert result.results == []
    assert result.failure is None


def test_each_call_carries_only_its_own_window() -> None:
    llm = _FakeLLM(["[]", "[]"])
    dp.run_decision_pass([WINDOW_A, WINDOW_B], llm)
    assert "regular chip" in llm.calls[0][1]["content"]
    assert "regular chip" not in llm.calls[1][1]["content"]
    assert "rubber" in llm.calls[1][1]["content"]


@pytest.mark.parametrize(
    ("quote", "text"),
    [
        ("We'll GO with  the chip.", "alice: we'll go with the chip, ok"),
        ("not there", "something else entirely"),
        ("", "anything"),
        ("...", "anything"),
    ],
)
def test_verbatim_matches_resolution_quoted_verbatim(quote: str, text: str) -> None:
    assert dp.quoted_verbatim(quote, text) == quoted_verbatim(quote, text)


def test_two_decisions_on_one_subject_get_distinct_titles() -> None:
    """Step 0 of T3: ingest's attach step merges two Decisions whose
    normalized titles match, which would erase the cross-meeting pair
    `openkos revisions` needs. The title therefore carries the VALUE."""
    first = dp.to_result(dp.DecisionItem("battery", "Use a solar cell.", "q"))
    later = dp.to_result(dp.DecisionItem("battery", "Use a lithium cell.", "q"))
    assert normalize_key(first.title) != normalize_key(later.title)


def test_the_same_decision_keeps_the_same_title_across_runs() -> None:
    one = dp.to_result(dp.DecisionItem("battery", "Use a solar cell.", "q"))
    two = dp.to_result(dp.DecisionItem("Battery", "Use a solar cell!", "q"))
    assert normalize_key(one.title) == normalize_key(two.title)


def test_the_title_is_bounded_at_a_word_boundary() -> None:
    value = "Adopt " + "very " * 60 + "long wording."
    title = dp.to_result(dp.DecisionItem("s", value, "q")).title
    assert len(title) <= dp.MAX_TITLE_CHARS
    assert value.startswith(title)
    assert not title.endswith(" ")


def test_the_subject_stays_available_in_the_description() -> None:
    result = dp.to_result(dp.DecisionItem("battery type", "Use solar.", "q"))
    assert result.description.startswith("battery type")


def test_min_value_words_is_three() -> None:
    assert dp.MIN_VALUE_WORDS == 3


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("We'll say two", True),  # exactly the floor: kept
        ("Yes we did", True),
        ("Use plastic", False),  # two words: below the floor
        ("two", False),
        ("  Use   plastic  ", False),  # whitespace never inflates the count
    ],
)
def test_value_below_the_word_floor_is_dropped(value: str, kept: bool) -> None:
    data = {"subject": "case", "value": value, "evidence": "q"}
    assert (dp.validate_item(data) is not None) is kept


def test_prompt_admits_imposed_requirements_and_excludes_actions() -> None:
    prompt = dp._SYSTEM_PROMPT
    assert "requirement or constraint" in prompt
    assert "without the transcript" in prompt
    assert "action items" in prompt
