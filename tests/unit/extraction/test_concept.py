"""Unit tests for `extraction/concept.py`: the classification prompt plus
fail-closed JSON parsing and validation.

All tests use a structural fake `LLMBackend` (mirrors `_FakeLLM` in
`tests/unit/retrieval/test_answer.py:41-50`) -- zero network, zero real
Ollama process.
"""

import ast
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos.extraction import concept as concept_mod
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns a fixed reply."""

    def __init__(self, reply: str = "{}") -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.reply


_CONCEPT_JSON = (
    '{"extract": true, "type": "Concept", "title": "Stoicism", '
    '"description": "A school of Hellenistic philosophy.", '
    '"body": "Founded by Zeno of Citium."}'
)

_ENTITY_JSON = (
    '{"extract": true, "type": "Entity", "title": "Zettelkasten App", '
    '"description": "A note-taking tool.", "body": ""}'
)


# --- Scaffold ---------------------------------------------------------------


def test_extraction_result_is_a_frozen_dataclass() -> None:
    """`ExtractionResult` carries type/title/description/body, and is immutable."""
    result = concept_mod.ExtractionResult(
        type="Concept", title="Stoicism", description="A philosophy.", body=""
    )

    assert result.type == "Concept"
    assert result.title == "Stoicism"
    assert result.description == "A philosophy."
    assert result.body == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.title = "Other"  # type: ignore[misc]


# --- Happy path: valid Concept / Entity -------------------------------------


def test_valid_concept_json_returns_extraction_result() -> None:
    """A well-formed `type: Concept` reply parses into a matching `ExtractionResult`."""
    llm = _FakeLLM(reply=_CONCEPT_JSON)

    result = concept_mod.extract_concept(
        "Stoicism is a school of philosophy.", source_title="Notes", llm=llm
    )

    assert result == concept_mod.ExtractionResult(
        type="Concept",
        title="Stoicism",
        description="A school of Hellenistic philosophy.",
        body="Founded by Zeno of Citium.",
    )


def test_valid_entity_json_returns_extraction_result() -> None:
    """A well-formed `type: Entity` reply parses with `type == "Entity"`."""
    llm = _FakeLLM(reply=_ENTITY_JSON)

    result = concept_mod.extract_concept(
        "A note-taking tool.", source_title="Notes", llm=llm
    )

    assert result is not None
    assert result.type == "Entity"
    assert result.title == "Zettelkasten App"
    assert result.description == "A note-taking tool."


# --- Parsing: fenced / prose-wrapped JSON -----------------------------------


def test_json_wrapped_in_code_fence_is_parsed() -> None:
    """A ```json ... ``` fenced reply is stripped and parsed (parse step 2)."""
    fenced = f"Here is the classification:\n```json\n{_CONCEPT_JSON}\n```\n"
    llm = _FakeLLM(reply=fenced)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is not None
    assert result.title == "Stoicism"


def test_json_embedded_in_prose_without_fence_is_parsed() -> None:
    """A reply with prose before/after the object is recovered by regex (parse step 3)."""
    prose = f"Sure, here you go: {_CONCEPT_JSON} -- hope that helps!"
    llm = _FakeLLM(reply=prose)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is not None
    assert result.title == "Stoicism"


# --- Fail-closed: malformed / invalid ---------------------------------------


def test_malformed_json_returns_none() -> None:
    """A reply that is not JSON in any recoverable form fails closed to `None`."""
    llm = _FakeLLM(reply="not json at all, sorry")

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_extract_false_returns_none() -> None:
    """`extract: false` means "nothing worth extracting" -- fails closed to `None`."""
    llm = _FakeLLM(reply='{"extract": false}')

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_invalid_type_returns_none() -> None:
    """A `type` outside `{Concept, Entity}` fails closed to `None`."""
    llm = _FakeLLM(
        reply='{"extract": true, "type": "Person", "title": "T", "description": "D"}'
    )

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_missing_title_returns_none() -> None:
    """An empty `title` fails closed to `None`."""
    llm = _FakeLLM(
        reply='{"extract": true, "type": "Concept", "title": "", "description": "D"}'
    )

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_missing_description_returns_none() -> None:
    """An empty `description` fails closed to `None`."""
    llm = _FakeLLM(
        reply='{"extract": true, "type": "Concept", "title": "T", "description": ""}'
    )

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_non_string_body_returns_none() -> None:
    """A `body` of the wrong type is a structural violation -- fails closed."""
    llm = _FakeLLM(
        reply=(
            '{"extract": true, "type": "Concept", "title": "T", '
            '"description": "D", "body": 42}'
        )
    )

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_non_string_type_returns_none() -> None:
    """A `type` that is a list or dict (not a hashable string) fails closed to
    `None` rather than crashing on the `in _VALID_TYPES` membership check --
    otherwise a plausible malformed model reply would raise `TypeError`
    (not an `OllamaError`), which the caller would not catch and would crash
    ingestion instead of degrading to Source-only."""
    for bad_type in ('["Concept"]', '{"k": "v"}'):
        llm = _FakeLLM(
            reply=(
                '{"extract": true, "type": ' + bad_type + ', '
                '"title": "T", "description": "D", "body": ""}'
            )
        )

        result = concept_mod.extract_concept("text", source_title="t", llm=llm)

        assert result is None


def test_lowercase_type_returns_none() -> None:
    """The closed vocabulary is case-sensitive: `"concept"` is not `"Concept"`."""
    llm = _FakeLLM(
        reply=(
            '{"extract": true, "type": "concept", "title": "T", '
            '"description": "D", "body": ""}'
        )
    )

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_whitespace_only_title_returns_none() -> None:
    """A whitespace-only `title` is empty after strip -- fails closed."""
    llm = _FakeLLM(
        reply=(
            '{"extract": true, "type": "Concept", "title": "   ", '
            '"description": "D", "body": ""}'
        )
    )

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_top_level_non_dict_json_returns_none() -> None:
    """A reply that parses as valid JSON but is not an object (e.g. an array)
    fails closed to `None`."""
    llm = _FakeLLM(reply="[1, 2, 3]")

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is None


def test_non_string_chat_reply_returns_none() -> None:
    """A backend that violates the `chat -> str` contract by returning a
    non-string must not crash the regex-based parser -- it fails closed."""

    class _NonStringLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            return None  # type: ignore[return-value]

    result = concept_mod.extract_concept("text", source_title="t", llm=_NonStringLLM())

    assert result is None


def test_blank_body_is_kept_as_empty_string() -> None:
    """A blank `body` is valid on its own -- the derived-object builder (not
    this module) is responsible for falling back to `description`."""
    llm = _FakeLLM(
        reply=(
            '{"extract": true, "type": "Concept", "title": "T", '
            '"description": "D", "body": ""}'
        )
    )

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result is not None
    assert result.body == ""


# --- OllamaError propagation -------------------------------------------------


def test_ollama_error_propagates_unswallowed() -> None:
    """An `OllamaError`-family exception raised by `chat` is never caught here
    (mirrors `retrieval/answer.py`'s `chat` boundary) -- it propagates to the
    caller, which owns the degrade-to-Source-only UX."""

    class _ExplodingLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            raise OllamaUnavailable("Ollama not reachable")

    with pytest.raises(OllamaUnavailable):
        concept_mod.extract_concept("text", source_title="t", llm=_ExplodingLLM())


# --- Prompt contract ----------------------------------------------------------


def test_prompt_contains_vocabulary_and_heuristic() -> None:
    """The system prompt pins the classification contract: the closed
    `{Concept, Entity}` vocabulary, the three-test heuristic, the
    prefer-specific-over-Entity rule, and the JSON-only instruction."""
    llm = _FakeLLM(reply=_CONCEPT_JSON)

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    assert len(llm.calls) == 1
    system_content = llm.calls[0][0]["content"]
    assert '"Concept"' in system_content
    assert '"Entity"' in system_content
    assert "distinct structure" in system_content
    assert "relationships" in system_content
    assert "recurrence" in system_content
    assert "fallback" in system_content
    assert "JSON object" in system_content


def test_prompt_carries_source_text_and_title() -> None:
    """The user message carries the raw source text and its title."""
    llm = _FakeLLM(reply=_CONCEPT_JSON)

    concept_mod.extract_concept(
        "a distinctive phrase zzqq", source_title="My Notes", llm=llm
    )

    user_content = llm.calls[0][1]["content"]
    assert "My Notes" in user_content
    assert "a distinctive phrase zzqq" in user_content


# --- Layering guard ------------------------------------------------------------


def test_extraction_and_llm_modules_do_not_import_config() -> None:
    """Neither `extraction/` nor `llm/` imports `openkos.config` (leaf discipline)."""
    dirs = [
        _REPO_ROOT / "src" / "openkos" / "extraction",
        _REPO_ROOT / "src" / "openkos" / "llm",
    ]
    modules: list[Path] = []
    for directory in dirs:
        modules.extend(sorted(directory.glob("*.py")))
    assert modules, "expected extraction/ and llm/ modules to exist"

    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert not any("config" in name for name in imported), (
            f"{path} imports config: {imported}"
        )
