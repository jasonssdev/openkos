"""Unit tests for `openkos.llm.parsing`: the shared fail-closed JSON
extraction helpers consolidated out of five module-local clones
(`resolution/adjudication.py`, `resolution/edge_typing.py`,
`resolution/volatility_typing.py`, `resolution/contradiction.py`, and the
list-variant `extraction/concept.py`).

`extract_json_object` mirrors the object-variant clones' 3-step recovery
(raw `json.loads`, fenced-code-block strip, first `{...}` block).
`extract_json_items` mirrors `extraction/concept.py`'s 4-step list-variant
recovery (raw, fenced, first `[...]` block, first `{...}` block, with a lone
object recovered as a one-item list). Both fail closed on non-`str` input
without raising.
"""

from openkos.llm import parsing

# ---------------------------------------------------------------------------
# extract_json_object
# ---------------------------------------------------------------------------


def test_extract_json_object_parses_plain_json() -> None:
    assert parsing.extract_json_object('{"verdict": "consistent"}') == {
        "verdict": "consistent"
    }


def test_extract_json_object_recovers_from_code_fence() -> None:
    fenced = '```json\n{"verdict": "consistent"}\n```'

    assert parsing.extract_json_object(fenced) == {"verdict": "consistent"}


def test_extract_json_object_recovers_from_plain_fence_without_json_tag() -> None:
    fenced = '```\n{"verdict": "consistent"}\n```'

    assert parsing.extract_json_object(fenced) == {"verdict": "consistent"}


def test_extract_json_object_recovers_first_brace_block_from_prose() -> None:
    prose = 'Sure, here you go: {"verdict": "consistent"} thanks!'

    assert parsing.extract_json_object(prose) == {"verdict": "consistent"}


def test_extract_json_object_non_string_input_returns_none() -> None:
    assert parsing.extract_json_object(None) is None
    assert parsing.extract_json_object(42) is None
    assert parsing.extract_json_object(["not", "a", "string"]) is None


def test_extract_json_object_non_dict_json_returns_none() -> None:
    assert parsing.extract_json_object("[1, 2, 3]") is None


def test_extract_json_object_unparseable_input_returns_none() -> None:
    assert parsing.extract_json_object("not json at all") is None


# ---------------------------------------------------------------------------
# extract_json_items
# ---------------------------------------------------------------------------


def test_extract_json_items_parses_plain_array() -> None:
    assert parsing.extract_json_items('[{"type": "Person"}, {"type": "Place"}]') == [
        {"type": "Person"},
        {"type": "Place"},
    ]


def test_extract_json_items_recovers_from_code_fence() -> None:
    fenced = '```json\n[{"type": "Person"}]\n```'

    assert parsing.extract_json_items(fenced) == [{"type": "Person"}]


def test_extract_json_items_recovers_first_bracket_block_from_prose() -> None:
    prose = 'Here: [{"type": "Person"}] enjoy!'

    assert parsing.extract_json_items(prose) == [{"type": "Person"}]


def test_extract_json_items_recovers_lone_object_as_one_item_list() -> None:
    prose = 'Here: {"type": "Person"} enjoy!'

    assert parsing.extract_json_items(prose) == [{"type": "Person"}]


def test_extract_json_items_drops_non_dict_array_elements() -> None:
    assert parsing.extract_json_items('[{"type": "Person"}, 42, "x"]') == [
        {"type": "Person"}
    ]


def test_extract_json_items_non_string_input_returns_empty_list() -> None:
    assert parsing.extract_json_items(None) == []
    assert parsing.extract_json_items(42) == []


def test_extract_json_items_unparseable_input_returns_empty_list() -> None:
    assert parsing.extract_json_items("not json at all") == []
