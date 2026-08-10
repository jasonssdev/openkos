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


def test_extract_json_items_mid_object_truncation_returns_empty_list() -> None:
    """A reply cut off mid-JSON-object degrades to `[]`, never a partial
    parse (#422): a generation-ceiling truncation lands in exactly this
    path, so this behavior -- true today -- is pinned explicitly rather
    than left implicit.

    All four candidates fail in sequence: raw `json.loads` raises on the
    unterminated object; `_strip_code_fence` requires a closing fence,
    absent here; `_first_bracket_block` requires a closing `]`, absent;
    `_first_brace_block` requires a closing `}`, absent. Every candidate
    is `None` or unparseable, so the loop falls through to `[]`.
    """
    truncated = '[{"type": "Person", "title": "Ada Lovelace", "summary": "Ma'

    assert parsing.extract_json_items(truncated) == []


def test_extract_json_items_survives_a_subjects_preamble() -> None:
    """#522: the enumerate-first prompt asks the model to name its subjects
    on a line before the array. Parsing already tolerates prose, but only
    because `_first_bracket_block` finds the FIRST `[...]` -- which is
    exactly why the prompt forbids square brackets in that line."""
    reply = (
        "SUBJECTS: the onboarding step; the Slack decision; pending work\n"
        '[{"type": "Decision", "title": "Postpone Slack"}]'
    )

    assert parsing.extract_json_items(reply) == [
        {"type": "Decision", "title": "Postpone Slack"}
    ]


def test_extract_json_items_recovers_despite_brackets_in_surrounding_prose() -> None:
    """A bracketed phrase before the payload must not cost the payload.

    Both recovery steps used to be greedy -- `\\[.*\\]` with DOTALL spanning
    from the first `[` anywhere to the last `]`, and the brace step spanning
    the first `{` to the last `}` across every object -- so a reply like
    `SUBJECTS: [a, b, c]` followed by a valid array parsed to NOTHING. Not a
    truncated extraction, a destroyed one, and indistinguishable downstream
    from the model answering `[]` (#524)."""
    payload = (
        '[{"type": "Decision", "title": "A"},'
        '{"type": "Event", "title": "B"},'
        '{"type": "Project", "title": "C"}]'
    )

    assert len(parsing.extract_json_items(payload)) == 3
    assert len(parsing.extract_json_items(f"SUBJECTS: a; b; c\n{payload}")) == 3
    assert len(parsing.extract_json_items(f"SUBJECTS: [a, b, c]\n{payload}")) == 3
    assert len(parsing.extract_json_items(f'SUBJECTS: ["a", "b"]\n{payload}')) == 3


def test_extract_json_items_prefers_the_payload_over_an_earlier_json_array() -> None:
    """A preamble that is ITSELF valid JSON must not win.

    `["a", "b"]` parses cleanly to a list of non-dicts. Returning that
    list's (empty) dict subset would be a silent zero-object extraction
    with no error anywhere -- so a candidate that yields no dicts is not an
    answer, and the scan continues."""
    payload = '[{"type": "Decision", "title": "A"}]'

    assert parsing.extract_json_items(f'["a", "b"]\n{payload}') == [
        {"type": "Decision", "title": "A"}
    ]


def test_extract_json_object_recovers_despite_braces_in_surrounding_prose() -> None:
    """`extract_json_object` carried the identical greedy defect, and the
    judge, edge typing, volatility typing and contradiction detection all
    run through it. `\\{.*\\}` spans the first `{` to the last `}`, so a
    reply with any brace-bearing prose around the payload parsed to
    nothing."""
    payload = '{"verdict": "consistent"}'

    assert parsing.extract_json_object(payload) == {"verdict": "consistent"}
    assert parsing.extract_json_object(f"Note: {{unsure}}\n{payload}") == {
        "verdict": "consistent"
    }
    assert parsing.extract_json_object(f"{payload}\nTrailing {{note}}") == {
        "verdict": "consistent"
    }


def test_extract_json_items_keeps_bare_multi_object_replies_fail_closed() -> None:
    """The `{...}{...}` contract survives the greedy regex that produced it.

    D2 recovers a LONE bare object, because a local model routinely emits
    one for a single-subject source. Two back-to-back bare objects stay
    `[]`: returning the first would be silent truncation reported as
    success, which is worse than a visible zero. That outcome used to be an
    accident of `\\{.*\\}` spanning both objects; it is now the stated rule."""
    lone = '{"type": "Decision", "title": "A"}'
    pair = '{"type": "Decision", "title": "A"}{"type": "Event", "title": "B"}'

    assert parsing.extract_json_items(f"Here: {lone}") == [
        {"type": "Decision", "title": "A"}
    ]
    assert parsing.extract_json_items(pair) == []


def test_extract_json_items_does_not_read_a_nested_object_as_a_second_reply() -> None:
    """The scan resumes after each parsed value, never inside it -- so a
    nested object is not mistaken for a back-to-back pair and wrongly
    fail-closed."""
    nested = '{"type": "Decision", "title": "A", "meta": {"depth": 1}}'

    assert parsing.extract_json_items(f"Note: {nested}") == [
        {"type": "Decision", "title": "A", "meta": {"depth": 1}}
    ]
