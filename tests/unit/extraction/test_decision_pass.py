"""The transcript decision pass core (#1348): pure admission rules plus one
LLM-backed function. Inert -- nothing in `concept.py` calls it yet."""

from collections.abc import Sequence

import pytest

from openkos.extraction import decision_pass as dp
from openkos.llm.base import BackendUnavailable, Message
from openkos.resolution.decision_subject import quoted_verbatim

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
    assert result.results[0].title == "chip"
    assert result.results[0].description == "Use the regular chip."


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
    reply = "[" + _item("case", "Plastic case.", "the case will be plastic") + "]"
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([reply]))
    assert result.results == []


def test_malformed_json_is_a_failure_not_a_raise() -> None:
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM(["I think they chose a chip."]))
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


def test_one_failed_window_does_not_lose_the_others() -> None:
    good = "[" + _item("case", "Plastic case.", "the case will be plastic") + "]"
    llm = _FakeLLM([BackendUnavailable("x"), good])
    result = dp.run_decision_pass([WINDOW_A, WINDOW_B], llm)
    assert [r.title for r in result.results] == ["case"]
    assert result.failure == "decision_pass: BackendUnavailable"


def test_dedup_by_normalized_subject_first_wins_across_windows() -> None:
    first = (
        "[" + _item("Remote  Case", "Wood.", "The case could be wood or plastic") + "]"
    )
    second = "[" + _item("remote case", "Plastic.", "the case will be plastic") + "]"
    result = dp.run_decision_pass([WINDOW_A, WINDOW_B], _FakeLLM([first, second]))
    assert [r.description for r in result.results] == ["Wood."]


def test_cap_bounds_pass_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dp, "MAX_PASS_DECISIONS", 2)
    items = ",".join(
        _item(f"topic {n}", f"Value {n}.", "we'll go with the regular chip")
        for n in range(5)
    )
    result = dp.run_decision_pass([WINDOW_A], _FakeLLM([f"[{items}]"]))
    assert [r.title for r in result.results] == ["topic 0", "topic 1"]


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
