"""Unit tests for `extraction/judge.py`: the selector-judge's prompt build,
fail-closed reply parsing, and `select()` orchestration.

Mirrors `test_concept.py`'s structural-fake-LLM discipline (module docstring
there): zero network, zero real Ollama process. `judge.py` is a LEAF --
`llm.base`, `llm.parsing`, stdlib only, never `extraction.concept` (design
D2) -- so these tests never import `concept_mod`.
"""

import json
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


# --- full-line echo salvage (#644) ----------------------------------------


def test_select_salvages_a_full_candidate_line_echo_with_single_quotes() -> None:
    """#644 (measured on a cold-start probe): instead of echoing the kept
    TITLE, the model sometimes echoes the WHOLE candidate line it was shown
    -- `type='Concept' title='Stoicism' description='...'`. That string is a
    valid `keep` entry to `_validate_selection`, but it matches no candidate
    title downstream, so the union degraded to the full unfiltered set.
    `select()` must resolve such an echo back to the bare candidate title."""
    line = "type='Concept' title='Stoicism' description='A school of philosophy.'"
    llm = _FakeLLM(json.dumps({"keep": [line]}))

    selected = judge_mod.select("source text", _CANDIDATES, llm)

    assert selected == ("Stoicism",)


def test_select_salvages_a_full_candidate_line_echo_with_double_quotes() -> None:
    """`_build_judge_messages` formats fields with `!r`, which switches to
    double quotes when the value contains an apostrophe -- so the echoed
    line can carry `title="..."` too. Both quote styles must salvage."""
    line = '2. type="Person" title="Epictetus" description="A Stoic philosopher."'
    llm = _FakeLLM(json.dumps({"keep": [line]}))

    selected = judge_mod.select("source text", _CANDIDATES, llm)

    assert selected == ("Epictetus",)


def test_select_salvage_replaces_a_case_drifted_echoed_title() -> None:
    """The salvage matches the extracted title against candidates with the
    same strip/casefold/whitespace-collapse normalization the union applies
    on both sides (design D4), and returns the candidate's EXACT title so
    the union's own matching cannot miss it."""
    line = "1. type='Concept' title='STOICISM' description='A school of philosophy.'"
    llm = _FakeLLM(json.dumps({"keep": [line]}))

    selected = judge_mod.select("source text", _CANDIDATES, llm)

    assert selected == ("Stoicism",)


def test_select_leaves_an_unresolvable_kept_string_as_is() -> None:
    """A kept string that neither matches a candidate title nor carries the
    candidate-line encoding stays as-is -- the union's closed-set matching
    already ignores it. Same for a full-line echo whose embedded title
    names no candidate: never invent a selection out of it."""
    fabricated = "type='Concept' title='Fabricated' description='Not a candidate.'"
    llm = _FakeLLM(json.dumps({"keep": ["utter garbage", fabricated, "Stoicism"]}))

    selected = judge_mod.select("source text", _CANDIDATES, llm)

    assert selected == ("utter garbage", fabricated, "Stoicism")


def test_select_leaves_a_clean_title_list_untouched() -> None:
    """A reply already echoing bare candidate titles passes through the
    salvage byte-identical, in reply order -- including alongside a
    full-line echo needing resolution."""
    line = "type='Concept' title='Stoicism' description='A school of philosophy.'"
    llm = _FakeLLM(json.dumps({"keep": ["Epictetus", line]}))

    selected = judge_mod.select("source text", _CANDIDATES, llm)

    assert selected == ("Epictetus", "Stoicism")


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
