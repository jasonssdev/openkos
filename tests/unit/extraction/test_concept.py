"""Unit tests for `extraction/concept.py`: the classification prompt plus
fail-closed JSON parsing and validation.

All tests use a structural fake `LLMBackend` (mirrors `_FakeLLM` in
`tests/unit/retrieval/test_answer.py:41-50`) -- zero network, zero real
Ollama process.

`extract_concept` returns `list[ExtractionResult]` (zero to
`_MAX_OBJECTS_PER_SOURCE` items) -- see `sdd/multi-object-extraction`
design/spec: an empty list means "nothing worth extracting", array
membership is the positive per-item signal (no more `extract` field), and
`OllamaError` still propagates unswallowed.
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

    def __init__(self, reply: str = "[]") -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.reply


# Per-type item fixtures (no `extract` field -- array membership is the
# positive signal per D3; a bare `{...}` item, not a `[...]`-wrapped array).
_CONCEPT_ITEM = (
    '{"type": "Concept", "title": "Stoicism", '
    '"description": "A school of Hellenistic philosophy.", '
    '"body": "Founded by Zeno of Citium."}'
)

_ENTITY_ITEM = (
    '{"type": "Entity", "title": "Zettelkasten App", '
    '"description": "A note-taking tool.", "body": ""}'
)

_PERSON_ITEM = (
    '{"type": "Person", "title": "Epictetus", '
    '"description": "A Stoic philosopher and former slave.", '
    '"body": "Taught that we control only our own judgments."}'
)

_ORGANIZATION_ITEM = (
    '{"type": "Organization", "title": "Praxis Foundation", '
    '"description": "A nonprofit researching Stoic philosophy.", "body": ""}'
)

_PLACE_ITEM = (
    '{"type": "Place", "title": "Yellowstone National Park", '
    '"description": "A national park in the western United States.", '
    '"body": "Known for its geysers and geothermal features."}'
)

_EVENT_ITEM = (
    '{"type": "Event", "title": "Stoicon 2026", '
    '"description": "An annual conference on Stoic philosophy.", '
    '"body": "Held over a single weekend with talks and workshops."}'
)

_PROCEDURE_ITEM = (
    '{"type": "Procedure", "title": "Morning Journaling Routine", '
    '"description": "A repeatable daily reflection practice.", '
    '"body": "Write three things you are grateful for, then one obstacle."}'
)

_DECISION_ITEM = (
    '{"type": "Decision", "title": "Frame the Essay Around Control", '
    '"description": "A choice to structure the essay around the dichotomy of '
    'control, made after weighing two alternative framings.", '
    '"body": "Chosen over a chronological-biography framing because it better '
    'serves a practical audience; status: adopted."}'
)

_PROJECT_ITEM = (
    '{"type": "Project", "title": "Stoicism Essay Series", '
    '"description": "An ongoing series of essays on Stoic practice, running '
    'over several months toward a publishable collection.", '
    '"body": "Six essays planned across Q1-Q2, each drafted then revised."}'
)


def _array(*items: str) -> str:
    """Join item fixtures into a top-level JSON array reply."""
    return "[" + ", ".join(items) + "]"


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


# --- Happy path: valid Concept / Entity / ... per type -----------------------


def test_valid_concept_json_returns_extraction_result() -> None:
    """A well-formed `type: Concept` item parses into a matching `ExtractionResult`."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    result = concept_mod.extract_concept(
        "Stoicism is a school of philosophy.", source_title="Notes", llm=llm
    )

    assert result == [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Stoicism",
            description="A school of Hellenistic philosophy.",
            body="Founded by Zeno of Citium.",
        )
    ]


def test_valid_entity_json_returns_extraction_result() -> None:
    """A well-formed `type: Entity` item parses with `type == "Entity"`."""
    llm = _FakeLLM(reply=_array(_ENTITY_ITEM))

    result = concept_mod.extract_concept(
        "A note-taking tool.", source_title="Notes", llm=llm
    )

    assert len(result) == 1
    assert result[0].type == "Entity"
    assert result[0].title == "Zettelkasten App"
    assert result[0].description == "A note-taking tool."


def test_valid_person_json_returns_extraction_result() -> None:
    """A well-formed `type: Person` item parses with `type == "Person"`
    (spec: Person preferred over Entity for a named individual)."""
    llm = _FakeLLM(reply=_array(_PERSON_ITEM))

    result = concept_mod.extract_concept(
        "Epictetus was a Stoic philosopher.", source_title="Notes", llm=llm
    )

    assert len(result) == 1
    assert result[0].type == "Person"
    assert result[0].title == "Epictetus"
    assert result[0].description == "A Stoic philosopher and former slave."


def test_valid_organization_json_returns_extraction_result() -> None:
    """A well-formed `type: Organization` item parses with `type ==
    "Organization"` (spec: Organization preferred over Entity for a named
    company/institution)."""
    llm = _FakeLLM(reply=_array(_ORGANIZATION_ITEM))

    result = concept_mod.extract_concept(
        "The Praxis Foundation researches Stoicism.", source_title="Notes", llm=llm
    )

    assert len(result) == 1
    assert result[0].type == "Organization"
    assert result[0].title == "Praxis Foundation"
    assert result[0].description == "A nonprofit researching Stoic philosophy."


def test_valid_place_json_returns_extraction_result() -> None:
    """A well-formed `type: Place` item parses with `type == "Place"`
    (spec: "Source about a location classifies as Place")."""
    llm = _FakeLLM(reply=_array(_PLACE_ITEM))

    result = concept_mod.extract_concept(
        "Yellowstone is a national park known for its geysers.",
        source_title="Notes",
        llm=llm,
    )

    assert len(result) == 1
    assert result[0].type == "Place"
    assert result[0].title == "Yellowstone National Park"
    assert result[0].description == "A national park in the western United States."


def test_valid_event_json_returns_extraction_result() -> None:
    """A well-formed `type: Event` item parses with `type == "Event"`
    (spec: "Source about a bounded happening classifies as Event")."""
    llm = _FakeLLM(reply=_array(_EVENT_ITEM))

    result = concept_mod.extract_concept(
        "Stoicon 2026 is an annual conference on Stoic philosophy.",
        source_title="Notes",
        llm=llm,
    )

    assert len(result) == 1
    assert result[0].type == "Event"
    assert result[0].title == "Stoicon 2026"
    assert result[0].description == "An annual conference on Stoic philosophy."


def test_valid_procedure_json_returns_extraction_result() -> None:
    """A well-formed `type: Procedure` item parses with `type ==
    "Procedure"` (spec: "Source about a repeatable how-to classifies as
    Procedure")."""
    llm = _FakeLLM(reply=_array(_PROCEDURE_ITEM))

    result = concept_mod.extract_concept(
        "A daily morning journaling routine.", source_title="Notes", llm=llm
    )

    assert len(result) == 1
    assert result[0].type == "Procedure"
    assert result[0].title == "Morning Journaling Routine"
    assert result[0].description == "A repeatable daily reflection practice."


def test_valid_decision_json_returns_extraction_result() -> None:
    """A well-formed `type: Decision` item parses with `type == "Decision"`
    (spec: "Single-source self-narrating decision classifies as Decision")."""
    llm = _FakeLLM(reply=_array(_DECISION_ITEM))

    result = concept_mod.extract_concept(
        "We decided to frame the essay around the dichotomy of control.",
        source_title="Notes",
        llm=llm,
    )

    assert len(result) == 1
    assert result[0].type == "Decision"
    assert result[0].title == "Frame the Essay Around Control"
    assert "dichotomy of control" in result[0].description


def test_valid_project_json_returns_extraction_result() -> None:
    """A well-formed `type: Project` item parses with `type == "Project"`
    (spec: "Ongoing effort with a goal and timespan classifies as
    Project")."""
    llm = _FakeLLM(reply=_array(_PROJECT_ITEM))

    result = concept_mod.extract_concept(
        "A multi-month series of essays on Stoic practice.",
        source_title="Notes",
        llm=llm,
    )

    assert len(result) == 1
    assert result[0].type == "Project"
    assert result[0].title == "Stoicism Essay Series"
    assert "series" in result[0].description.lower()


# --- Tie-break regression guard: non-zero array position ---------------------


def test_second_array_item_resolves_decision_type() -> None:
    """A 2-item reply where item[1] is a Decision-shaped object is validated
    and classified independently of its array position -- the tie-break
    rubric (and validation) is NOT position-biased toward item[0] (design:
    per-object tie-break application; regression guard for Phase 5)."""
    llm = _FakeLLM(reply=_array(_PERSON_ITEM, _DECISION_ITEM))

    result = concept_mod.extract_concept(
        "Epictetus's biography, and the decision to frame the essay around "
        "the dichotomy of control.",
        source_title="Notes",
        llm=llm,
    )

    assert len(result) == 2
    assert result[0].type == "Person"
    assert result[1].type == "Decision"
    assert result[1].title == "Frame the Essay Around Control"


def test_second_array_item_resolves_organization_type() -> None:
    """A second regression fixture: item[1] is an Organization-shaped
    object in a 2-item reply, proving non-zero array positions are not
    dropped or mis-typed."""
    llm = _FakeLLM(reply=_array(_PLACE_ITEM, _ORGANIZATION_ITEM))

    result = concept_mod.extract_concept(
        "Yellowstone National Park, and the Praxis Foundation.",
        source_title="Notes",
        llm=llm,
    )

    assert len(result) == 2
    assert result[0].type == "Place"
    assert result[1].type == "Organization"
    assert result[1].title == "Praxis Foundation"


# --- Parsing: array reply shapes (D2) -----------------------------------------


def test_clean_json_array_is_parsed() -> None:
    """A clean top-level JSON array reply parses directly (parse step 1)."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert [r.title for r in result] == ["Stoicism", "Zettelkasten App"]


def test_json_array_wrapped_in_code_fence_is_parsed() -> None:
    """A ```json ... ``` fenced array reply is stripped and parsed (parse step 2)."""
    fenced = f"Here is the classification:\n```json\n{_array(_CONCEPT_ITEM)}\n```\n"
    llm = _FakeLLM(reply=fenced)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_json_array_embedded_in_prose_without_fence_is_parsed() -> None:
    """A reply with prose before/after the array is recovered by regex (parse step 3)."""
    prose = f"Sure, here you go: {_array(_CONCEPT_ITEM)} -- hope that helps!"
    llm = _FakeLLM(reply=prose)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_lone_top_level_object_is_recovered_as_single_item_list() -> None:
    """D2: a lone top-level `{...}` object (wrong shape -- not array-wrapped)
    is RECOVERED as a one-item list rather than failing closed to `[]`. A
    local LLM routinely emits a lone object for a single-object source; this
    is valid content on a shape technicality, not invalid data."""
    llm = _FakeLLM(reply=_CONCEPT_ITEM)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_lone_top_level_object_in_code_fence_is_recovered() -> None:
    """D2 recovery also applies through a fenced lone object (parse step 2
    feeding the same recovery path as parse step 1)."""
    fenced = f"```json\n{_CONCEPT_ITEM}\n```"
    llm = _FakeLLM(reply=fenced)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_bare_object_embedded_in_prose_without_brackets_is_recovered_by_step_four() -> (
    None
):
    """Parse step 4: a reply that fails steps 1-3 (prose, no fence, no
    `[...]` brackets) and carries a single bare `{...}` object is recovered
    by the greedy brace-block regex -- the ONLY step that can parse it."""
    prose = f"Sure, here is the object: {_CONCEPT_ITEM} hope this helps."
    llm = _FakeLLM(reply=prose)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_two_bare_objects_back_to_back_without_array_wrapping_returns_empty_list() -> (
    None
):
    """Two unwrapped bare objects back-to-back (`{...}{...}`, no `[...]`) are
    NOT recovered: the greedy step-4 regex spans first-brace-to-last-brace
    across both objects, that span fails `json.loads`, and the reply
    degrades to `[]` -- the intentional fail-closed outcome for malformed,
    non-array-wrapped multi-object replies (D2 recovers only a lone
    object)."""
    llm = _FakeLLM(reply=_CONCEPT_ITEM + _CONCEPT_ITEM)

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_array_with_non_dict_elements_filters_them_out() -> None:
    """An array containing non-dict elements (e.g. raw numbers) keeps only
    the dict elements rather than failing the whole reply closed."""
    llm = _FakeLLM(reply=f"[1, {_CONCEPT_ITEM}, 2]")

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_array_of_non_dict_items_returns_empty_list() -> None:
    """A reply that parses as a valid JSON array but contains no dict
    elements fails closed to `[]` (was: `None`, for a non-dict top-level
    value; now item-level filtering yields an empty list instead)."""
    llm = _FakeLLM(reply="[1, 2, 3]")

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_malformed_json_returns_empty_list() -> None:
    """A reply that is not JSON in any recoverable form fails closed to `[]`."""
    llm = _FakeLLM(reply="not json at all, sorry")

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_non_string_chat_reply_returns_empty_list() -> None:
    """A backend that violates the `chat -> str` contract by returning a
    non-string must not crash the regex-based parser -- it fails closed."""

    class _NonStringLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            return None  # type: ignore[return-value]

    result = concept_mod.extract_concept("text", source_title="t", llm=_NonStringLLM())

    assert result == []


def test_empty_array_reply_returns_empty_list() -> None:
    """An explicit empty array reply -- the model's positive "nothing worth
    extracting" signal -- returns `[]`."""
    llm = _FakeLLM(reply="[]")

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


# --- Per-item validation (D3): fail-closed, independent per candidate --------


def test_item_without_extract_field_still_validates() -> None:
    """D3: array membership is the positive extraction signal -- an item with
    no `extract` key at all (the new item shape) still validates normally."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_item_with_explicit_extract_true_still_validates() -> None:
    """D3: a model that still emits the retired `extract: true` flag is not
    penalized -- only an EXPLICIT `extract: false` is rejected."""
    item = (
        '{"extract": true, "type": "Concept", "title": "Stoicism", '
        '"description": "A school of Hellenistic philosophy.", "body": ""}'
    )
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_item_with_explicit_extract_false_is_dropped() -> None:
    """D3: an item carrying an EXPLICIT `extract: false` is dropped -- the
    one case the retired flag still has bite."""
    item = (
        '{"extract": false, "type": "Concept", "title": "Stoicism", '
        '"description": "A school of Hellenistic philosophy.", "body": ""}'
    )
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_invalid_type_item_is_dropped() -> None:
    """A `type` outside the closed `{Concept, Entity, Place, Event, Procedure,
    Decision, Project, Person, Organization}` set fails closed and is
    dropped. `"Animal"` is a genuinely invalid sentinel, outside the
    vocabulary in any batch (spec: "Classifier degrades on unknown type")."""
    item = '{"type": "Animal", "title": "T", "description": "D"}'
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_missing_title_item_is_dropped() -> None:
    """An empty `title` fails closed and is dropped."""
    item = '{"type": "Concept", "title": "", "description": "D"}'
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_missing_description_item_is_dropped() -> None:
    """An empty `description` fails closed and is dropped."""
    item = '{"type": "Concept", "title": "T", "description": ""}'
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_non_string_body_item_is_dropped() -> None:
    """A `body` of the wrong type is a structural violation -- fails closed."""
    item = '{"type": "Concept", "title": "T", "description": "D", "body": 42}'
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_non_string_type_item_is_dropped() -> None:
    """A `type` that is a list or dict (not a hashable string) fails closed
    rather than crashing on the `in _VALID_TYPES` membership check --
    otherwise a plausible malformed model reply would raise `TypeError`
    (not an `OllamaError`), which the caller would not catch and would crash
    ingestion instead of degrading to Source-only."""
    for bad_type in ('["Concept"]', '{"k": "v"}'):
        item = (
            '{"type": ' + bad_type + ', "title": "T", "description": "D", "body": ""}'
        )
        llm = _FakeLLM(reply=_array(item))

        result = concept_mod.extract_concept("text", source_title="t", llm=llm)

        assert result == []


def test_lowercase_type_item_is_dropped() -> None:
    """The closed vocabulary is case-sensitive: `"concept"` is not `"Concept"`."""
    item = '{"type": "concept", "title": "T", "description": "D", "body": ""}'
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_whitespace_only_title_item_is_dropped() -> None:
    """A whitespace-only `title` is empty after strip -- fails closed."""
    item = '{"type": "Concept", "title": "   ", "description": "D", "body": ""}'
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_blank_body_is_kept_as_empty_string() -> None:
    """A blank `body` is valid on its own -- the derived-object builder (not
    this module) is responsible for falling back to `description`."""
    item = '{"type": "Concept", "title": "T", "description": "D", "body": ""}'
    llm = _FakeLLM(reply=_array(item))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].body == ""


def test_mixed_valid_and_malformed_items_keeps_only_valid_ones() -> None:
    """spec: "Mixed valid and malformed candidates" -- a 3-item array where
    one candidate has a missing required field drops only that candidate,
    keeping the other 2 valid ones (order preserved)."""
    malformed = '{"type": "Concept", "title": "", "description": "D"}'
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, malformed, _ENTITY_ITEM))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert [r.title for r in result] == ["Stoicism", "Zettelkasten App"]


def test_all_items_malformed_returns_empty_list() -> None:
    """spec: "All candidates malformed" -- every candidate in the reply
    array fails validation, so the result is `[]`."""
    bad_1 = '{"type": "Animal", "title": "T", "description": "D"}'
    bad_2 = '{"type": "Concept", "title": "", "description": "D"}'
    llm = _FakeLLM(reply=_array(bad_1, bad_2))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


# --- CAP = 5, applied post-validation, first-5-in-reply-order ----------------


def test_exactly_five_valid_items_are_all_kept() -> None:
    """5 valid candidates -- exactly at the cap -- are all kept."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    items = [
        f'{{"type": "Concept", "title": "Item {i}", "description": "D"}}'
        for i in range(cap)
    ]
    llm = _FakeLLM(reply=_array(*items))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert [r.title for r in result] == [f"Item {i}" for i in range(cap)]


def test_more_than_five_valid_items_are_truncated_to_first_five_in_order() -> None:
    """spec: "LLM proposes more than CAP objects" -- 7 valid candidates are
    truncated to exactly 5, keeping the first 5 in reply order."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    items = [
        f'{{"type": "Concept", "title": "Item {i}", "description": "D"}}'
        for i in range(cap + 2)
    ]
    llm = _FakeLLM(reply=_array(*items))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert [r.title for r in result] == [f"Item {i}" for i in range(cap)]


def test_cap_applies_after_validation_not_before() -> None:
    """The cap counts only VALIDATED items: a malformed item ahead of 5 valid
    ones does not consume a cap slot, so all 5 valid items survive."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    malformed = '{"type": "Animal", "title": "T", "description": "D"}'
    valid_items = [
        f'{{"type": "Concept", "title": "Item {i}", "description": "D"}}'
        for i in range(cap)
    ]
    llm = _FakeLLM(reply=_array(malformed, *valid_items))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert [r.title for r in result] == [f"Item {i}" for i in range(cap)]


# --- extract_concept: zero / one / N results, OllamaError propagation -------


def test_extract_concept_returns_empty_list_when_nothing_worth_extracting() -> None:
    """spec: "No objects worth extracting" -- `extract_concept` returns `[]`."""
    llm = _FakeLLM(reply="[]")

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert result == []


def test_extract_concept_returns_list_of_length_one() -> None:
    """spec: "Exactly one object extracted" -- a list of length 1."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 1


def test_extract_concept_returns_list_of_length_n_under_cap() -> None:
    """spec: "Multiple distinct objects extracted, under cap" -- 3 distinct,
    richly described objects yield a list of length 3, each a valid
    `ExtractionResult`."""
    llm = _FakeLLM(reply=_array(_PERSON_ITEM, _EVENT_ITEM, _DECISION_ITEM))

    result = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(result) == 3
    assert all(isinstance(r, concept_mod.ExtractionResult) for r in result)
    assert [r.type for r in result] == ["Person", "Event", "Decision"]


def test_ollama_error_propagates_unswallowed() -> None:
    """An `OllamaError`-family exception raised by `chat` is never caught here
    (mirrors `retrieval/answer.py`'s `chat` boundary) -- it propagates to the
    caller, which owns the degrade-to-Source-only UX. No new sentinel value
    represents LLM failure -- callers distinguish exception from `[]` exactly
    as they distinguished exception from `None` before this change."""

    class _ExplodingLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            raise OllamaUnavailable("Ollama not reachable")

    with pytest.raises(OllamaUnavailable):
        concept_mod.extract_concept("text", source_title="t", llm=_ExplodingLLM())


# --- Prompt contract ----------------------------------------------------------


def test_prompt_contains_vocabulary_and_heuristic() -> None:
    """The system prompt pins the classification contract: the closed
    `{Concept, Entity, Place, Event, Procedure, Decision, Project, Person,
    Organization}` vocabulary, the aboutness heuristic (a borrowed name is a
    label, not the subject), the Person/Organization/Place/Concept-outrank-
    Entity tie-break, and the JSON-array-only instruction."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

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
    assert '"Decision"' in system_content
    assert '"Project"' in system_content
    assert "nine" in system_content
    assert "fundamentally about" in system_content
    assert "borrowed" in system_content
    assert "fallback" in system_content
    assert "outrank" in system_content
    assert "JSON array" in system_content


def test_prompt_new_opening_frames_extraction_as_a_list_decision() -> None:
    """Phase 1 (D1): the prompt's opening framing moved from "decide whether
    it is worth extracting as ONE derived knowledge object" to a list
    decision applying the rubric to EACH object independently."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert (
        "decide which distinct derived knowledge objects, if any, it is "
        "worth extracting" in system_content
    )
    assert (
        "Apply the type rubric and tie-breaks below to EACH object "
        "independently" in system_content
    )
    assert "as ONE derived knowledge object" not in system_content


def test_prompt_contains_anti_enumeration_paragraph_verbatim() -> None:
    """Phase 1 (D1): the anti-enumeration paragraph is present verbatim,
    including the meeting-transcript -> Event+Decisions-not-5-Persons
    anchor and the closing "When in doubt, leave it out." (design #1115)."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert (
        "A source may be about more than one thing: extract each DISTINCT "
        "object the source is genuinely about." in system_content
    )
    assert "Prefer FEWER, RICHER objects over many shallow ones." in system_content
    assert "Do NOT enumerate every named entity" in system_content
    assert (
        "a meeting transcript is fundamentally about the meeting itself "
        "(an Event) and any Decisions reached" in system_content
    )
    assert (
        "NOT about each of the five participants named around the table; "
        "extract the Event and the Decisions, not five Person stubs" in system_content
    )
    assert "When in doubt, leave it out." in system_content


def test_prompt_json_array_template_shape() -> None:
    """Phase 1 (D1): the JSON shape moved from a single `{...}` object to a
    top-level `[{...}, ...]` array, and the per-item `extract` field was
    dropped from the template."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert "Return ONLY a JSON array" in system_content
    assert '[{"type": "Person"' in system_content
    assert "Do NOT wrap the array in an outer object." in system_content
    assert '"extract": true|false' not in system_content
    assert "Return [] if nothing is worth extracting." in system_content


def test_prompt_no_longer_forbids_decision() -> None:
    """The former guard forbidding `Decision` ("is NOT in this vocabulary
    and MUST NOT be emitted... never invent Decision") is retracted: the
    prompt no longer instructs the model to withhold `Decision` (spec:
    "Prompt no longer forbids Decision")."""
    llm = _FakeLLM(reply=_array(_DECISION_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert "MUST NOT be emitted" not in system_content
    assert "never invent" not in system_content


def test_prompt_pins_decision_vs_concept_and_event_disambiguation() -> None:
    """The system prompt gives positive Decision-vs-Concept-vs-Event
    disambiguation: a choice made with rationale/alternatives/status is a
    Decision, distinct from a general idea (Concept) or a dated happening
    (Event) (spec: "Decision disambiguates from Concept and Event")."""
    llm = _FakeLLM(reply=_array(_DECISION_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert '"Decision"' in system_content
    assert "rationale" in system_content
    assert "alternatives" in system_content
    assert "status" in system_content


def test_prompt_pins_project_vs_event_disambiguation() -> None:
    """The system prompt gives positive Project-vs-Event disambiguation: an
    ongoing effort defined by a goal and a timespan is a Project, distinct
    from a single bounded happening (Event) (spec: "Project disambiguates
    from Event")."""
    llm = _FakeLLM(reply=_array(_PROJECT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert '"Project"' in system_content
    assert "goal" in system_content
    assert "timespan" in system_content


def test_prompt_pins_landmark_named_after_person_tie_break() -> None:
    """The system prompt's tie-break prose resolves the KOM-silent
    landmark-named-after-a-person case explicitly: a site honoring a person
    or organization is `Place` ONLY when the source is about the physical
    site itself; when the source is about the honoree, it is Person or
    Organization instead (design: Decision 2, "Landmark named after a
    person/org"). This pins the PROMPT's encoded preference, not an actual
    LLM's output -- classification itself is not deterministic Python code."""
    llm = _FakeLLM(reply=_array(_PERSON_ITEM))

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
    llm = _FakeLLM(reply=_array(_EVENT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert '"Event"' in system_content
    assert "genuinely about the location itself" in system_content
    assert "bounded, dated happening" in system_content


def test_prompt_pins_urbanism_example_under_name_vs_concept_tie_break() -> None:
    """The "urbanism" general-geographic-idea example concerns the Concept-
    vs-Place distinction, so it lives under tie-break (1) ("Name vs. denoted
    concept"), not under the Entity-outranking tie-break (3). This pins the
    3 tie-break `.index()` positions unchanged (Phase 1, tie-break chain
    kept VERBATIM)."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

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
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

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
