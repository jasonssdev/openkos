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
    exemption must not change its outcome."""
    llm = _FakeLLM(reply=_array(_MARIA_ITEM, _CALL_WITH_MARIA_TWIN_ITEM))

    result = _objects(
        "Maria and I talked about her move.",
        source_title="Call with Maria Salazar — 2026-07-14",
        llm=llm,
    )

    assert [r.title for r in result] == ["Maria Salazar"]


def test_exempt_procedure_alone_is_not_a_survivor_that_drops_others() -> None:
    """#413 floor guard: when the ONLY object is the exempt `Procedure`
    twin, nothing is dropped and nothing else must be invented -- the same
    single-subject floor that already protected the `mcp-launch` shape."""
    llm = _FakeLLM(reply=_array(_RESEARCH_AGENT_PROCEDURE_ITEM))

    result = _objects(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

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
    run's contribution before the union is built, like the twin rule."""
    run1 = _array(_MEETING_DISCUSSION_FRAMING_ITEM, _DECISION_ITEM)
    run2 = _array(_DECISION_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Frame the Essay Around Control")])

    outcome = concept_mod.extract_concept_union(
        "Meeting notes.", source_title="Team Meeting", llm=llm
    )

    assert "Meeting Discussion on Remote Control Design" not in {
        r.title for r in outcome.objects
    }
    judge_call = llm.calls[2]
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
    the behavior every existing measurement was taken against."""
    llm = _FakeLLM(reply=_array(_CONCEPT_ITEM))
    text = "x" * concept_mod._CHUNK_THRESHOLD

    outcome = concept_mod.extract_concept(text, source_title="Notes", llm=llm)

    assert len(llm.calls) == 1
    assert outcome.report.chunks == 1
    assert [r.title for r in outcome.objects] == ["Stoicism"]


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

    The judge is told to keep BOTH titles, so nothing downstream of the twin
    rule can remove the twin for it."""
    run1 = _array(_DICHOTOMY_ITEM)
    run2 = _array(_DICHOTOMY_ITEM, _PERSON_ITEM)
    llm = _SequencedLLM([run1, run2, _keep_reply("Dichotomy of Control", "Epictetus")])

    outcome = concept_mod.extract_concept_union(
        "Notes on what is up to us.",
        source_title="Dichotomy of Control",
        llm=llm,
    )

    assert [r.title for r in outcome.objects] == ["Epictetus"]
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
    `chunks + 1` total calls (one per chunk, plus one judge call), and
    `report.runs == 1`."""
    text = _long_text()
    windows = concept_mod._chunk_lines(text)
    replies: list[str | Exception] = ["[]"] * len(windows)
    replies[0] = _array(_DECISION_ITEM)
    replies.append(_keep_reply("Frame the Essay Around Control"))
    llm = _SequencedLLM(replies)

    outcome = concept_mod.extract_concept_union(text, source_title="Meeting", llm=llm)

    assert len(llm.calls) == len(windows) + 1
    assert outcome.report.runs == 1
    assert outcome.report.chunks == len(windows)


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


def test_union_backstop_truncates_a_judge_selected_set_above_12() -> None:
    """A judge-selected set of more than 12 is truncated to exactly 12,
    applied strictly AFTER re-admission -- not before the judge."""

    def item(i: int) -> str:
        return (
            f'{{"type": "Concept", "title": "Subject {i}", '
            f'"description": "Distinct subject {i}.", "body": ""}}'
        )

    run1 = _array(*(item(i) for i in range(1, 16)))  # 15 distinct
    run2 = _array()
    judge_reply = _keep_reply(*(f"Subject {i}" for i in range(1, 16)))
    llm = _SequencedLLM([run1, run2, judge_reply])

    outcome = concept_mod.extract_concept_union("Notes.", source_title="Notes", llm=llm)

    assert len(outcome.objects) == 12
    assert outcome.report.produced == 15
    assert outcome.report.retained == 12


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


def test_lone_exempt_procedure_does_not_re_ask() -> None:
    """The predicate has TWO conjuncts: normalized title equality AND a type
    that is not `_TWIN_EXEMPT_TYPE`. A lone `Procedure` restating the source
    title is not a droppable twin (#413), so no second call goes out -- the
    `lesson` fixture's lone object is exactly this shape."""
    llm = _SequencedLLM(
        [_array(_RESEARCH_AGENT_PROCEDURE_ITEM), _array(_APATHEIA_ITEM)]
    )

    outcome = concept_mod.extract_concept(
        "A walkthrough of building a research agent.",
        source_title="Building a Research Agent with the Claude Agent SDK",
        llm=llm,
    )

    assert len(llm.calls) == 1
    assert [r.title for r in outcome.objects] == [
        "Building a Research Agent with the Claude Agent SDK"
    ]


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
