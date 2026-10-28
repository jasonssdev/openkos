"""`selector_judge` off: the union extractor makes no judge call (ADR-0080).

The union-of-runs extractor keeps its ceiling of 24 and its positional backstop
of 20; the selector judge's cut between them is opt-in. These tests pin the
default (no judge call, `judge_status="skipped"`, backstop applies to the
ceiling-bounded set) and that `selector_judge=True` restores the judge.
"""

from collections.abc import Sequence

from openkos.extraction import concept as concept_mod
from openkos.extraction import judge as judge_mod
from openkos.llm.base import Message


def _item(i: int) -> str:
    return (
        f'{{"type": "Concept", "title": "Subject {i:02d}", '
        f'"description": "Distinct subject {i}.", "body": ""}}'
    )


def _array(lo: int, hi: int) -> str:
    return "[" + ", ".join(_item(i) for i in range(lo, hi + 1)) + "]"


class _Recorder:
    """Answers extraction calls from a fixed pair and records judge calls."""

    def __init__(self, run1: str, run2: str, judge_reply: str = "[]") -> None:
        self._runs = [run1, run2]
        self._judge_reply = judge_reply
        self.extraction_calls = 0
        self.judge_calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        if messages[0].get("content") == judge_mod._JUDGE_SYSTEM_PROMPT:
            self.judge_calls += 1
            return self._judge_reply
        reply = self._runs[min(self.extraction_calls, 1)]
        self.extraction_calls += 1
        return reply


def test_default_makes_no_judge_call_and_reports_skipped() -> None:
    llm = _Recorder(_array(1, 3), _array(2, 4))

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert llm.judge_calls == 0
    assert llm.extraction_calls == 2
    assert outcome.report.judge_status == "skipped"
    assert outcome.report.judged_out_titles == ()
    assert [o.title for o in outcome.objects] == [
        f"Subject {i:02d}" for i in range(1, 5)
    ]


def test_default_applies_the_backstop_over_the_ceiling_bounded_set() -> None:
    # 25 distinct subjects: the ceiling keeps 24, the backstop keeps 20.
    llm = _Recorder(_array(1, 21), _array(15, 25))

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert llm.judge_calls == 0
    assert outcome.report.pre_judge_dropped == 1
    assert len(outcome.objects) == 20
    assert len(outcome.report.discarded_titles) == 4  # 24 kept - 20 backstop
    assert outcome.report.judge_failure_causes == ()


def test_selector_judge_true_restores_the_judge_call() -> None:
    keep = '{"keep": ["Subject 01", "Subject 02"]}'
    llm = _Recorder(_array(1, 3), _array(2, 4), judge_reply=keep)

    outcome = concept_mod.extract_concept_union(
        "Notes.", source_title="Notes", llm=llm, selector_judge=True
    )

    assert llm.judge_calls == 1
    assert outcome.report.judge_status == "ok"
    assert [o.title for o in outcome.objects] == ["Subject 01", "Subject 02"]


def test_estimate_drops_the_judge_call_unless_selector_judge() -> None:
    off = concept_mod.estimate_extraction_calls("Notes.", source_title="Notes")
    on = concept_mod.estimate_extraction_calls(
        "Notes.", source_title="Notes", selector_judge=True
    )
    assert (off.calls, on.calls) == (2, 3)


def test_max_calls_drops_the_judge_attempts_unless_selector_judge() -> None:
    off = concept_mod.max_extraction_calls(
        "Notes.", union_judge=True, transcript_decisions=False
    )
    on = concept_mod.max_extraction_calls(
        "Notes.", union_judge=True, transcript_decisions=False, selector_judge=True
    )
    assert on - off == judge_mod.JUDGE_ATTEMPTS
