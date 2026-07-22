"""Unit tests for `resolution/volatility_typing.py`: the config-free LLM
volatility-tier suggestion leaf over concept TYPES present in a bundle
(freshness-suggest-windows, S2 -- `suggest-volatility`).

All tests use a `tmp_path` bundle and a reply-QUEUE fake `LLMBackend`
(records `.calls`, returns queued replies in call order) -- zero network,
zero real Ollama process. Mirrors `_FakeLLM` in
`tests/unit/resolution/test_edge_typing.py`.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.model import types
from openkos.resolution import volatility_typing as volatility_typing_mod


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns queued
    replies in call order. If `error` is set, `chat` raises it instead of
    returning (and does not consume a queued reply)."""

    def __init__(
        self, replies: Sequence[str] = (), *, error: BaseException | None = None
    ) -> None:
        self._replies = list(replies)
        self.error = error
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return self._replies.pop(0)


def _write_doc(
    path: Path, *, doc_type: str = "Person", title: str = "Stub", body: str = "Body."
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n{body}", encoding="utf-8"
    )


def _valid_reply(tier: str = "slow", rationale: str = "changes occasionally") -> str:
    return f'{{"tier": "{tier}", "rationale": "{rationale}"}}'


# ---------------------------------------------------------------------------
# `TierSuggestion`
# ---------------------------------------------------------------------------


def test_tier_suggestion_carries_type_name_default_tier_and_rationale() -> None:
    suggestion = volatility_typing_mod.TierSuggestion(
        type_name="Person",
        current_default="slow",
        suggested_tier="volatile",
        rationale="churns fast",
    )

    assert suggestion.type_name == "Person"
    assert suggestion.current_default == "slow"
    assert suggestion.suggested_tier == "volatile"
    assert suggestion.rationale == "churns fast"


def test_tier_suggestion_is_frozen() -> None:
    import dataclasses

    suggestion = volatility_typing_mod.TierSuggestion(
        type_name="Person", current_default="slow", suggested_tier=None, rationale=""
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        suggestion.rationale = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# `suggest_volatility` -- one suggestion per distinct type, sorted-name order
# ---------------------------------------------------------------------------


def test_suggest_volatility_returns_one_suggestion_per_distinct_type_sorted(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Project", title="A")
    _write_doc(tmp_path / "b.md", doc_type="Person", title="B")
    _write_doc(tmp_path / "c.md", doc_type="Person", title="C")
    llm = _FakeLLM(
        replies=[
            _valid_reply("slow", "people change slowly"),
            _valid_reply("volatile", "projects churn"),
        ]
    )

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert [s.type_name for s in result] == ["Person", "Project"]
    assert result[0].suggested_tier == "slow"
    assert result[0].rationale == "people change slowly"
    assert result[0].current_default == types.TYPE_TO_DEFAULT_VOLATILITY["Person"]
    assert result[1].suggested_tier == "volatile"
    assert result[1].current_default == types.TYPE_TO_DEFAULT_VOLATILITY["Project"]
    assert len(llm.calls) == 2


def test_suggest_volatility_one_llm_call_per_type_not_per_concept(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    _write_doc(tmp_path / "b.md", doc_type="Person", title="B")
    _write_doc(tmp_path / "c.md", doc_type="Person", title="C")
    llm = _FakeLLM(replies=[_valid_reply()])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert len(llm.calls) == 1


def test_suggest_volatility_on_empty_bundle_returns_empty_list_no_llm_calls(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM()

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert result == []
    assert llm.calls == []


def test_suggest_volatility_skips_docs_with_blank_type(tmp_path: Path) -> None:
    """A doc with no `type` frontmatter key (`LintDoc.type == ""`) is not a
    real concept type -- it must never surface as a `""`-named suggestion."""
    (tmp_path / "a.md").write_text("---\ntitle: Untyped\n---\nBody.", encoding="utf-8")
    _write_doc(tmp_path / "b.md", doc_type="Person", title="B")
    llm = _FakeLLM(replies=[_valid_reply()])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert [s.type_name for s in result] == ["Person"]
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# Fail-closed parse
# ---------------------------------------------------------------------------


def test_suggest_volatility_malformed_reply_degrades_only_that_type(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    _write_doc(tmp_path / "b.md", doc_type="Project", title="B")
    llm = _FakeLLM(
        replies=[
            _valid_reply("slow", "person rationale"),
            "not json at all -- garbage reply",
        ]
    )

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 2
    assert result[0].type_name == "Person"
    assert result[0].suggested_tier == "slow"
    assert result[1].type_name == "Project"
    assert result[1].suggested_tier is None
    assert result[1].rationale.strip() != ""


def test_suggest_volatility_invalid_tier_value_degrades_to_none(tmp_path: Path) -> None:
    """A reply whose `tier` is not a member of `VOLATILITY_TIERS` degrades to
    `suggested_tier=None`, never surfaced as if it were a valid tier."""
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=['{"tier": "eventually", "rationale": "bogus tier"}'])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier is None


def test_suggest_volatility_missing_tier_key_degrades_to_none(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=['{"rationale": "no tier field at all"}'])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier is None
    assert result[0].rationale == "no tier field at all"


def test_suggest_volatility_non_string_tier_field_degrades_to_none(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=['{"tier": 42, "rationale": "numeric tier"}'])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier is None
    assert result[0].rationale == "numeric tier"


def test_suggest_volatility_non_string_reply_degrades_to_none(tmp_path: Path) -> None:
    """A backend that violates the `-> str` contract (e.g. returns `None`)
    must not crash the parser (fail-closed: `_extract_json_object`'s
    non-string guard, mirrors `edge_typing.py`'s own equivalent test)."""
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=[None])  # type: ignore[list-item]

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier is None
    assert result[0].rationale == volatility_typing_mod._MALFORMED_REPLY_RATIONALE


def test_suggest_volatility_reply_that_is_valid_json_but_not_an_object_degrades(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=["[1, 2, 3]"])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier is None
    assert result[0].rationale == volatility_typing_mod._MALFORMED_REPLY_RATIONALE


def test_suggest_volatility_degrade_with_blank_rationale_falls_back_to_stable_text(
    tmp_path: Path,
) -> None:
    """A reply whose `tier` fails validation AND omits `rationale` entirely
    must not surface a blank rationale on the degrade path
    (`TierSuggestion.rationale` invariant: never blank on degrade)."""
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=['{"tier": "eventually"}'])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier is None
    assert result[0].rationale.strip() != ""


def test_suggest_volatility_non_string_tier_with_blank_rationale_falls_back(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(replies=['{"tier": 42, "rationale": "   "}'])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier is None
    assert result[0].rationale.strip() != ""


def test_suggest_volatility_valid_static_tier_is_accepted(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Place", title="A")
    llm = _FakeLLM(replies=[_valid_reply("static", "places rarely change")])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(result) == 1
    assert result[0].suggested_tier == "static"


# ---------------------------------------------------------------------------
# OllamaError propagation (unswallowed)
# ---------------------------------------------------------------------------


def test_suggest_volatility_propagates_ollama_error_unswallowed(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A")
    llm = _FakeLLM(error=OllamaUnavailable("not reachable"))

    with pytest.raises(OllamaUnavailable):
        volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)


# ---------------------------------------------------------------------------
# Deterministic input selection (spec: "Deterministic Input Selection")
# ---------------------------------------------------------------------------


def test_suggest_volatility_samples_first_five_concepts_by_sorted_identity(
    tmp_path: Path,
) -> None:
    """Six `Person` docs exist; only the FIRST N=5 by sorted `identity` are
    sampled into the single `llm.chat` call for that type."""
    for name in ("f", "d", "a", "c", "b", "e"):
        _write_doc(
            tmp_path / f"{name}.md", doc_type="Person", title=name, body=f"Body {name}."
        )
    llm = _FakeLLM(replies=[_valid_reply()])

    volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(llm.calls) == 1
    user_content = llm.calls[0][1]["content"]
    for included in ("Body a.", "Body b.", "Body c.", "Body d.", "Body e."):
        assert included in user_content
    assert "Body f." not in user_content


def test_suggest_volatility_truncates_each_body_to_1000_chars(tmp_path: Path) -> None:
    long_body = "x" * 2000
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A", body=long_body)
    llm = _FakeLLM(replies=[_valid_reply()])

    volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    user_content = llm.calls[0][1]["content"]
    assert "x" * 1001 not in user_content
    assert "x" * 1000 in user_content


def test_suggest_volatility_sampled_input_is_deterministic_across_two_calls(
    tmp_path: Path,
) -> None:
    """Same bundle yields the identical set+order of sampled bodies across
    two separate `suggest_volatility` invocations (spec's input-determinism
    scenario -- LLM output is NOT required to be deterministic, only what is
    shown to it)."""
    for name in ("b", "a", "c"):
        _write_doc(
            tmp_path / f"{name}.md", doc_type="Person", title=name, body=f"Body {name}."
        )
    llm_one = _FakeLLM(replies=[_valid_reply("slow", "first run")])
    llm_two = _FakeLLM(replies=[_valid_reply("volatile", "second run")])

    volatility_typing_mod.suggest_volatility(tmp_path, llm=llm_one)
    volatility_typing_mod.suggest_volatility(tmp_path, llm=llm_two)

    assert llm_one.calls == llm_two.calls


def test_suggest_volatility_types_iterated_in_sorted_name_order(tmp_path: Path) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Project", title="A")
    _write_doc(tmp_path / "b.md", doc_type="Decision", title="B")
    _write_doc(tmp_path / "c.md", doc_type="Concept", title="C")
    llm = _FakeLLM(replies=[_valid_reply(), _valid_reply(), _valid_reply()])

    result = volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert [s.type_name for s in result] == ["Concept", "Decision", "Project"]


# ---------------------------------------------------------------------------
# Prompt shape
# ---------------------------------------------------------------------------


def test_suggest_volatility_builds_a_two_message_json_only_prompt(
    tmp_path: Path,
) -> None:
    _write_doc(tmp_path / "a.md", doc_type="Person", title="A", body="Alpha body text.")
    llm = _FakeLLM(replies=[_valid_reply()])

    volatility_typing_mod.suggest_volatility(tmp_path, llm=llm)

    assert len(llm.calls) == 1
    messages = llm.calls[0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Person" in messages[1]["content"]
    assert "Alpha body text." in messages[1]["content"]
    assert "JSON" in messages[0]["content"]
