"""Unit tests for `extraction/judge.py`: the selector-judge's prompt build,
fail-closed reply parsing, and `select()` orchestration.

Mirrors `test_concept.py`'s structural-fake-LLM discipline (module docstring
there): zero network, zero real Ollama process. `judge.py` is a LEAF --
`llm.base`, `llm.parsing`, stdlib only, never `extraction.concept` (design
D2) -- so these tests never import `concept_mod`.
"""

from collections.abc import Sequence

import pytest

from openkos.extraction import judge as judge_mod
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns a fixed
    reply, or raises a fixed exception instead."""

    def __init__(self, reply: str = "", *, raises: Exception | None = None) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self.raises is not None:
            raise self.raises
        return self.reply


_CANDIDATES = (
    judge_mod.JudgeCandidate(
        type="Concept", title="Stoicism", description="A school of philosophy."
    ),
    judge_mod.JudgeCandidate(
        type="Person", title="Epictetus", description="A Stoic philosopher."
    ),
)


# --- prompt construction ------------------------------------------------


def test_select_embeds_source_text_and_every_candidate_title() -> None:
    """`select()` sends the source text and every candidate title in the
    chat messages it builds (design: closed candidate list, D3)."""
    llm = _FakeLLM('{"keep": ["Stoicism"]}')

    judge_mod.select("A source about self-control.", _CANDIDATES, llm)

    assert len(llm.calls) == 1
    sent = "\n".join(m["content"] for m in llm.calls[0])
    assert "A source about self-control." in sent
    assert "Stoicism" in sent
    assert "Epictetus" in sent


def test_judge_prompt_states_the_framing_rejection_rule() -> None:
    """#533: the judge is the only stage positioned to rescue tail-heavy
    replies, and the measured failure is that it kept the framing objects
    the extractor front-loads (`Meeting Discussion on Remote Control
    Design` at position 1 in 10 of 10 stored TS3005b runs, decision
    coverage flat at 4 of 14 WITH the judge in the loop). The system
    prompt must carry an explicit rejection of container/framing
    candidates -- this pins the clause so a prompt edit cannot silently
    drop it."""
    prompt = judge_mod._JUDGE_SYSTEM_PROMPT
    assert "names the source document, meeting, or gathering itself" in prompt
    assert "NEVER a genuine subject" in prompt


# --- valid reply parsing --------------------------------------------------


def test_select_returns_titles_in_reply_order_on_valid_keep_list() -> None:
    """A valid `{"keep": [...]}` reply returns titles in reply order,
    parsed via `parsing.extract_json_object` (design D3: a bare array is
    NOT the reply shape -- `extract_json_items` would silently return `[]`
    on it, since a bare string array has no dict elements)."""
    llm = _FakeLLM('{"keep": ["Epictetus", "Stoicism"]}')

    selected = judge_mod.select("source text", _CANDIDATES, llm)

    assert selected == ("Epictetus", "Stoicism")


def test_select_rejects_a_bare_json_array_reply() -> None:
    """Mutation guard (task 1.3): if `select()` used
    `parsing.extract_json_items` instead of `extract_json_object`, a bare
    JSON array of titles would silently parse to a one-item dict-wrapped
    list rather than failing -- this reply shape must be rejected."""
    llm = _FakeLLM('["Stoicism", "Epictetus"]')

    selected = judge_mod.select("source text", _CANDIDATES, llm)

    assert selected is None


# --- fail-closed reply validation ----------------------------------------


def test_select_returns_none_on_non_json_reply() -> None:
    llm = _FakeLLM("not json at all")

    assert judge_mod.select("source text", _CANDIDATES, llm) is None


def test_select_returns_none_when_keep_key_is_missing() -> None:
    llm = _FakeLLM('{"selected": ["Stoicism"]}')

    assert judge_mod.select("source text", _CANDIDATES, llm) is None


def test_select_returns_none_when_keep_is_not_a_list() -> None:
    llm = _FakeLLM('{"keep": "Stoicism"}')

    assert judge_mod.select("source text", _CANDIDATES, llm) is None


def test_select_returns_none_when_keep_has_non_string_elements() -> None:
    llm = _FakeLLM('{"keep": ["Stoicism", 7]}')

    assert judge_mod.select("source text", _CANDIDATES, llm) is None


def test_select_returns_none_when_keep_is_an_empty_list() -> None:
    llm = _FakeLLM('{"keep": []}')

    assert judge_mod.select("source text", _CANDIDATES, llm) is None


# --- llm.chat failure is total (design D7) --------------------------------


def test_select_returns_none_when_llm_chat_raises() -> None:
    """`select()` catches every exception from its own `llm.chat` call and
    returns `None` -- the judge is an optional refinement whose failure
    must never propagate (design D7)."""
    llm = _FakeLLM(raises=OllamaUnavailable("boom"))

    assert judge_mod.select("source text", _CANDIDATES, llm) is None


def test_select_does_not_propagate_an_arbitrary_exception() -> None:
    """Any exception from `llm.chat`, not only `OllamaError`, is caught."""
    llm = _FakeLLM(raises=RuntimeError("unexpected"))

    assert judge_mod.select("source text", _CANDIDATES, llm) is None


# --- module boundary (task 1.9) -------------------------------------------


def test_judge_module_never_imports_concept() -> None:
    """Static check: `judge.py` must never import `extraction.concept`
    (design D2 -- no cycle back into the orchestrator module)."""
    import ast
    from pathlib import Path

    source = Path(judge_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    assert not any("concept" in name for name in imported_modules)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
