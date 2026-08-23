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
import hashlib
import json
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from openkos.extraction import concept as concept_mod
from openkos.extraction import judge as judge_mod
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaGenerationCapped, OllamaUnavailable
from openkos.model.types import CLASSIFIABLE_TYPES

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


def _objects(*args: object, **kwargs: object) -> "list[concept_mod.ExtractionResult]":
    """`extract_concept(...).objects`.

    Every test below predates the #404 cap report and asserts on the object
    list alone; routing them through one accessor keeps those assertions
    about what they were written to check, instead of restating `.objects`
    forty-five times.
    """
    return concept_mod.extract_concept(*args, **kwargs).objects  # type: ignore[arg-type]


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

    result = _objects(
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

    result = _objects("A note-taking tool.", source_title="Notes", llm=llm)

    assert len(result) == 1
    assert result[0].type == "Entity"
    assert result[0].title == "Zettelkasten App"
    assert result[0].description == "A note-taking tool."


def test_valid_person_json_returns_extraction_result() -> None:
    """A well-formed `type: Person` item parses with `type == "Person"`
    (spec: Person preferred over Entity for a named individual)."""
    llm = _FakeLLM(reply=_array(_PERSON_ITEM))

    result = _objects(
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

    result = _objects(
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

    result = _objects(
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

    result = _objects(
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

    result = _objects(
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

    result = _objects(
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

    result = _objects(
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

    result = _objects(
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

    result = _objects(
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

    result = _objects("text", source_title="t", llm=llm)

    assert [r.title for r in result] == ["Stoicism", "Zettelkasten App"]


def test_json_array_wrapped_in_code_fence_is_parsed() -> None:
    """A ```json ... ``` fenced array reply is stripped and parsed (parse step 2)."""
    fenced = f"Here is the classification:\n```json\n{_array(_CONCEPT_ITEM)}\n```\n"
    llm = _FakeLLM(reply=fenced)

    result = _objects("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_json_array_embedded_in_prose_without_fence_is_parsed() -> None:
    """A reply with prose before/after the array is recovered by regex (parse step 3)."""
    prose = f"Sure, here you go: {_array(_CONCEPT_ITEM)} -- hope that helps!"
    llm = _FakeLLM(reply=prose)

    result = _objects("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_lone_top_level_object_is_recovered_as_single_item_list() -> None:
    """D2: a lone top-level `{...}` object (wrong shape -- not array-wrapped)
    is RECOVERED as a one-item list rather than failing closed to `[]`. A
    local LLM routinely emits a lone object for a single-object source; this
    is valid content on a shape technicality, not invalid data."""
    llm = _FakeLLM(reply=_CONCEPT_ITEM)

    result = _objects("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_lone_top_level_object_in_code_fence_is_recovered() -> None:
    """D2 recovery also applies through a fenced lone object (parse step 2
    feeding the same recovery path as parse step 1)."""
    fenced = f"```json\n{_CONCEPT_ITEM}\n```"
    llm = _FakeLLM(reply=fenced)

    result = _objects("text", source_title="t", llm=llm)

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

    result = _objects("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_two_bare_objects_back_to_back_without_array_wrapping_returns_empty_list() -> (
    None
):
    """Two unwrapped bare objects back-to-back (`{...}{...}`, no `[...]`) are
    NOT recovered -- the intentional fail-closed outcome for malformed,
    non-array-wrapped multi-object replies (D2 recovers only a lone object).

    This used to be a side effect of the greedy `\\{.*\\}` step spanning both
    objects and failing `json.loads`. That regex is gone, and the rule is
    now stated in `parsing.extract_json_items`: returning the FIRST of
    several bare objects would be silent truncation reported as success,
    which is worse than a visible zero."""
    llm = _FakeLLM(reply=_CONCEPT_ITEM + _CONCEPT_ITEM)

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_array_with_non_dict_elements_filters_them_out() -> None:
    """An array containing non-dict elements (e.g. raw numbers) keeps only
    the dict elements rather than failing the whole reply closed."""
    llm = _FakeLLM(reply=f"[1, {_CONCEPT_ITEM}, 2]")

    result = _objects("text", source_title="t", llm=llm)

    assert len(result) == 1
    assert result[0].title == "Stoicism"


def test_array_of_non_dict_items_returns_empty_list() -> None:
    """A reply that parses as a valid JSON array but contains no dict
    elements fails closed to `[]` (was: `None`, for a non-dict top-level
    value; now item-level filtering yields an empty list instead)."""
    llm = _FakeLLM(reply="[1, 2, 3]")

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_malformed_json_returns_empty_list() -> None:
    """A reply that is not JSON in any recoverable form fails closed to `[]`."""
    llm = _FakeLLM(reply="not json at all, sorry")

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_non_string_chat_reply_returns_empty_list() -> None:
    """A backend that violates the `chat -> str` contract by returning a
    non-string must not crash the regex-based parser -- it fails closed."""

    class _NonStringLLM:
        def chat(self, messages: Sequence[Message]) -> str:
            return None  # type: ignore[return-value]

    result = _objects("text", source_title="t", llm=_NonStringLLM())

    assert result == []


def test_empty_array_reply_returns_empty_list() -> None:
    """An explicit empty array reply -- the model's positive "nothing worth
    extracting" signal -- returns `[]`."""
    llm = _FakeLLM(reply="[]")

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


# --- Per-item validation (D3): fail-closed, independent per candidate --------


def test_item_without_extract_field_still_validates() -> None:
    """D3: array membership is the positive extraction signal -- an item with
    no `extract` key at all (the new item shape) still validates normally."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    result = _objects("text", source_title="t", llm=llm)

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

    result = _objects("text", source_title="t", llm=llm)

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

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_invalid_type_item_is_dropped() -> None:
    """A `type` outside the closed `{Concept, Entity, Place, Event, Procedure,
    Decision, Project, Person, Organization}` set fails closed and is
    dropped. `"Animal"` is a genuinely invalid sentinel, outside the
    vocabulary in any batch (spec: "Classifier degrades on unknown type")."""
    item = '{"type": "Animal", "title": "T", "description": "D"}'
    llm = _FakeLLM(reply=_array(item))

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_missing_title_item_is_dropped() -> None:
    """An empty `title` fails closed and is dropped."""
    item = '{"type": "Concept", "title": "", "description": "D"}'
    llm = _FakeLLM(reply=_array(item))

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_missing_description_item_is_dropped() -> None:
    """An empty `description` fails closed and is dropped."""
    item = '{"type": "Concept", "title": "T", "description": ""}'
    llm = _FakeLLM(reply=_array(item))

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_non_string_body_item_is_dropped() -> None:
    """A `body` of the wrong type is a structural violation -- fails closed."""
    item = '{"type": "Concept", "title": "T", "description": "D", "body": 42}'
    llm = _FakeLLM(reply=_array(item))

    result = _objects("text", source_title="t", llm=llm)

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

        result = _objects("text", source_title="t", llm=llm)

        assert result == []


def test_lowercase_type_item_is_dropped() -> None:
    """The closed vocabulary is case-sensitive: `"concept"` is not `"Concept"`."""
    item = '{"type": "concept", "title": "T", "description": "D", "body": ""}'
    llm = _FakeLLM(reply=_array(item))

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_whitespace_only_title_item_is_dropped() -> None:
    """A whitespace-only `title` is empty after strip -- fails closed."""
    item = '{"type": "Concept", "title": "   ", "description": "D", "body": ""}'
    llm = _FakeLLM(reply=_array(item))

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_blank_body_is_kept_as_empty_string() -> None:
    """A blank `body` is valid on its own -- the derived-object builder (not
    this module) is responsible for falling back to `description`."""
    item = '{"type": "Concept", "title": "T", "description": "D", "body": ""}'
    llm = _FakeLLM(reply=_array(item))

    # `source_title` deliberately differs from the object's title: `"t"`
    # made this validation test a sole source-title twin, which now spends a
    # re-ask call (#584) it has no business exercising.
    result = _objects("text", source_title="Notes", llm=llm)

    assert len(result) == 1
    assert result[0].body == ""


def test_mixed_valid_and_malformed_items_keeps_only_valid_ones() -> None:
    """spec: "Mixed valid and malformed candidates" -- a 3-item array where
    one candidate has a missing required field drops only that candidate,
    keeping the other 2 valid ones (order preserved)."""
    malformed = '{"type": "Concept", "title": "", "description": "D"}'
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, malformed, _ENTITY_ITEM))

    result = _objects("text", source_title="t", llm=llm)

    assert [r.title for r in result] == ["Stoicism", "Zettelkasten App"]


def test_all_items_malformed_returns_empty_list() -> None:
    """spec: "All candidates malformed" -- every candidate in the reply
    array fails validation, so the result is `[]`."""
    bad_1 = '{"type": "Animal", "title": "T", "description": "D"}'
    bad_2 = '{"type": "Concept", "title": "", "description": "D"}'
    llm = _FakeLLM(reply=_array(bad_1, bad_2))

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


# --- CAP, applied post-validation, first-N-in-reply-order --------------------


def test_cap_is_six_the_last_position_measured_free_of_decay() -> None:
    """The cap is 6, and that is a MEASURED boundary, not a round number.

    `evals/extraction_cap/` scores reply POSITION against the hand-written
    ground truth in `examples/extraction-corpus/`. Over two English sources,
    15 runs per cell, at both model-default sampling and temperature 0.1
    (#404):

        position 6:  39 genuine subjects,  0 known facets
        position 7:   9 genuine subjects, 24 known facets

    Position 6 did not hold a known facet once, in any of the four cells.
    Position 7 is where enumeration decay starts. So 6 admits real material
    that 5 was discarding -- `Brand Guidelines Skill` on one fixture, the
    primary `Procedure` on the other, the latter in 13 of 13 runs -- while 7
    would start admitting the tail.

    Pinned as a LITERAL on purpose. Every other test in this section reads the
    constant symbolically, which is right for behaviour but means all of them
    would keep passing if someone rounded this to 10. The value is the finding,
    so the value is what gets asserted.
    """
    assert concept_mod._MAX_OBJECTS_PER_SOURCE == 6


def test_exactly_cap_valid_items_are_all_kept() -> None:
    """Candidates exactly at the cap are all kept."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    items = [
        f'{{"type": "Concept", "title": "Item {i}", "description": "D"}}'
        for i in range(cap)
    ]
    llm = _FakeLLM(reply=_array(*items))

    result = _objects("text", source_title="t", llm=llm)

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

    result = _objects("text", source_title="t", llm=llm)

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

    result = _objects("text", source_title="t", llm=llm)

    assert [r.title for r in result] == [f"Item {i}" for i in range(cap)]


# --- Deterministic anti-twin enforcement (5b) --------------------------------

_MARIA_ITEM = (
    '{"type": "Person", "title": "Maria Salazar", '
    '"description": "A friend discussing her move.", "body": ""}'
)

_APATHEIA_ITEM = (
    '{"type": "Concept", "title": "Apatheia", '
    '"description": "A Stoic term for freedom from destructive emotion.", '
    '"body": ""}'
)

_DICHOTOMY_ITEM = (
    '{"type": "Concept", "title": "Dichotomy of Control", '
    '"description": "The Stoic distinction between what is and is not up to us.", '
    '"body": ""}'
)

_CALL_WITH_MARIA_TWIN_ITEM = (
    '{"type": "Event", "title": "Call with Maria Salazar — 2026-07-14", '
    '"description": "A phone call between the author and Maria Salazar.", '
    '"body": ""}'
)

_MCP_LAUNCHING_EVENT_ITEM = (
    '{"type": "Event", "title": "MCP Launching", '
    '"description": "The launch of the Model Context Protocol.", "body": ""}'
)


def test_source_title_twin_dropped_when_genuine_objects_survive() -> None:
    """5b: a reply carrying a fourth candidate whose title exactly restates
    `source_title` (the measured `call-with-maria` shape, 4b.6 diagnostic
    probe) alongside three genuine objects yields only the three genuine
    ones, in reply order -- the twin is dropped because it is redundant
    with surviving objects."""
    llm = _FakeLLM(
        reply=_array(
            _MARIA_ITEM,
            _APATHEIA_ITEM,
            _DICHOTOMY_ITEM,
            _CALL_WITH_MARIA_TWIN_ITEM,
        )
    )

    result = _objects(
        "Maria and I talked about her move, then about apatheia and the "
        "dichotomy of control.",
        source_title="Call with Maria Salazar — 2026-07-14",
        llm=llm,
    )

    assert [r.title for r in result] == [
        "Maria Salazar",
        "Apatheia",
        "Dichotomy of Control",
    ]
    assert all(r.title != "Call with Maria Salazar — 2026-07-14" for r in result)


def test_source_title_twin_kept_when_it_is_the_only_object() -> None:
    """5b floor guard: a reply whose ONLY object restates `source_title`
    (the measured `mcp-launch` shape -- a genuinely single-subject source
    whose only subject IS what its title names) is kept unchanged.
    Suppressing it would emit `[]` for genuine content, which the floor
    (design D4/5b) forbids. This may pass trivially before the 5b.3
    implementation exists -- it is the regression alarm for the floor
    rule, not proof of the drop behavior on its own."""
    llm = _FakeLLM(reply=_array(_MCP_LAUNCHING_EVENT_ITEM))

    result = _objects(
        "MCP is launching next week.", source_title="MCP Launching", llm=llm
    )

    assert len(result) == 1
    assert result[0].title == "MCP Launching"
    assert result[0].type == "Event"


# --- The Procedure exemption (#413) -----------------------------------------

_RESEARCH_AGENT_PROCEDURE_ITEM = (
    '{"type": "Procedure", "title": "Building a Research Agent with the '
    'Claude Agent SDK", "description": "How to build a research agent on the '
    'SDK.", "body": "Install the SDK, define the subagents, add guardrails."}'
)

_AGENT_SDK_ITEM = (
    '{"type": "Concept", "title": "Claude Agent SDK", '
    '"description": "The toolkit the tutorial builds on.", "body": ""}'
)

_GUARDRAILS_ITEM = (
    '{"type": "Concept", "title": "Human-in-the-Loop Guardrails", '
    '"description": "Approval checkpoints around an agent\'s actions.", '
    '"body": ""}'
)

_RESEARCH_AGENT_CONCEPT_TWIN_ITEM = (
    '{"type": "Concept", "title": "Building a Research Agent with the '
    'Claude Agent SDK", "description": "This document describes building a '
    'research agent.", "body": ""}'
)


def test_primary_procedure_survives_alongside_the_subjects_it_yields() -> None:
    """#413: a tutorial's primary `Procedure` is NOT a source-title twin.

    The prompt asks the model to choose `Procedure` when a source "teaches a
    repeatable how-to", and for a tutorial the title IS the procedure. The
    twin rule then deleted exactly that object whenever the source was rich
    enough to also yield its secondary subjects -- so the richer the source,
    the more likely its central object was the one discarded. The Source and
    the Procedure are different roles: one is the bibliographic anchor, the
    other is the how-to a reader retrieves.
    """
    llm = _FakeLLM(
        reply=_array(
            _RESEARCH_AGENT_PROCEDURE_ITEM,
            _AGENT_SDK_ITEM,
            _GUARDRAILS_ITEM,
        )
    )

    result = _objects(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert [r.title for r in result] == [
        "Building a Research Agent with the Claude Agent SDK",
        "Claude Agent SDK",
        "Human-in-the-Loop Guardrails",
    ]
    assert result[0].type == "Procedure"


def test_non_procedure_twin_still_dropped_beside_an_exempt_procedure() -> None:
    """#413 does not widen the rule beyond the `Procedure` role: a
    content-free `Concept` echo of the source title is still a twin and is
    still dropped, even when the exempt `Procedure` sharing that title is
    what keeps it company. The exemption keys on the object's role, not on
    the title having been claimed by something."""
    llm = _FakeLLM(
        reply=_array(
            _RESEARCH_AGENT_PROCEDURE_ITEM,
            _RESEARCH_AGENT_CONCEPT_TWIN_ITEM,
            _AGENT_SDK_ITEM,
        )
    )

    result = _objects(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert [(r.type, r.title) for r in result] == [
        ("Procedure", "Building a Research Agent with the Claude Agent SDK"),
        ("Concept", "Claude Agent SDK"),
    ]


def test_procedure_exemption_does_not_rescue_a_non_procedure_twin_alone() -> None:
    """#413 regression alarm for the case the twin rule was built on: the
    measured `call-with-maria` shape has no `Procedure` in it at all, so the
    exemption must not change its outcome.

    The `"[]"` second reply answers the #584 re-ask. Once the twin is
    dropped the list is one object titled `Maria Salazar`, whose tokens are
    contained in the source title's, so the containment trigger fires here
    -- correctly: this source genuinely carries three subjects (the spec's
    `call-with-maria` scenario), and one surviving object IS the collapse.
    Spelled out so this test asserts the twin drop rather than a repeating
    fake's reply being deduplicated away."""
    llm = _SequencedLLM([_array(_MARIA_ITEM, _CALL_WITH_MARIA_TWIN_ITEM), "[]"])

    result = _objects(
        "Maria and I talked about her move.",
        source_title="Call with Maria Salazar — 2026-07-14",
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert [r.title for r in result] == ["Maria Salazar"]


def test_exempt_procedure_alone_is_not_a_survivor_that_drops_others() -> None:
    """#413 floor guard: when the ONLY object is the exempt `Procedure`
    twin, nothing is dropped and nothing else must be invented -- the same
    single-subject floor that already protected the `mcp-launch` shape.

    The `"[]"` second reply answers the #584 re-ask, which this shape now
    triggers (the trigger is title-only, so the `Procedure` exemption does
    not hold it back). Spelled out rather than left to a repeating fake:
    with the re-ask answering nothing, the object surviving is attributable
    to the floor and to nothing else, which is what this test claims."""
    llm = _SequencedLLM([_array(_RESEARCH_AGENT_PROCEDURE_ITEM), "[]"])

    result = _objects(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert len(result) == 1
    assert result[0].type == "Procedure"


def test_exempt_procedure_is_not_reported_as_a_cap_casualty() -> None:
    """#413 x #404: the exempt `Procedure` counts as a produced, retained
    object, so the cap report must not learn about it as either a twin drop
    or a discard."""
    procedure = concept_mod.ExtractionResult(
        type="Procedure",
        title="The Source",
        description="How to do the thing the source teaches.",
        body="",
    )
    concept = concept_mod.ExtractionResult(
        type="Concept", title="Something Else", description="D", body=""
    )

    kept = concept_mod._drop_source_title_twins(
        [procedure, concept], source_title="  the   SOURCE  "
    )

    assert kept == [procedure, concept]


def test_twin_exempt_type_is_in_the_vocabulary() -> None:
    """A typo in `_TWIN_EXEMPT_TYPE` would not raise -- it would silently
    restore the deletion the exemption exists to stop, because no validated
    object could ever equal it. Pin it to the closed vocabulary instead."""
    assert concept_mod._TWIN_EXEMPT_TYPE in CLASSIFIABLE_TYPES


def test_judge_readmit_types_subset_of_classifiable_types() -> None:
    """`_JUDGE_READMIT_TYPES` (#668) is the ADDITIVE-only widening of
    judge re-admission -- a typo here would silently narrow re-admission
    rather than fail loudly, the same alarm shape as
    `test_twin_exempt_type_is_in_the_vocabulary` above. Every member must be
    a real, classifiable type."""
    assert concept_mod._JUDGE_READMIT_TYPES <= CLASSIFIABLE_TYPES


def test_person_title_twin_of_source_still_dropped() -> None:
    """D1 regression guard (#668): `_JUDGE_READMIT_TYPES` only widens the
    ADDITIVE judge re-admission site -- it must never leak into the
    DELETION twin-drop rule, which stays scoped to `_TWIN_EXEMPT_TYPE`
    only. `Person` is not exempt here, unlike `Procedure`."""
    person_twin = concept_mod.ExtractionResult(
        type="Person",
        title="Team Meeting",
        description="A person whose title happens to restate the source.",
        body="",
    )
    concept = concept_mod.ExtractionResult(
        type="Concept", title="Something Else", description="D", body=""
    )

    kept = concept_mod._drop_source_title_twins(
        [person_twin, concept], source_title="Team Meeting"
    )

    assert kept == [concept]


# --- The framing-object rule (#522/#533) -------------------------------------

_AMI_FRAMING_EVENT_ITEM = (
    '{"type": "Event", "title": "AMI meeting TS3005a", '
    '"description": "A meeting about remote control design.", "body": ""}'
)

_MEETING_DISCUSSION_FRAMING_ITEM = (
    '{"type": "Event", "title": "Meeting Discussion on Remote Control Design", '
    '"description": "The discussion held during the meeting.", "body": ""}'
)

_BATTERY_DECISION_ITEM = (
    '{"type": "Decision", "title": "Use a Rechargeable Battery", '
    '"description": "The team chose a rechargeable battery over disposables, '
    'weighing cost against convenience.", '
    '"body": "Chosen for cost and environmental reasons; status: adopted."}'
)

_SPANISH_FRAMING_EVENT_ITEM = (
    '{"type": "Event", "title": "Reunión con el equipo de producto", '
    '"description": "La reunión del equipo de producto.", "body": ""}'
)


def test_framing_variant_dropped_even_when_not_a_title_twin() -> None:
    """#522/#533: on a meeting-shaped source, an object whose OWN title is
    meeting-shaped names the gathering, not a subject -- and the model
    reconstructs such titles from content, so exact comparison against the
    source title cannot catch them (`Meeting Discussion on Remote Control
    Design` beside source `AMI meeting TS3005b`, measured at position 1 in
    10 of 10 stored runs)."""
    llm = _FakeLLM(
        reply=_array(_MEETING_DISCUSSION_FRAMING_ITEM, _BATTERY_DECISION_ITEM)
    )

    result = _objects(
        "The team discussed the battery and settled on rechargeable.",
        source_title="AMI meeting TS3005b",
        llm=llm,
    )

    assert [r.title for r in result] == ["Use a Rechargeable Battery"]


def test_framing_object_dropped_even_as_the_only_object() -> None:
    """#522: the twin rule's single-object floor disarmed it exactly when
    the failure was worst -- a source that collapses TO its own container
    title kept it (27 of 49 stored collapses). A framing object is never a
    subject at any reply length, so the floor does not apply to it and the
    honest result is `[]`."""
    llm = _FakeLLM(reply=_array(_AMI_FRAMING_EVENT_ITEM))

    result = _objects(
        "Speaker A: welcome everyone. Speaker B: thanks.",
        source_title="AMI meeting TS3005a",
        llm=llm,
    )

    assert result == []


def test_spanish_framing_object_dropped_alone() -> None:
    """#522: the measured Spanish collapse shape -- a 747 B meeting note
    collapsing to one `Event` restating `Reunión con el equipo de producto`
    in 10 of 10 union-path runs. `reuni[oó]n` is already in the lexicon, so
    the rule must fire on it too."""
    llm = _FakeLLM(reply=_array(_SPANISH_FRAMING_EVENT_ITEM))

    result = _objects(
        "Se propuso un plan y se acordó el versionado.",
        source_title="Reunión con el equipo de producto",
        llm=llm,
    )

    assert result == []


def test_framing_rule_inert_on_a_non_meeting_source() -> None:
    """Scope guard: the rule is gated on the SOURCE being meeting-shaped.
    On an ordinary document a title carrying a gathering word can be a
    genuine subject (`Sprint Retrospective Practices` in an agile handbook),
    and deleting it would be silent data loss -- the asymmetry #459
    documented for the prompt gate applies to the output gate identically."""
    retro_item = (
        '{"type": "Concept", "title": "Sprint Retrospective Practices", '
        '"description": "Techniques for running useful retrospectives.", '
        '"body": ""}'
    )
    llm = _FakeLLM(reply=_array(retro_item, _CONCEPT_ITEM))

    result = _objects(
        "A handbook chapter on retrospectives and stoicism.",
        source_title="Agile Handbook",
        llm=llm,
    )

    assert [r.title for r in result] == ["Sprint Retrospective Practices", "Stoicism"]


def test_meeting_titled_person_still_dropped_by_framing_removal() -> None:
    """D1 regression guard (#668): `_JUDGE_READMIT_TYPES` widening judge
    re-admission must not leak into `_drop_framing_objects`'s deletion,
    which stays scoped to `_TWIN_EXEMPT_TYPE` only -- a `Person` titled
    after the meeting itself (the #522/#533 framing shape) is still
    dropped."""
    framing_person = concept_mod.ExtractionResult(
        type="Person",
        title="Team Meeting",
        description="A person named after the gathering itself.",
        body="",
    )
    concept = concept_mod.ExtractionResult(
        type="Concept", title="Something Else", description="D", body=""
    )

    kept = concept_mod._drop_framing_objects(
        [framing_person, concept], meeting_shaped=True
    )

    assert kept == [concept]


def test_procedure_exempt_from_framing_drop() -> None:
    """#413's role exemption extends to the framing rule: a `Procedure`
    carrying the steps is not a lazy restatement of the gathering, even
    when its title carries a meeting word on a meeting-shaped source."""
    checklist = (
        '{"type": "Procedure", "title": "Standup Facilitation Checklist", '
        '"description": "How to run the daily standup.", '
        '"body": "Timebox to 15 minutes; park discussions; rotate facilitator."}'
    )
    llm = _FakeLLM(reply=_array(checklist))

    result = _objects(
        "Notes on how we run our standup.",
        source_title="Weekly standup notes",
        llm=llm,
    )

    assert len(result) == 1
    assert result[0].type == "Procedure"


def test_union_framing_drop_applies_per_run_before_merge() -> None:
    """#533: a framing object must never reach the judge -- it consumes a
    candidate slot and (measured) always wins the prefix. Dropped from each
    run's contribution before the union is built, like the twin rule. Two
    genuine candidates, because a single-candidate union skips the judge
    call this test inspects (#644)."""
    run1 = _array(_MEETING_DISCUSSION_FRAMING_ITEM, _DECISION_ITEM)
    run2 = _array(_DECISION_ITEM, _CONCEPT_ITEM)
    # A meeting-shaped source now also spends the #668 D6 participant
    # capture call before the judge -- "[]" finds nothing further.
    llm = _SequencedLLM(
        [run1, run2, "[]", _keep_reply("Frame the Essay Around Control", "Stoicism")]
    )

    outcome = concept_mod.extract_concept_union(
        "Meeting notes.", source_title="Team Meeting", llm=llm
    )

    assert "Meeting Discussion on Remote Control Design" not in {
        r.title for r in outcome.objects
    }
    judge_call = llm.calls[3]
    assert "Meeting Discussion on Remote Control Design" not in judge_call[1]["content"]


# --- Ungrounded acronym expansions (#423) ------------------------------------

_FABRICATED_MCP_ITEM = (
    '{"type": "Concept", "title": "MCP (Machine Control Protocol)", '
    '"description": "A protocol for connecting tools.", "body": ""}'
)

_GROUNDED_MCP_ITEM = (
    '{"type": "Concept", "title": "MCP (Model Context Protocol)", '
    '"description": "A protocol for connecting tools.", "body": ""}'
)

_MCP_SOURCE = (
    "Skills combine with the Model Context Protocol to reach external "
    "systems like BigQuery."
)


def test_ungrounded_expansion_stripped_from_title() -> None:
    """#423: a parenthetical acronym expansion the source never contains was
    not read off the source -- it is fabricated content (the measured shape:
    `MCP (Machine Control Protocol)` and four other false expansions, all on
    the Spanish fixture, 17 stored emissions, zero on English). The title
    keeps the acronym and loses the invented claim."""
    llm = _FakeLLM(reply=_array(_FABRICATED_MCP_ITEM))

    result = _objects(_MCP_SOURCE, source_title="Pre-built Skills", llm=llm)

    assert [r.title for r in result] == ["MCP"]


def test_grounded_expansion_is_kept() -> None:
    """The complement: an expansion the source states verbatim is a checkable
    claim that checks out. `MCP (Model Context Protocol)` on the English
    fixtures expanded correctly in 102 of 102 stored emissions -- the rule
    must not touch it."""
    llm = _FakeLLM(reply=_array(_GROUNDED_MCP_ITEM))

    result = _objects(_MCP_SOURCE, source_title="Pre-built Skills", llm=llm)

    assert [r.title for r in result] == ["MCP (Model Context Protocol)"]


def test_expansion_first_ungrounded_collapses_to_acronym() -> None:
    """The expansion-first form makes the same factual claim in the other
    order: `Machine Control Protocol (MCP)` with no grounding keeps only the
    acronym."""
    item = (
        '{"type": "Concept", "title": "Machine Control Protocol (MCP)", '
        '"description": "A protocol.", "body": ""}'
    )
    llm = _FakeLLM(reply=_array(item))

    result = _objects(_MCP_SOURCE, source_title="Pre-built Skills", llm=llm)

    assert [r.title for r in result] == ["MCP"]


def test_grounding_ignores_case_and_line_breaks() -> None:
    """Grounding uses the same strip/casefold/whitespace-collapse rule as
    every other title comparison in this module -- a source that writes the
    expansion across a line break still grounds it."""
    source = "the model context\nprotocol is how skills reach tools."
    llm = _FakeLLM(reply=_array(_GROUNDED_MCP_ITEM))

    result = _objects(source, source_title="Pre-built Skills", llm=llm)

    assert [r.title for r in result] == ["MCP (Model Context Protocol)"]


def test_stripped_fabrications_merge_to_one_object_in_union() -> None:
    """#423 x #456: two runs fabricating DIFFERENT expansions used to merge
    as two distinct objects. Stripping runs before the union merge, so both
    collapse to one `MCP` candidate."""
    other_fabrication = (
        '{"type": "Concept", "title": "MCP (Multi-Cloud Platform)", '
        '"description": "A protocol for connecting tools.", "body": ""}'
    )
    llm = _SequencedLLM(
        [_array(_FABRICATED_MCP_ITEM), _array(other_fabrication), _keep_reply("MCP")]
    )

    outcome = concept_mod.extract_concept_union(
        _MCP_SOURCE, source_title="Pre-built Skills", llm=llm
    )

    assert [r.title for r in outcome.objects] == ["MCP"]


def test_strip_leaves_a_title_without_expansions_byte_identical() -> None:
    """Regression (#538 CI): the strip used to collapse whitespace on EVERY
    title, not just rewritten ones -- silently repairing a title with an
    embedded newline that `okf.build_concept`'s stricter single-line gate
    exists to reject, so the builder-degrade path stopped degrading. A title
    the expansion rule does not touch must pass through byte-identical."""
    item = (
        '{"type": "Concept", "title": "Stoic Framework\\nExtra Line", '
        '"description": "A framework.", "body": ""}'
    )
    llm = _FakeLLM(reply=_array(item))

    result = _objects(_MCP_SOURCE, source_title="Pre-built Skills", llm=llm)

    assert result[0].title == "Stoic Framework\nExtra Line"


def test_expansion_strip_leaves_description_and_body_alone() -> None:
    """Scope: only the TITLE's parenthetical expansion makes a checkable
    claim this rule owns. Description and body pass through untouched."""
    item = (
        '{"type": "Concept", "title": "MCP (Machine Control Protocol)", '
        '"description": "Machine Control Protocol everywhere.", '
        '"body": "Machine Control Protocol again."}'
    )
    llm = _FakeLLM(reply=_array(item))

    result = _objects(_MCP_SOURCE, source_title="Pre-built Skills", llm=llm)

    assert result[0].title == "MCP"
    assert result[0].description == "Machine Control Protocol everywhere."
    assert result[0].body == "Machine Control Protocol again."


# --- Empty-reply retry (#524) ------------------------------------------------


def test_empty_reply_is_retried_once_on_the_single_call_path() -> None:
    """#524: the model returns `[]` on substantive meeting sources in ~5% of
    single-pass runs (re-measured post-#529: still occurring), violating the
    prompt's own positive default. The failure is non-deterministic, so ONE
    retry drops the rate quadratically -- the union path already gets this
    for free from its second run."""
    llm = _SequencedLLM(["[]", _array(_CONCEPT_ITEM)])

    result = _objects("A substantive note about Stoicism.", source_title="N", llm=llm)

    assert [r.title for r in result] == ["Stoicism"]
    assert len(llm.calls) == 2


def test_empty_reply_is_retried_at_most_once() -> None:
    """Two empty replies mean `[]` is the answer -- a genuinely blank or
    unintelligible source must not loop."""
    llm = _SequencedLLM(["[]", "[]"])

    result = _objects("Some text.", source_title="N", llm=llm)

    assert result == []
    assert len(llm.calls) == 2


def test_non_empty_reply_is_never_retried() -> None:
    """The retry keys on ZERO validated results only."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    _objects("Some text.", source_title="N", llm=llm)

    assert len(llm.calls) == 1


def test_empty_chunk_replies_are_not_retried() -> None:
    """Chunked scope guard: a window with no extractable content is normal
    (#454's fan-out), not the #524 failure -- per-chunk retries would
    multiply calls on long material for nothing."""
    long_source = "\n".join(f"line {i} of a very long transcript." for i in range(700))
    assert len(long_source) > concept_mod._CHUNK_THRESHOLD
    windows = concept_mod._chunk_lines(long_source)
    llm = _SequencedLLM(["[]"] * len(windows))

    result = _objects(long_source, source_title="N", llm=llm)

    assert result == []
    assert len(llm.calls) == len(windows)


# --- extract_concept: zero / one / N results, OllamaError propagation -------


def test_extract_concept_returns_empty_list_when_nothing_worth_extracting() -> None:
    """spec: "No objects worth extracting" -- `extract_concept` returns `[]`."""
    llm = _FakeLLM(reply="[]")

    result = _objects("text", source_title="t", llm=llm)

    assert result == []


def test_extract_concept_returns_list_of_length_one() -> None:
    """spec: "Exactly one object extracted" -- a list of length 1."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    result = _objects("text", source_title="t", llm=llm)

    assert len(result) == 1


def test_extract_concept_returns_list_of_length_n_under_cap() -> None:
    """spec: "Multiple distinct objects extracted, under cap" -- 3 distinct,
    richly described objects yield a list of length 3, each a valid
    `ExtractionResult`."""
    llm = _FakeLLM(reply=_array(_PERSON_ITEM, _EVENT_ITEM, _DECISION_ITEM))

    result = _objects("text", source_title="t", llm=llm)

    assert len(result) == 3
    assert all(isinstance(r, concept_mod.ExtractionResult) for r in result)
    assert [r.type for r in result] == ["Person", "Event", "Decision"]


def test_multi_topic_reply_parses_to_n_extraction_results() -> None:
    """D3: a source developing several distinct subjects (spec scenario
    "Multi-topic source yields one object per distinct subject" -- the
    `call-with-maria` fixture, discussing a person, a philosophical
    correction, and a choice made) parses to 3 `ExtractionResult`s of
    distinct types, one per subject -- not collapsed to a single object."""
    llm = _FakeLLM(reply=_array(_PERSON_ITEM, _CONCEPT_ITEM, _DECISION_ITEM))

    result = _objects(
        "Maria and I talked about her move, then about Stoicism and the "
        "dichotomy of control, and decided to frame the essay around it.",
        source_title="Call with Maria",
        llm=llm,
    )

    assert len(result) == 3
    assert [r.type for r in result] == ["Person", "Concept", "Decision"]
    assert result[0].title == "Epictetus"
    assert result[1].title == "Stoicism"
    assert result[2].title == "Frame the Essay Around Control"


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


def test_prompt_repoints_rubric_to_candidate_objects_not_the_whole_source() -> None:
    """D2: the framing above the nine type bullets no longer asks "what is
    the source about" as one per-source question -- a framing with exactly
    one answer by construction. It now instructs the model to first
    identify the candidate distinct objects the source contains, then
    classify EACH candidate independently -- so the rubric below can be
    applied N times, not collapsed to one."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert (
        "identify the candidate distinct objects the source contains" in system_content
    )
    assert "classify EACH candidate" in system_content
    assert "Classify by what the source is fundamentally about:" not in system_content


def test_prompt_contains_anti_enumeration_paragraph_verbatim() -> None:
    """Phase 1 (D1): the anti-enumeration paragraph is present verbatim,
    including the meeting-transcript -> Event+Decisions-not-5-Persons
    anchor (design #1115), plus the sub-topic clause that extends the same
    restraint to section headings, features, and explanatory terms. The
    paragraph's former closing "When in doubt, leave it out." is GONE --
    see `test_prompt_does_not_reinstate_the_empty_array_escape_hatch` for
    that negative guard."""
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
    assert (
        "a section heading, a feature, a component, or a term that exists "
        "only to EXPLAIN the source's main subject is part of that object's "
        "body, not a separate object" in system_content
    )


def test_prompt_states_multiplicity_decision_test_adjacent_to_anti_enumeration() -> (
    None
):
    """D3: a stated test that decides single-topic vs multi-topic PER
    SUBJECT -- a source developing several distinct subjects (a person
    discussed, an idea corrected, a decision made) yields one object per
    subject, while a source developing only one subject still yields
    exactly ONE object. This paragraph is ADDITIVE, placed adjacent to the
    verbatim-pinned anti-enumeration paragraph (never edited inside it) and
    before the positive default paragraph (design DD2/D3)."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert "Multiplicity is decided per subject, not per source" in system_content
    assert "a person discussed, an idea corrected, a decision made" in system_content
    assert (
        "A source developing only one subject still yields exactly ONE "
        "object." in system_content
    )

    anti_enumeration_end = system_content.index(
        "A document explaining one topic usually yields exactly ONE object."
    )
    multiplicity_start = system_content.index(
        "Multiplicity is decided per subject, not per source"
    )
    positive_default_start = system_content.index(
        "Restraint means FEWER objects, never ZERO"
    )
    assert anti_enumeration_end < multiplicity_start < positive_default_start


def test_prompt_states_anti_twin_clause_after_multiplicity_paragraph() -> None:
    """D4/5b (narrowed 2026-08-04, was 5.5-5.6's unconditional wording):
    prompt wording alone cannot carry the anti-twin rule at the 8B tier --
    the unconditional clause left the exact-title twin in 2 of 5 harness
    runs, and a narrower clause carrying a CONCRETE forbidden-title example
    made it WORSE (twinned in 4 of 4, twice as the ONLY object -- priming).
    The rule is now enforced deterministically in
    `_drop_source_title_twins` (5b.3); this soft, example-free clause only
    asks the model to prefer not producing a source-restating "twin"
    ALONGSIDE another genuine candidate, and explicitly preserves the floor
    (a source whose one genuine subject IS what its own title names still
    yields that subject). The clause is ADDITIVE, placed adjacent to (never
    inside) the verbatim-pinned anti-enumeration paragraph, after the D3
    multiplicity paragraph and before the positive default paragraph."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert (
        "restate the SOURCE's own title and scope" in system_content
        or "restate the Source's own title and scope" in system_content
    )
    assert "twin" in system_content.lower()
    assert "MUST NOT be produced" in system_content
    # Not a blanket ban on shared words: a candidate that shares words with
    # the source title while still targeting one specific subject inside it
    # (e.g. a Person named in the title) remains distinct.
    assert "specific subject" in system_content

    anti_enumeration_end = system_content.index(
        "A document explaining one topic usually yields exactly ONE object."
    )
    multiplicity_start = system_content.index(
        "Multiplicity is decided per subject, not per source"
    )
    twin_clause_start = system_content.index("twin")
    positive_default_start = system_content.index(
        "Restraint means FEWER objects, never ZERO"
    )
    assert (
        anti_enumeration_end
        < multiplicity_start
        < twin_clause_start
        < positive_default_start
    )


def test_prompt_repoints_named_entity_bullets_to_the_candidate() -> None:
    """Fourth axis (design open question #1, resolved 2026-08-04): the seven
    named-entity type bullets (Person, Organization, Place, Event, Procedure,
    Decision, Project) still phrased per-source aboutness ("the source is
    fundamentally about ONE specific, named X"), which is inconsistent with
    D2's per-candidate framing above the rubric ("identify the candidate
    distinct objects ... then classify EACH candidate independently").
    Measured consequence (gate run 170255Z): every named-entity-typed source
    is pinned at exactly 1 object with zero variance, because the bullet
    itself still asks a per-source question with exactly one answer. The
    bullets must describe the CANDIDATE, not the source."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert "the source is fundamentally about" not in system_content
    assert '"Person": the candidate is ONE specific, named' in system_content
    assert '"Organization": the candidate is ONE specific, named' in system_content
    assert '"Place": the candidate is ONE specific, named' in system_content
    assert '"Event": the candidate is ONE bounded, dated happening' in system_content
    assert '"Procedure": the candidate is ONE repeatable how-to' in system_content
    assert '"Decision": the candidate is ONE choice that was made' in system_content
    assert '"Project": the candidate is ONE ongoing effort' in system_content


def test_prompt_concept_bullet_repoints_aboutness_clause_to_the_candidate() -> None:
    """Review WARNING (4b): the Concept bullet's aboutness clause must
    discriminate CANDIDATE vs SOURCE, not just repeat the word "candidate"
    incidentally -- pin the exact clause so a future edit can't silently
    revert it to "classify by what the source is actually about"."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert (
        "classify by what the candidate is actually about, not by whose "
        "name it carries" in system_content
    )
    assert "classify by what the source is actually about" not in system_content


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
    # The JSON-template block no longer repeats the empty-array invitation
    # -- it was the SECOND of two, and the pair drove the `[]`-for-every-
    # instructional-document defect. See
    # `test_prompt_does_not_reinstate_the_empty_array_escape_hatch`.
    assert "Return [] if nothing is worth extracting." not in system_content


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


def test_prompt_does_not_reinstate_the_empty_array_escape_hatch() -> None:
    """Regression fence for the "extracts nothing from instructional
    sources" defect.

    The prompt used to stack THREE suppression levers -- "When in doubt,
    leave it out.", "If nothing is worth extracting, return an empty array
    [].", and a second "Return [] if nothing is worth extracting." in the
    JSON-template block. With qwen3:8b that combination made a how-to /
    tutorial / FAQ reply with a literal `[]` (two tokens), so `openkos
    ingest` derived zero objects from an entire instructional corpus.

    `[]` must still be REACHABLE -- an empty source has to have an out --
    but it may be offered exactly ONCE, and only as a last resort. This
    test asserts on the PROMPT TEXT, not on a model's output, so it is
    offline and deterministic; it fences the phrasing, it does not replace
    a live eval."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert "When in doubt, leave it out." not in system_content
    assert "If nothing is worth extracting" not in system_content
    assert "Return [] if nothing is worth extracting." not in system_content
    # Exactly one surviving mention of the empty array as an outcome, and it
    # is framed as a last resort rather than an invitation.
    assert system_content.count("empty array []") == 1
    assert (
        "Return an empty array [] only as a last resort, for a source with "
        "no substantive content at all" in system_content
    )


def test_prompt_states_the_positive_extraction_default() -> None:
    """A substantive source normally yields AT LEAST ONE object -- its
    primary subject -- and restraint means fewer objects, never zero. This
    is the positive counterweight that replaced the removed escape
    hatches; without it the model treats "prefer fewer" as "prefer none"."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert (
        "Restraint means FEWER objects, never ZERO: a source with "
        "substantive content normally yields AT LEAST ONE object" in system_content
    )
    assert "Extract that primary subject rather than declining." in system_content


def test_prompt_routes_instructional_sources_to_procedure_or_concept() -> None:
    """The nine type definitions frame seven types as "ONE specific, NAMED
    X", which left a how-to / tutorial / reference / FAQ -- a document about
    no named subject -- with no rubric branch to land on. A clarifying
    paragraph (the definitions themselves are untouched) routes such a
    source to `Procedure` (a repeatable how-to) or `Concept` (an idea,
    topic, tool, or framework), and states that `Concept` does NOT require a
    proper name."""
    llm = _FakeLLM(reply=_array(_PROCEDURE_ITEM))

    concept_mod.extract_concept("some source text", source_title="Notes", llm=llm)

    system_content = llm.calls[0][0]["content"]
    assert "Not every source is about a NAMED subject." in system_content
    assert (
        "how-to, tutorial, guide, reference page, or FAQ -- still has a "
        "primary subject" in system_content
    )
    assert '"Concept" does NOT require a proper name.' in system_content
    # The clarifier must keep BOTH landing types available, not collapse
    # every instructional document onto one of them.
    clarifier_start = system_content.index("Not every source is about a NAMED subject.")
    clarifier = system_content[
        clarifier_start : system_content.index("Tie-breaks, applied in this order:")
    ]
    assert '"Procedure"' in clarifier
    assert '"Concept"' in clarifier


def test_prompt_carries_source_text_and_title() -> None:
    """The user message carries the raw source text and its title."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept(
        "a distinctive phrase zzqq", source_title="My Notes", llm=llm
    )

    user_content = llm.calls[0][1]["content"]
    assert "My Notes" in user_content
    assert "a distinctive phrase zzqq" in user_content


def test_prompt_frames_source_title_as_non_authoritative_metadata() -> None:
    """DD1: the title stays (it is still handed off from ingest and still
    appears in the user turn), but its label must stop presenting it as the
    pre-computed answer to "what is this document about" -- a bare `SOURCE
    TITLE:` prefix reads as an authoritative topic statement, which is what
    caused the H1-derived title to produce twin objects (D1 verdict,
    `twin_rate` 0.34 vs 0.13). The label must mark the title as
    context/metadata the model should weigh, not defer to."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept(
        "a distinctive phrase zzqq", source_title="My Notes", llm=llm
    )

    user_content = llm.calls[0][1]["content"]
    assert "My Notes" in user_content
    assert "SOURCE TITLE: My Notes" not in user_content
    assert "not authoritative" in user_content


@pytest.mark.parametrize(
    "title",
    [
        "AMI meeting TS3005b",
        "Weekly team MEETING",
        "Sprint retrospective notes",
        "Standup 2026-08-07",
        "Project kickoff with the design team",
        "Engineering huddle",
        # Spanish (#522). The guard shipped English-only, so a Spanish
        # meeting source received exactly the priming the guard exists to
        # remove -- and the collapse probe measured it collapsing 10/10.
        "Reunión con el equipo de producto",
        "Reunion de equipo",
        "Reuniones semanales de producto",
        "REUNIÓN DE SEGUIMIENTO",
    ],
)
def test_prompt_omits_meeting_shaped_source_title(title: str) -> None:
    """#459: a title naming the document as a meeting -- an
    extractable-Event-shaped container -- is omitted from the user message
    entirely. Measured on `TS3005b.transcript` (blind chunked path,
    qwen3:8b): under `AMI meeting TS3005b` extraction collapsed to 1 object
    in 20/20 runs; with the title line omitted it produced 8 in 5/5 runs
    (post-cap subject recall 0.51 vs ~0.0). The metadata-only label does
    not hold against this title shape, so the guard removes the priming
    channel instead of relabeling it. Display titles are untouched -- this
    filters the PROMPT only."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept(
        "a distinctive phrase zzqq", source_title=title, llm=llm
    )

    user_content = llm.calls[0][1]["content"]
    assert title not in user_content
    assert "SOURCE TITLE" not in user_content
    assert "a distinctive phrase zzqq" in user_content
    # #522: omitting the title also removes the only non-English text in the
    # user turn, and the system prompt is entirely English. On a Spanish
    # source that flipped output titles to English in 28 of 30 measured runs.
    # The no-title path therefore carries its own language anchor.
    assert "same language as the SOURCE TEXT" in user_content


def test_titled_prompt_carries_no_language_anchor() -> None:
    """The anchor belongs to the no-title path ONLY (#522).

    A source that keeps its title already has source-language text in the
    user turn, so it needs no instruction -- and adding one there would be
    an unmeasured change to the path almost every source takes. #459's
    asymmetry applies to added text as much as to removed text."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept(
        "a distinctive phrase zzqq", source_title="Designing the sync engine", llm=llm
    )

    user_content = llm.calls[0][1]["content"]
    assert "same language as the SOURCE TEXT" not in user_content


@pytest.mark.parametrize(
    "title",
    [
        "Skills vs Tools in the Claude Agent SDK",
        "Remote control functional design",
        "Understanding session cookies",
        "Designing the sync engine",
        "Sales call transcript review",
        # Spanish polysemes held OUT of the lexicon on #459's own grounds
        # (#522). `junta` is a board or a mechanical gasket far more often
        # than a gathering; `sesión` and `llamada` are the direct analogues
        # of the excluded `session` and `call`.
        "Junta directiva de accionistas",
        "Junta de culata del motor",
        "Sesión de diseño del producto",
        "Llamada a la API de pagos",
    ],
)
def test_prompt_keeps_non_meeting_source_title(title: str) -> None:
    """The meeting guard is NARROW by measurement: `large-03` scores BEST
    under its own derived title (0.75 post-cap recall vs 0.57 with the line
    omitted), so the title must survive for descriptive titles -- including
    ones containing polysemous words the lexicon deliberately excludes
    (`session`, `sync`, `call`), which name technical topics far more often
    than gatherings."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept(
        "a distinctive phrase zzqq", source_title=title, llm=llm
    )

    user_content = llm.calls[0][1]["content"]
    assert title in user_content
    assert "SOURCE TITLE" in user_content
    assert "not authoritative" in user_content


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


# --- Cap reporting (#404) ---------------------------------------------------


def _n_valid_items(n: int) -> list[str]:
    return [
        f'{{"type": "Concept", "title": "Item {i}", "description": "D"}}'
        for i in range(n)
    ]


def test_extraction_report_is_a_frozen_dataclass() -> None:
    """`ExtractionReport` carries the pre-cap count, the post-cap count, and
    the titles the cap discarded -- and is immutable, like every other
    report shape in the codebase."""
    report = concept_mod.ExtractionReport(
        produced=7, retained=5, discarded_titles=("Item 5", "Item 6")
    )

    assert report.produced == 7
    assert report.retained == 5
    assert report.discarded_titles == ("Item 5", "Item 6")
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.produced = 1  # type: ignore[misc]


def test_outcome_reports_nothing_discarded_when_under_the_cap() -> None:
    """The healthy path must stay silent: `produced == retained` and no
    discarded titles, so a caller can render a notice on truncation alone
    without special-casing."""
    llm = _FakeLLM(reply=_array(*_n_valid_items(3)))

    outcome = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(outcome.objects) == 3
    assert outcome.report.produced == 3
    assert outcome.report.retained == 3
    assert outcome.report.discarded_titles == ()


def test_outcome_reports_produced_and_retained_when_truncated() -> None:
    """#404: the whole point -- a source proposing 20 objects and one
    proposing 5 were indistinguishable downstream, because only the
    truncated list survived the call."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    llm = _FakeLLM(reply=_array(*_n_valid_items(cap + 15)))

    outcome = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert len(outcome.objects) == cap
    assert outcome.report.produced == cap + 15
    assert outcome.report.retained == cap


def test_outcome_names_the_discarded_titles_in_reply_order() -> None:
    """Naming what was lost is what makes the loss attributable -- a bare
    count tells a user something vanished but not what, and the measurement
    behind #404 showed the discarded tail is exactly what a reader needs to
    judge whether the cap hurt them."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    llm = _FakeLLM(reply=_array(*_n_valid_items(cap + 3)))

    outcome = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert outcome.report.discarded_titles == tuple(
        f"Item {i}" for i in range(cap, cap + 3)
    )


def test_report_counts_validated_objects_not_raw_reply_items() -> None:
    """The cap already applies post-validation, so the report must too: a
    malformed item is not something the cap discarded, it is something
    validation rejected, and conflating the two would report a loss that
    never happened."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    items = [
        '{"type": "NotAType", "title": "X", "description": "D"}',
        *_n_valid_items(cap + 1),
    ]
    llm = _FakeLLM(reply=_array(*items))

    outcome = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert outcome.report.produced == cap + 1
    assert outcome.report.retained == cap
    assert outcome.report.discarded_titles == (f"Item {cap}",)


def test_report_is_computed_after_source_title_twin_dropping() -> None:
    """`_drop_source_title_twins` runs BEFORE the cap, so a dropped twin was
    never a cap casualty. Reporting it as one would blame the cap for a
    deliberate, separate rule."""
    cap = concept_mod._MAX_OBJECTS_PER_SOURCE
    items = [
        '{"type": "Concept", "title": "The Source", "description": "D"}',
        *_n_valid_items(cap),
    ]
    llm = _FakeLLM(reply=_array(*items))

    outcome = concept_mod.extract_concept("text", source_title="The Source", llm=llm)

    assert outcome.report.produced == cap
    assert outcome.report.retained == cap
    assert outcome.report.discarded_titles == ()


def test_empty_extraction_reports_zero_produced() -> None:
    """`[]` is a valid answer, not a truncation -- it must not render a
    notice."""
    llm = _FakeLLM(reply="[]")

    outcome = concept_mod.extract_concept("text", source_title="t", llm=llm)

    assert outcome.objects == []
    assert outcome.report.produced == 0
    assert outcome.report.retained == 0
    assert outcome.report.discarded_titles == ()


# --- type_alternative: the runner-up type the model also weighed (#401) ------


def test_validate_reads_a_type_alternative() -> None:
    """A well-formed `type_alternative` is carried onto the result (#401).

    The type decides the bundle subdirectory, the `index.md` catalog
    section, and the default volatility tier. When the model was genuinely
    torn between two of them, that is load-bearing information, and today it
    is discarded the instant the reply is parsed.
    """
    result = concept_mod._validate(
        {
            "type": "Event",
            "title": "Hellenistic Ethics Seminar",
            "description": "A seminar taught this term.",
            "body": "",
            "type_alternative": "Project",
        }
    )

    assert result is not None
    assert result.type == "Event"
    assert result.type_alternative == "Project"


def test_validate_defaults_type_alternative_to_none() -> None:
    """An absent `type_alternative` means the model was not torn -- the
    common case, and the one that must stay byte-identical to today."""
    result = concept_mod._validate(
        {
            "type": "Concept",
            "title": "Apatheia",
            "description": "A Stoic concept.",
            "body": "",
        }
    )

    assert result is not None
    assert result.type_alternative is None


@pytest.mark.parametrize(
    "alternative",
    ["Sandwich", "", "   ", 7, None, ["Project"], {"type": "Project"}],
)
def test_validate_degrades_a_bad_type_alternative_without_dropping_the_object(
    alternative: object,
) -> None:
    """A malformed `type_alternative` degrades to `None`; the object SURVIVES.

    This is the one place the module's usual fail-closed-per-item rule is
    deliberately softened, and the asymmetry is the point. `type`, `title`
    and `description` are load-bearing: a bad one makes the object
    unusable, so the whole candidate is dropped. `type_alternative` is
    ADVISORY -- it changes nothing about where the document lands or what it
    says. Dropping a genuine, well-formed object because the model garbled
    an optional advisory field would trade real knowledge for a diagnostic,
    which is a strictly worse bundle.
    """
    result = concept_mod._validate(
        {
            "type": "Event",
            "title": "Hellenistic Ethics Seminar",
            "description": "A seminar taught this term.",
            "body": "",
            "type_alternative": alternative,
        }
    )

    assert result is not None
    assert result.type == "Event"
    assert result.type_alternative is None


def test_validate_ignores_a_type_alternative_equal_to_the_chosen_type() -> None:
    """`type_alternative` echoing `type` carries no information.

    "I chose Event, and my runner-up was Event" describes no boundary. It is
    normalized to `None` here rather than propagated, so a downstream reader
    never has to special-case it and no document ever claims a near-boundary
    call that did not happen.
    """
    result = concept_mod._validate(
        {
            "type": "Event",
            "title": "Hellenistic Ethics Seminar",
            "description": "A seminar taught this term.",
            "body": "",
            "type_alternative": "Event",
        }
    )

    assert result is not None
    assert result.type_alternative is None


def test_system_prompt_offers_the_optional_alternative_field() -> None:
    """The reply shape the model is handed names the optional field.

    Without this the model never emits it and the whole signal is dead --
    the validation above would be correct and permanently inert.
    """
    assert "type_alternative" in concept_mod._SYSTEM_PROMPT


# --- Chunked extraction (#454) ----------------------------------------------
#
# Measured basis (2026-08-06 probes, recorded on #454): qwen3:8b under the
# production prompt returns EXACTLY one object per chat call on transcript
# material regardless of input size -- 16/16 chunk calls plus every whole-doc
# cell. Multiplicity therefore has to come from call structure: a 40.8 KB
# transcript that yields 1 Event whole yields 9 distinct objects (3 Decision,
# 2 Concept, 2 Project, 2 Event) when split into ~4 KB chunks and merged.


class _SequencedLLM:
    """A structural `LLMBackend` whose replies differ per call.

    `replies[i]` answers call `i`; an Exception instance raises instead.
    Records every call like `_FakeLLM` so tests can assert the fan-out.
    """

    def __init__(self, replies: Sequence[str | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        reply = self.replies[len(self.calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _long_text(lines: int = 700, width: int = 30) -> str:
    """Deterministic multi-line text comfortably above `_CHUNK_THRESHOLD`."""
    return "\n".join(f"A: line {i:04d} " + "x" * width for i in range(lines))


def test_chunk_lines_returns_whole_text_when_under_target() -> None:
    """Text at or under the target packs into exactly one chunk."""
    text = "alpha\nbeta\ngamma"

    assert concept_mod._chunk_lines(text, target=100) == [text]


def test_chunk_lines_reconstructs_the_text_exactly() -> None:
    """Joining the chunks with newlines restores the input byte-for-byte --
    chunking must never lose or duplicate source content."""
    text = _long_text()

    chunks = concept_mod._chunk_lines(text, target=1000)

    assert len(chunks) > 1
    assert "\n".join(chunks) == text


def test_chunk_lines_never_splits_inside_a_line() -> None:
    """A single line longer than the target becomes its own chunk, whole --
    splitting mid-line would hand the model a truncated utterance."""
    oversized = "B: " + "y" * 500
    text = f"first\n{oversized}\nlast"

    chunks = concept_mod._chunk_lines(text, target=100)

    assert oversized in chunks


def test_chunk_lines_packs_multiline_chunks_to_at_most_target() -> None:
    """Every chunk holding more than one line stays within the target."""
    text = _long_text()

    chunks = concept_mod._chunk_lines(text, target=1000)

    for chunk in chunks:
        if "\n" in chunk:
            assert len(chunk) <= 1000


def test_small_source_makes_exactly_one_chat_call() -> None:
    """At or under `_CHUNK_THRESHOLD` the single-call path is untouched --
    the behavior every existing measurement was taken against. Two objects
    in the reply, so the count stays about chunk fan-out: a LONE object on a
    source this long now buys a #642 low-yield re-ask, which is a separate
    call this test does not pin."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, _PERSON_ITEM))
    text = "x" * concept_mod._CHUNK_THRESHOLD

    outcome = concept_mod.extract_concept(text, source_title="Notes", llm=llm)

    assert len(llm.calls) == 1
    assert outcome.report.chunks == 1
    assert [r.title for r in outcome.objects] == ["Stoicism", "Epictetus"]


def test_large_source_makes_one_chat_call_per_chunk() -> None:
    """Above the threshold, one chat call per `_chunk_lines` window, each
    carrying its own window's text and the SAME source title. The title must
    not be meeting-shaped: the #459 guard omits such a title from EVERY
    chunk's message (which is the point of the guard -- the collapse it
    breaks acted per chunk), and this test pins per-chunk propagation of a
    title that survives the guard."""
    text = _long_text()
    assert len(text) > concept_mod._CHUNK_THRESHOLD
    expected = concept_mod._chunk_lines(text)
    llm = _SequencedLLM(["[]"] * len(expected))

    outcome = concept_mod.extract_concept(text, source_title="Field Notes", llm=llm)

    assert len(llm.calls) == len(expected)
    assert outcome.report.chunks == len(expected)
    for call, chunk in zip(llm.calls, expected, strict=True):
        user = call[1]["content"]
        assert chunk in user
        assert "Field Notes" in user


def test_chunk_target_is_read_at_call_time_not_bound_at_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patching `_CHUNK_TARGET` MUST change the windows production packs.

    This is the #714 arm-inert defect written down as a test. `_chunk_lines`
    used to take `target: int = _CHUNK_TARGET`, and a signature default binds
    once, when the function is defined -- so a measurement arm that
    reassigned the module constant chunked at 4 KB while reporting itself as
    an 8 KB arm. The bug is invisible in the arm's own output: it produces
    plausible numbers for a treatment that never ran."""
    text = _long_text()
    before = concept_mod._chunk_lines(text)

    monkeypatch.setattr(concept_mod, "_CHUNK_TARGET", 8_000)
    after = concept_mod._chunk_lines(text)

    assert len(after) < len(before)
    assert "\n".join(after) == text


def _meeting_shaped_text(chars: int = 16_000) -> str:
    """Speaker-turn text `_transcript_shaped_text` recognizes, sized into the
    band between the meeting threshold and the prose one (#714).

    Three recurring labels with many turns each, which is what the #673
    detector requires -- `_long_text` above deliberately uses ONE label and so
    is NOT transcript-shaped, which is why it cannot serve here."""
    speakers = ("Ana", "Beto", "Caro")
    lines: list[str] = []
    size = 0
    index = 0
    while size < chars:
        line = f"{speakers[index % 3]}: turno {index:04d} " + "palabra " * 5
        lines.append(line)
        size += len(line) + 1
        index += 1
    return "\n".join(lines)


def _same_size_prose(chars: int = 16_000) -> str:
    """Prose of the SAME size carrying no speaker turns at all -- the 13-17 KB
    band `_CHUNK_THRESHOLD`'s docstring records as measured-working on the
    whole-document path (#379 gate: 5-10 objects per document)."""
    lines: list[str] = []
    size = 0
    index = 0
    while size < chars:
        line = f"Paragraph {index:04d} develops the argument further " + "word " * 8
        lines.append(line)
        size += len(line) + 1
        index += 1
    return "\n".join(lines)


def test_union_chunks_a_meeting_shaped_source_below_the_prose_threshold() -> None:
    """#714: a 16 KB meeting transcript takes the CHUNKED path.

    Measured cause (`evals/generation_ceiling/`): on the whole-document path
    this source's extraction call reached the shipped 8192 generation ceiling
    and raised, failing 2 of 3 runs under #715's clause; chunked, the worst
    call generated 1731. `_extract_once` is the only call whose cut-off
    propagates, so this is the fan-out the ceiling failure is about.

    The fixture's own gate verdict is asserted first: a fixture that stopped
    being transcript-shaped would take the prose branch, chunk at 18 000, and
    this test would then be measuring nothing while still passing."""
    text = _meeting_shaped_text()
    assert concept_mod._is_meeting_shaped("TS3005a transcript", text)
    assert 12_000 < len(text) < 18_000
    llm = _SequencedLLM(["[]"] * 40)

    outcome = concept_mod.extract_concept_union(
        text, source_title="TS3005a transcript", llm=llm
    )

    assert outcome.report.chunks > 1
    assert outcome.report.runs == 1


def test_union_keeps_same_size_prose_on_the_two_pass_path() -> None:
    """The twin of the test above, and not a formality (#714).

    Prose in this band is the path every existing extraction measurement was
    taken against, and chunking it is measured COLLATERAL: on the 16 948-char
    `large-03` control the same lowered threshold took the retained set from 8
    objects to 17, fragmenting one subject across windows (the #699 class).

    Lowering `_CHUNK_THRESHOLD` itself -- rather than branching on shape --
    passes the transcript test above while silently regressing this band, so
    the pair is the guard: remove either one and a flat lower threshold looks
    correct."""
    text = _same_size_prose()
    assert not concept_mod._is_meeting_shaped("Field Notes", text)
    assert 12_000 < len(text) < 18_000
    llm = _SequencedLLM(["[]"] * 40)

    outcome = concept_mod.extract_concept_union(
        text, source_title="Field Notes", llm=llm
    )

    assert outcome.report.chunks == 1
    assert outcome.report.runs == 2


def test_single_run_path_chunks_a_meeting_shaped_source_too() -> None:
    """The legacy `extract_concept` path takes the same boundary (#714).

    Both are production: `cli/main.py` picks between them on the `union_judge`
    config flag, so a fix applied to one only would leave whether #714 is fixed
    depending on a setting."""
    text = _meeting_shaped_text()
    assert concept_mod._is_meeting_shaped("TS3005a transcript", text)
    assert 12_000 < len(text) < 18_000
    llm = _SequencedLLM(["[]"] * 40)

    outcome = concept_mod.extract_concept(
        text, source_title="TS3005a transcript", llm=llm
    )

    assert outcome.report.chunks > 1


def test_single_run_path_keeps_same_size_prose_whole() -> None:
    """The legacy path's half of the twin (#714) -- same reason as the union's.

    Carries the union twin's fixture guards for the same reason it does: a
    `_same_size_prose` retuned below the meeting threshold would still assert
    `chunks == 1` and still pass, while no longer testing the boundary at all."""
    text = _same_size_prose()
    assert not concept_mod._is_meeting_shaped("Field Notes", text)
    assert 12_000 < len(text) < 18_000
    llm = _SequencedLLM(["[]"] * 40)

    outcome = concept_mod.extract_concept(text, source_title="Field Notes", llm=llm)

    assert outcome.report.chunks == 1


def test_chunked_results_merge_in_chunk_order() -> None:
    """Validated objects concatenate in chunk order, first chunk first."""
    text = _long_text()
    replies: list[str | Exception] = ["[]"] * len(concept_mod._chunk_lines(text))
    replies[0] = _array(_DECISION_ITEM)
    replies[1] = _array(_PERSON_ITEM, _PROJECT_ITEM)
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept(text, source_title="Meeting", llm=llm)

    assert [r.title for r in outcome.objects] == [
        "Frame the Essay Around Control",
        "Epictetus",
        "Stoicism Essay Series",
    ]


def test_chunked_merge_dedups_by_type_and_normalized_title() -> None:
    """The same subject surfacing in two chunks lands once, keeping the
    first occurrence -- title comparison is the twin rule's normalization
    (strip + casefold + collapsed whitespace), never fuzzy."""
    variant = (
        '{"type": "Concept", "title": "  STOICISM ", '
        '"description": "Same subject, later chunk.", "body": ""}'
    )
    text = _long_text()
    replies: list[str | Exception] = ["[]"] * len(concept_mod._chunk_lines(text))
    replies[0] = _array(_CONCEPT_ITEM)
    replies[1] = _array(variant)
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept(text, source_title="Meeting", llm=llm)

    assert [r.title for r in outcome.objects] == ["Stoicism"]
    assert outcome.objects[0].description == "A school of Hellenistic philosophy."
    assert outcome.report.produced == 1


def test_chunked_merge_keeps_same_title_under_different_types() -> None:
    """Dedup keys on (type, title): a Concept and an Event sharing a title
    are different objects, and the downstream slug guard owns that collision."""
    event_stoicism = (
        '{"type": "Event", "title": "Stoicism", '
        '"description": "A dated happening oddly named.", "body": ""}'
    )
    text = _long_text()
    replies: list[str | Exception] = ["[]"] * len(concept_mod._chunk_lines(text))
    replies[0] = _array(_CONCEPT_ITEM)
    replies[1] = _array(event_stoicism)
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept(text, source_title="Meeting", llm=llm)

    assert [(r.type, r.title) for r in outcome.objects] == [
        ("Concept", "Stoicism"),
        ("Event", "Stoicism"),
    ]


def test_chunked_twin_drop_applies_to_the_merged_list() -> None:
    """A source-title twin from ANY chunk is dropped when a non-twin
    exists across the whole merge -- the rule sees the merged list, not
    each chunk's slice of it."""
    twin = (
        '{"type": "Event", "title": "Team Meeting", '
        '"description": "The meeting itself.", "body": ""}'
    )
    text = _long_text()
    replies: list[str | Exception] = ["[]"] * len(concept_mod._chunk_lines(text))
    replies[0] = _array(_DECISION_ITEM)
    replies[1] = _array(twin)
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept(text, source_title="Team Meeting", llm=llm)

    assert [r.title for r in outcome.objects] == ["Frame the Essay Around Control"]


def test_chunked_cap_applies_after_the_merge() -> None:
    """The cap slices the MERGED list: seven distinct objects across chunks
    keep the first six, and the report names the seventh as the casualty."""

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": ""}}'
        )

    text = _long_text()
    replies: list[str | Exception] = ["[]"] * len(concept_mod._chunk_lines(text))
    replies[0] = _array(*(item(i) for i in range(1, 5)))
    replies[1] = _array(*(item(i) for i in range(5, 8)))
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept(text, source_title="Meeting", llm=llm)

    assert outcome.report.produced == 7
    assert outcome.report.retained == 6
    assert outcome.report.discarded_titles == ("Subject 7",)
    assert len(outcome.objects) == 6


def test_ollama_error_from_a_later_chunk_propagates_unswallowed() -> None:
    """The module's `OllamaError` contract is unchanged by chunking: a
    backend failure on any chunk propagates to the caller's degrade seam."""
    text = _long_text()
    replies: list[str | Exception] = ["[]"] * len(concept_mod._chunk_lines(text))
    replies[1] = OllamaUnavailable("Ollama not reachable")
    llm = _SequencedLLM(replies)

    with pytest.raises(OllamaUnavailable):
        concept_mod.extract_concept(text, source_title="Meeting", llm=llm)


# --- Union-of-runs + selector judge (#456) -----------------------------------
#
# `extract_concept_union` is a SIBLING orchestrator in this module (design
# D1) -- `extract_concept` stays untouched (regression, task 2.22). Below
# `_CHUNK_THRESHOLD` it runs `_extract_once` TWICE, twin-drops each run's own
# output, merges by richer body, ceils at `_MAX_JUDGE_CANDIDATES`, asks
# `judge.select`, deterministically re-admits `Procedure`, then applies the
# `_UNION_BACKSTOP` cap exactly once, last.


def _keep_reply(*titles: str) -> str:
    """A well-formed judge reply keeping exactly `titles`."""
    quoted = ", ".join(f'"{t}"' for t in titles)
    return f'{{"keep": [{quoted}]}}'


def test_union_runs_extraction_twice_below_the_chunk_threshold() -> None:
    """Below `_CHUNK_THRESHOLD`, `extract_concept_union` issues 2 calls with
    identical extraction messages, and a candidate unique to run 2 survives
    in the merged union handed to the judge (recall claim)."""
    run1 = _array(_CONCEPT_ITEM)
    run2 = _array(_CONCEPT_ITEM, _PERSON_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism", "Epictetus")])

    outcome = concept_mod.extract_concept_union(
        "Some notes.", source_title="Notes", llm=llm
    )

    # 2 extraction calls + 1 judge call.
    assert len(llm.calls) == 3
    assert llm.calls[0] == llm.calls[1]
    assert {r.title for r in outcome.objects} == {"Stoicism", "Epictetus"}


def test_union_twin_drop_applies_to_the_merged_list() -> None:
    """A source-title twin emitted by ONE run is dropped from the union when
    a non-twin exists anywhere across the merge -- the rule sees the merged
    list, exactly like the chunked path
    (`test_chunked_twin_drop_applies_to_the_merged_list`).

    The source title here is deliberately NOT meeting-shaped: with a
    meeting-shaped one (this test's previous form used `Team Meeting`),
    `_drop_framing_objects` removes the twin first and the assertion passes
    without the twin rule running at all."""
    run1 = _array(_PERSON_ITEM, _DICHOTOMY_ITEM)
    run2 = _array(_PERSON_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Epictetus")])

    outcome = concept_mod.extract_concept_union(
        "Notes on what is up to us.",
        source_title="Dichotomy of Control",
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Epictetus"]


def test_union_twin_kept_by_one_runs_floor_is_dropped_from_the_merged_union() -> None:
    """#581: run 1 emits ONLY the twin, so its own single-object floor keeps
    it; run 2 emits the twin beside a genuine subject, so run 2 drops it.
    Applying the rule to the merged list -- rather than to each run -- is
    what stops the floor-kept copy from leaking back in beside that genuine
    subject, which is precisely the case `_drop_source_title_twins`'s
    docstring promises to drop.

    The judge is told to keep ALL titles, so nothing downstream of the twin
    rule can remove the twin for it. Run 2 carries a second genuine subject
    beside the twin, because a merged union of one candidate skips the
    judge call this test inspects (#644)."""
    run1 = _array(_DICHOTOMY_ITEM)
    run2 = _array(_DICHOTOMY_ITEM, _PERSON_ITEM, _CONCEPT_ITEM)
    llm = _SequencedLLM(
        [
            run1,
            run2,
            _keep_reply("Dichotomy of Control", "Epictetus", "Stoicism"),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        "Notes on what is up to us.",
        source_title="Dichotomy of Control",
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Epictetus", "Stoicism"]
    # The twin never reaches the judge either: the drop still runs before
    # the `_MAX_JUDGE_CANDIDATES` ceiling, not after it.
    assert "Dichotomy of Control" not in llm.calls[2][1]["content"]


def test_union_floor_keeps_the_twin_when_the_whole_merge_is_twins() -> None:
    """The 5b floor survives the move: both runs emit only the twin, so the
    MERGED union is all-twin and the object is kept. The
    `mcp-launch` shape (a genuinely single-subject source whose only subject
    is what its title names) must not become `[]` on the union path -- and
    it is the failure mode a post-merge twin drop would introduce if the
    floor were dropped along with the move.

    The `"[]"` third reply answers the sole-twin re-ask (#584), which this
    merged list now triggers: it finds nothing further, so the floor is what
    keeps the object here, exactly as before. Without it the judge reply
    would be consumed by the re-ask and the judge call would degrade."""
    llm = _SequencedLLM(
        [
            _array(_DICHOTOMY_ITEM),
            _array(_DICHOTOMY_ITEM),
            "[]",
            _keep_reply("Dichotomy of Control"),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        "Notes on what is up to us.",
        source_title="Dichotomy of Control",
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Dichotomy of Control"]


def test_union_merge_keeps_the_richer_body_on_collision() -> None:
    """A `(type, normalized-title)` collision across runs keeps the
    candidate with the longer `body`, not first-occurrence order."""
    thin = (
        '{"type": "Concept", "title": "Stoicism", '
        '"description": "A school of philosophy.", "body": "short"}'
    )
    rich = (
        '{"type": "Concept", "title": "Stoicism", '
        '"description": "A school of philosophy.", '
        '"body": "A much longer and richer body describing Stoicism in detail."}'
    )
    llm = _SequencedLLM([_array(thin), _array(rich), _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(outcome.objects) == 1
    assert outcome.objects[0].body.startswith("A much longer")


def test_union_merge_description_tie_break_on_equal_body_length() -> None:
    """Equal `body` length falls back to the longer `description`."""
    short_desc = (
        '{"type": "Concept", "title": "Stoicism", '
        '"description": "Short.", "body": "same length"}'
    )
    long_desc = (
        '{"type": "Concept", "title": "Stoicism", '
        '"description": "A much longer description of Stoicism.", '
        '"body": "same length"}'
    )
    llm = _SequencedLLM(
        [_array(short_desc), _array(long_desc), _keep_reply("Stoicism")]
    )

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.objects[0].description == "A much longer description of Stoicism."


def test_union_merge_both_equal_keeps_first_occurrence_order() -> None:
    """When body and description both tie, the FIRST occurrence (run 1)
    wins, keeping merge order deterministic."""
    llm = _SequencedLLM(
        [_array(_CONCEPT_ITEM), _array(_CONCEPT_ITEM), _keep_reply("Stoicism")]
    )

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(outcome.objects) == 1
    assert outcome.objects[0].description == "A school of Hellenistic philosophy."


def test_union_chunked_source_makes_exactly_chunks_plus_one_calls() -> None:
    """Above `_CHUNK_THRESHOLD`, no second extraction pass per chunk: exactly
    `chunks + 1` total calls (one per chunk, plus one judge call) on a
    NON-meeting-shaped source, and `report.runs == 1`. `source_title` here
    is deliberately not meeting-shaped so the #668 D6 participant capture
    pass (a separate, additional call gated on `_MEETING_SHAPED_TITLE_RE`)
    does not change this count; see
    `test_union_chunked_meeting_source_spends_one_extra_capture_call` for
    the meeting-shaped case."""
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    replies: list[str | Exception] = ["[]"] * len(windows)
    # Two distinct candidates: a single-candidate union skips the judge
    # entirely (#644), which would make this exactly-chunks+1 count wrong.
    replies[0] = _array(_DECISION_ITEM, _CONCEPT_ITEM)
    replies.append(_keep_reply("Frame the Essay Around Control", "Stoicism"))
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept_union(
        text, source_title="Weekly Notes", llm=llm
    )

    assert len(llm.calls) == len(windows) + 1
    assert outcome.report.runs == 1
    assert outcome.report.chunks == len(windows)


def test_union_chunked_meeting_source_spends_one_extra_capture_call() -> None:
    """#668 design D6: the participant capture pass fires on BOTH union
    paths -- the chunked branch included. A meeting-shaped chunked source
    spends `chunks + 2` calls: one per chunk, one capture call, one judge
    call."""
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    replies: list[str | Exception] = ["[]"] * len(windows)
    replies[0] = _array(_DECISION_ITEM, _CONCEPT_ITEM)
    replies.append("[]")  # participant capture call: nothing further
    replies.append(_keep_reply("Frame the Essay Around Control", "Stoicism"))
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept_union(text, source_title="Meeting", llm=llm)

    assert len(llm.calls) == len(windows) + 2
    assert outcome.report.runs == 1
    assert outcome.report.chunks == len(windows)
    assert outcome.report.participant_capture_runs == 1


def test_union_ceiling_caps_judge_input_at_24_candidates() -> None:
    """More than `_MAX_JUDGE_CANDIDATES` (24) merged candidates: the judge
    sees exactly 24, and `pre_judge_dropped` counts the remainder."""

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": ""}}'
        )

    run1 = _array(*(item(i) for i in range(1, 22)))  # 21 distinct
    run2 = _array(*(item(i) for i in range(15, 26)))  # 4 new (22-25)
    # 25 total distinct subjects across both runs -> ceiling(24) drops 1.
    judge_reply = _keep_reply(*(f"Subject {i}" for i in range(1, 25)))
    llm = _SequencedLLM([run1, run2, judge_reply])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    judge_call = llm.calls[2]
    judge_user_content = judge_call[1]["content"]
    assert judge_user_content.count("Subject ") == 24
    assert outcome.report.pre_judge_dropped == 1


def test_union_judge_success_reports_judged_out_titles() -> None:
    """A successful judge selection names the dropped titles in
    `judged_out_titles`, and `judge_status == "ok"`."""
    run1 = _array(_CONCEPT_ITEM, _ENTITY_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "ok"
    assert outcome.report.judged_out_titles == ("Zettelkasten App",)
    assert {r.title for r in outcome.objects} == {"Stoicism"}


def test_union_judge_title_match_is_normalized_not_raw() -> None:
    """A judge reply echoing a kept title in different CASE ("stoicism" for
    candidate "Stoicism") still admits that candidate, and never misreports
    it in `judged_out_titles` -- admission matches via the module's shared
    `_normalize_title` on BOTH sides (design D4). Mutation this catches:
    reverting the admission filter to raw `c.title in selected` equality,
    which silently drops the candidate and blames the judge for it."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("stoicism", "Epictetus")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert {r.title for r in outcome.objects} == {"Stoicism", "Epictetus"}
    assert "Stoicism" not in outcome.report.judged_out_titles
    assert outcome.report.judge_status == "ok"


def test_union_same_title_different_type_candidates_are_both_admitted() -> None:
    """Pin of a deliberate bound (#457): the judge reply is title-only, so
    two different-typed candidates sharing one normalized title cannot be
    disambiguated -- a selected title admits BOTH, and neither is reported
    in `judged_out_titles`. Damage is bounded by `_UNION_BACKSTOP`; the
    reply-protocol change that could tell them apart is tracked in #457,
    and this test is the alarm if admission behavior drifts before it."""
    entity_twin = (
        '{"type": "Entity", "title": "Stoicism", '
        '"description": "An organization named after the philosophy.", '
        '"body": ""}'
    )
    run1 = _array(_CONCEPT_ITEM, entity_twin)
    run2 = _array()
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert [(r.type, r.title) for r in outcome.objects] == [
        ("Concept", "Stoicism"),
        ("Entity", "Stoicism"),
    ]
    assert outcome.report.judged_out_titles == ()
    assert outcome.report.judge_status == "ok"


def test_union_procedure_survives_judge_rejection_via_deterministic_readmission() -> (
    None
):
    """A judge-rejected `Procedure` candidate is retained AND absent from
    `judged_out_titles` -- deterministic post-filter re-admission (D5), never
    a judge prompt clause."""
    run1 = _array(_CONCEPT_ITEM, _PROCEDURE_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    # Judge rejects the Procedure -- only "Stoicism" is kept in its reply.
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    titles = {r.title for r in outcome.objects}
    assert "Morning Journaling Routine" in titles
    assert "Morning Journaling Routine" not in outcome.report.judged_out_titles


def test_procedure_behavior_unchanged_at_all_three_sites() -> None:
    """Non-regression (#668): `_JUDGE_READMIT_TYPES` widens the ADDITIVE
    judge re-admission site only. `Procedure`'s behavior at twin-drop,
    framing-drop, and judge re-admission stays byte-identical to before
    this change -- no meeting-shape or anchor gate applies to it."""
    procedure_twin = concept_mod.ExtractionResult(
        type="Procedure",
        title="Team Meeting",
        description="A procedure whose title restates the source.",
        body="Steps.",
    )
    concept = concept_mod.ExtractionResult(
        type="Concept", title="Something Else", description="D", body=""
    )

    # Twin-drop: `Procedure` stays exempt, survives.
    assert concept_mod._drop_source_title_twins(
        [procedure_twin, concept], source_title="Team Meeting"
    ) == [procedure_twin, concept]

    # Framing-drop: `Procedure` stays exempt, survives.
    assert concept_mod._drop_framing_objects(
        [procedure_twin, concept], meeting_shaped=True
    ) == [procedure_twin, concept]

    # Judge re-admission: `Procedure` is re-admitted even without an
    # anchor, on a non-meeting source -- unaffected by the anchor/
    # meeting-shape gate that now scopes `Person`/`Organization`.
    run1 = _array(_CONCEPT_ITEM, _PROCEDURE_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    titles = {r.title for r in outcome.objects}
    assert "Morning Journaling Routine" in titles
    assert "Morning Journaling Routine" not in outcome.report.judged_out_titles


# --- Judge re-admission of Person/Organization participants (#668) ---------

_PERSON_WITH_ANCHOR_ITEM = (
    '{"type": "Person", "title": "Jordan Ellis", '
    '"description": "Jordan Ellis chairs the weekly planning meeting.", '
    '"body": ""}'
)

_PERSON_CHAIR_ROLE_ITEM = (
    '{"type": "Person", "title": "Morgan Lee", '
    '"description": "Morgan Lee, chair of the committee.", "body": ""}'
)

_PERSON_NAME_ONLY_ITEM = (
    '{"type": "Person", "title": "Alex Rivera", '
    '"description": "Alex Rivera is mentioned in passing.", "body": ""}'
)


def test_judge_dropped_person_with_anchor_on_meeting_source_is_readmitted() -> None:
    """Judge re-admission (#668) restores a `Person` candidate the judge
    dropped when the source is meeting-shaped AND the candidate carries a
    participant anchor -- deterministic re-admission, never a judge prompt
    clause, mirroring `Procedure`'s existing D5 re-admission."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_WITH_ANCHOR_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    # Judge rejects Jordan Ellis -- only "Stoicism" is kept in its reply.
    # "[]" is the #668 D6 participant capture call: nothing further.
    llm = _SequencedLLM([run1, run2, "[]", _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes.", source_title="Team Meeting", llm=llm
    )

    titles = {r.title for r in outcome.objects}
    assert "Jordan Ellis" in titles
    assert "Jordan Ellis" not in outcome.report.judged_out_titles


# `test_person_without_anchor_not_readmitted` (#668) lived here and asserted
# the exact rule #712 retired: a name-only `Person` stays dropped on a
# meeting-shaped source. Its replacement is
# `test_name_only_participant_on_meeting_source_is_readmitted`, which asserts
# the inverse, and the property it also carried -- "re-admission is not a
# blanket type amnesty" -- is preserved by
# `test_name_only_participant_on_non_meeting_source_is_not_readmitted`, since
# the SCOPE half of the conjunct is what still refuses.


def test_person_with_meeting_role_anchor_is_readmitted() -> None:
    """A `Person` carrying a meeting role ("chair") alongside its name is
    re-admitted (#668) -- the anchor requirement is satisfied by a role
    cue, not only a relation verb."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_CHAIR_ROLE_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    # "[]" is the #668 D6 participant capture call: nothing further.
    llm = _SequencedLLM([run1, run2, "[]", _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes.", source_title="Team Meeting", llm=llm
    )

    titles = {r.title for r in outcome.objects}
    assert "Morgan Lee" in titles
    assert "Morgan Lee" not in outcome.report.judged_out_titles


def test_person_not_readmitted_from_non_meeting_source() -> None:
    """Scope rule (#668): re-admission of `Person`/`Organization` is gated
    on the SOURCE being meeting-shaped, exactly like `_drop_framing_objects`
    -- an anchored `Person` on a non-meeting (technical-article) source is
    NOT re-admitted through this path."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_WITH_ANCHOR_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union(
        "A technical article.", source_title="API Reference Guide", llm=llm
    )

    titles = {r.title for r in outcome.objects}
    assert "Jordan Ellis" not in titles
    assert "Jordan Ellis" in outcome.report.judged_out_titles


def test_participant_readmitted_reported_separately_from_judge_selected() -> None:
    """Stub-flooding guard fields (#668 design D5): a `Person` restored
    ONLY by the re-admission conjunct (the judge itself did not select it)
    is reported in `participant_readmitted_titles`, and NOT in
    `participant_judge_selected_titles` -- the two counts must stay
    distinguishable, or the guard cannot tell re-admission from genuine
    judge selection.

    Uses the NAME-ONLY fixture on purpose (#712): re-admission no longer
    asks for a role, affiliation or relation cue, so a bare name is the
    case that must now come back."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_NAME_ONLY_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    # "[]" is the #668 D6 participant capture call: nothing further.
    llm = _SequencedLLM([run1, run2, "[]", _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes.", source_title="Team Meeting", llm=llm
    )

    assert outcome.report.participant_readmitted_titles == ("Alex Rivera",)
    assert outcome.report.participant_judge_selected_titles == ()


def test_participant_selected_by_judge_reported_in_selected_not_readmitted() -> None:
    """The other side of the same guard: a `Person` the judge's OWN reply
    keeps is reported in `participant_judge_selected_titles`, NOT in
    `participant_readmitted_titles` -- re-admission never claims credit
    for a candidate the judge genuinely selected."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_WITH_ANCHOR_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    # "[]" is the #668 D6 participant capture call: nothing further.
    llm = _SequencedLLM([run1, run2, "[]", _keep_reply("Stoicism", "Jordan Ellis")])

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes.", source_title="Team Meeting", llm=llm
    )

    assert outcome.report.participant_judge_selected_titles == ("Jordan Ellis",)
    assert outcome.report.participant_readmitted_titles == ()


def test_name_only_participant_on_meeting_source_is_readmitted() -> None:
    """#712: the anchor gate is RETIRED. A name-only `Person` the judge
    dropped on a meeting-shaped source is re-admitted like any other
    participant, and the discard list is empty.

    This is the test that used to assert the opposite. The gate it enforced
    read `_PARTICIPANT_ANCHOR_RE` over the candidate's OWN description --
    text the model wrote from the prompt's own vocabulary -- so it was
    checking the prompt against itself. The owner ruling is that every
    named person is identified; anti-flooding belongs to the participant
    budget lane, not to a lexicon."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_NAME_ONLY_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    # "[]" is the #668 D6 participant capture call: nothing further.
    llm = _SequencedLLM([run1, run2, "[]", _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes.", source_title="Team Meeting", llm=llm
    )

    assert "Alex Rivera" in {r.title for r in outcome.objects}
    assert outcome.report.participant_unreadmitted_discarded_titles == ()


def test_name_only_participant_on_non_meeting_source_is_not_readmitted() -> None:
    """#712 removed ONE HALF of the re-admission conjunct, not both. The
    SCOPE rule (#668 D3) survives: `Person`/`Organization` re-admission is
    still gated on the source being meeting-shaped, so a bare name the
    judge dropped on a technical article stays dropped and is reported in
    `participant_unreadmitted_discarded_titles`.

    Without this test, deleting the whole conjunct would look identical to
    deleting the anchor half."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_NAME_ONLY_ITEM)
    run2 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism")])

    outcome = concept_mod.extract_concept_union(
        "A technical article.", source_title="API Reference Guide", llm=llm
    )

    assert "Alex Rivera" not in {r.title for r in outcome.objects}
    assert outcome.report.participant_unreadmitted_discarded_titles == ("Alex Rivera",)


# --- Scoped participant capture pass (#668 design D6) -----------------------

_CAPTURED_PARTICIPANT_ITEM = (
    '{"type": "Person", "title": "Sam Okafor", '
    '"description": "Sam Okafor, the meeting facilitator.", "body": ""}'
)


def test_participant_capture_pass_joins_candidates_before_judge_on_meeting_source() -> (
    None
):
    """#668 design D6: on a meeting-shaped source, a scoped second call --
    shaped like the #584 sole-twin re-ask, gated on the SAME
    `_MEETING_SHAPED_TITLE_RE` predicate as judge re-admission -- asks
    specifically for Person/Organization participants and joins its
    findings into `merged` BEFORE the judge, so a captured candidate is
    selected through the SAME existing pipeline as every other candidate:
    the judge's own reply must name it to be kept, exactly like any
    general-pass candidate (no bypass)."""
    run1 = _array(_CONCEPT_ITEM)
    run2 = _array(_DECISION_ITEM)
    llm = _SequencedLLM(
        [
            run1,
            run2,
            _array(_CAPTURED_PARTICIPANT_ITEM),
            _keep_reply("Stoicism", "Frame the Essay Around Control", "Sam Okafor"),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes.", source_title="Team Meeting", llm=llm
    )

    titles = {r.title for r in outcome.objects}
    assert "Sam Okafor" in titles
    assert len(llm.calls) == 4
    capture_call = llm.calls[2]
    assert capture_call[0]["content"] == concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT
    judge_call = llm.calls[3]
    assert "Sam Okafor" in judge_call[1]["content"]
    assert outcome.report.participant_capture_runs == 1
    assert outcome.report.participant_capture_added_titles == ("Sam Okafor",)


def test_participant_capture_user_turn_carries_the_language_anchor() -> None:
    """#713: the participant pass returned English descriptions AND English
    BODIES from a 100% Spanish source, on 3 of 3 runs.

    The bodies were TRANSLATIONS of the source's own turns, not summaries in
    the wrong language -- what `query` cites back, and for a `Person` object,
    personal data restated by a model rather than quoted.

    Cause: this was the ONLY extraction call in the pipeline that omitted
    `_LANGUAGE_ANCHOR`. Its docstring justified the omission on the grounds
    that "the source text itself still carries the source's language", and
    `evals/participant_language` measured that assumption false: harmful field
    share 0.75 without the anchor, 0.00 with it, over 48 scored fields, with
    MORE candidates retained and no latency cost.

    The anchor is asserted on the USER turn specifically. `_build_messages`
    places it there for the meeting-shaped branch (#522), and a system-turn
    placement is a different, unmeasured configuration."""
    messages = concept_mod._build_participant_capture_messages(
        "Ana: buenos días.", "Reunión semanal"
    )

    user = messages[1]["content"]
    assert concept_mod._LANGUAGE_ANCHOR in user
    # The anchor is ADDITIVE: the title is this pass's own reference point for
    # which meeting it is, and dropping it would silently change what #668
    # measured.
    assert "Reunión semanal" in user
    assert "Ana: buenos días." in user
    assert messages[0]["content"] == concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT


def test_participant_capture_pass_does_not_fire_on_non_meeting_source() -> None:
    """Scope rule (#668 design D6): the capture pass is gated on the exact
    same meeting-shape predicate as judge re-admission -- a non-meeting
    (technical-article) source spends no extra call and the merged
    candidate set is unaffected."""
    run1 = _array(_CONCEPT_ITEM)
    run2 = _array(_PERSON_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism", "Epictetus")])

    outcome = concept_mod.extract_concept_union(
        "A technical article.", source_title="API Reference Guide", llm=llm
    )

    assert len(llm.calls) == 3
    assert outcome.report.participant_capture_runs == 0
    assert outcome.report.participant_capture_added_titles == ()


def test_system_prompt_asks_a_transcript_for_its_subjects_too() -> None:
    """#715: meeting-shaped sources retained people and NO subjects at all.

    `evals/stage_attrition` established the mechanism: the pinned
    anti-enumeration paragraph says a transcript is about "the meeting itself
    (an Event) and any Decisions reached", the model emits the Event and stops,
    and `_drop_framing_objects` then correctly deletes it -- so the source
    yields nothing. The clause pinned here COMPLETES that instruction rather
    than contradicting it; the #380 paragraph keeps every pinned byte and
    `_drop_framing_objects` is not relaxed.

    ADJACENCY is asserted, not just presence. The clause was measured spliced
    immediately after the multiplicity test, and a later edit that keeps the
    words but moves them elsewhere in the prompt would leave
    `evals/stage_attrition`'s numbers describing a configuration the code no
    longer ships -- the same silent drift its own splice assertion exists to
    prevent on the other side.

    The clause text is read from `TRANSCRIPT_SUBJECTS_CLAUSE`, never re-declared
    here. A hand-copied literal would make this test the SECOND place the exact
    bytes live and `evals/stage_attrition`'s ablation arm the third, so a
    rewording could leave the probe removing text the prompt no longer contains
    -- measuring one prompt twice while reporting clause against no-clause."""
    anchor = "A source developing only one subject still yields exactly ONE object.\n\n"
    clause = concept_mod.TRANSCRIPT_SUBJECTS_CLAUSE

    assert concept_mod._SYSTEM_PROMPT.count(anchor) == 1
    assert concept_mod._SYSTEM_PROMPT.count(clause) == 1
    assert anchor + clause in concept_mod._SYSTEM_PROMPT
    # The clause must actually say the thing it was measured saying. Reading it
    # from the module makes every assertion above tautological on its own: a
    # clause reworded to empty would still be "present once" and "adjacent".
    assert "meeting, call, or interview transcript" in clause
    assert "BOTH halves" in clause


def test_participant_capture_pass_leaves_system_prompt_byte_identical() -> None:
    """D6 constraint: the capture pass is a NEW, separate prompt constant --
    `_SYSTEM_PROMPT`, the general extraction prompt, is never touched by
    this change. Pinned by hash (mirrors `CONTROL_PROMPT_SHA`'s own
    precedent in `evals/extraction_collapse/`) rather than a full-string
    comparison, so any future accidental edit to `_SYSTEM_PROMPT` fails
    loudly here.

    The pin has been rolled ONCE, deliberately, for #715's measured transcript
    clause -- it did its job and caught that edit. Roll it only alongside a
    measurement; a hash updated to make a red test green is the one failure
    mode this guard cannot survive."""
    assert (
        hashlib.sha256(concept_mod._SYSTEM_PROMPT.encode()).hexdigest()
        == "6514c14bc12ec1d8645cc0bee8343b2a102082b81341c334297f194e49e961cb"
    )
    assert concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT != concept_mod._SYSTEM_PROMPT


@pytest.mark.parametrize(
    "judge_failure",
    [
        "not json",
        "",
        OllamaUnavailable("boom"),
    ],
)
def test_union_judge_failure_degrades_to_the_full_backstopped_union(
    judge_failure: str | Exception,
) -> None:
    """`llm.chat` raising `OllamaError`, an empty reply, or an unparseable
    reply during the judge call -- all three: the full merged union is kept
    (no candidate lost), `judge_status == "failed"`, and no exception
    escapes `extract_concept_union` itself."""
    run1 = _array(_CONCEPT_ITEM)
    run2 = _array(_PERSON_ITEM)
    llm = _SequencedLLM([run1, run2, judge_failure])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "failed"
    assert {r.title for r in outcome.objects} == {"Stoicism", "Epictetus"}


def test_union_valid_empty_selection_degrades_to_the_full_backstopped_union() -> None:
    """A judge reply that is valid in shape but whose admitted set -- after
    closed-candidate-list matching and Procedure re-admission -- is empty
    MUST NOT return zero objects while the merged union is non-empty
    (spec: "Valid selection admitting zero objects degrades the same way",
    2026-08-07 gate finding on `TS3005a.transcript`). The full merged union
    (backstop-truncated) is kept, and `judge_status` is a distinct degrade
    value, never `"ok"` and never `"failed"` (that value names the
    unparseable/exception/empty-reply case, not a valid-but-empty one)."""
    run1 = _array(_CONCEPT_ITEM)
    run2 = _array(_PERSON_ITEM)
    # Well-formed reply, but names a title absent from every candidate --
    # nothing closed-set matches, and there is no Procedure to re-admit.
    llm = _SequencedLLM([run1, run2, _keep_reply("A Fabricated Title")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert {r.title for r in outcome.objects} == {"Stoicism", "Epictetus"}
    assert outcome.report.judge_status not in ("ok", "failed")
    assert outcome.report.judged_out_titles == ()


def test_union_empty_merged_union_skips_the_judge_entirely() -> None:
    """Both runs returning empty arrays: NO judge call is made (exactly the
    2 extraction calls), and `judge_status == "skipped"` -- the default
    whose docstring already means "judge not run". A judge call selecting
    among zero candidates spends a real LLM round trip to decide nothing,
    and used to land `judge_status == "ok"` despite admitting nothing."""
    llm = _SequencedLLM([_array(), _array(), _keep_reply("Unused")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(llm.calls) == 2
    assert outcome.report.judge_status == "skipped"
    assert outcome.objects == []


def test_union_single_candidate_skips_the_judge_call() -> None:
    """#644: a merged union of exactly ONE candidate makes NO judge call --
    the call is a provable no-op (every outcome keeps that candidate: a kept
    title keeps it, `None`/failed keeps the full set, an empty admitted set
    degrades to the full set), so spending it can only add noise. It was
    exactly that noise -- a full-line echo on the first, cold-start file of
    a batch -- that produced #644's degrade notice. `judge_status` is
    `"skipped"` ("judge not run"), sharing the empty-union skip's value."""
    llm = _SequencedLLM(
        [_array(_CONCEPT_ITEM), _array(_CONCEPT_ITEM), _keep_reply("Stoicism")]
    )

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(llm.calls) == 2  # 2 extraction calls, NO judge call
    assert outcome.report.judge_status == "skipped"
    assert [r.title for r in outcome.objects] == ["Stoicism"]
    assert outcome.report.produced == 1
    assert outcome.report.retained == 1
    assert outcome.report.judged_out_titles == ()


def test_union_full_line_echo_reply_is_salvaged_end_to_end() -> None:
    """#644, the multi-candidate variant: a judge reply echoing a WHOLE
    candidate line instead of the bare title is salvaged by `judge.select`
    back to the candidate title, so the union selects normally
    (`judge_status == "ok"`) instead of degrading to the full unfiltered
    set with `judge_status == "empty"`."""
    run1 = _array(_CONCEPT_ITEM, _PERSON_ITEM)
    echo = (
        "{\"keep\": [\"1. type='Concept' title='Stoicism' "
        "description='A school of Hellenistic philosophy.'\"]}"
    )
    llm = _SequencedLLM([run1, _array(), echo])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "ok"
    assert [r.title for r in outcome.objects] == ["Stoicism"]
    assert outcome.report.judged_out_titles == ("Epictetus",)


def test_union_backstop_passes_through_a_set_of_7_unchanged() -> None:
    """A judge-selected set of 7 passes through the backstop unchanged."""

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": ""}}'
        )

    run1 = _array(*(item(i) for i in range(1, 8)))  # 7 distinct
    run2 = _array()
    judge_reply = _keep_reply(*(f"Subject {i}" for i in range(1, 8)))
    llm = _SequencedLLM([run1, run2, judge_reply])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(outcome.objects) == 7
    assert outcome.report.produced == 7
    assert outcome.report.retained == 7


def test_union_backstop_passes_through_the_measured_real_source_range() -> None:
    """Issue #564: 15 and 17 judge-approved objects were measured on
    genuine sources (a course webinar and a meeting transcript), and the
    old backstop of 12 truncated both -- by position, so what survived was
    luck. Sets in the measured real-source range now pass untouched."""

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": ""}}'
        )

    run1 = _array(*(item(i) for i in range(1, 18)))  # 17 distinct
    run2 = _array()
    judge_reply = _keep_reply(*(f"Subject {i}" for i in range(1, 18)))
    llm = _SequencedLLM([run1, run2, judge_reply])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(outcome.objects) == 17
    assert outcome.report.produced == 17
    assert outcome.report.retained == 17
    assert outcome.report.discarded_titles == ()


def test_union_backstop_truncates_a_judge_selected_set_above_the_backstop() -> None:
    """A judge-selected set above `_UNION_BACKSTOP` (20) is truncated to
    exactly 20, applied strictly AFTER re-admission -- not before the
    judge -- and the report names the casualties."""

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": ""}}'
        )

    run1 = _array(*(item(i) for i in range(1, 24)))  # 23 distinct
    run2 = _array()
    judge_reply = _keep_reply(*(f"Subject {i}" for i in range(1, 24)))
    llm = _SequencedLLM([run1, run2, judge_reply])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(outcome.objects) == 20
    assert outcome.report.produced == 23
    assert outcome.report.retained == 20
    assert outcome.report.discarded_titles == ("Subject 21", "Subject 22", "Subject 23")


def test_union_extraction_run_2_error_propagates_unswallowed() -> None:
    """An `OllamaError` from run 2's extraction call (not the judge)
    propagates unswallowed -- the judge's fail-closed contract is its own,
    never extended to cover extraction failures."""
    run1 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, OllamaUnavailable("boom")])

    with pytest.raises(OllamaUnavailable):
        concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)


def test_extract_concept_regression_suite_still_green_and_prompt_untouched() -> None:
    """Regression guard (task 2.22): `extract_concept_union` is additive --
    `extract_concept`'s own `_SYSTEM_PROMPT` is untouched by this change."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    outcome = concept_mod.extract_concept("Some notes.", source_title="Notes", llm=llm)

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Stoicism"]


# --- Bounded re-ask on a sole source-title twin (#584) -----------------------
#
# When the FINAL, filtered object list is exactly one object and that object
# is a *droppable* source-title twin, the extractor asks once more with a
# DIFFERENT instruction and keeps whatever the second ask adds.
#
# Three properties define the design and are each fenced below:
#
# - It ADDS, never replaces (`measure_single_object_rate.py:49-57`): the
#   original object survives whatever the re-ask returns, so the guard stays
#   bounded on a genuinely single-subject source.
# - The second ask carries a DIFFERENT prompt: `extract_concept_union`
#   already asks twice with identical messages and the collapse survives 10
#   of 10 runs, so a second identical ask is measurably useless.
# - It fires on the twin predicate's BOTH conjuncts -- a lone `Procedure`
#   restating the source title is exempt (#413) and must not re-ask.

_DICHOTOMY_TWIN_TITLE = "Dichotomy of Control"

_DICHOTOMY_ECHO_ENTITY_ITEM = (
    '{"type": "Entity", "title": "Dichotomy of Control", '
    '"description": "The same subject the first pass already kept.", '
    '"body": ""}'
)


def test_sole_droppable_twin_re_asks_and_keeps_what_the_second_ask_adds() -> None:
    """#584: the reported harm -- a short source under an umbrella-topic
    title yields one object where its body holds more. When the sole
    surviving object is a droppable source-title twin, a second ask goes out
    and its findings are ADDED beside the original."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us, and on freedom from destructive emotion.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert [r.title for r in outcome.objects] == [
        "Dichotomy of Control",
        "Apatheia",
    ]


def test_union_sole_droppable_twin_re_asks_before_the_judge() -> None:
    """The trigger is applied SYMMETRICALLY on both paths (#581's
    precedent), at the point where the merged list is final and filtered --
    so the re-ask's findings reach the judge as ordinary candidates."""
    llm = _SequencedLLM(
        [
            _array(_DICHOTOMY_ITEM),
            _array(_DICHOTOMY_ITEM),
            _array(_APATHEIA_ITEM),
            _keep_reply("Dichotomy of Control", "Apatheia"),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        "Notes on what is up to us, and on freedom from destructive emotion.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    # 2 extraction runs + 1 re-ask + 1 judge call.
    assert len(llm.calls) == 4
    assert [r.title for r in outcome.objects] == [
        "Dichotomy of Control",
        "Apatheia",
    ]
    assert "Apatheia" in llm.calls[3][1]["content"]


def test_re_ask_returning_nothing_leaves_the_objects_untouched() -> None:
    """The bound: a re-ask that finds nothing leaves the output exactly as
    it was before this change -- same object, same cap accounting. Only the
    report's disclosure of the spent call differs."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert outcome.objects == [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Dichotomy of Control",
            description=("The Stoic distinction between what is and is not up to us."),
            body="",
        )
    ]
    assert outcome.report.produced == 1
    assert outcome.report.retained == 1
    assert outcome.report.discarded_titles == ()


def test_re_ask_cannot_empty_a_genuinely_single_subject_source() -> None:
    """The floor is not weakened -- `test_source_title_twin_kept_when_it_is
    _the_only_object`'s contract on the new path. A genuinely single-subject
    source (the `mcp-launch` shape, and the probe's `Replica Lag` negative
    control) also triggers the re-ask, and its object survives whatever
    comes back: here the second ask merely echoes the subject already kept,
    which adds nothing and removes nothing."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), _array(_DICHOTOMY_ECHO_ENTITY_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert [(r.type, r.title) for r in outcome.objects] == [
        ("Concept", "Dichotomy of Control")
    ]


def test_lone_exempt_procedure_re_asks_because_the_trigger_ignores_type() -> None:
    """The trigger is TITLE-ONLY, unlike the drop rule's two-conjunct
    predicate. Measured (#584, `qwen3:8b`, `--runs 5 --seed 7`): the `lesson`
    treatment arm returns one object in 5 of 5 runs and it is a `Procedure`
    every time, so a type-blind trigger is the only one that reaches the
    fixture reproducing the defect.

    The `Procedure` exemption (#413) exists to stop a DELETION -- dropping a
    rich tutorial's primary how-to was silent data loss. A re-ask deletes
    nothing, so that rationale does not transfer: a lone `Procedure`
    restating its source title is exactly as suspicious as a lone `Concept`
    doing the same, and asking whether the body develops anything further
    cannot harm either.

    This test asserted the opposite before the live probe came back. What it
    genuinely guarded -- that the exemption still protects the `Procedure`
    from the DROP rule -- is guarded by
    `test_primary_procedure_survives_alongside_the_subjects_it_yields` and
    `test_lone_exempt_procedure_is_never_dropped_by_the_twin_rule`."""
    llm = _SequencedLLM(
        [_array(_RESEARCH_AGENT_PROCEDURE_ITEM), _array(_APATHEIA_ITEM)]
    )

    outcome = concept_mod.extract_concept(
        "A walkthrough of building a research agent, and of apatheia.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert [r.title for r in outcome.objects] == [
        "Building a Research Agent with the Claude Agent SDK",
        "Apatheia",
    ]


def test_lone_exempt_procedure_is_never_dropped_by_the_twin_rule() -> None:
    """The drop rule's exemption is untouched by the widened trigger: a
    `Procedure` restating the source title survives the twin rule whatever
    else the source yields (#413). Asserted on `_drop_source_title_twins`
    directly, so no re-ask call can stand in for the guarantee."""
    procedure = concept_mod.ExtractionResult(
        type="Procedure",
        title="Building a Research Agent with the Claude Agent SDK",
        description="How to build a research agent on the SDK.",
        body="Install the SDK, define the subagents, add guardrails.",
    )
    genuine = concept_mod.ExtractionResult(
        type="Concept",
        title="Claude Agent SDK",
        description="The toolkit the tutorial builds on.",
        body="",
    )

    kept = concept_mod._drop_source_title_twins(
        [procedure, genuine],
        source_title="Building a Research Agent with the Claude Agent SDK",
    )

    assert kept == [procedure, genuine]
    assert not concept_mod._is_droppable_source_title_twin(
        procedure, source_title="Building a Research Agent with the Claude Agent SDK"
    )
    # ...and the title-only predicate the TRIGGER uses says the opposite,
    # which is exactly the difference between gating a deletion and gating
    # an addition.
    assert concept_mod._restates_source_title(
        procedure, source_title="Building a Research Agent with the Claude Agent SDK"
    )


def test_no_re_ask_when_a_lone_procedure_does_not_restate_the_title() -> None:
    """Widening the trigger to ignore TYPE does not widen it to ignore the
    TITLE. A lone `Procedure` whose title is not the source's own is the
    ordinary single-subject reply, and no second call goes out."""
    llm = _SequencedLLM([_array(_PROCEDURE_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "A daily reflection practice.", source_title="Notes", llm=llm
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Morning Journaling Routine"]


def test_no_re_ask_when_a_procedure_twin_keeps_company() -> None:
    """The `exactly one` half of the trigger is unchanged by the widening: a
    `Procedure` restating the source title BESIDE a genuine subject is the
    rich-tutorial shape (#413), which the drop rule deliberately keeps whole
    -- two objects, so no re-ask."""
    llm = _SequencedLLM(
        [
            _array(_RESEARCH_AGENT_PROCEDURE_ITEM, _AGENT_SDK_ITEM),
            _array(_APATHEIA_ITEM),
        ]
    )

    outcome = concept_mod.extract_concept(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == [
        "Building a Research Agent with the Claude Agent SDK",
        "Claude Agent SDK",
    ]


def test_re_ask_returning_nothing_leaves_a_lone_procedure_untouched() -> None:
    """The additive bound survives the widening, on the newly-reachable
    path: the re-ask fires on a lone `Procedure` twin, finds nothing, and
    the output is what it was before the trigger existed."""
    llm = _SequencedLLM([_array(_RESEARCH_AGENT_PROCEDURE_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert outcome.objects == [
        concept_mod.ExtractionResult(
            type="Procedure",
            title="Building a Research Agent with the Claude Agent SDK",
            description="How to build a research agent on the SDK.",
            body="Install the SDK, define the subagents, add guardrails.",
        )
    ]
    assert outcome.report.produced == 1
    assert outcome.report.retained == 1
    assert outcome.report.discarded_titles == ()
    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ()


def test_no_re_ask_when_more_than_one_object_survives() -> None:
    """The re-ask must not fire on every source -- that would silently
    double extraction cost. Two surviving objects is not the collapse."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM, _PERSON_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on Stoicism and Epictetus.", source_title="Notes", llm=llm
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Stoicism", "Epictetus"]


def test_no_re_ask_when_two_twins_both_survive_the_floor() -> None:
    """The `exactly one` half of the trigger, on the only shape where it is
    load-bearing: two candidates that are BOTH source-title twins leave no
    non-twin for `_drop_source_title_twins` to keep, so its floor returns
    both. The list is still not the collapse -- it holds two objects -- and
    no second call goes out."""
    entity_twin = (
        '{"type": "Entity", "title": "Dichotomy of Control", '
        '"description": "A tool named after the idea.", "body": ""}'
    )
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM, entity_twin), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.type for r in outcome.objects] == ["Concept", "Entity"]


def test_no_re_ask_when_the_lone_object_is_not_a_title_twin() -> None:
    """One object that does NOT restate the source title is the ordinary
    single-subject reply, not the twin collapse #584 measures."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on Stoicism.", source_title="Notes", llm=llm
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Stoicism"]


def test_re_ask_carries_a_different_instruction_than_the_extraction_prompt() -> None:
    """`extract_concept_union` already asks twice with IDENTICAL messages and
    the collapse survives 10 of 10 runs, so the second ask must differ. The
    extraction prompt itself is untouched -- the re-ask uses a separate
    constant (the probe's `CONTROL_PROMPT_SHA` pins the extraction one)."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), "[]"])

    concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    first_system = llm.calls[0][0]["content"]
    second_system = llm.calls[1][0]["content"]
    assert first_system == concept_mod._SYSTEM_PROMPT
    assert second_system == concept_mod._REASK_SYSTEM_PROMPT
    assert second_system != first_system
    # The re-ask names what it wants rather than repeating the general
    # extraction request.
    assert "any FURTHER distinct subject in its own right" in second_system


def test_re_ask_prompt_makes_an_empty_answer_correct_and_expected() -> None:
    """The negative control's risk: a genuinely single-subject source also
    triggers the re-ask, and an instruction that pressures the model to
    produce more will make it invent subjects. "Nothing further" is written
    into the prompt as a first-class answer, not left implicit."""
    system = concept_mod._REASK_SYSTEM_PROMPT

    assert "An empty array [] is a CORRECT and EXPECTED answer here." in system
    assert (
        "Many sources genuinely cover exactly one subject, and for those "
        "the first pass was right: answer [] and nothing else." in system
    )
    assert "Do not invent a subject" in system


def test_re_ask_user_turn_names_the_object_already_kept() -> None:
    """The second ask asks for what the body covers BEYOND the subject the
    title already names, so it must say which object is already kept --
    otherwise "do not repeat it" has no referent."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), "[]"])

    concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    user_content = llm.calls[1][1]["content"]
    assert "ALREADY KEPT" in user_content
    assert "Dichotomy of Control" in user_content
    assert "Notes on what is up to us." in user_content


def test_report_names_the_re_ask_call_and_what_it_added() -> None:
    """A silent extra model call is exactly the cost this project reports
    rather than hides: the report carries the spent call AND the titles it
    contributed."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us, and on freedom from destructive emotion.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ("Apatheia",)


def test_report_defaults_to_no_re_ask_when_the_trigger_never_fires() -> None:
    """An untriggered source reports zero spent re-ask calls and no added
    titles -- the fields are readable as "this never happened"."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    outcome = concept_mod.extract_concept(
        "Notes on Stoicism.", source_title="Notes", llm=llm
    )

    assert outcome.report.reask_runs == 0
    assert outcome.report.reask_added_titles == ()


def test_re_ask_backend_failure_keeps_the_original_object() -> None:
    """The re-ask is an OPTIONAL extra call on top of an already-complete
    result, exactly like `judge.select` -- so its failure degrades to
    "added nothing" (design D7's argument) instead of destroying validated
    extraction work. The spent call is still reported."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), OllamaUnavailable("boom")])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Dichotomy of Control"]
    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ()


def test_re_ask_addition_restating_the_title_is_filtered_whatever_its_type() -> None:
    """The additions filter is TITLE-ONLY, like the trigger, because both
    are additive.

    `_REASK_SYSTEM_PROMPT` already tells the model not to restate the kept
    subject "under another name or another type". A `Procedure` whose title
    is the source's own is exactly the "another type" case it forbids, so
    admitting it would contradict the instruction just sent -- and
    `_dedup_merged` cannot catch it, since its key is
    `(type, normalized-title)` and the types differ.

    #413's exemption does not apply here. What it bought was the right of a
    PRIMARY `Procedure` -- one the first pass genuinely found -- not to be
    DELETED. A re-ask addition restating the source title is not that."""
    procedure_echo = (
        '{"type": "Procedure", "title": "Dichotomy of Control", '
        '"description": "Steps for applying the dichotomy.", '
        '"body": "Sort what is up to you from what is not."}'
    )
    llm = _SequencedLLM(
        [_array(_DICHOTOMY_ITEM), _array(procedure_echo, _APATHEIA_ITEM)]
    )

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us, and on freedom from destructive emotion.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    # The genuine addition survives; the same-titled echo does not, so no
    # two objects share a title under different types.
    assert [(r.type, r.title) for r in outcome.objects] == [
        ("Concept", "Dichotomy of Control"),
        ("Concept", "Apatheia"),
    ]
    assert outcome.report.reask_added_titles == ("Apatheia",)


def test_filtering_every_addition_leaves_the_kept_object_untouched() -> None:
    """The bound the additions filter cannot break: it only ever removes
    ADDITIONS, so a re-ask whose every finding is filtered is
    indistinguishable from one that returned nothing -- byte-identical to
    the output before this guard existed."""
    procedure_echo = (
        '{"type": "Procedure", "title": "Dichotomy of Control", '
        '"description": "Steps for applying the dichotomy.", "body": ""}'
    )
    entity_echo = (
        '{"type": "Entity", "title": "dichotomy of CONTROL", '
        '"description": "The same subject, differently cased.", "body": ""}'
    )
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), _array(procedure_echo, entity_echo)])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert outcome.objects == [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Dichotomy of Control",
            description="The Stoic distinction between what is and is not up to us.",
            body="",
        )
    ]
    assert outcome.report.produced == 1
    assert outcome.report.retained == 1
    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ()


# --- Topic containment: the trigger only (#584) ------------------------------
#
# The live probe found the trigger never firing on the fixture that
# reproduces the defect:
#
#     source_title : 'Lesson 3: Setting Up a Python Project'
#     object       : Procedure: 'Setting Up a Python Project'
#     restates?    : False    <- exact comparison is correct; it IS not equal
#
# The model strips the framing and titles the object after the umbrella
# TOPIC. So the harm is "the object restates the topic the title names", and
# an exact-match trigger cannot see that class by construction.
#
# The trigger widens to token containment. The ADDITIONS FILTER does NOT --
# see `test_re_ask_addition_contained_in_the_title_is_still_kept`.

_LESSON_SOURCE_TITLE = "Lesson 3: Setting Up a Python Project"

_LESSON_PROCEDURE_ITEM = (
    '{"type": "Procedure", "title": "Setting Up a Python Project", '
    '"description": "How to lay out a new Python project.", '
    '"body": "Create the venv, pin the lockfile, add the tests tree."}'
)

_LOCKFILE_ITEM = (
    '{"type": "Concept", "title": "Dependency Lockfile", '
    '"description": "A pinned record of resolved dependency versions.", '
    '"body": ""}'
)


def test_lone_object_restating_the_titles_topic_triggers_a_re_ask() -> None:
    """The measured shape (#584, `qwen3:8b`, `--runs 5 --seed 7`): the
    `lesson` treatment arm returns ONE `Procedure` whose title is the source
    title minus its framing. Exact comparison says "not a twin" -- correctly,
    since literally it is not -- so the trigger has to read topic
    containment or it cannot reach this class at all."""
    llm = _SequencedLLM([_array(_LESSON_PROCEDURE_ITEM), _array(_LOCKFILE_ITEM)])

    outcome = concept_mod.extract_concept(
        "Create the venv, pin the lockfile, add the tests tree.",
        source_title=_LESSON_SOURCE_TITLE,
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert [r.title for r in outcome.objects] == [
        "Setting Up a Python Project",
        "Dependency Lockfile",
    ]
    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ("Dependency Lockfile",)


def test_containment_trigger_respects_word_boundaries() -> None:
    """`"Rust"` must not be found inside `"Trust Boundaries"`. Containment is
    token-level, never raw substring -- the substring reading is how a
    predicate starts matching anything that happens to share letters."""
    rust = (
        '{"type": "Concept", "title": "Rust", '
        '"description": "A systems programming language.", "body": ""}'
    )
    llm = _SequencedLLM([_array(rust), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "A note about the language.", source_title="Trust Boundaries", llm=llm
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Rust"]


def test_no_trigger_when_one_generic_token_is_all_that_is_shared() -> None:
    """#555's failure mode, fenced. `resolution/similarity.py` drops short
    tokens, which manufactures single-token titles, and containment then
    matches anything sharing one generic token -- `ai-agent` alone landed in
    eleven duplicate groups. A trigger that fires on nearly every source is
    not a narrower option than "sole object alone"; it is that option plus a
    predicate to maintain. So containment requires the contained side to keep
    at least `_MIN_TOPIC_TOKENS` meaningful tokens."""
    agents = (
        '{"type": "Concept", "title": "Agents", '
        '"description": "Software that acts on your behalf.", "body": ""}'
    )
    llm = _SequencedLLM([_array(agents), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "A note about agents.", source_title="AI Agents in Practice", llm=llm
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Agents"]


def test_no_trigger_when_the_lone_title_shares_no_meaningful_tokens() -> None:
    """An ordinary single-subject reply, whose object carries a title of its
    own, still does not spend a call."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on Stoicism.",
        source_title="Setting Up a Python Project",
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Stoicism"]


def test_no_containment_trigger_when_a_second_object_survives() -> None:
    """The `exactly one` half of the trigger is untouched by the widening: a
    topic-restating object BESIDE a genuine subject is not the collapse."""
    llm = _SequencedLLM(
        [_array(_LESSON_PROCEDURE_ITEM, _LOCKFILE_ITEM), _array(_APATHEIA_ITEM)]
    )

    outcome = concept_mod.extract_concept(
        "Create the venv, pin the lockfile, add the tests tree.",
        source_title=_LESSON_SOURCE_TITLE,
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == [
        "Setting Up a Python Project",
        "Dependency Lockfile",
    ]


def test_containment_re_ask_returning_nothing_leaves_the_object_untouched() -> None:
    """The additive bound on the newly reachable path: the containment
    trigger fires, the second ask finds nothing, and the output is what it
    was before the trigger existed."""
    llm = _SequencedLLM([_array(_LESSON_PROCEDURE_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "Create the venv, pin the lockfile, add the tests tree.",
        source_title=_LESSON_SOURCE_TITLE,
        llm=llm,
    )

    assert outcome.objects == [
        concept_mod.ExtractionResult(
            type="Procedure",
            title="Setting Up a Python Project",
            description="How to lay out a new Python project.",
            body="Create the venv, pin the lockfile, add the tests tree.",
        )
    ]
    assert outcome.report.produced == 1
    assert outcome.report.retained == 1
    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ()


def test_re_ask_addition_contained_in_the_title_is_still_kept() -> None:
    """The asymmetry, pinned: the TRIGGER reads containment, the ADDITIONS
    FILTER stays on exact restatement.

    Different cost of error. The trigger decides whether to SPEND A CALL --
    being wrong costs one call. The filter decides whether to DISCARD A
    FOUND SUBJECT -- being wrong loses real content. So the filter keeps the
    tighter threshold, and an addition whose title is merely contained in
    the source title survives."""
    llm = _SequencedLLM(
        [
            _array(_LESSON_PROCEDURE_ITEM),
            _array(
                '{"type": "Concept", '
                '"title": "Python Project", "description": "The unit of work a '
                'lesson sets up.", "body": ""}'
            ),
        ]
    )

    outcome = concept_mod.extract_concept(
        "Create the venv, pin the lockfile, add the tests tree.",
        source_title=_LESSON_SOURCE_TITLE,
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == [
        "Setting Up a Python Project",
        "Python Project",
    ]


def test_no_trigger_on_partial_overlap_that_is_not_containment() -> None:
    """Containment means the smaller token set is a SUBSET, not that the two
    titles share a token. This is a measured shape, not a hypothetical: the
    `producto` flat arm collapsed 5 of 5 to a `Procedure` titled `Onboarding
    Process` under the source title `Onboarding, Slack y trabajo pendiente`
    -- one token in common, and the rest genuinely different. Firing there
    would spend a call on an object that named its own subject."""
    onboarding = (
        '{"type": "Procedure", "title": "Onboarding Process", '
        '"description": "How a new hire is brought on.", "body": ""}'
    )
    llm = _SequencedLLM([_array(onboarding), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Cómo se incorpora a alguien nuevo.",
        source_title="Onboarding, Slack y trabajo pendiente",
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Onboarding Process"]


def test_no_trigger_when_containment_rests_only_on_tiny_tokens() -> None:
    """Tokens below `_MIN_TOPIC_TOKEN_LENGTH` are dropped BEFORE the
    two-token floor is counted, so a pair of two-letter generic tokens
    cannot satisfy it. This is #555's `ai-agent` family: short generic
    tokens are exactly the ones that match everything, and counting them as
    topic evidence would re-import the failure that predicate suffered."""
    ai_ml = (
        '{"type": "Concept", "title": "AI ML", '
        '"description": "Two field abbreviations.", "body": ""}'
    )
    llm = _SequencedLLM([_array(ai_ml), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "A note on the two fields.",
        source_title="AI ML Systems in Practice",
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["AI ML"]


def test_exact_restatement_still_triggers_below_the_token_floor() -> None:
    """Containment WIDENS the trigger; it must never narrow it. A one-word
    source title exactly restated keeps only one meaningful token, which is
    under `_MIN_TOPIC_TOKENS` -- so the exact-restatement shortcut is what
    preserves the behaviour the trigger had before containment existed."""
    stoicism_twin = (
        '{"type": "Concept", "title": "Stoicism", '
        '"description": "A school of Hellenistic philosophy.", "body": ""}'
    )
    llm = _SequencedLLM([_array(stoicism_twin), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "A note on the school and on freedom from destructive emotion.",
        source_title="Stoicism",
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert [r.title for r in outcome.objects] == ["Stoicism", "Apatheia"]


# --- The sole-object-restates-source disclosure flag (#585) ---------------
#
# #585's chosen criterion is "keep the object, mark the Source". The
# extraction layer owns only the OBSERVATION -- whether the final list is
# one object restating its source -- and reports it. Stamping it onto the
# Source's frontmatter is `cli/main.py`'s job, and the split is deliberate:
# this module stays config-free and write-free, exactly as `_MAX_OBJECTS_
# PER_SOURCE`'s cap report already does.


def test_sole_object_restating_the_source_is_reported() -> None:
    """The defect #585 names, measured on the shape it was filed for: the
    re-ask fires, finds nothing further, and the one object left restates
    the source. The flag is what makes that visible to the caller."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Dichotomy of Control"]
    assert outcome.report.sole_object_restates_source is True


def test_sole_object_flag_is_false_when_the_reask_added_a_subject() -> None:
    """The re-ask succeeding is exactly the case that is NOT dishonest
    output: two objects reach the bundle, and the second one is content the
    title never named. Reading the flag off the pre-re-ask list would mark
    this Source anyway, so it is computed on the FINAL list."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us, and on freedom from destructive emotion.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Dichotomy of Control", "Apatheia"]
    assert outcome.report.sole_object_restates_source is False


def test_sole_object_flag_is_false_for_a_lone_distinct_subject() -> None:
    """One object is not the defect. A source yielding a single subject its
    title does not name is honest output, and marking it would make the
    notice noise -- the failure mode #566 is open for."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    outcome = concept_mod.extract_concept(
        "Notes on Stoicism.", source_title="Notes", llm=llm
    )

    assert [r.title for r in outcome.objects] == ["Stoicism"]
    assert outcome.report.sole_object_restates_source is False


def test_sole_object_flag_is_false_when_two_objects_survive() -> None:
    """`exactly one` is half the predicate. Two objects -- even when one of
    them restates the source and rode the twin floor in -- is not a source
    whose SOLE object is a restatement."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM, _PERSON_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on Stoicism and Epictetus.", source_title="Notes", llm=llm
    )

    assert outcome.report.sole_object_restates_source is False


def test_sole_object_flag_is_type_blind() -> None:
    """A lone `Procedure` restating its source title is marked like any
    other type. `_TWIN_EXEMPT_TYPE` exists to stop a DELETION (#413); this
    is a disclosure, which adds information and removes none, so the
    exemption's rationale does not transfer -- the same argument
    `_restates_source_title` already makes for both additive decisions."""
    llm = _SequencedLLM([_array(_RESEARCH_AGENT_PROCEDURE_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert [r.type for r in outcome.objects] == ["Procedure"]
    assert outcome.report.sole_object_restates_source is True


def test_sole_object_flag_fires_on_topic_containment() -> None:
    """The predicate is `_restates_source_topic`, the re-ask trigger's own,
    not the exact `_restates_source_title`. The `lesson` class #584 measured
    -- an object titled after the umbrella topic with the framing stripped
    -- is the commonest shape of this defect, and an exact comparison is
    blind to it."""
    stripped = (
        '{"type": "Concept", "title": "Setting Up a Python Project", '
        '"description": "The project layout the lesson builds.", "body": ""}'
    )
    llm = _SequencedLLM([_array(stripped), "[]"])

    outcome = concept_mod.extract_concept(
        "The lesson walks through a project layout.",
        source_title="Lesson 3: Setting Up a Python Project",
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Setting Up a Python Project"]
    assert outcome.report.sole_object_restates_source is True


def test_sole_object_flag_is_reported_on_the_union_path() -> None:
    """Both orchestrators report it. `extract_concept_union` is the
    PRODUCT-ON path (`config.DEFAULT_UNION_JUDGE`), so a flag only the
    single-run path set would never fire for a real user."""
    run = _array(_DICHOTOMY_ITEM)
    llm = _SequencedLLM([run, run, "[]", _keep_reply("Dichotomy of Control")])

    outcome = concept_mod.extract_concept_union(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Dichotomy of Control"]
    assert outcome.report.sole_object_restates_source is True


def test_sole_object_flag_defaults_to_false_on_an_empty_extraction() -> None:
    """Zero objects is a degrade `extraction_status` already names, not a
    restatement. The flag must not fire there, or a Source with nothing
    derived would carry a notice claiming it derived something."""
    llm = _FakeLLM(reply="[]")

    outcome = concept_mod.extract_concept("Notes.", source_title="Notes", llm=llm)

    assert outcome.objects == []
    assert outcome.report.sole_object_restates_source is False


# --- Acronym/expansion in the ADDITIVE predicate (issue #586) -------------
#
# `MCP` and `Model Context Protocol` are the same subject, and the twin
# comparison is exact-normalized, so it cannot see that. #586 asks where
# that awareness belongs.
#
# IDENTITY is already answered downstream: `resolution.similarity.
# acronym_expansion_match` (#397) pairs them and feeds
# duplicates -> adjudicate -> merge, measured on a real 19-document bundle
# where it fired on exactly two pairs, this one among them.
#
# What was blind is EXTRACTION's ADDITIVE pair -- the #584 re-ask trigger
# and the #585 disclosure. Neither removes anything: one spends a call, one
# adds a sentence. So they learn it, and the DROP rule does not: dropping an
# object for writing the fuller name is #413's mistake exactly.

_MCP_ACRONYM_ITEM = (
    '{"type": "Concept", "title": "MCP", '
    '"description": "A protocol for tool-augmented models.", "body": ""}'
)

_MCP_EXPANSION_ITEM = (
    '{"type": "Concept", "title": "Model Context Protocol", '
    '"description": "A protocol for tool-augmented models.", "body": ""}'
)


def test_expansion_object_under_an_acronym_source_triggers_the_reask() -> None:
    """#586's own case, stated as the issue narrows it: H1 `# MCP`, object
    `Model Context Protocol`.

    Exact comparison answers "not a twin" -- literally it is not -- so the
    source collapsed to one object and nothing asked whether its body
    covered anything further. The second call is what that costs."""
    llm = _SequencedLLM([_array(_MCP_EXPANSION_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "MCP lets a model call tools, and it also covers freedom from "
        "destructive emotion.",
        source_title="MCP",
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert [r.title for r in outcome.objects] == ["Model Context Protocol", "Apatheia"]


def test_sole_expansion_object_under_an_acronym_source_is_disclosed() -> None:
    """The #585 half: when the re-ask finds nothing further, the Source is
    marked. Before this, an acronym-titled source storing one object named
    after its expansion looked like ordinary derived output."""
    llm = _SequencedLLM([_array(_MCP_EXPANSION_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "MCP lets a model call tools.", source_title="MCP", llm=llm
    )

    assert [r.title for r in outcome.objects] == ["Model Context Protocol"]
    assert outcome.report.sole_object_restates_source is True


def test_acronym_object_under_an_expansion_source_also_matches() -> None:
    """Symmetric, like the resolution-layer matcher it mirrors: which side
    carries the acronym must not change the verdict."""
    llm = _SequencedLLM([_array(_MCP_ACRONYM_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "This protocol lets a model call tools.",
        source_title="Model Context Protocol",
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["MCP"]
    assert outcome.report.sole_object_restates_source is True


def test_acronym_matching_never_reaches_the_drop_rule() -> None:
    """THE #413 GUARD, and the reason this landed in the additive predicate
    alone.

    An object named after the source's expansion, sitting beside a genuine
    second subject, is exactly the shape `_drop_source_title_twins` deletes
    when it considers something a twin. It must survive: punishing the model
    for writing the fuller name is the mistake #413 was filed for, and this
    rule is the one operation in the module that destroys content."""
    llm = _SequencedLLM(
        [_array(_MCP_EXPANSION_ITEM, _APATHEIA_ITEM), _array(_CONCEPT_ITEM)]
    )

    outcome = concept_mod.extract_concept(
        "MCP lets a model call tools, and Apatheia is freedom from emotion.",
        source_title="MCP",
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Model Context Protocol", "Apatheia"]


def test_acronym_matching_leaves_chunk_merge_dedup_untouched() -> None:
    """THE ISSUE'S EXPLICIT CONSTRAINT, pinned as behaviour.

    `_normalize_title` is shared with `_dedup_merged`/`_merge_union`
    precisely so two rules deciding "same title" differently cannot let an
    object dodge one by matching the other. Folding acronym matching into it
    would start merging `MCP` with `Model Context Protocol` across chunks --
    two distinct objects becoming one, which is silent data loss.

    The source title is neither, so no twin or re-ask logic is in play: this
    asks only whether the dedup key still tells the two apart."""
    llm = _SequencedLLM([_array(_MCP_ACRONYM_ITEM, _MCP_EXPANSION_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        "Notes on the protocol.", source_title="Notes", llm=llm
    )

    assert [r.title for r in outcome.objects] == ["MCP", "Model Context Protocol"]


def test_a_two_letter_acronym_does_not_match() -> None:
    """Bounded by the same three-letter floor the resolution-layer matcher
    uses, and for its reason: two-letter initialisms are far too common to
    carry identity, and on a corpus about agents most titles would qualify."""
    ai_item = (
        '{"type": "Concept", "title": "Artificial Intelligence", '
        '"description": "The field.", "body": ""}'
    )
    llm = _SequencedLLM([_array(ai_item), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept("AI notes.", source_title="AI", llm=llm)

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Artificial Intelligence"]
    assert outcome.report.sole_object_restates_source is False


def test_a_shared_initial_is_not_an_acronym_match() -> None:
    """An initialism abbreviates SEVERAL words. Runs of one word are
    excluded by construction, or every title sharing a first letter would
    match -- the floodgate #555 paid for in the containment arm."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "A note on the school.", source_title="Sto", llm=llm
    )

    assert len(llm.calls) == 1
    assert outcome.report.sole_object_restates_source is False


# --- Low-yield trigger: a lone NON-restating object on a long source (#642) --
#
# The restate trigger (#584/#586) fires only when the sole object restates
# the source's own topic. A source collapsing to one LEGITIMATE-but-
# insufficient object shows no twin symptom and got no second attempt:
# `02-how-claude-code-works.md` (2411 chars) returned a single object titled
# `Agentic Loop` in 3 of 5 runs (`qwen3:8b`), silently losing Context
# Window / Tools / Permissions. Manually invoking the re-ask on those runs
# recovered exactly those three subjects all 3 times -- identical to the
# good runs' object set.
#
# So the trigger widens: a sole object that does NOT restate the source also
# spends the one extra call, but only when the source is long enough for
# "one object" to be a suspicious answer (`_REASK_LOW_YIELD_THRESHOLD`).
# The restate arm stays length-independent -- the negative control
# (`Replica Lag`, 1260 chars, genuinely single-subject) relies on it firing
# below the threshold, where the additive re-ask added zero junk in 5 of 5
# runs.


def _low_yield_source(chars: int) -> str:
    """Deterministic prose of EXACTLY `chars` characters, so the boundary
    tests pin the threshold comparison itself rather than a length that
    happens to land on one side of it."""
    base = (
        "The loop reads the context window, calls tools, and checks "
        "permissions before every step. "
    )
    return (base * (chars // len(base) + 1))[:chars]


def test_low_yield_threshold_is_two_thousand_chars() -> None:
    """The measured rationale, pinned: 2000 sits below the measured recovery
    case (2411 chars, recovery 3/3) and above the trivial-note class -- a
    46-byte note yielding one object is a CORRECT answer, not a collapse."""
    assert concept_mod._REASK_LOW_YIELD_THRESHOLD == 2000


def test_lone_non_restating_object_on_a_long_source_triggers_a_re_ask() -> None:
    """#642's measured shape: one legitimate object that does NOT restate
    the source title, from a source long enough that one object is a
    suspicious yield. The re-ask fires, and its additions merge exactly as
    on the restate path. Exactly 2000 chars, so this test is also the
    `>=` half of the boundary."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM), _array(_PERSON_ITEM)])

    outcome = concept_mod.extract_concept(
        _low_yield_source(2000), source_title="Notes", llm=llm
    )

    assert len(llm.calls) == 2
    assert [r.title for r in outcome.objects] == ["Stoicism", "Epictetus"]
    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ("Epictetus",)


def test_no_re_ask_for_a_lone_non_restating_object_just_under_the_threshold() -> None:
    """The `<` half of the boundary: at 1999 chars the lone non-restating
    object is today's ordinary single-subject reply, and no second call goes
    out. Below the threshold the widening changes nothing."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        _low_yield_source(1999), source_title="Notes", llm=llm
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Stoicism"]
    assert outcome.report.reask_runs == 0
    assert outcome.report.reask_added_titles == ()


def test_restate_trigger_still_fires_below_the_low_yield_threshold() -> None:
    """The restate arm is length-INDEPENDENT, exactly as before #642: a sole
    restating object fires the re-ask on a source far under 2000 chars. The
    negative control (`Replica Lag`, 1260 chars) relies on this arm, and the
    length gate must never reach it."""
    llm = _SequencedLLM([_array(_DICHOTOMY_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        "Notes on what is up to us.",
        source_title=_DICHOTOMY_TWIN_TITLE,
        llm=llm,
    )

    assert len(llm.calls) == 2
    assert [r.title for r in outcome.objects] == ["Dichotomy of Control", "Apatheia"]
    assert outcome.report.reask_runs == 1


def test_no_low_yield_re_ask_when_more_than_one_object_survives() -> None:
    """The `exactly one` half of the trigger is untouched by the widening:
    two surviving objects on a long source is a normal yield, whatever the
    length -- firing there would silently double extraction cost on every
    long source."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM, _PERSON_ITEM), _array(_APATHEIA_ITEM)])

    outcome = concept_mod.extract_concept(
        _low_yield_source(2500), source_title="Notes", llm=llm
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == ["Stoicism", "Epictetus"]
    assert outcome.report.reask_runs == 0


def test_low_yield_re_ask_returning_nothing_leaves_the_object_untouched() -> None:
    """The additive bound on the newly reachable path: the length trigger
    fires, the second ask finds nothing, and the output is byte-identical to
    what it was before the trigger existed. The false-positive cost is one
    clean extra call, nothing more."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        _low_yield_source(2411), source_title="Notes", llm=llm
    )

    assert outcome.objects == [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Stoicism",
            description="A school of Hellenistic philosophy.",
            body="Founded by Zeno of Citium.",
        )
    ]
    assert outcome.report.reask_runs == 1
    assert outcome.report.reask_added_titles == ()


def test_low_yield_re_ask_does_not_mark_the_source_as_restating() -> None:
    """The #585 notice keeps its own predicate untouched: a low-yield
    re-ask fires on an object that does NOT restate the source, so
    `sole_object_restates_source` stays False even when the re-ask adds
    nothing. The notice answers "did the source collapse to a restatement
    of itself", not "did we spend a second call"."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM), "[]"])

    outcome = concept_mod.extract_concept(
        _low_yield_source(2411), source_title="Notes", llm=llm
    )

    assert outcome.report.reask_runs == 1
    assert outcome.report.sole_object_restates_source is False


# --- Wrong-language title gate (#618) ---------------------------------------
#
# Measured mechanism (evals/language_leak/, #563): on a ~24 KB code-switched
# Spanish transcript, 0.69 of window-level candidate titles carried English,
# and the slug is the permanent Concept ID. A named-language prompt anchor
# was measured and REJECTED (leak 0.69 -> 0.63, +83% latency) -- prompt
# instructions do not carry this rule at this tier, so the gate is
# deterministic and post-extraction, on the CHUNKED paths only (the 39 short
# fixture documents showed zero leakage; the single-call path is untouched).
#
# The class distinction the gate encodes: a wrong-language title quoted
# VERBATIM from the source prose (`Model Context Protocol`) is the subject's
# own proper name and passes; a translatable title RENDERED in the wrong
# language (`Recovery of Knowledge Project`) is the harmful class and drops.


def _spanish_lines(chars: int) -> str:
    """Deterministic Spanish prose comfortably above `chars`, line-shaped so
    `_chunk_lines` windows it like the transcripts the leak was measured on."""
    lines = [
        "Ana: Revisamos el avance del proyecto y las decisiones pendientes "
        "sobre la capa de almacenamiento con el equipo de datos.",
        "Bruno: La migración terminó y los índices se regeneran con el "
        "modelo nuevo; la búsqueda mejoró bastante en las pruebas.",
        "Carla: Falta documentar el procedimiento de ingesta para el equipo "
        "de soporte, que lo usa a diario en la operación.",
    ]
    blocks: list[str] = []
    index = 0
    while sum(len(b) + 1 for b in blocks) <= chars:
        blocks.append(f"{lines[index % len(lines)]} (bloque {index})")
        index += 1
    return "\n".join(blocks)


def test_dominant_language_detects_spanish_and_english() -> None:
    spanish = "la decisión de el equipo sobre los datos y las pruebas en un día"
    english = "the decision of the team about the data and the tests in a day"

    assert concept_mod._dominant_language(spanish) == "es"
    assert concept_mod._dominant_language(english) == "en"


def test_dominant_language_is_none_when_no_side_clearly_wins() -> None:
    """Fail-open by construction: an empty text, a neutral text, and an
    evenly mixed text all yield `None`, and `None` disables the gate."""
    assert concept_mod._dominant_language("") is None
    assert concept_mod._dominant_language("MCP HTTP 2026") is None
    assert concept_mod._dominant_language("el equipo de datos / the data team") is None


def test_language_marker_word_lists_are_disjoint() -> None:
    """A token in both lists would vote for both sides at once; the sets
    must stay disjoint or the voting is incoherent."""
    assert not concept_mod._ES_FUNCTION_WORDS & concept_mod._EN_FUNCTION_WORDS


def test_title_language_pure_english_pure_spanish_and_neutral() -> None:
    assert concept_mod._title_language("Recovery of Knowledge Project") == "en"
    assert concept_mod._title_language("Procedimiento de la ingesta") == "es"
    # Neutral (no function words at all) and mixed both decline to vote.
    assert concept_mod._title_language("Model Context Protocol") is None
    assert concept_mod._title_language("Guía de setup and usage") is None


def _es_item(title: str) -> str:
    return (
        f'{{"type": "Concept", "title": "{title}", '
        '"description": "Un objeto derivado.", "body": ""}'
    )


def test_gate_drops_a_pure_wrong_language_non_verbatim_title() -> None:
    """The harmful class exactly: a translatable title rendered in English
    on a Spanish document, with no verbatim support in the prose."""
    source = _spanish_lines(500)
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Recovery of Knowledge Project",
            description="d",
            body="",
        ),
        concept_mod.ExtractionResult(
            type="Concept", title="Procedimiento de ingesta", description="d", body=""
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == ["Procedimiento de ingesta"]
    assert dropped == ("Recovery of Knowledge Project",)
    assert dropped_recombined == ()


def test_gate_keeps_a_verbatim_quoted_wrong_language_title() -> None:
    """`Model Context Protocol`-shaped: the subject's own proper name,
    quoted from the prose -- wrong-language by voting, but verbatim in the
    source, so it passes untouched (case-insensitively)."""
    source = _spanish_lines(500) + "\nAna: El model context protocol ya funciona."
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Model Context Protocol",
            description="d",
            body="",
        ),
        # Same voting class (pure `en` title) but nowhere in the prose.
        concept_mod.ExtractionResult(
            type="Concept",
            title="Decision on the Storage",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == ["Model Context Protocol"]
    assert dropped == ("Decision on the Storage",)
    assert dropped_recombined == ()


def test_gate_keeps_neutral_and_mixed_titles() -> None:
    """A title with no function words (acronyms, proper nouns) carries no
    language; a mixed title is a dominant-language title quoting a term.
    Both pass -- the gate drops only the PURELY wrong-language class."""
    source = _spanish_lines(500)
    results = [
        concept_mod.ExtractionResult(
            type="Concept", title="MCP", description="d", body=""
        ),
        concept_mod.ExtractionResult(
            type="Concept",
            title="Guía de setup and usage",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == ["MCP", "Guía de setup and usage"]
    assert dropped == ()
    assert dropped_recombined == ()


def test_gate_drops_a_neutral_english_recombination(monkeypatch: object) -> None:
    """#630's residual class exactly: a bare English noun phrase -- no
    function words, so #618's voter is blind to it -- assembled from prose
    fragments that never sit adjacent. Non-verbatim, no Spanish
    orthography, bigrams non-adjacent: it drops."""
    source = (
        _spanish_lines(500) + "\nAna: El knowledge del equipo alimenta la recovery del "
        "sistema y el project sigue en curso."
    )
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Knowledge Recovery Project",
            description="d",
            body="",
        ),
        concept_mod.ExtractionResult(
            type="Concept",
            title="Procedimiento de ingesta",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == ["Procedimiento de ingesta"]
    assert dropped == ()
    assert dropped_recombined == ("Knowledge Recovery Project",)


def test_gate_keeps_a_neutral_title_quoted_verbatim() -> None:
    """A neutral multi-word title that IS in the prose is a quote, not a
    recombination -- adjacency passes by construction and the paren-strip
    (#592's precedent) keeps `Proper Name (ACRO)` shaped titles safe."""
    source = (
        _spanish_lines(500)
        + "\nBruno: El evaluation harness ya corre en la integración."
    )
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Evaluation Harness (EH)",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == ["Evaluation Harness (EH)"]
    assert dropped == ()
    assert dropped_recombined == ()


def test_gate_exempts_spanish_orthography_before_the_adjacency_test() -> None:
    """The demonstrated false-positive class (#630): `Snapshot Derivado` --
    Spanish morphology composing an English loanword's singular while the
    prose holds the plural, so adjacency fails structurally. The `-ado`
    orthographic marker exempts it BEFORE the adjacency test, and an
    accented word is exempt the same way."""
    source = (
        _spanish_lines(500)
        + "\nCarla: Los snapshots derivados se regeneran cada noche, y la "
        "migración de configuración sigue su curso."
    )
    results = [
        concept_mod.ExtractionResult(
            type="Concept", title="Snapshot Derivado", description="d", body=""
        ),
        concept_mod.ExtractionResult(
            type="Concept", title="Migración Nocturna", description="d", body=""
        ),
        # A survivor with function words, so the all-drop floor can never
        # mask a broken exemption in this fixture.
        concept_mod.ExtractionResult(
            type="Concept",
            title="Procedimiento de ingesta",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == [
        "Snapshot Derivado",
        "Migración Nocturna",
        "Procedimiento de ingesta",
    ]
    assert dropped == ()
    assert dropped_recombined == ()


def test_gate_keeps_inflected_and_digit_interrupted_spanish_neutral_titles() -> None:
    """#656's live false-positive class: legitimate Spanish-neutral titles
    with NO orthographic marker, whose prose support is morphologically
    inflected (`fuentes inmutables` for `Fuente Inmutable`) or interrupted
    by a numeric token (`un repositorio 100% local` for `Repositorio
    Local`). The inflection-tolerant, digit-blind adjacency must keep both
    -- while a genuine English recombination in the same document still
    drops, so the fold cannot have widened into an exemption."""
    source = (
        _spanish_lines(500)
        + "\nDiego: Queremos un repositorio 100% local para el equipo, y las "
        "fuentes inmutables nunca se reescriben."
        + "\nAna: El knowledge del equipo alimenta la recovery del sistema y "
        "el project sigue en curso."
    )
    results = [
        concept_mod.ExtractionResult(
            type="Concept", title="Repositorio Local", description="d", body=""
        ),
        concept_mod.ExtractionResult(
            type="Concept", title="Fuente Inmutable", description="d", body=""
        ),
        concept_mod.ExtractionResult(
            type="Concept",
            title="Knowledge Recovery Project",
            description="d",
            body="",
        ),
        # A survivor with function words, so the all-drop floor can never
        # mask a broken fold in this fixture.
        concept_mod.ExtractionResult(
            type="Concept",
            title="Procedimiento de ingesta",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == [
        "Repositorio Local",
        "Fuente Inmutable",
        "Procedimiento de ingesta",
    ]
    assert dropped == ()
    assert dropped_recombined == ("Knowledge Recovery Project",)


def test_gate_separates_language_drops_from_recombination_drops() -> None:
    """#780: the two branches of the gate answer opposite operator
    questions -- a wrong-language vote says the model is leaking English
    into a Spanish corpus (check the language anchor), a #630 recombination
    says the model is inventing non-verbatim titles (check extraction
    quality). One merged list forced the notice to mislabel one class as
    the other, pointing a whole investigation at the wrong subsystem."""
    source = (
        _spanish_lines(500) + "\nAna: El knowledge del equipo alimenta la recovery del "
        "sistema y el project sigue en curso."
    )
    results = [
        # Pure-English function words: the #618 language-vote class.
        concept_mod.ExtractionResult(
            type="Concept",
            title="Decision on the Storage",
            description="d",
            body="",
        ),
        # Gate-neutral English noun phrase, bigrams non-adjacent: #630.
        concept_mod.ExtractionResult(
            type="Concept",
            title="Knowledge Recovery Project",
            description="d",
            body="",
        ),
        concept_mod.ExtractionResult(
            type="Concept",
            title="Procedimiento de ingesta",
            description="d",
            body="",
        ),
    ]

    kept, dropped_language, dropped_recombined = (
        concept_mod._drop_wrong_language_titles(results, source_text=source)
    )

    assert [r.title for r in kept] == ["Procedimiento de ingesta"]
    assert dropped_language == ("Decision on the Storage",)
    assert dropped_recombined == ("Knowledge Recovery Project",)


def test_chunked_extraction_reports_recombination_drops_in_their_own_field() -> None:
    """End to end on the chunked path (#780): a #630 recombination drop
    lands in `recombined_dropped_titles`, never in
    `wrong_language_dropped_titles` -- the report keeps the two classes
    apart so the notice can name the branch that fired."""
    text = _spanish_lines(19_000)
    assert len(text) > concept_mod._CHUNK_THRESHOLD
    windows = concept_mod._chunk_lines(text)
    replies: list[str | Exception] = ["[]"] * len(windows)
    replies[0] = _array(_es_item("Procedimiento de ingesta"))
    replies[1] = _array(_es_item("Knowledge Recovery Project"))
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept(text, source_title="Notas", llm=llm)

    assert [r.title for r in outcome.objects] == ["Procedimiento de ingesta"]
    assert outcome.report.recombined_dropped_titles == ("Knowledge Recovery Project",)
    assert outcome.report.wrong_language_dropped_titles == ()


def test_adjacency_normalization_folds_inflection_and_digits() -> None:
    """#656's mechanics, at the seam: pure-digit tokens dissolve on both
    sides, a trailing `s` on a >3-letter word folds on both sides, and a
    3-letter word never folds (`dos` stays `dos`)."""
    assert concept_mod._bigram_adjacent(
        "Repositorio Local", "un repositorio 100% local"
    )
    assert concept_mod._bigram_adjacent("Fuente Inmutable", "las fuentes inmutables")
    # Symmetric fold: a plural title against singular prose also matches.
    assert concept_mod._bigram_adjacent("Fuentes Inmutables", "la fuente inmutable")
    # A 3-letter word never folds -- `dos` must not become `do`.
    assert concept_mod._bigram_adjacent("Fase Dos", "la fase dos del plan")
    assert not concept_mod._bigram_adjacent("Fase Dos", "la fase del plan dos veces")
    # Digit words in the TITLE dissolve too, so `Fase 2` has one word left
    # and passes as no-bigrams rather than never matching.
    assert concept_mod._bigram_adjacent("Fase 2", "cualquier prosa")
    # The fold must not manufacture adjacency for a recombination.
    assert not concept_mod._bigram_adjacent(
        "Knowledge Project", "el knowledge recovery y el project"
    )


def test_no_english_function_word_triggers_the_orthographic_exemption() -> None:
    """The disjointness discipline, extended to #630's marker list: no
    English function word may carry a Spanish orthographic marker, or the
    exemption would shield the exact class the gate exists to drop."""
    for word in concept_mod._EN_FUNCTION_WORDS:
        assert not concept_mod._spanish_orthography(word), word


def test_bigram_adjacency_mechanics() -> None:
    """The check itself: a verbatim quote passes, a recombination fails, a
    single word or acronym has no bigrams and passes, and prose punctuation
    dissolves so a sentence boundary does not break adjacency."""
    prose = "El knowledge recovery. Project nuevo del equipo."

    assert concept_mod._bigram_adjacent("Knowledge Recovery", prose)
    # `recovery` and `project` sit across a sentence boundary -- adjacent
    # after punctuation dissolves (the LENIENT reading; it only reduces
    # drops).
    assert concept_mod._bigram_adjacent("Recovery Project", prose)
    assert not concept_mod._bigram_adjacent("Knowledge Project", prose)
    assert concept_mod._bigram_adjacent("MCP", prose)


def test_gate_fails_open_when_the_source_has_no_dominant_language() -> None:
    """No dominant language, no gate: a heavily code-switched document the
    voter cannot call is left alone rather than guessed at."""
    source = "el equipo de datos / the data team"
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Recovery of Knowledge Project",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == ["Recovery of Knowledge Project"]
    assert dropped == ()
    assert dropped_recombined == ()


def test_gate_keeps_everything_when_it_would_drop_everything() -> None:
    """The floor: a gate that empties the extraction result silently
    deletes real content behind a classifier's opinion -- same philosophy
    as `_drop_source_title_twins`' floor. All-drop means the voter is
    probably wrong about this document; keep the set untouched."""
    source = _spanish_lines(500)
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Recovery of Knowledge Project",
            description="d",
            body="",
        ),
        concept_mod.ExtractionResult(
            type="Concept",
            title="Decision on the Storage",
            description="d",
            body="",
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert kept == results
    assert dropped == ()
    assert dropped_recombined == ()


def test_chunked_extraction_drops_wrong_language_titles_and_reports() -> None:
    """End to end on the chunked `extract_concept` path: a window emitting
    the harmful class loses that title, the report names it, and the
    dominant-language objects survive."""
    text = _spanish_lines(19_000)
    assert len(text) > concept_mod._CHUNK_THRESHOLD
    windows = concept_mod._chunk_lines(text)
    replies: list[str | Exception] = ["[]"] * len(windows)
    replies[0] = _array(_es_item("Procedimiento de ingesta"))
    replies[1] = _array(_es_item("Recovery of Knowledge Project"))
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept(text, source_title="Notas", llm=llm)

    assert [r.title for r in outcome.objects] == ["Procedimiento de ingesta"]
    assert outcome.report.wrong_language_dropped_titles == (
        "Recovery of Knowledge Project",
    )


def test_single_call_path_never_runs_the_language_gate() -> None:
    """Below `_CHUNK_THRESHOLD` the gate does not exist: the 39 short
    fixture documents showed ZERO leakage in either direction (#563), so
    gating them would risk false drops on a path with no measured defect."""
    text = _spanish_lines(2_000)
    assert len(text) <= concept_mod._CHUNK_THRESHOLD
    llm = _FakeLLM(reply=_array(_es_item("Recovery of Knowledge Project")))

    outcome = concept_mod.extract_concept(text, source_title="Notas", llm=llm)

    assert [r.title for r in outcome.objects] == ["Recovery of Knowledge Project"]
    assert outcome.report.wrong_language_dropped_titles == ()


def test_union_chunked_path_drops_wrong_language_titles_and_reports() -> None:
    """The union path's chunked branch applies the same gate at the same
    point (#581's symmetric-filters precedent), before the judge sees the
    candidate list."""
    text = _spanish_lines(19_000)
    windows = concept_mod._chunk_lines(text)
    replies: list[str | Exception] = ["[]"] * len(windows)
    replies[0] = _array(_es_item("Procedimiento de ingesta"))
    replies[1] = _array(_es_item("Decision on the Storage"))
    # The judge call follows the window calls; keep everything it is shown.
    replies.append('{"keep": ["Procedimiento de ingesta"]}')
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept_union(text, source_title="Notas", llm=llm)

    assert [r.title for r in outcome.objects] == ["Procedimiento de ingesta"]
    assert outcome.report.wrong_language_dropped_titles == ("Decision on the Storage",)


def test_gate_verbatim_check_ignores_parenthesized_acronym_suffixes() -> None:
    """`Model Context Protocol (MCP)` is the proper name plus its acronym --
    the `(...)` span breaks a raw substring match against prose that names
    the protocol without it, and dropping the title for that would delete a
    correct proper name (#592's balanced-span precedent, applied to the
    verbatim check)."""
    source = _spanish_lines(500) + "\nAna: El model context protocol ya funciona."
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Model Context Protocol (MCP)",
            description="d",
            body="",
        ),
        concept_mod.ExtractionResult(
            type="Concept", title="Procedimiento de ingesta", description="d", body=""
        ),
    ]

    kept, dropped, dropped_recombined = concept_mod._drop_wrong_language_titles(
        results, source_text=source
    )

    assert [r.title for r in kept] == [
        "Model Context Protocol (MCP)",
        "Procedimiento de ingesta",
    ]
    assert dropped == ()
    assert dropped_recombined == ()


# --- Content-shape transcript detection (#673) ------------------------------

_TRANSCRIPT_SHAPED_TEXT = (
    "# AMI meeting TS3005a\n"
    "\n"
    "A: Okay, shall we get started with the agenda?\n"
    "B: Yes, I have the notes from last time.\n"
    "A: Great, first item is the remote control design.\n"
    "C: I think the shape should be curved.\n"
    "B: The market research supports that.\n"
    "A: Any objections to the curved shape?\n"
    "C: None from me.\n"
    "B: Let's also talk about the battery.\n"
    "A: The battery should be rechargeable.\n"
    "C: Agreed, rechargeable is better.\n"
    "B: Then we are settled on both points.\n"
    "A: Moving on to the next item now.\n"
)
"""Twelve speaker turns across three recurring labels, one heading line --
the structural shape of a real meeting transcript whose TITLE carries no
gathering word (`TS3005a transcript`, the #673 null-experiment shape)."""


def test_transcript_shaped_text_fires_on_speaker_turns() -> None:
    """#673: recurring short speaker labels covering most of the document
    are the transcript signature -- deterministic, zero model calls."""
    assert concept_mod._transcript_shaped_text(_TRANSCRIPT_SHAPED_TEXT)


def test_transcript_shaped_text_is_language_neutral() -> None:
    """#673 constraint: structure, not lexicon -- a Spanish transcript with
    Spanish names fires identically, with no word list involved."""
    text = "\n".join(
        [
            "Ana: ¿Empezamos con la agenda de hoy?",
            "Luis: Sí, tengo las notas de la última vez.",
            "Ana: Primero el diseño del control remoto.",
            "Luis: Creo que la forma debería ser curva.",
            "Ana: ¿Alguna objeción a la forma curva?",
            "Luis: Ninguna por mi parte.",
            "Ana: También hablemos de la batería.",
            "Luis: De acuerdo, recargable es mejor.",
            "Ana: Entonces quedamos en ambos puntos.",
            "Luis: Perfecto, seguimos avanzando.",
        ]
    )
    assert concept_mod._transcript_shaped_text(text)


def test_transcript_shaped_text_accepts_timestamped_turns() -> None:
    """#673: timestamped turn prefixes (`[00:12:34] A: ...`) are the other
    named transcript line shape; the timestamp must not hide the label."""
    lines = []
    for index in range(6):
        lines.append(f"[00:1{index}:02] A: a point being made here")
        lines.append(f"[00:1{index}:40] B: a reply to that point")
    assert concept_mod._transcript_shaped_text("\n".join(lines))


def test_transcript_shaped_text_rejects_prose() -> None:
    """An ordinary markdown document must never fire -- the #459 asymmetry:
    a false positive silently reroutes the document to the meeting prompt
    branch, which measurably regressed recall when applied broadly."""
    text = (
        "# Designing the sync engine\n"
        "\n"
        "The sync engine reconciles local and remote state. It uses a\n"
        "three-way merge over content digests.\n"
        "\n"
        "## Conflict handling\n"
        "\n"
        "Conflicts are surfaced to the user rather than auto-resolved.\n"
        "The design favours explicitness over convenience.\n"
    )
    assert not concept_mod._transcript_shaped_text(text)


def test_transcript_shaped_text_rejects_key_value_blocks() -> None:
    """`key: value` metadata blocks share the colon shape but not the
    RECURRENCE shape -- every key appears once, so no label recurs."""
    text = "\n".join(
        [
            "name: openkos",
            "version: 0.2.4",
            "license: MIT",
            "author: someone",
            "homepage: https://example.org",
            "language: python",
            "keywords: knowledge, extraction",
            "status: active",
            "audience: developers",
            "platform: any",
            "encoding: utf-8",
            "layout: src",
        ]
    )
    assert not concept_mod._transcript_shaped_text(text)


def test_transcript_shaped_text_rejects_log_lines() -> None:
    """Log files recur on severity labels (`INFO:`, `ERROR:`) and on
    date-time prefixes; both are structurally excluded (all-caps labels of
    three or more letters, digit-bearing labels) without any lexicon."""
    plain = "\n".join(
        ["INFO: service starting", "ERROR: bind failed", "INFO: retrying"] * 4
    )
    dated = "\n".join(
        f"2026-08-13 10:22:{sec:02d} INFO: heartbeat ok" for sec in range(12)
    )
    assert not concept_mod._transcript_shaped_text(plain)
    assert not concept_mod._transcript_shaped_text(dated)


def test_transcript_shaped_text_rejects_single_speaker() -> None:
    """A single recurring label is a narration or log shape, not a
    conversation -- two distinct recurring speakers minimum."""
    text = "\n".join(f"Narrator: line number {n} of the story" for n in range(12))
    assert not concept_mod._transcript_shaped_text(text)


def test_transcript_shaped_text_rejects_below_turn_floor() -> None:
    """Two speakers exchanging a handful of lines is a quote, not a
    transcript -- the floor keeps short embedded dialogues out."""
    text = "\n".join(
        [
            "A: a first point",
            "B: a first reply",
            "A: a second point",
            "B: a second reply",
            "A: a third point",
            "B: a third reply",
            "A: a fourth point",
            "B: a fourth reply",
        ]
    )
    assert not concept_mod._transcript_shaped_text(text)


def test_transcript_shaped_text_rejects_dialogue_buried_in_prose() -> None:
    """A transcript EXCERPT inside a longer article must not flip the whole
    document: turn lines must cover at least half of the non-blank lines."""
    prose = [
        f"Prose sentence number {n} discussing the interview in detail."
        for n in range(30)
    ]
    dialogue = [
        "A: a quoted question from the interview"
        if n % 2 == 0
        else "B: a quoted answer"
        for n in range(10)
    ]
    assert not concept_mod._transcript_shaped_text("\n".join(prose + dialogue))


def test_meeting_shaped_predicate_is_title_or_content() -> None:
    """#673: `_is_meeting_shaped` extends -- never replaces -- the title
    gate. Either signal alone is sufficient; neither is necessary."""
    assert concept_mod._is_meeting_shaped("Team Meeting", "ordinary prose")
    assert concept_mod._is_meeting_shaped("TS3005a transcript", _TRANSCRIPT_SHAPED_TEXT)
    assert not concept_mod._is_meeting_shaped("API Reference Guide", "ordinary prose")


def test_capture_fires_on_code_titled_transcript_source() -> None:
    """#673 requested outcome: the participant machinery (#668 D6 capture
    pass) fires on a source whose TITLE is a code but whose CONTENT is
    speaker-turn shaped -- the exact null-experiment configuration that
    measured inert under the title-only gate."""
    run1 = _array(_CONCEPT_ITEM)
    run2 = _array(_DECISION_ITEM)
    llm = _SequencedLLM(
        [
            run1,
            run2,
            _array(_CAPTURED_PARTICIPANT_ITEM),
            _keep_reply("Stoicism", "Frame the Essay Around Control", "Sam Okafor"),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        _TRANSCRIPT_SHAPED_TEXT, source_title="TS3005a transcript", llm=llm
    )

    titles = {r.title for r in outcome.objects}
    assert "Sam Okafor" in titles
    assert outcome.report.participant_capture_runs == 1


def test_capture_still_skips_code_titled_prose_source() -> None:
    """The widened gate must not leak: a code-titled ORDINARY document
    (neither title nor content transcript-shaped) spends no capture call."""
    run1 = _array(_CONCEPT_ITEM)
    run2 = _array(_PERSON_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Stoicism", "Epictetus")])

    outcome = concept_mod.extract_concept_union(
        "A technical article.", source_title="TS3005a transcript", llm=llm
    )

    assert len(llm.calls) == 3
    assert outcome.report.participant_capture_runs == 0


def test_prompt_omits_code_title_on_transcript_shaped_source() -> None:
    """#673: the prompt channel follows the same single predicate -- a
    content-detected transcript takes the no-title branch (with its #522
    language anchor), exactly as a gathering-titled source does."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept(
        _TRANSCRIPT_SHAPED_TEXT, source_title="TS3005a transcript", llm=llm
    )

    user_content = llm.calls[0][1]["content"]
    assert "TS3005a transcript" not in user_content
    assert "SOURCE TITLE" not in user_content
    assert "same language as the SOURCE TEXT" in user_content


def test_transcript_shaped_text_rejects_markdown_structure_lines() -> None:
    """FP found by the #673 repo sweep: an SDD spec recurs on
    `#### Requirement:` and `- Scenario:` lines, which share the
    label-colon shape. A speaker turn never begins with a markdown
    structure character -- excluded structurally, no lexicon."""
    blocks = []
    for n in range(8):
        blocks.append(f"#### Requirement: The system MUST do thing {chr(97 + n)}")
        blocks.append(f"- Scenario: WHEN thing happens THEN outcome {chr(97 + n)}")
        blocks.append(f"- Scenario: WHEN other thing THEN outcome {chr(97 + n)}")
    assert not concept_mod._transcript_shaped_text("\n".join(blocks))


# ---------------------------------------------------------------------------
# #701 -- the extractor reports the progress it already knows
# ---------------------------------------------------------------------------


def test_union_reports_a_chunk_counter_for_a_chunked_source() -> None:
    """A chunked source names which window it is on, `i/N`.

    A 4m 28s ingest showed ONE static line for its entire duration while the
    engine underneath ran a model call per ~4 KB window. Perceived duration
    is not measured duration: four minutes behind `chunk 7/12` reads as a
    system working through a known amount of work, and the same four minutes
    behind a frozen line reads as a hang -- which users act on with Ctrl+C.
    """
    seen: list[str] = []
    text = "Una frase con contenido suficiente.\n" * 900
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    outcome = concept_mod.extract_concept_union(
        text, source_title="Notas", llm=llm, on_progress=seen.append
    )

    chunks = outcome.report.chunks
    assert chunks > 1, "fixture must exceed the chunk threshold to test this"
    chunk_phases = [p for p in seen if p.startswith("extracting chunk ")]
    assert chunk_phases == [
        f"extracting chunk {i}/{chunks}" for i in range(1, chunks + 1)
    ]


def test_union_reports_both_passes_for_an_unchunked_source() -> None:
    """Below the chunk threshold the union runs the identical prompt TWICE,
    so `pass 1/2` and `pass 2/2` is the honest counter -- reporting a chunk
    count of 1 would describe a shape this branch does not have."""
    seen: list[str] = []
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM))

    concept_mod.extract_concept_union(
        "A short source.", source_title="Notes", llm=llm, on_progress=seen.append
    )

    assert "extracting pass 1/2" in seen
    assert "extracting pass 2/2" in seen
    assert not [p for p in seen if p.startswith("extracting chunk ")]


def test_union_names_the_judge_phase() -> None:
    """The judge is a distinct wait, and it is the LAST one -- a run that
    appeared to stop after the final chunk was in fact still judging."""
    seen: list[str] = []
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM))

    concept_mod.extract_concept_union(
        "A short source.", source_title="Notes", llm=llm, on_progress=seen.append
    )

    judging = [p for p in seen if p.startswith("judging ")]
    assert judging == ["judging 2 candidates"]


def test_union_stays_silent_about_a_judge_it_never_runs() -> None:
    """A single candidate makes the judge a provable no-op and it is skipped
    (#644). Announcing a phase that does not run would be worse than saying
    nothing: the reader would attribute the next silent stretch to it."""
    seen: list[str] = []
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))

    concept_mod.extract_concept_union(
        "A short source.", source_title="Notes", llm=llm, on_progress=seen.append
    )

    assert not [p for p in seen if p.startswith("judging ")]


def test_union_names_the_reask_only_when_it_actually_fires() -> None:
    """The re-ask is conditional, so its phase is too.

    `_add_reask_subjects` returns `(0, ())` and makes NO call when its
    trigger does not fire, and a phase line printed regardless would be a
    label on a wait that never happened.
    """
    fired: list[str] = []
    quiet: list[str] = []
    # Sole object, long source -> the low-yield arm of the trigger fires.
    long_text = "x" * (concept_mod._REASK_LOW_YIELD_THRESHOLD + 1)
    concept_mod.extract_concept_union(
        long_text,
        source_title="Notes",
        llm=_FakeLLM(reply=_array(_CONCEPT_ITEM)),
        on_progress=fired.append,
    )
    # Two objects -> the `len == 1` conjunct fails, no re-ask.
    concept_mod.extract_concept_union(
        "A short source.",
        source_title="Notes",
        llm=_FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM)),
        on_progress=quiet.append,
    )

    assert "re-asking for a further subject" in fired
    assert "re-asking for a further subject" not in quiet


def test_extraction_runs_unchanged_without_a_progress_hook() -> None:
    """`on_progress=None` is the default and costs nothing.

    Every existing caller -- the eval harnesses among them -- keeps calling
    the two extractors positionally with no hook, so the seam must be purely
    additive.
    """
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM))

    outcome = concept_mod.extract_concept_union(
        "A short source.", source_title="Notes", llm=llm
    )

    assert [result.title for result in outcome.objects] == [
        "Stoicism",
        "Zettelkasten App",
    ]


def test_single_run_extractor_also_reports_its_chunks() -> None:
    """The `union_judge: false` rollback path chunks too, and `ingest` picks
    between the two extractors at runtime -- so a hook wired to only one of
    them would make the progress display depend on a config key the operator
    set for an unrelated reason."""
    seen: list[str] = []
    text = "Una frase con contenido suficiente.\n" * 900

    outcome = concept_mod.extract_concept(
        text,
        source_title="Notas",
        llm=_FakeLLM(reply=_array(_CONCEPT_ITEM)),
        on_progress=seen.append,
    )

    chunks = outcome.report.chunks
    assert chunks > 1
    assert [p for p in seen if p.startswith("extracting chunk ")] == [
        f"extracting chunk {i}/{chunks}" for i in range(1, chunks + 1)
    ]


def test_union_names_the_participant_pass_only_on_a_meeting_shaped_source() -> None:
    """The participant capture is gated on `meeting_shaped`, so its phase is
    too.

    Both directions in one test, because either alone passes for the wrong
    reason: a test that only checks the meeting case cannot tell a correct
    gate from no gate at all, and one that only checks the ordinary case
    cannot tell a correct gate from a phase that never fires.
    """
    meeting: list[str] = []
    ordinary: list[str] = []

    concept_mod.extract_concept_union(
        "Notes from the session.",
        source_title="AMI meeting TS3005b",
        llm=_FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM)),
        on_progress=meeting.append,
    )
    concept_mod.extract_concept_union(
        "A short source.",
        source_title="Notes",
        llm=_FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM)),
        on_progress=ordinary.append,
    )

    assert "capturing further participants" in meeting
    assert "capturing further participants" not in ordinary


def test_single_run_extractor_names_its_short_source_phases() -> None:
    """`extract_concept`'s non-chunked branch reports too, including the
    #524 empty-result retry -- the one wait a reader would otherwise see as
    the first call simply taking twice as long."""
    first: list[str] = []
    retried: list[str] = []

    concept_mod.extract_concept(
        "A short source.",
        source_title="Notes",
        llm=_FakeLLM(reply=_array(_CONCEPT_ITEM)),
        on_progress=first.append,
    )
    # An empty reply triggers the single retry (#524).
    concept_mod.extract_concept(
        "A short source.",
        source_title="Notes",
        llm=_FakeLLM(reply="[]"),
        on_progress=retried.append,
    )

    assert first[0] == "extracting the source"
    assert "retrying an empty extraction" not in first
    assert "retrying an empty extraction" in retried


def test_a_raising_progress_hook_never_fails_the_extraction() -> None:
    """A broken display must not destroy four minutes of model work.

    This seam fires once per chunk into a LIVE terminal display, which has
    more ways to fail than a plain stderr write. The extraction is the
    valuable thing; the counter describing it is not.
    """

    def _explode(_phase: str) -> None:
        raise RuntimeError("the terminal went away")

    outcome = concept_mod.extract_concept_union(
        "A short source.",
        source_title="Notes",
        llm=_FakeLLM(reply=_array(_CONCEPT_ITEM, _ENTITY_ITEM)),
        on_progress=_explode,
    )

    assert [result.title for result in outcome.objects] == [
        "Stoicism",
        "Zettelkasten App",
    ]


def test_a_progress_hook_never_swallows_a_keyboard_interrupt() -> None:
    """Ctrl+C during a long ingest is the user asking to stop.

    The isolation above catches `Exception`, deliberately not
    `BaseException`: swallowing `KeyboardInterrupt` here would break Ctrl+C
    during exactly the long wait #701 exists to make bearable.
    """

    def _interrupt(_phase: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        concept_mod.extract_concept_union(
            "A short source.",
            source_title="Notes",
            llm=_FakeLLM(reply=_array(_CONCEPT_ITEM)),
            on_progress=_interrupt,
        )


# --- Advisory name grounding (#712 design D5) ------------------------------


def test_names_absent_from_source_flags_a_name_the_source_never_writes() -> None:
    """#712 D5: with the anchor gate retired, the ONE remaining check on a
    proposed participant is whether the source writes their name at all.

    It is ADVISORY, never rejecting. The owner ruling is that every named
    person is identified, so a check that DROPS on a near-miss would delete a
    real person -- the exact failure the retired gate committed, in the other
    direction. The consequence here is a printed line."""
    results = [
        concept_mod.ExtractionResult(
            type="Person",
            title="Ana Ríos",
            description="Chaired the meeting.",
            body="",
        ),
        concept_mod.ExtractionResult(
            type="Person",
            title="Someone Invented",
            description="Attended.",
            body="",
        ),
    ]

    absent = concept_mod._names_absent_from_source(
        results, source_text="Ana Ríos: empecemos por el índice."
    )

    assert absent == ("Someone Invented",)


def test_names_absent_from_source_tolerates_an_accent_the_model_dropped() -> None:
    """`Germán` in the source and `German` in the candidate is the SAME
    person losing a diacritic in the model's transcription, not a fabricated
    name. NFD decomposition plus a combining-mark strip on BOTH sides buys
    that, and the alternative -- flagging it -- would train the operator to
    ignore this notice."""
    results = [
        concept_mod.ExtractionResult(
            type="Person",
            title="German Vega",
            description="Representative.",
            body="",
        )
    ]

    absent = concept_mod._names_absent_from_source(
        results, source_text="Desde Vega Ingeniería habla Germán Vega."
    )

    assert absent == ()


def test_names_absent_from_source_skips_a_label_only_transcript() -> None:
    """AMI transcripts name their speakers `A:`, `B:`, `C:` and nothing
    else. Every proposed participant on such a source is 'absent' by
    construction, so the check computes NOTHING there rather than emitting a
    flood of false alarms that would bury the one real case."""
    results = [
        concept_mod.ExtractionResult(
            type="Person",
            title="User Interface Designer",
            description="Presented the concept.",
            body="",
        )
    ]
    source = "\n".join(
        line
        for _ in range(6)
        for line in ("A: Let us start.", "B: Agreed, next item.", "C: One moment.")
    )

    assert concept_mod._names_absent_from_source(results, source_text=source) == ()


def test_names_absent_from_source_ignores_non_participant_types() -> None:
    """Only `Person`/`Organization` carry a name the source is expected to
    write. A `Concept` title is SYNTHESIZED -- the module's own standing
    rule -- so grounding it would flag every correct object."""
    results = [
        concept_mod.ExtractionResult(
            type="Concept",
            title="Retrieval Quality Review",
            description="A synthesized topic title.",
            body="",
        )
    ]

    absent = concept_mod._names_absent_from_source(
        results, source_text="Hablamos de la calidad de la recuperación."
    )

    assert absent == ()


def test_union_reports_participant_names_absent_from_source() -> None:
    """The advisory has to REACH a caller. `_names_absent_from_source`
    computed and never reported is the same defect #690 spent a PR
    diagnosing -- `participant_unreadmitted_discarded_titles` sat on the
    report since #668 with no reader -- one function further along."""
    invented = (
        '{"type": "Person", "title": "Nadie Real", '
        '"description": "Attended the meeting.", "body": ""}'
    )
    run1 = _array(_CONCEPT_ITEM, invented)
    run2 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, run2, "[]", _keep_reply("Stoicism", "Nadie Real")])

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes. Ana ran the agenda.",
        source_title="Team Meeting",
        llm=llm,
    )

    assert outcome.report.participant_names_absent_from_source == ("Nadie Real",)


def test_union_reports_no_absent_names_when_the_source_writes_them() -> None:
    """The healthy path stays silent, like every other notice in this
    module -- an advisory that fires on correct runs is one the operator
    learns to skip."""
    real = (
        '{"type": "Person", "title": "Ana", '
        '"description": "Ran the agenda.", "body": ""}'
    )
    run1 = _array(_CONCEPT_ITEM, real)
    run2 = _array(_CONCEPT_ITEM)
    llm = _SequencedLLM([run1, run2, "[]", _keep_reply("Stoicism", "Ana")])

    outcome = concept_mod.extract_concept_union(
        "Team meeting notes. Ana ran the agenda.",
        source_title="Team Meeting",
        llm=llm,
    )

    assert outcome.report.participant_names_absent_from_source == ()


def test_names_absent_from_source_requires_a_word_boundary() -> None:
    """R4/R3 review finding on PR #719: the grounding was a raw substring
    test, so a short name was 'found' inside any unrelated word that
    happened to contain it -- `Ana` in `mañana`, `Vega` in `Vegas`.

    That silently un-fires the advisory exactly where it is most needed: a
    short fabricated name is the easiest to hallucinate and the easiest to
    find by accident. The bias table declares which false NEGATIVES are
    accepted; this one was not among them, it was a bug."""
    results = [
        concept_mod.ExtractionResult(
            type="Person", title="Ana", description="Attended.", body=""
        )
    ]

    absent = concept_mod._names_absent_from_source(
        results, source_text="Seguimos mañana con el análisis pendiente."
    )

    assert absent == ("Ana",)


def test_names_absent_from_source_still_matches_a_name_next_to_punctuation() -> None:
    """The boundary must not be so strict it breaks the ordinary case: a
    transcript writes `Ana:` with a colon, and a source writing `(Ana)` or
    `Ana,` is the same person. A rule that only matched a space-delimited
    name would flag every real speaker."""
    results = [
        concept_mod.ExtractionResult(
            type="Person", title="Ana", description="Ran the agenda.", body=""
        )
    ]

    assert (
        concept_mod._names_absent_from_source(
            results, source_text="Ana: partamos por el índice."
        )
        == ()
    )


def test_names_absent_from_source_exempts_a_mostly_label_only_transcript() -> None:
    """R4 review finding on PR #719: the exemption asked `all()` over every
    matched label, so ONE longer label anywhere in an otherwise `A:`/`B:`
    transcript disabled it for the whole source and re-flagged every
    role-titled participant.

    An AMI transcript with a single `Presenter:` line is still a source that
    does not state real names, and the flood of false alarms is what buries
    the one real case."""
    lines = [
        line
        for _ in range(6)
        for line in ("A: Let us start.", "B: Agreed.", "C: One moment.")
    ]
    lines.append("Presenter: Thanks everyone.")
    results = [
        concept_mod.ExtractionResult(
            type="Person",
            title="Industrial Designer",
            description="Presented.",
            body="",
        )
    ]

    absent = concept_mod._names_absent_from_source(
        results, source_text="\n".join(lines)
    )

    assert absent == ()


def test_single_run_extraction_also_reports_absent_participant_names() -> None:
    """R1 review finding on PR #719: the advisory was computed at ONE
    construction site. `cli/main.py` picks
    `extract_concept_union if union_judge else extract_concept`, so with
    `union_judge` off the legacy single path stored participants and
    reported nothing -- the same "computed but never surfaced" defect #690
    spent a PR diagnosing."""
    invented = (
        '{"type": "Person", "title": "Nadie Real", '
        '"description": "Attended the meeting.", "body": ""}'
    )
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM, invented))

    outcome = concept_mod.extract_concept(
        "Team meeting notes. Ana ran the agenda.",
        source_title="Team Meeting",
        llm=llm,
    )

    assert outcome.report.participant_names_absent_from_source == ("Nadie Real",)


# --------------------------------------------------------------------------- #
# Concurrent fan-out (#744)                                                     #
# --------------------------------------------------------------------------- #


class _WindowKeyedLLM:
    """A structural `LLMBackend` that answers by WINDOW CONTENT, not call order.

    `_SequencedLLM` cannot serve a concurrent fan-out: it indexes
    `replies[len(self.calls) - 1]`, so which reply a window receives depends on
    which thread reached `chat` first. Every assertion built on it would be
    describing the scheduler rather than the code under test. Keying on the
    window text makes a window's reply identical under any interleaving, which
    is the only condition under which an ORDER assertion means anything.

    `on_call` runs inside the call, before the reply, and is how a test
    manufactures a completion order that differs from the input order.
    """

    def __init__(
        self,
        windows: Sequence[str],
        replies: dict[int, str | Exception],
        *,
        on_call: "Callable[[int], None] | None" = None,
    ) -> None:
        self._windows = list(windows)
        self._replies = replies
        self._on_call = on_call
        self._lock = threading.Lock()
        self.call_order: list[int] = []
        self.call_threads: list[int] = []

    def _index_for(self, user: str) -> int:
        for index, window in enumerate(self._windows):
            if window in user:
                return index
        raise AssertionError("no window matched the prompt")

    def chat(self, messages: Sequence[Message]) -> str:
        index = self._index_for(messages[1]["content"])
        with self._lock:
            self.call_order.append(index)
            self.call_threads.append(threading.get_ident())
        if self._on_call is not None:
            self._on_call(index)
        reply = self._replies.get(index, "[]")
        if isinstance(reply, Exception):
            raise reply
        return reply


def _finish_second_window_first() -> "Callable[[int], None]":
    """An `on_call` hook that makes window 0 finish AFTER window 1, with no
    wall-clock assumption.

    A fixed `time.sleep` in window 0 would only PROBABLY produce that order:
    under scheduling delay on a loaded runner, window 0 could still finish
    first, and the test would then pass VACUOUSLY -- input order and
    completion order would agree, which is precisely the condition an
    `as_completed` implementation also satisfies. A handshake removes the
    guess: window 0 blocks until window 1 has been served, so the inversion
    the test depends on is a fact rather than a race it usually wins.

    The pool has at least two slots, so window 1 is always reachable while
    window 0 waits. The timeout only stops a hang from becoming an infinite
    one; it is never reached on a correct implementation.
    """
    second_served = threading.Event()

    def _hook(index: int) -> None:
        if index == 1:
            second_served.set()
        elif index == 0:
            second_served.wait(timeout=10)

    return _hook


def _titled_item(title: str) -> str:
    return (
        f'{{"type": "Concept", "title": "{title}", '
        f'"description": "From one window.", "body": ""}}'
    )


def test_concurrent_fan_out_actually_runs_two_windows_at_once() -> None:
    """Two windows must be in flight SIMULTANEOUSLY, proven by a barrier.

    Without this the whole feature can be inert and every other test here
    still passes: `map` over a pool of one, or a `concurrent` flag never
    threaded through, produces identical results, identical order and
    identical call counts -- only the wall clock would differ, and no unit
    test reads that. The barrier is the only assertion that can see the
    regime it guards. Serial execution cannot release it: the first window
    would wait alone until the timeout and raise `BrokenBarrierError`.
    """
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    assert len(windows) >= 2
    barrier = threading.Barrier(2, timeout=10)

    def _wait(index: int) -> None:
        # ONLY the first two windows meet at the barrier. Attaching it to every
        # window would make the test depend on the window COUNT being even: the
        # barrier resets after each pair, so an odd tail window would wait alone
        # for the full timeout and fail with BrokenBarrierError for a reason
        # that has nothing to do with concurrency. The pool has two slots, so
        # windows 0 and 1 are always the two that start together.
        if index < 2:
            barrier.wait()

    llm = _WindowKeyedLLM(windows, {}, on_call=_wait)

    concept_mod.extract_concept(
        text, source_title="Field Notes", llm=llm, concurrent=True
    )

    assert len(llm.call_order) == len(windows)
    assert len(set(llm.call_threads)) > 1


def test_concurrent_fan_out_keeps_window_order_when_completion_order_differs() -> None:
    """Results concatenate in WINDOW order even when the first window is the
    last to finish -- the `map`-not-`as_completed` rule, made falsifiable.

    `_dedup_merged` keeps the FIRST occurrence of a `(type, normalized title)`
    key, so completion-ordered results would silently change which duplicate
    wins: a quality regression wearing a throughput change's clothes. Window 0
    is deliberately slowed so that an `as_completed` implementation would put
    its object LAST and fail here.
    """
    text = _long_text()
    windows = concept_mod._chunk_lines(text)

    llm = _WindowKeyedLLM(
        windows,
        {
            0: _array(_titled_item("First Window")),
            1: _array(_titled_item("Second Window")),
        },
        on_call=_finish_second_window_first(),
    )

    outcome = concept_mod.extract_concept(
        text, source_title="Field Notes", llm=llm, concurrent=True
    )

    assert llm.call_order[0] == 0  # submitted first...
    assert [r.title for r in outcome.objects] == ["First Window", "Second Window"]


def test_concurrent_fan_out_dedup_keeps_the_earlier_WINDOW_not_the_earlier_reply() -> (
    None
):
    """The same subject in two windows resolves to the earlier WINDOW's copy,
    even when the later window answers first. This is the concrete data-loss
    `as_completed` would cause, asserted on the surviving description."""
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    early = (
        '{"type": "Concept", "title": "Stoicism", '
        '"description": "The earlier window.", "body": ""}'
    )
    late = (
        '{"type": "Concept", "title": "stoicism", '
        '"description": "The later window.", "body": ""}'
    )

    llm = _WindowKeyedLLM(
        windows,
        {0: _array(early), 1: _array(late)},
        on_call=_finish_second_window_first(),
    )

    outcome = concept_mod.extract_concept(
        text, source_title="Field Notes", llm=llm, concurrent=True
    )

    assert [r.title for r in outcome.objects] == ["Stoicism"]
    assert outcome.objects[0].description == "The earlier window."


def test_concurrent_fan_out_reports_progress_only_from_the_calling_thread() -> None:
    """The `on_progress` hook (#701) must never be invoked from a worker.

    A display writing to stderr from two threads interleaves its own output,
    and the hook's contract was written for a single-threaded caller. Consuming
    `map`'s iterator on the calling thread is what keeps this true without a
    lock -- so this test is the reason the implementation may stay lock-free.
    """
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    llm = _WindowKeyedLLM(windows, {})
    seen: list[int] = []

    concept_mod.extract_concept(
        text,
        source_title="Field Notes",
        llm=llm,
        concurrent=True,
        on_progress=lambda _phase: seen.append(threading.get_ident()),
    )

    assert seen
    assert set(seen) == {threading.get_ident()}


def test_concurrent_fan_out_propagates_a_window_failure_and_discards_the_rest() -> None:
    """A backend failure on any window propagates unswallowed and takes every
    partial result with it -- `concept.py`'s existing all-or-nothing contract,
    unchanged by concurrency. `map` re-raises in INPUT order, so the exception
    a caller sees does not depend on which thread failed first."""
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    llm = _WindowKeyedLLM(
        windows,
        {0: _array(_titled_item("Survivor")), 1: OllamaUnavailable("backend down")},
    )

    with pytest.raises(OllamaUnavailable):
        concept_mod.extract_concept(
            text, source_title="Field Notes", llm=llm, concurrent=True
        )


def test_serial_fan_out_is_the_default_and_stays_single_threaded() -> None:
    """`concurrent` defaults to False, and the serial path is untouched: one
    call per window, in window order, all on the calling thread. The default
    must remain byte-identical because a stock Ollama serializes anyway --
    threading it would buy nothing and change the #701 progress vocabulary for
    every user who never opted in."""
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    llm = _WindowKeyedLLM(windows, {})

    concept_mod.extract_concept(text, source_title="Field Notes", llm=llm)

    assert llm.call_order == list(range(len(windows)))
    assert set(llm.call_threads) == {threading.get_ident()}


def test_union_path_fans_out_concurrently_too() -> None:
    """Both production entry points take the flag, per `_chunk_threshold_for`'s
    one-definition rule: `cli/main.py` picks between them on `union_judge`, so
    a lever wired into one only would leave whether #744 is active depending on
    an unrelated setting."""
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    assert len(windows) >= 2
    barrier = threading.Barrier(2, timeout=10)

    def _wait(index: int) -> None:
        # ONLY the first two windows meet at the barrier. Attaching it to every
        # window would make the test depend on the window COUNT being even: the
        # barrier resets after each pair, so an odd tail window would wait alone
        # for the full timeout and fail with BrokenBarrierError for a reason
        # that has nothing to do with concurrency. The pool has two slots, so
        # windows 0 and 1 are always the two that start together.
        if index < 2:
            barrier.wait()

    llm = _WindowKeyedLLM(windows, {}, on_call=_wait)

    concept_mod.extract_concept_union(
        text, source_title="Field Notes", llm=llm, concurrent=True
    )

    assert len(set(llm.call_threads)) > 1


def test_fan_out_concurrency_is_two_and_not_a_tunable() -> None:
    """#739 measured the speedup saturating at 2 (arms 2, 3 and 4 are
    statistically indistinguishable, t~0.05) while memory keeps climbing
    (9.1 / 11.1 / 12.9 GB). The value is a private constant precisely so no
    config key can raise it past the point the measurement supports."""
    assert concept_mod.FAN_OUT_CONCURRENCY == 2


def test_concurrent_fan_out_drains_in_flight_windows_before_it_raises() -> None:
    """A failure must not return while a sibling window is still running.

    Executor threads are not daemons, so leaving one in flight does not skip
    the wait -- it moves it to interpreter exit, where the CLI has already
    printed "keeping the Source only" and the command appears to hang for up
    to one `chat_timeout`. Window 0 fails instantly while window 1 is still
    inside its call; the flag can only be set if the fan-out drained it
    before propagating.
    """
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    drained = threading.Event()

    def _slow_second(index: int) -> None:
        if index == 1:
            time.sleep(0.3)
            drained.set()

    llm = _WindowKeyedLLM(
        windows,
        {0: OllamaUnavailable("backend down")},
        on_call=_slow_second,
    )

    with pytest.raises(OllamaUnavailable):
        concept_mod.extract_concept(
            text, source_title="Field Notes", llm=llm, concurrent=True
        )

    assert drained.is_set()


def test_concurrent_fan_out_reports_before_the_first_window_returns() -> None:
    """Progress must appear BEFORE the first window completes.

    Completion-only reporting leaves the display frozen for the whole first
    call -- 5 to 20 seconds on the latencies this change measures -- which is
    the exact "line that reads as a hang" condition #701 exists to prevent.
    The serial path never had this gap because it reports before each call.
    The hook is asserted to have fired while every window is still blocked,
    so a fan-out that only reported completions cannot pass.
    """
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    released = threading.Event()
    reported_before_any_return: list[str] = []

    def _block(_index: int) -> None:
        released.wait(timeout=10)

    def _on_progress(phase: str) -> None:
        reported_before_any_return.append(phase)
        released.set()  # only reachable if a report preceded every return

    llm = _WindowKeyedLLM(windows, {}, on_call=_block)

    concept_mod.extract_concept(
        text,
        source_title="Field Notes",
        llm=llm,
        concurrent=True,
        on_progress=_on_progress,
    )

    assert reported_before_any_return
    assert str(len(windows)) in reported_before_any_return[0]
    assert str(concept_mod.FAN_OUT_CONCURRENCY) in reported_before_any_return[0]


# --- fans_out: the public "will this source overlap windows" seam (#746) -----


def test_fans_out_is_false_below_the_threshold() -> None:
    """A source that fits one whole-document call has no windows to overlap,
    so #744's lever cannot be involved in anything that happens to it."""
    assert not concept_mod.fans_out("Short notes about control.", source_title="Notes")


def test_fans_out_is_true_above_the_threshold() -> None:
    """A chunked source is exactly the case the fan-out governs."""
    text = _long_text()

    assert concept_mod.fans_out(text, source_title="Field Notes")


def test_fans_out_follows_the_meeting_shaped_boundary_not_a_fixed_number() -> None:
    """The boundary BRANCHES on shape since #714 -- 12 000 chars for a
    meeting-shaped source against 18 000 otherwise -- so a caller must ask
    this function rather than compare against a constant of its own.

    The text below sits between the two thresholds, which is the only region
    where a fixed-number caller and the real pipeline disagree.

    Its content is PLAIN PROSE, with no speaker labels: `_is_meeting_shaped`
    is a title gate OR a content gate since #673, so text that could itself
    read as a transcript would leave the two arms differing in two ways at
    once, and the non-meeting arm passing for the wrong reason. Here only the
    title varies, which is exactly the gate this test means to exercise."""
    line = "The team reviewed the quarterly numbers " + "x" * 40
    text = "\n".join(f"{line} {i:04d}" for i in range(200))
    assert 12_000 < len(text) < 18_000
    assert not concept_mod._transcript_shaped_text(text)

    assert concept_mod.fans_out(text, source_title="Weekly Sync Meeting")
    assert not concept_mod.fans_out(text, source_title="Field Notes")


def test_fans_out_is_false_exactly_at_the_threshold() -> None:
    """The boundary is `>`, not `>=`: a source of EXACTLY the threshold length
    still takes the whole-document path.

    Pinned because an off-by-one here silently moves a source onto a
    different pipeline -- one chat call becomes several, with different
    prompts and a different selection path -- while every other test, which
    sits comfortably on one side or the other, keeps passing."""
    threshold = concept_mod._chunk_threshold_for(meeting_shaped=False)
    text = "x" * threshold
    assert not concept_mod._is_meeting_shaped("Field Notes", text)

    assert not concept_mod.fans_out(text, source_title="Field Notes")
    assert concept_mod.fans_out(text + "x", source_title="Field Notes")


# --- #754: an unavailable judge must not hand the cap an unreviewed set ----


def _many_items(n: int) -> str:
    return _array(
        *(
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": ""}}'
            for i in range(1, n + 1)
        )
    )


def test_union_skips_the_positional_cap_when_the_judge_is_unavailable() -> None:
    """#754's compounding pair. When both judge attempts fail, NOTHING has
    ranked the candidates -- so applying `_UNION_BACKSTOP` afterwards cuts by
    ARRIVAL ORDER, and the three objects it dropped in the reported run were
    not the weakest, merely the last emitted.

    Owner ruling: keep the unreviewed set whole rather than compound an
    arbitrary cut onto it. The set is still bounded -- `_MAX_JUDGE_CANDIDATES`
    (24) capped it before the judge ever saw it -- so this cannot grow
    without limit."""
    llm = _SequencedLLM([_many_items(23), _array(), "not json", "not json"])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "failed"
    assert len(outcome.objects) == 23
    assert outcome.report.produced == 23
    assert outcome.report.retained == 23
    assert outcome.report.discarded_titles == ()


def test_the_skipped_cap_is_still_bounded_by_the_pre_judge_ceiling() -> None:
    """Skipping the backstop does not remove every bound: the 24-candidate
    pre-judge ceiling already truncated the merged union, and it is what
    keeps an unavailable judge from storing an unbounded set."""
    llm = _SequencedLLM([_many_items(40), _array(), "not json", "not json"])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "failed"
    assert len(outcome.objects) == concept_mod._MAX_JUDGE_CANDIDATES
    assert outcome.report.pre_judge_dropped == 40 - concept_mod._MAX_JUDGE_CANDIDATES


def test_the_cap_still_applies_when_the_judge_selected_normally() -> None:
    """The skip is scoped to `failed`. A successful selection IS a ranking,
    so cutting its tail at the backstop is a cut through ranked material and
    stays exactly as it was."""
    judge_reply = _keep_reply(*(f"Subject {i}" for i in range(1, 24)))
    llm = _SequencedLLM([_many_items(23), _array(), judge_reply])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "ok"
    assert len(outcome.objects) == concept_mod._UNION_BACKSTOP
    assert outcome.report.discarded_titles != ()


def test_the_cap_still_applies_when_the_judge_replied_but_matched_nothing() -> None:
    """Deliberate boundary. `empty` means the judge RAN and its reply named
    no candidate -- a legible verdict the operator can act on, unlike
    `failed`, where nothing is known about the set at all.

    The arbitrariness argument does partly apply here too (the kept set is
    equally unranked), and that is recorded rather than acted on: widening
    the skip is a second decision, not a detail of this one."""
    llm = _SequencedLLM([_many_items(23), _array(), _keep_reply("Nothing Matching")])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert outcome.report.judge_status == "empty"
    assert len(outcome.objects) == concept_mod._UNION_BACKSTOP


# --- Quoted-evidence advisory (#801) ---------------------------------------

_HELIOS_SOURCE = (
    "# Project Helios sync\n"
    "\n"
    "- Priya owns the schema migration plan.\n"
    "- The team chose PostgreSQL as the primary datastore.\n"
)
"""The shape of the real source behind #801, reduced to the lines that
decide the check. The migration-ownership sentence is the one the defect
dropped."""

_UNEVIDENCED_DECISION = concept_mod.ExtractionResult(
    type="Decision",
    title="Schema migration ownership decision",
    description="Who owns the schema migration plan for Project Helios.",
    body=(
        "# Schema migration ownership decision\n"
        "The decision regarding who owns the schema migration plan for "
        "Project Helios."
    ),
)
"""#801's actual stored object, strings intact: its whole body restates its
own description, and the source sentence it came from is gone."""

_EVIDENCED_DECISION = concept_mod.ExtractionResult(
    type="Decision",
    title="Primary datastore decision",
    description="The datastore the team settled on.",
    body="The team chose PostgreSQL as the primary datastore.",
)

_EVIDENCED_PERSON = concept_mod.ExtractionResult(
    type="Person",
    title="Priya Nair",
    description="Owns the schema migration plan.",
    body="Priya owns the schema migration plan.",
)


def test_unevidenced_titles_flags_only_the_object_that_quotes_nothing() -> None:
    """#801's regression, built from the reported run's own strings.

    The `Decision` whose body restates its description is flagged, while
    the two siblings from the SAME source are not. That contrast is the
    whole test: seven of eight objects in the reported run DID carry a
    quoted line, so a check that flagged them all would be measuring
    nothing about #801 -- it would just be reporting every object."""
    results = [_UNEVIDENCED_DECISION, _EVIDENCED_DECISION, _EVIDENCED_PERSON]

    unevidenced = concept_mod._unevidenced_titles(results, source_text=_HELIOS_SOURCE)

    assert unevidenced == ("Schema migration ownership decision",)


def test_unevidenced_titles_reports_in_results_order() -> None:
    """Order is the caller's contract, mirroring every other named-title
    field on the report: the operator reads the notice against the objects
    the run wrote, in the order it wrote them."""
    other = dataclasses.replace(
        _UNEVIDENCED_DECISION, title="Another Ungrounded Decision"
    )
    results = [_UNEVIDENCED_DECISION, _EVIDENCED_PERSON, other]

    unevidenced = concept_mod._unevidenced_titles(results, source_text=_HELIOS_SOURCE)

    assert unevidenced == (
        "Schema migration ownership decision",
        "Another Ungrounded Decision",
    )


def test_unevidenced_titles_falls_back_to_description_on_a_blank_body() -> None:
    """The check must read the text that will BE WRITTEN.
    `ExtractionResult.body` is documented as optionally blank, with the
    builder falling back to `description` -- so checking `body` alone would
    let every blank-body object through unexamined, and a blank body is
    precisely how #801's object would have escaped if the model had put its
    restatement in `description` instead.

    Here the description DOES quote the source, so the object is grounded
    and must not be flagged."""
    results = [
        concept_mod.ExtractionResult(
            type="Person",
            title="Priya Nair",
            description="Priya owns the schema migration plan.",
            body="",
        )
    ]

    assert concept_mod._unevidenced_titles(results, source_text=_HELIOS_SOURCE) == ()


def test_unevidenced_titles_flags_a_blank_body_whose_description_quotes_nothing() -> (
    None
):
    """The other half of the fallback: falling back must not mean passing.
    A blank body with a paraphrasing description is exactly as ungrounded
    as #801's object and is flagged the same way."""
    results = [
        concept_mod.ExtractionResult(
            type="Decision",
            title="Schema migration ownership decision",
            description=(
                "The decision regarding who owns the schema migration plan "
                "for Project Helios."
            ),
            body="",
        )
    ]

    assert concept_mod._unevidenced_titles(results, source_text=_HELIOS_SOURCE) == (
        "Schema migration ownership decision",
    )


def test_unevidenced_titles_is_empty_when_every_object_quotes_its_source() -> None:
    """The healthy path stays silent, like every other advisory in this
    module -- one that fires on correct runs is one the operator learns to
    skip."""
    results = [_EVIDENCED_DECISION, _EVIDENCED_PERSON]

    assert concept_mod._unevidenced_titles(results, source_text=_HELIOS_SOURCE) == ()


def test_single_run_path_reports_unevidenced_titles() -> None:
    """The LEGACY single-run path reports the advisory too. `cli/main.py`
    picks `extract_concept_union if union_judge else extract_concept`, so
    wiring only the union path would store ungrounded objects and surface
    nothing whenever `union_judge` is off -- the "computed but never read"
    defect #690 already cost a PR, and #712's participant advisory carries
    the same comment for the same reason."""
    ungrounded = (
        '{"type": "Decision", "title": "Schema migration ownership decision", '
        '"description": "Who owns the schema migration plan.", '
        '"body": "The decision regarding who owns the schema migration plan '
        'for Project Helios."}'
    )
    grounded = (
        '{"type": "Decision", "title": "Primary datastore decision", '
        '"description": "The datastore the team settled on.", '
        '"body": "The team chose PostgreSQL as the primary datastore."}'
    )
    llm = _FakeLLM(reply=_array(ungrounded, grounded))

    outcome = concept_mod.extract_concept(
        _HELIOS_SOURCE, source_title="Project Helios sync", llm=llm
    )

    assert outcome.report.unevidenced_titles == ("Schema migration ownership decision",)


def test_union_path_reports_unevidenced_titles() -> None:
    """And the DEFAULT path reports it as well. Both sites, deliberately:
    the two return sites are the pair #712's comment names, and a report
    field populated on one of them is a field whose reader cannot trust
    it."""
    ungrounded = (
        '{"type": "Decision", "title": "Schema migration ownership decision", '
        '"description": "Who owns the schema migration plan.", '
        '"body": "The decision regarding who owns the schema migration plan '
        'for Project Helios."}'
    )
    grounded = (
        '{"type": "Decision", "title": "Primary datastore decision", '
        '"description": "The datastore the team settled on.", '
        '"body": "The team chose PostgreSQL as the primary datastore."}'
    )
    llm = _SequencedLLM(
        [
            _array(ungrounded, grounded),
            _array(grounded),
            _keep_reply(
                "Schema migration ownership decision", "Primary datastore decision"
            ),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        _HELIOS_SOURCE, source_title="Project Helios sync", llm=llm
    )

    assert outcome.report.unevidenced_titles == ("Schema migration ownership decision",)


def test_unevidenced_titles_are_scored_on_the_retained_set() -> None:
    """Computed on what the bundle STORES, never on the pre-cap list -- the
    same rule `sole_object_restates_source` and
    `participant_names_absent_from_source` both state. An object the
    backstop discarded is not stored, so it is not a claim the bundle
    makes, and naming it would send the operator looking for a document
    that does not exist."""
    ungrounded_items = [
        (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "A paraphrase of nothing in the source.", '
            f'"body": "This body quotes no line of the source at all."}}'
        )
        for i in range(1, concept_mod._UNION_BACKSTOP + 4)
    ]
    judge_reply = _keep_reply(
        *(f"Subject {i}" for i in range(1, concept_mod._UNION_BACKSTOP + 4))
    )
    llm = _SequencedLLM([_array(*ungrounded_items), _array(), judge_reply])

    outcome = concept_mod.extract_concept_union(
        _HELIOS_SOURCE, source_title="Project Helios sync", llm=llm
    )

    assert outcome.report.discarded_titles != ()
    assert len(outcome.report.unevidenced_titles) == len(outcome.objects)
    assert set(outcome.report.unevidenced_titles).isdisjoint(
        outcome.report.discarded_titles
    )


def test_unevidenced_titles_defaults_to_empty_on_a_bare_report() -> None:
    """Defaulted so every existing construction site -- both
    `evals/model_spike/` harnesses included -- keeps working unchanged, and
    `()` is the honest default: it claims no disclosure, which is what an
    untouched site is entitled to claim."""
    assert concept_mod.ExtractionReport().unevidenced_titles == ()


# --- the judge's failure causes reach the report (#795) --------------------
#
# `judge.select` names WHY each attempt failed since #795, and
# `extract_concept_union` carries that onto `ExtractionReport`. Nothing
# between the two was executed end to end: `test_judge.py` calls `select`
# directly and `test_judge_failure_notice.py` builds the report by hand, so a
# bug in the wiring itself would have been caught by neither.


def _two_subject_reply() -> str:
    return json.dumps(
        [
            {
                "type": "Concept",
                "title": "Trazabilidad documental",
                "description": "Un principio de registro verificable.",
                "body": "",
            },
            {
                "type": "Decision",
                "title": "Cifrado de respaldos",
                "description": "Se acuerda cifrar los respaldos nocturnos.",
                "body": "",
            },
        ],
        ensure_ascii=False,
    )


def test_union_carries_the_judge_failure_causes_onto_the_report() -> None:
    """Two extraction passes, then a judge whose every attempt is unusable.

    The causes must arrive on the report NAMED, not merely counted: the
    whole point of #795 is that a timeout, a parse failure and a backend
    refusal need different fixes.
    """
    llm = _SequencedLLM(
        [_two_subject_reply(), _two_subject_reply()]
        + ["not json at all"] * judge_mod.JUDGE_ATTEMPTS
    )

    outcome = concept_mod.extract_concept_union(
        "Un documento breve sobre trazabilidad y respaldos.",
        source_title="Notas",
        llm=llm,
    )

    assert outcome.report.judge_status == "failed"
    assert (
        outcome.report.judge_failure_causes
        == (f"{judge_mod.JUDGE_FAILURE_UNPARSEABLE}: no-json",)
        * judge_mod.JUDGE_ATTEMPTS
    )


def test_union_carries_a_recovered_failure_onto_the_report() -> None:
    """The half that the retry hid.

    The judge fails once and succeeds on the retry, so the SELECTION is
    fine -- and the run still had a real judge failure. Reporting only when
    the selection is unusable would under-count by exactly these, which is
    the rate #795 measured at 2 of 3 and could not see from a run's output.
    """
    llm = _SequencedLLM(
        [
            _two_subject_reply(),
            _two_subject_reply(),
            "not json at all",
            json.dumps({"keep": ["Trazabilidad documental"]}),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        "Un documento breve sobre trazabilidad y respaldos.",
        source_title="Notas",
        llm=llm,
    )

    assert outcome.report.judge_status == "ok"
    assert outcome.report.judge_failure_causes == (
        f"{judge_mod.JUDGE_FAILURE_UNPARSEABLE}: no-json",
    )


def test_a_judge_that_never_ran_reports_no_causes() -> None:
    """A single merged candidate skips the judge call entirely (#644), so
    it failed at nothing. Empty causes and "the judge was not called" stay
    one value rather than needing a sentinel."""
    single = json.dumps(
        [
            {
                "type": "Concept",
                "title": "Trazabilidad documental",
                "description": "Un principio de registro verificable.",
                "body": "",
            }
        ],
        ensure_ascii=False,
    )
    llm = _SequencedLLM([single, single])

    outcome = concept_mod.extract_concept_union(
        "Un documento breve sobre trazabilidad.", source_title="Notas", llm=llm
    )

    assert outcome.report.judge_status == "skipped"
    assert outcome.report.judge_failure_causes == ()


# --- an optional call names WHY it added nothing (#828) ---------------------
#
# `_reask_for_further_subjects` and `_capture_further_participants` both
# collapsed every backend failure into `[]`, which is byte-identical to the
# answer their own prompts name as correct and expected -- "nothing further
# here". #828 measured an Ollama runaway hitting the 8192-token generation
# ceiling inside one of them: 222 seconds paid, and the run reported nothing
# at all. This is the gap #795 closed for the judge, in the two places that
# still had it, and the degrade contract those two functions are built on is
# unchanged: the additions are still empty and the exception still never
# propagates.

_MEETING_TITLE = "Weekly platform meeting"
"""Meeting-shaped by `_MEETING_SHAPED_TITLE_RE`, so the participant-capture
pass fires on it and `extract_concept_union` spends BOTH optional calls."""


def _long_meeting_text() -> str:
    """At least `_REASK_LOW_YIELD_THRESHOLD` chars and well under
    `_CHUNK_THRESHOLD`: long enough for the #642 low-yield re-ask arm to
    trigger on a sole object, short enough to keep the unchunked two-run
    union path (and therefore a countable call sequence)."""
    line = "The team reviewed the nightly backup pipeline once again. "
    text = line * 40
    assert len(text) >= concept_mod._REASK_LOW_YIELD_THRESHOLD
    assert len(text) < concept_mod._CHUNK_THRESHOLD
    return text


_SOLE_SUBJECT_ITEM = (
    '{"type": "Concept", "title": "Backup Encryption", '
    '"description": "Nightly backups are encrypted at rest.", "body": ""}'
)
"""One object whose title neither restates `_MEETING_TITLE` nor matches
`_MEETING_SHAPED_TITLE_RE`, so it survives every filter and leaves the
merged list at exactly one candidate -- the re-ask trigger, and the
single-candidate union that skips the judge call entirely (#644)."""


def _kept_subject() -> "concept_mod.ExtractionResult":
    return concept_mod.ExtractionResult(
        type="Concept",
        title="Backup Encryption",
        description="Nightly backups are encrypted at rest.",
        body="",
    )


def test_the_reask_names_the_exception_type_when_the_backend_fails() -> None:
    """The cause the pre-#828 `except Exception: return []` destroyed."""
    llm = _SequencedLLM([OllamaUnavailable("ollama is not running")])

    outcome = concept_mod._reask_for_further_subjects(
        _long_meeting_text(), _MEETING_TITLE, _kept_subject(), llm
    )

    assert outcome.failure == f"{concept_mod.OPTIONAL_CALL_REASK}: OllamaUnavailable"


def test_the_reask_reports_no_failure_when_the_call_succeeds() -> None:
    """`None` means the call ran, so an empty `additions` beside it is the
    honest "found nothing further" the re-ask prompt asks for."""
    llm = _SequencedLLM([_array(_CONCEPT_ITEM)])

    outcome = concept_mod._reask_for_further_subjects(
        _long_meeting_text(), _MEETING_TITLE, _kept_subject(), llm
    )

    assert outcome.failure is None
    assert [result.title for result in outcome.additions] == ["Stoicism"]


def test_the_reask_still_adds_nothing_and_never_raises_when_the_backend_fails() -> None:
    """The guard the whole #584 design rests on, unchanged by #828: a bonus
    call's failure must never destroy the object the first pass produced,
    so it degrades to empty additions and the exception stays inside."""
    llm = _SequencedLLM([OllamaGenerationCapped("generation hit the ceiling")])

    outcome = concept_mod._reask_for_further_subjects(
        _long_meeting_text(), _MEETING_TITLE, _kept_subject(), llm
    )

    assert outcome.additions == []


def test_the_participant_capture_names_the_exception_type_when_it_fails() -> None:
    llm = _SequencedLLM([OllamaUnavailable("ollama is not running")])

    outcome = concept_mod._capture_further_participants(
        _long_meeting_text(), _MEETING_TITLE, llm
    )

    assert outcome.failure == (
        f"{concept_mod.OPTIONAL_CALL_PARTICIPANT_CAPTURE}: OllamaUnavailable"
    )


def test_the_participant_capture_reports_no_failure_when_the_call_succeeds() -> None:
    llm = _SequencedLLM([_array(_PERSON_ITEM)])

    outcome = concept_mod._capture_further_participants(
        _long_meeting_text(), _MEETING_TITLE, llm
    )

    assert outcome.failure is None
    assert [result.title for result in outcome.additions] == ["Epictetus"]


def test_the_participant_capture_adds_nothing_and_never_raises_when_it_fails() -> None:
    llm = _SequencedLLM([OllamaGenerationCapped("generation hit the ceiling")])

    outcome = concept_mod._capture_further_participants(
        _long_meeting_text(), _MEETING_TITLE, llm
    )

    assert outcome.additions == []


def test_an_optional_call_records_the_exception_type_and_never_its_message() -> None:
    """#795's rule, binding here for the same reason: the TYPE separates a
    timeout from a refusal, which is what changes an operator's next move,
    while a MESSAGE can carry a host, a path, or a model's own text into a
    line this repo also writes to a Source's frontmatter. A switch to
    `str(exc)` fails here and nowhere else."""
    leaky = OllamaUnavailable(
        "cannot reach http://192.168.1.42:11434/api/generate while reading "
        "/Users/someone/private/notes.md"
    )

    outcomes = (
        concept_mod._reask_for_further_subjects(
            _long_meeting_text(),
            _MEETING_TITLE,
            _kept_subject(),
            _SequencedLLM([leaky]),
        ),
        concept_mod._capture_further_participants(
            _long_meeting_text(), _MEETING_TITLE, _SequencedLLM([leaky])
        ),
    )

    for outcome in outcomes:
        assert outcome.failure is not None
        assert "OllamaUnavailable" in outcome.failure
        assert "192.168.1.42" not in outcome.failure
        assert "/Users/someone/private/notes.md" not in outcome.failure
        assert "cannot reach" not in outcome.failure


def test_union_carries_both_optional_call_failures_in_pipeline_order() -> None:
    """One sole-object, meeting-shaped source spends BOTH optional calls,
    and both fail with DIFFERENT types -- so the order is readable and a
    single collapsed entry would be visible.

    The order is the order the pipeline spends them: the #584 re-ask feeds
    the merged candidate list first, the #668 participant capture second.
    """
    llm = _SequencedLLM(
        [
            _array(_SOLE_SUBJECT_ITEM),
            _array(_SOLE_SUBJECT_ITEM),
            OllamaUnavailable("ollama is not running"),
            OllamaGenerationCapped("generation hit the ceiling"),
        ]
    )

    outcome = concept_mod.extract_concept_union(
        _long_meeting_text(), source_title=_MEETING_TITLE, llm=llm
    )

    assert outcome.report.optional_call_failures == (
        f"{concept_mod.OPTIONAL_CALL_REASK}: OllamaUnavailable",
        f"{concept_mod.OPTIONAL_CALL_PARTICIPANT_CAPTURE}: OllamaGenerationCapped",
    )
    assert [result.title for result in outcome.objects] == ["Backup Encryption"]


def test_union_leaves_the_optional_call_failures_empty_when_neither_fails() -> None:
    """Both calls RAN and both honestly found nothing: that is the answer
    their prompts name as correct, and it must stay distinguishable from a
    backend that never answered."""
    llm = _SequencedLLM(
        [_array(_SOLE_SUBJECT_ITEM), _array(_SOLE_SUBJECT_ITEM), "[]", "[]"]
    )

    outcome = concept_mod.extract_concept_union(
        _long_meeting_text(), source_title=_MEETING_TITLE, llm=llm
    )

    assert outcome.report.reask_runs == 1
    assert outcome.report.participant_capture_runs == 1
    assert outcome.report.optional_call_failures == ()


def test_the_empty_union_early_return_carries_the_optional_call_failure() -> None:
    """`extract_concept_union` has TWO return sites, and the empty-union one
    (`judge_input` empty, no judge call spent) paid for the participant
    capture exactly as the judged return did.

    Both other union tests script a non-empty union, so neither reaches this
    branch: wiring the cause into the judged return alone would leave this
    one computing `optional_call_failures` and dropping it on the way out --
    the "computed but never read" defect #690 already cost a PR, cited by
    the single-run path's own test above and binding here for the same
    reason.

    The shape that reaches it: a meeting-shaped source whose two extraction
    passes yield no valid candidate at all. The re-ask never fires (its
    trigger reads "exactly one object"), while the participant capture
    fires UNCONDITIONALLY on a meeting-shaped source -- so the run spends a
    real bonus call, that call fails, and the report it returns is the only
    place that failure can still be named."""
    llm = _SequencedLLM(["[]", "[]", OllamaGenerationCapped("hit the ceiling")])

    outcome = concept_mod.extract_concept_union(
        _long_meeting_text(), source_title=_MEETING_TITLE, llm=llm
    )

    assert outcome.objects == []
    # Three calls, not two: the third IS the spent participant capture, and
    # an assertion on the report alone could not tell a swallowed failure
    # from a call that was never made.
    assert len(llm.calls) == 3
    assert outcome.report.reask_runs == 0
    assert outcome.report.participant_capture_runs == 1
    assert outcome.report.judge_status == "skipped"
    assert outcome.report.optional_call_failures == (
        f"{concept_mod.OPTIONAL_CALL_PARTICIPANT_CAPTURE}: OllamaGenerationCapped",
    )


def test_the_single_run_path_carries_the_reask_failure_too() -> None:
    """`cli/main.py` picks `extract_concept` whenever `union_judge` is off,
    so wiring only the union path would spend the call and report nothing
    there -- the "computed but never read" defect #690 already cost a PR."""
    llm = _SequencedLLM(
        [_array(_SOLE_SUBJECT_ITEM), OllamaGenerationCapped("hit the ceiling")]
    )

    outcome = concept_mod.extract_concept(
        _long_meeting_text(), source_title=_MEETING_TITLE, llm=llm
    )

    assert outcome.report.optional_call_failures == (
        f"{concept_mod.OPTIONAL_CALL_REASK}: OllamaGenerationCapped",
    )


def test_optional_call_failures_defaults_to_empty_on_a_bare_report() -> None:
    """Defaulted so every existing construction site keeps working, and `()`
    is the honest default: it claims no failure, which is what an untouched
    site is entitled to claim."""
    assert concept_mod.ExtractionReport().optional_call_failures == ()


def test_a_capture_that_both_fails_and_adds_still_reports_its_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_add_participant_capture` propagates `outcome.failure` on BOTH of its
    branches, and only the empty-additions one is reachable today: a failure
    implies `additions == []` everywhere in production.

    That makes the non-empty branch dead-but-correct wiring, and dead-but-
    correct is exactly what rots. Every other test of a capture failure
    scripts an EMPTY-additions failure, so dropping `failure` from the
    non-empty return would be caught by nothing -- until something made that
    state reachable, at which point a paid call that ran away would be
    reported as a clean run that found somebody.

    The state is therefore constructed rather than provoked. `additions`
    carries a real candidate so `_dedup_merged` runs and the tail is read,
    which is the branch under test, and the assertion is that the cause
    survives the merge rather than being dropped alongside it.
    """
    addition = concept_mod.ExtractionResult(
        type="Person",
        title="Epictetus",
        description="Runs the ingestion pipeline",
        body="Epictetus runs the ingestion pipeline.",
    )
    cause = f"{concept_mod.OPTIONAL_CALL_PARTICIPANT_CAPTURE}: OllamaGenerationCapped"
    monkeypatch.setattr(
        concept_mod,
        "_capture_further_participants",
        lambda *args, **kwargs: concept_mod.OptionalCallOutcome(
            additions=[addition], failure=cause
        ),
    )
    existing = concept_mod.ExtractionResult(
        type="Concept",
        title="Ingestion pipeline",
        description="The pipeline under discussion",
        body="The pipeline under discussion.",
    )

    objects, capture_runs, added_titles, failure = concept_mod._add_participant_capture(
        [existing],
        source_text=_long_meeting_text(),
        source_title=_MEETING_TITLE,
        meeting_shaped=True,
        llm=_SequencedLLM([]),
    )

    assert failure == cause
    assert capture_runs == 1
    assert added_titles == ("Epictetus",)
    assert [result.title for result in objects] == ["Ingestion pipeline", "Epictetus"]
