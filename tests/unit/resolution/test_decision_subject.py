"""Unit tests for `resolution/decision_subject.py`: the config-free
subject-pass leaf (decision-revision-detector, Slice 1 / S1).

All tests are pure or use a module-local `LLMBackend` double
(`_ScriptedLLM`/`_RaisingLLM`) -- zero network, zero real Ollama process.
"""

import hashlib
import json
from collections.abc import Sequence

import pytest

from openkos.extraction import evidence as evidence_mod
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.resolution import decision_subject


class _ScriptedLLM:
    """A structural `LLMBackend`: returns queued replies in call order,
    recording every call's messages."""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self._replies.pop(0)


class _RaisingLLM:
    """A structural `LLMBackend`: raises `error` on its `error_at`-th
    (1-based) call, otherwise returns the next queued reply."""

    def __init__(
        self,
        replies: Sequence[str],
        *,
        error: BaseException,
        error_at: int,
    ) -> None:
        self._replies = list(replies)
        self.error = error
        self.error_at = error_at
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if len(self.calls) == self.error_at:
            raise self.error
        return self._replies.pop(0)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        None,
        '{"no_subject_key": "x"}',
        '{"subject": 123}',
        '{"subject": ""}',
        '{"subject": "   "}',
        '{"subject": "' + ("x" * 201) + '"}',
    ],
)
def test_parse_subject_reply_rejects_malformed_subject_field(raw: object) -> None:
    assert decision_subject.parse_subject_reply(raw, "some body text") is None


def test_parse_subject_reply_yields_subject_value_and_evidence_on_a_well_formed_reply() -> (
    None
):
    body = "We will run billing on Postgres for the next quarter."
    raw = (
        '{"subject": "billing tool", "value": "Postgres", '
        '"evidence": "We will run billing on Postgres for the next quarter."}'
    )
    result = decision_subject.parse_subject_reply(raw, body)
    assert result == decision_subject.DecisionSubject(
        subject="billing tool",
        value="Postgres",
        evidence="We will run billing on Postgres for the next quarter.",
    )


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  Postgres  ", "Postgres"),
    ],
)
def test_parse_subject_reply_value_field(
    raw_value: str | None, expected: str | None
) -> None:
    raw = {"subject": "billing tool", "value": raw_value}
    result = decision_subject.parse_subject_reply(json.dumps(raw), "some body text")
    assert result is not None
    assert result.value == expected


@pytest.mark.parametrize(
    ("quote", "text", "expected"),
    [
        (
            "Priya owns the schema migration plan.",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "PRIYA owns THE schema migration plan.",
            "priya owns the schema migration plan.",
            True,
        ),
        (
            "Priya  owns\nthe schema migration plan.",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "Priya owns the schema migration plan",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "Priya owns the schema migration plan,",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "Priya owns the schema migration plan;",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "Priya owns the schema migration plan:",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "Priya owns the schema migration plan!",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "Priya owns the schema migration plan?",
            "Priya owns the schema migration plan.",
            True,
        ),
        (
            "A wholly different sentence.",
            "Priya owns the schema migration plan.",
            False,
        ),
        ("   ", "Priya owns the schema migration plan.", False),
        ("", "Priya owns the schema migration plan.", False),
    ],
)
def test_quoted_verbatim_normalization(quote: str, text: str, expected: bool) -> None:
    assert decision_subject.quoted_verbatim(quote, text) is expected


@pytest.mark.parametrize(
    "value",
    [
        "Priya owns the schema migration plan.",
        "PRIYA OWNS the schema   migration\nplan",
        "Priya owns the schema migration plan for Project Helios.",
        "A wholly different sentence",
        "",
        "   ",
    ],
)
def test_quoted_verbatim_parity_with_extraction_evidence_normalize(value: str) -> None:
    """`quoted_verbatim`'s normalization step agrees with
    `extraction.evidence._normalize` on the same table of strings, byte for
    byte (design.md Decision 5). Imports the private `_normalize` name for
    this test-time parity check ONLY -- production code never cross-imports
    it (`decision_subject._normalize` is its own module-local copy)."""
    assert decision_subject._normalize(value) == evidence_mod._normalize(value)


def test_parse_subject_reply_evidence_field() -> None:
    body = "Priya owns the schema migration plan for Project Helios."
    verbatim_raw = json.dumps(
        {
            "subject": "schema migration ownership",
            "value": "Priya",
            "evidence": "Priya owns the schema migration plan for Project Helios.",
        }
    )
    verbatim_result = decision_subject.parse_subject_reply(verbatim_raw, body)
    assert verbatim_result is not None
    assert (
        verbatim_result.evidence
        == "Priya owns the schema migration plan for Project Helios."
    )

    paraphrased_raw = json.dumps(
        {
            "subject": "schema migration ownership",
            "value": "Priya",
            "evidence": "Priya is responsible for the migration.",
        }
    )
    paraphrased_result = decision_subject.parse_subject_reply(paraphrased_raw, body)
    assert paraphrased_result is not None
    assert paraphrased_result.evidence is None
    # The subject stands even when the evidence is dropped.
    assert paraphrased_result.subject == "schema migration ownership"
    assert paraphrased_result.value == "Priya"


def test_subject_input_digest_changes_with_title_or_body_only() -> None:
    base = decision_subject.subject_input_digest("Billing tool", "We chose Postgres.")
    title_changed = decision_subject.subject_input_digest(
        "Billing decision", "We chose Postgres."
    )
    body_changed = decision_subject.subject_input_digest(
        "Billing tool", "We chose SQLite."
    )
    stable = decision_subject.subject_input_digest("Billing tool", "We chose Postgres.")

    assert title_changed != base
    assert body_changed != base
    assert stable == base


def test_subject_prompt_version_is_derived_from_the_system_prompt() -> None:
    expected = hashlib.sha256(
        decision_subject._SUBJECT_SYSTEM_PROMPT.encode()
    ).hexdigest()[:16]
    assert expected == decision_subject.SUBJECT_PROMPT_VERSION


def test_build_subject_messages_shape() -> None:
    messages = decision_subject.build_subject_messages(
        "decisions/use-postgres", "Use Postgres", "We chose Postgres for billing."
    )
    assert len(messages) == 2
    assert messages[0] == {
        "role": "system",
        "content": decision_subject._SUBJECT_SYSTEM_PROMPT,
    }
    assert messages[1] == {
        "role": "user",
        "content": "[decisions/use-postgres — Use Postgres]\nWe chose Postgres for billing.",
    }


def test_derive_subjects_partial_batch_on_llm_failure() -> None:
    requests = [
        decision_subject.SubjectRequest(
            concept_id=f"decisions/d{i}", title=f"Decision {i}", body=f"Body {i}."
        )
        for i in range(3)
    ]
    well_formed_reply = json.dumps({"subject": "topic", "value": "x", "evidence": ""})
    error = OllamaUnavailable("backend down")
    llm = _RaisingLLM([well_formed_reply], error=error, error_at=2)

    batch = decision_subject.derive_subjects(requests, llm=llm)

    assert len(batch.results) == 1
    assert batch.failure is error
    assert batch.failed_index == 2
    assert len(llm.calls) == 2


def test_derive_subjects_persists_only_well_formed_results() -> None:
    requests = [
        decision_subject.SubjectRequest(
            concept_id="decisions/malformed", title="Malformed", body="Body one."
        ),
        decision_subject.SubjectRequest(
            concept_id="decisions/well-formed", title="Well formed", body="Body two."
        ),
    ]
    malformed_reply = "not json"
    well_formed_reply = json.dumps(
        {"subject": "topic", "value": "chosen value", "evidence": ""}
    )
    llm = _ScriptedLLM([malformed_reply, well_formed_reply])

    batch = decision_subject.derive_subjects(requests, llm=llm)

    assert batch.failure is None
    assert batch.results == [
        ("decisions/malformed", None),
        (
            "decisions/well-formed",
            decision_subject.DecisionSubject(
                subject="topic", value="chosen value", evidence=None
            ),
        ),
    ]


def test_derive_subjects_guards_only_the_chat_call() -> None:
    """The `OllamaError` guard wraps ONLY `llm.chat` (design.md, mirroring
    `find_contradictions`'s #441 contract): an `OllamaError` raised by the
    caller's own `on_progress` -- code that runs AFTER the guarded call --
    must still propagate untouched, never be caught and folded into
    `SubjectBatch.failure`. A guard widened past `llm.chat` to also cover
    `on_progress` would instead catch this and return a batch failure."""
    requests = [
        decision_subject.SubjectRequest(
            concept_id="decisions/only", title="Only", body="Body only."
        )
    ]
    llm = _ScriptedLLM([json.dumps({"subject": "topic", "value": "x", "evidence": ""})])
    progress_error = OllamaUnavailable("on_progress exploded")

    def _raising_progress(index: int, total: int, concept_id: str) -> None:
        raise progress_error

    with pytest.raises(OllamaUnavailable):
        decision_subject.derive_subjects(
            requests, llm=llm, on_progress=_raising_progress
        )
