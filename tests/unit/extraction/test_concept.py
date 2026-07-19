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

_PERSON_JSON = (
    '{"extract": true, "type": "Person", "title": "Epictetus", '
    '"description": "A Stoic philosopher and former slave.", '
    '"body": "Taught that we control only our own judgments."}'
)

_ORGANIZATION_JSON = (
    '{"extract": true, "type": "Organization", "title": "Praxis Foundation", '
    '"description": "A nonprofit researching Stoic philosophy.", "body": ""}'
)

_PLACE_JSON = (
    '{"extract": true, "type": "Place", "title": "Yellowstone National Park", '
    '"description": "A national park in the western United States.", '
    '"body": "Known for its geysers and geothermal features."}'
)

_EVENT_JSON = (
    '{"extract": true, "type": "Event", "title": "Stoicon 2026", '
    '"description": "An annual conference on Stoic philosophy.", '
    '"body": "Held over a single weekend with talks and workshops."}'
)

_PROCEDURE_JSON = (
    '{"extract": true, "type": "Procedure", "title": "Morning Journaling Routine", '
    '"description": "A repeatable daily reflection practice.", '
    '"body": "Write three things you are grateful for, then one obstacle."}'
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


def test_valid_person_json_returns_extraction_result() -> None:
    """A well-formed `type: Person` reply parses with `type == "Person"`
    (spec: Person preferred over Entity for a named individual)."""
    llm = _FakeLLM(reply=_PERSON_JSON)

    result = concept_mod.extract_concept(
        "Epictetus was a Stoic philosopher.", source_title="Notes", llm=llm
    )

    assert result is not None
    assert result.type == "Person"
    assert result.title == "Epictetus"
    assert result.description == "A Stoic philosopher and former slave."


def test_valid_organization_json_returns_extraction_result() -> None:
    """A well-formed `type: Organization` reply parses with `type ==
    "Organization"` (spec: Organization preferred over Entity for a named
    company/institution)."""
    llm = _FakeLLM(reply=_ORGANIZATION_JSON)

    result = concept_mod.extract_concept(
        "The Praxis Foundation researches Stoicism.", source_title="Notes", llm=llm
    )

    assert result is not None
    assert result.type == "Organization"
    assert result.title == "Praxis Foundation"
    assert result.description == "A nonprofit researching Stoic philosophy."


def test_valid_place_json_returns_extraction_result() -> None:
    """A well-formed `type: Place` reply parses with `type == "Place"`
    (spec: "Source about a location classifies as Place")."""
    llm = _FakeLLM(reply=_PLACE_JSON)

    result = concept_mod.extract_concept(
        "Yellowstone is a national park known for its geysers.",
        source_title="Notes",
        llm=llm,
    )

    assert result is not None
    assert result.type == "Place"
    assert result.title == "Yellowstone National Park"
    assert result.description == "A national park in the western United States."


def test_valid_event_json_returns_extraction_result() -> None:
    """A well-formed `type: Event` reply parses with `type == "Event"`
    (spec: "Source about a bounded happening classifies as Event")."""
    llm = _FakeLLM(reply=_EVENT_JSON)

    result = concept_mod.extract_concept(
        "Stoicon 2026 is an annual conference on Stoic philosophy.",
        source_title="Notes",
        llm=llm,
    )

    assert result is not None
    assert result.type == "Event"
    assert result.title == "Stoicon 2026"
    assert result.description == "An annual conference on Stoic philosophy."


def test_valid_procedure_json_returns_extraction_result() -> None:
    """A well-formed `type: Procedure` reply parses with `type ==
    "Procedure"` (spec: "Source about a repeatable how-to classifies as
    Procedure")."""
    llm = _FakeLLM(reply=_PROCEDURE_JSON)

    result = concept_mod.extract_concept(
        "A daily morning journaling routine.", source_title="Notes", llm=llm
    )

    assert result is not None
    assert result.type == "Procedure"
    assert result.title == "Morning Journaling Routine"
    assert result.description == "A repeatable daily reflection practice."


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
    """A `type` outside the closed `{Concept, Entity, Place, Person,
    Organization}` set fails closed to `None`. `"Animal"` is a genuinely
    invalid sentinel, outside the vocabulary in any batch (spec: "Classifier
    degrades on unknown type")."""
    llm = _FakeLLM(
        reply='{"extract": true, "type": "Animal", "title": "T", "description": "D"}'
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
                '{"extract": true, "type": ' + bad_type + ", "
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
    `{Concept, Entity, Place, Event, Procedure, Person, Organization}`
    vocabulary, the aboutness heuristic (a borrowed name is a label, not the
    subject), the Person/Organization/Place/Concept-outrank-Entity
    tie-break, and the JSON-only instruction."""
    llm = _FakeLLM(reply=_CONCEPT_JSON)

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    assert len(llm.calls) == 1
    system_content = llm.calls[0][0]["content"]
    assert '"Concept"' in system_content
    assert '"Entity"' in system_content
    assert '"Person"' in system_content
    assert '"Organization"' in system_content
    assert '"Place"' in system_content
    assert '"Event"' in system_content
    assert '"Procedure"' in system_content
    assert "fundamentally about" in system_content
    assert "borrowed" in system_content
    assert "fallback" in system_content
    assert "outrank" in system_content
    assert "JSON object" in system_content


def test_prompt_pins_landmark_named_after_person_tie_break() -> None:
    """The system prompt's tie-break prose resolves the KOM-silent
    landmark-named-after-a-person case explicitly: a site honoring a person
    or organization is `Place` ONLY when the source is about the physical
    site itself; when the source is about the honoree, it is Person or
    Organization instead (design: Decision 2, "Landmark named after a
    person/org"). This pins the PROMPT's encoded preference, not an actual
    LLM's output -- classification itself is not deterministic Python code."""
    llm = _FakeLLM(reply=_PERSON_JSON)

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"].lower()
    assert "landmark" in system_content
    assert "honoree" in system_content
    assert "physical site" in system_content


def test_prompt_pins_event_at_a_place_tie_break() -> None:
    """The system prompt's tie-break prose states a positive outcome for an
    event that happens at a place: a source about a bounded, dated happening
    is `Event`, not `Place` -- `Place` is chosen only when the source is
    genuinely about the location itself as a site (spec: "Event-at-a-place
    disambiguates to Event, not Place"; design: Decision 2, retraction of
    the former "no Event type" claim)."""
    llm = _FakeLLM(reply=_EVENT_JSON)

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert '"Event"' in system_content
    assert "genuinely about the location itself" in system_content
    assert "bounded, dated happening" in system_content


def test_prompt_pins_urbanism_example_under_name_vs_concept_tie_break() -> None:
    """The "urbanism" general-geographic-idea example concerns the Concept-
    vs-Place distinction, so it lives under tie-break (1) ("Name vs. denoted
    concept"), not under the Entity-outranking tie-break (3)."""
    llm = _FakeLLM(reply=_CONCEPT_JSON)

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    rule_1_start = system_content.index("(1) Name vs. denoted concept")
    rule_2_start = system_content.index("(2) Among specific named continuants")
    rule_3_start = system_content.index("(3) Person, Organization, Place, and Concept")

    urbanism_index = system_content.index("urbanism")
    assert rule_1_start < urbanism_index < rule_2_start

    rule_3_text = system_content[rule_3_start:]
    assert "urbanism" not in rule_3_text


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
